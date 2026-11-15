import types

import pytest

from scraper import render as R

DOM = "<html>" + "x" * 300 + "</html>"


def proc(out=DOM.encode(), code=0):
    return types.SimpleNamespace(stdout=out, returncode=code)


def mk(runner, **kw):
    slept = []
    r = R.Renderer("chrome.exe", runner=runner, sleep=slept.append, **kw)
    return r, slept


def test_render_builds_a_headless_dump_dom_command_with_a_throwaway_profile():
    seen = []

    def runner(args, capture_output, timeout):
        seen.append((args, timeout))
        return proc()
    r, _ = mk(runner)
    assert r("https://a.ro/cariere") == DOM
    args, timeout = seen[0]
    assert args[0] == "chrome.exe" and "--headless=new" in args and "--dump-dom" in args and args[-1] == "https://a.ro/cariere"
    assert any(a.startswith("--user-data-dir=") for a in args) and any(a.startswith("--virtual-time-budget=") for a in args)
    assert timeout == 40.0


def test_render_failures_raise_render_error():
    with pytest.raises(R.RenderError):
        mk(lambda *a, **k: proc(code=1))[0]("https://a.ro")
    with pytest.raises(R.RenderError):
        mk(lambda *a, **k: proc(out=b"<html></html>"))[0]("https://a.ro")  # empty DOM

    def timeout(*a, **k):
        raise TimeoutError
    r, _ = mk(timeout)
    with pytest.raises(R.RenderError):
        r("https://a.ro")
    assert r.stats["failed"] == 1


def test_no_browser_means_unavailable(monkeypatch):
    monkeypatch.setattr(R, "find_browser", lambda *a, **k: None)
    r = R.Renderer(runner=lambda *a, **k: proc())
    assert r.available is False
    with pytest.raises(R.RenderError):
        r("https://a.ro")


def test_render_respects_robots_and_paces_per_host():
    r, slept = mk(lambda *a, **k: proc(), allowed=lambda u: "private" not in u, min_interval=5.0)
    t = [0.0]
    r._clock = lambda: t[0]
    r("https://a.ro/1")
    t[0] = 2.0
    r("https://a.ro/2")
    assert slept == [3.0]
    with pytest.raises(R.RenderError):
        r("https://a.ro/private/x")
    assert r.stats["blocked"] == 1


def test_find_browser_prefers_env_then_known_paths():
    assert R.find_browser(env={"DERKAISER_BROWSER": "/x/b"}, exists=lambda p: p == "/x/b", which=lambda n: None) == "/x/b"
    assert R.find_browser(env={}, exists=lambda p: p.endswith("msedge.exe"), which=lambda n: None).endswith("msedge.exe")
    assert R.find_browser(env={}, exists=lambda p: False, which=lambda n: "/bin/chromium" if n == "chromium" else None) == "/bin/chromium"
    assert R.find_browser(env={}, exists=lambda p: False, which=lambda n: None) is None
