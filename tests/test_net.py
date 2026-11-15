import json

import pytest

from scraper import net as N


class Fake:
    """Scripted transport: url -> list of responses (status, headers, body) or Exceptions, consumed in order."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def __call__(self, req, timeout):
        self.calls.append((req.get_method(), req.full_url))
        queue = self.script.get(req.full_url)
        if not queue:
            return 404, {}, b""
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item


def mk(script, **kw):
    slept = []
    kw.setdefault("respect_robots", False)
    n = N.Net(opener=Fake(script), sleep=slept.append, rng=lambda: 1.0, **kw)
    return n, slept


def test_get_returns_text_and_paces_per_host():
    n, slept = mk({"https://a.ro/1": [(200, {}, b"one")], "https://a.ro/2": [(200, {}, b"two")], "https://b.ro/1": [(200, {}, b"b")]},
                  interval=3.0)
    t = [0.0]
    n._clock = lambda: t[0]
    assert n.get("https://a.ro/1") == "one"
    t[0] = 1.0
    assert n.get("https://a.ro/2") == "two" and slept == [2.0]  # same host: waits the remaining 2 s
    assert n.get("https://b.ro/1") == "b" and slept == [2.0]    # other host: no wait


def test_retries_transient_errors_then_succeeds_and_honours_retry_after():
    n, slept = mk({"https://a.ro/x": [(503, {"Retry-After": "7"}, b""), OSError("reset"), (200, {}, b"ok")]}, interval=0)
    assert n.get("https://a.ro/x") == "ok"
    assert n.stats["retries"] == 2 and 7.0 in slept


def test_client_errors_are_not_retried():
    n, _ = mk({"https://a.ro/x": [(404, {}, b"")]})
    with pytest.raises(N.HTTPFail) as e:
        n.get("https://a.ro/x")
    assert e.value.status == 404 and n.stats["requests"] == 1


def test_circuit_breaker_skips_a_dead_host():
    n, _ = mk({"https://dead.ro/a": [OSError("timeout")], "https://dead.ro/b": [(200, {}, b"x")]}, interval=0, retries=0,
              breaker_threshold=2)
    for _ in range(2):
        with pytest.raises(N.NetError):
            n.get("https://dead.ro/a")
    with pytest.raises(N.CircuitOpen):
        n.get("https://dead.ro/b")
    assert n.stats["circuit_open"] == 1


def test_robots_txt_blocks_disallowed_paths_and_missing_robots_allows():
    robots = b"User-agent: *\nDisallow: /private/\n"
    n, _ = mk({"https://a.ro/robots.txt": [(200, {}, robots)], "https://a.ro/cariere": [(200, {}, b"ok")],
               "https://a.ro/private/x": [(200, {}, b"no")]}, respect_robots=True, interval=0)
    assert n.get("https://a.ro/cariere") == "ok"
    with pytest.raises(N.Blocked):
        n.get("https://a.ro/private/x")
    n2, _ = mk({"https://b.ro/p": [(200, {}, b"ok")]}, respect_robots=True, interval=0)  # robots.txt -> 404
    assert n2.get("https://b.ro/p") == "ok"


def test_status_scans_content_for_expired_markers():
    n, _ = mk({"https://a.ro/live": [(200, {}, b"<h1>Dev</h1>")], "https://a.ro/gone": [(200, {}, b"Anuntul a expirat")],
               "https://a.ro/404": [(404, {}, b"")], "https://dead.ro/x": [OSError("x")]}, interval=0, retries=0)
    assert [n.status(u) for u in ("https://a.ro/live", "https://a.ro/gone", "https://a.ro/404", "https://dead.ro/x")] == [200, 410, 404, 0]


def test_post_json_and_disk_cache(tmp_path):
    n, _ = mk({"https://x.ro/api": [(200, {}, json.dumps({"rows": []}).encode())], "https://a.ro/p": [(200, {}, b"page")]},
              cache_dir=tmp_path, interval=0)
    assert n.post_json("https://x.ro/api", {"a": 1}) == {"rows": []}
    assert n.get("https://a.ro/p") == "page" and n.get("https://a.ro/p") == "page"
    assert n.stats["cache_hits"] == 1 and n.stats["requests"] == 2
