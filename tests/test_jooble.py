import json
from scraper import jooble


def test_normalize_and_dedupe_and_resume(tmp_path):
    calls = []

    def post(url, body):
        calls.append(body)
        jobs = [{"title": f"Job {i}", "link": f"https://x/{i}", "company": "A", "location": "Pitesti", "source": "s"} for i in range(20)]
        return {"jobs": jobs if body["page"] == 1 else jobs[:3]}

    out = tmp_path / "j.jsonl"
    s = jooble.run(post, "K", out, max_requests=10, pages=2, queries=[("a", "x"), ("b", "y")])
    assert s["new"] == 20 and s["stopped"] == ""  # second query returns the same 20 urls
    n = len(calls)
    jooble.run(post, "K", out, max_requests=10, pages=2, queries=[("a", "x"), ("b", "y")])
    assert len(calls) == n  # resumed: nothing re-requested
    assert len(out.read_text().splitlines()) == 20


def test_quota_stops(tmp_path):
    post = lambda u, b: {"jobs": [{"title": "t", "link": f"https://x/{b['page']}{b['keywords']}"}] * 20}
    s = jooble.run(post, "K", tmp_path / "j.jsonl", max_requests=2, pages=5, queries=[("a", "x")])
    assert s["stopped"] == "max_requests" and s["requests"] == 2
