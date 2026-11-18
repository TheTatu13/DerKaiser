"""Polite, resilient HTTP for DerKaiser (the safety net under every layer).

* one request at a time PER HOST, at least ``interval`` seconds apart (hosts run in parallel),
* retry with full-jitter exponential backoff on timeouts / connection errors / 429 / 5xx (``Retry-After`` honoured),
* circuit breaker per host: after ``breaker_threshold`` consecutive transient failures the host is skipped for the
  rest of the run (``CircuitOpen``) instead of burning time on timeouts,
* robots.txt respected for page fetches (``Blocked``); a missing/unreadable robots.txt means allowed,
* body size cap, optional on-disk cache (reruns and resumes do not refetch).

The transport is injectable (``opener``), so everything here is tested offline.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

USER_AGENT = "DerKaiser-peViitor/0.1 (volunteer project; github.com/thetatu13)"
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_READ = 64_000_000

# Brewtality's expiry phrases + Romanian ones
EXPIRED_MARKERS = (
    "sorry, this position is no longer available", "position is no longer available", "job is no longer available",
    "this vacancy is no longer available", "no longer accepting applications", "this position has been filled",
    "job expired", "the page you are looking for doesn't exist",
    "anuntul a expirat", "anunțul a expirat", "postul a fost ocupat", "nu mai este disponibil", "pagina nu a fost gasita",
    "pagina nu a fost găsită", "pozitia a fost ocupata", "poziția a fost ocupată",
)


class NetError(Exception):
    """Base for every network-layer failure."""


class HTTPFail(NetError):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} {url}")
        self.status = status


class Blocked(NetError):
    """robots.txt disallows this URL."""


class CircuitOpen(NetError):
    """Host skipped: too many consecutive failures this run."""


Opener = Callable[[urllib.request.Request, float], "tuple[int, dict, bytes]"]


def urllib_opener(req: urllib.request.Request, timeout: float) -> tuple[int, dict, bytes]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers.items()), resp.read(MAX_READ)  # the per-request cap is Net.max_bytes (applied after); this is only a hard ceiling
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, b""


class Net:
    def __init__(self, *, opener: Opener = urllib_opener, interval: float = 3.0, timeout: float = 12.0,
                 max_bytes: int = 2_000_000, retries: int = 2, backoff_base: float = 1.0, backoff_max: float = 20.0,
                 breaker_threshold: int = 4, respect_robots: bool = True, cache_dir: str | Path | None = None,
                 cache_ttl: float = 24 * 3600, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, user_agent: str = USER_AGENT, rng=random.random):
        self.opener, self.interval, self.timeout, self.max_bytes = opener, interval, timeout, max_bytes
        self.retries, self.backoff_base, self.backoff_max = retries, backoff_base, backoff_max
        self.breaker_threshold, self.respect_robots, self.user_agent = breaker_threshold, respect_robots, user_agent
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl, self._sleep, self._clock, self._rng = cache_ttl, sleep, clock, rng
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self._fails: dict[str, int] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.stats = {"requests": 0, "retries": 0, "cache_hits": 0, "blocked": 0, "circuit_open": 0}
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---- plumbing ----
    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def _pace(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.interval - (self._clock() - last)
            if gap > 0:
                self._sleep(gap)
        self._last[host] = self._clock()

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), self.backoff_max)
            except ValueError:
                pass
        return self._rng() * min(self.backoff_max, self.backoff_base * (2 ** attempt))

    def _request(self, method: str, url: str, body: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
        host = urlsplit(url).netloc.lower()
        if self._fails.get(host, 0) >= self.breaker_threshold:
            self.stats["circuit_open"] += 1
            raise CircuitOpen(host)
        hdrs = {"User-Agent": self.user_agent, "Accept": "text/html,application/json,*/*", **(headers or {})}
        last_err: Exception | None = None
        retry_after = None
        for attempt in range(self.retries + 1):
            if attempt:
                self.stats["retries"] += 1
                self._sleep(self._backoff(attempt - 1, retry_after))
            with self._host_lock(host):
                self._pace(host)
                self.stats["requests"] += 1
                try:
                    status, resp_headers, data = self.opener(urllib.request.Request(url, data=body, headers=hdrs, method=method), self.timeout)
                except Exception as exc:  # noqa: BLE001 - timeout / DNS / reset: transient
                    last_err, retry_after = exc, None
                    continue
            if status in RETRY_STATUS and attempt < self.retries:
                last_err, retry_after = HTTPFail(status, url), {k.lower(): v for k, v in resp_headers.items()}.get("retry-after")
                continue
            if status in RETRY_STATUS:
                self._fails[host] = self._fails.get(host, 0) + 1
                raise HTTPFail(status, url)
            self._fails[host] = 0
            return status, data[: self.max_bytes]
        self._fails[host] = self._fails.get(host, 0) + 1
        raise NetError(f"{host}: {last_err.__class__.__name__ if last_err else 'failed'}")

    # ---- robots ----
    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        host = parts.netloc.lower()
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                status, data = self._request("GET", f"{parts.scheme}://{host}/robots.txt")
                if status == 200:
                    rp.parse(data.decode("utf-8", "replace").splitlines())
                    self._robots[host] = rp
                else:
                    self._robots[host] = None
            except NetError:
                self._robots[host] = None
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    # ---- public API ----
    def get(self, url: str) -> str:
        cached = self._cache_read(url)
        if cached is not None:
            self.stats["cache_hits"] += 1
            return cached
        if not self.allowed(url):
            self.stats["blocked"] += 1
            raise Blocked(url)
        status, data = self._request("GET", url)
        if status >= 400:
            raise HTTPFail(status, url)
        text = data.decode("utf-8", "replace")
        self._cache_write(url, text)
        return text

    def post_json(self, url: str, body: dict) -> dict:
        status, data = self._request("POST", url, json.dumps(body).encode(), {"Content-Type": "application/json"})
        if status >= 400:
            raise HTTPFail(status, url)
        return json.loads(data.decode("utf-8", "replace") or "{}")

    def status(self, url: str) -> int:
        """Live check as Brewtality does it: GET + content scan (HEAD lies on some sites). 0 = dead/unreachable."""
        try:
            status, data = self._request("GET", url)
        except NetError:
            return 0
        if status >= 400:
            return status
        if any(m in data[:20000].decode("utf-8", "replace").lower() for m in EXPIRED_MARKERS):
            return 410
        return status

    # ---- cache ----
    def _cache_path(self, url: str) -> Path | None:
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".html") if self.cache_dir else None

    def _cache_read(self, url: str) -> str | None:
        p = self._cache_path(url)
        if p and p.exists() and (time.time() - p.stat().st_mtime) < self.cache_ttl:
            try:
                return p.read_text(encoding="utf-8")
            except OSError:
                return None
        return None

    def _cache_write(self, url: str, text: str) -> None:
        p = self._cache_path(url)
        if p:
            try:
                tmp = p.with_suffix(".tmp")
                tmp.write_text(text, encoding="utf-8")
                tmp.replace(p)
            except OSError:
                pass
