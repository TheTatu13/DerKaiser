"""JavaScript rendering with the browser already on the PC (Chrome or Edge, headless ``--dump-dom``): nothing to install.

Used only when the static layers found nothing. Polite by construction: robots.txt is checked first (``allowed``),
renders are paced per host, at most ``max_parallel`` browsers run at once, every render has a hard timeout and its
own throwaway profile folder. The process runner is injectable, so this is tested offline.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

BROWSER_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/microsoft-edge",
)


class RenderError(Exception):
    """The page could not be rendered (blocked by robots, browser failed, timeout, empty DOM)."""


def find_browser(env=os.environ, exists=os.path.exists, which=shutil.which) -> str | None:
    forced = env.get("DERKAISER_BROWSER")
    if forced and exists(forced):
        return forced
    for p in BROWSER_PATHS:
        if exists(p):
            return p
    for name in ("chrome", "msedge", "chromium", "google-chrome"):
        found = which(name)
        if found:
            return found
    return None


class Renderer:
    def __init__(self, browser: str | None = None, *, runner: Callable = subprocess.run,
                 allowed: Callable[[str], bool] = lambda url: True, max_parallel: int = 2, timeout: float = 40.0,
                 virtual_time_ms: int = 9000, min_interval: float = 5.0, min_dom_chars: int = 200,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.browser = browser or find_browser()
        self.runner, self.allowed, self.timeout, self.virtual_time_ms = runner, allowed, timeout, virtual_time_ms
        self.min_interval, self.min_dom_chars, self._sleep, self._clock = min_interval, min_dom_chars, sleep, clock
        self._sem = threading.Semaphore(max_parallel)
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self.stats = {"renders": 0, "failed": 0, "blocked": 0}

    @property
    def available(self) -> bool:
        return bool(self.browser)

    def _pace(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.min_interval - (self._clock() - last)
            if gap > 0:
                self._sleep(gap)
        self._last[host] = self._clock()

    def __call__(self, url: str) -> str:
        if not self.browser:
            raise RenderError("no browser found")
        if not self.allowed(url):
            self.stats["blocked"] += 1
            raise RenderError("blocked by robots.txt")
        host = urlsplit(url).netloc.lower()
        with self._lock:
            host_lock = self._host_locks.setdefault(host, threading.Lock())
        with self._sem, host_lock:
            self._pace(host)
            self.stats["renders"] += 1
            with tempfile.TemporaryDirectory(prefix="dk_profile_") as profile:
                args = [self.browser, "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions",
                        "--mute-audio", "--blink-settings=imagesEnabled=false", f"--user-data-dir={profile}",
                        f"--virtual-time-budget={self.virtual_time_ms}", "--dump-dom", url]
                try:
                    proc = self.runner(args, capture_output=True, timeout=self.timeout)
                except Exception as exc:  # noqa: BLE001 - timeout, missing binary...
                    self.stats["failed"] += 1
                    raise RenderError(f"{exc.__class__.__name__}") from exc
        html = (proc.stdout or b"").decode("utf-8", "replace")
        if proc.returncode != 0 or len(html) < self.min_dom_chars:
            self.stats["failed"] += 1
            raise RenderError(f"browser exit {proc.returncode}, dom {len(html)} chars")
        return html
