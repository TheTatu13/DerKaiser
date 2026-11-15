import json

import pytest

from scraper import engine as E
from scraper import jobs as J

GH = json.dumps({"jobs": [{"title": f"Job {i}", "absolute_url": f"https://boards.greenhouse.io/alfa/jobs/{i}", "location": {"name": "Cluj"}}
                          for i in range(3)]})
LD = """<script type="application/ld+json">{"@type":"JobPosting","title":"Contabil","url":"https://beta.ro/cariere/contabil-1",
"jobLocation":{"address":{"addressLocality":"Pitesti","addressCountry":"RO"}}}</script>"""
AD = "<h1>Sofer camion</h1><p>Responsabilități. Cerințe: permis. Oferim beneficii. Aplică acum.</p>"


def site(pages):
    def get(url):
        if url not in pages:
            raise OSError("404")
        return pages[url]
    return get


OK = lambda url: 200  # noqa: E731


def run(pages, name="Alfa", website="alfa.ro", rows=(), **kw):
    kw.setdefault("status", OK)
    return E.run_firm("00000001", name, website, list(rows), fetch=site(pages), **kw)


def layers(res):
    return [(t["layer"], t["state"]) for t in res["layers"]]


def test_cascade_stops_at_first_layer_that_finds_jobs():
    pages = {"https://alfa.ro": '<a href="/cariere">c</a>', "https://alfa.ro/cariere": "https://boards.greenhouse.io/alfa",
             J.ats_url("greenhouse", "alfa"): GH}
    res = run(pages)
    assert res["method"] == "greenhouse" and len(res["jobs"]) == 3 and layers(res) == [("ats", "ok")]


def test_broken_layer_falls_through_to_the_next_doll():
    pages = {"https://beta.ro": '<a href="/cariere">c</a>',
             "https://beta.ro/cariere": "https://beta.recruitee.com " + LD,   # ATS feed is down -> jsonld takes over
             }
    res = run(pages, "Beta", "beta.ro")
    assert res["method"] == "jsonld" and [t["layer"] for t in res["layers"]] == ["ats", "jsonld"]
    assert res["layers"][0]["state"] == "empty"


def test_layer_exception_is_contained(monkeypatch):
    def boom(ctx):
        raise RuntimeError("selector exploded")
    monkeypatch.setattr(E, "LAYERS", [("ats", boom), ("listing", E.layer_listing)])
    pages = {"https://alfa.ro": '<a href="/cariere">c</a>', "https://alfa.ro/cariere": '<a href="/cariere/sofer-camion">x</a>',
             "https://alfa.ro/cariere/sofer-camion": AD}
    res = run(pages)
    assert res["layers"][0]["state"] == "error" and res["method"] == "listing" and len(res["jobs"]) == 1


def test_request_budget_stops_a_runaway_firm():
    calls = []

    def fetch(url):
        calls.append(url)
        return '<a href="/cariere/a">c</a><a href="/jobs/b">j</a><a href="/careers/c">k</a>'
    res = E.run_firm("00000001", "Alfa", "alfa.ro", [], fetch=fetch, status=OK, max_requests=3)
    assert len(calls) == 3 and "truncated" in res["flags"] and res["requests"] == 3


def test_time_limit_stops_the_firm():
    t = [0.0]

    def fetch(url):
        t[0] += 100
        return '<a href="/cariere">c</a><a href="/jobs">j</a>'
    res = E.run_firm("00000001", "Alfa", "alfa.ro", [], fetch=fetch, status=OK, max_seconds=150, clock=lambda: t[0])
    assert "truncated" in res["flags"]


def test_uniform_title_flood_is_rejected():
    flood = json.dumps({"jobs": [{"title": "Operator", "absolute_url": f"https://boards.greenhouse.io/alfa/jobs/{i}",
                                  "location": {"name": "Cluj"}} for i in range(12)]})
    pages = {"https://alfa.ro": "https://boards.greenhouse.io/alfa", J.ats_url("greenhouse", "alfa"): flood}
    res = run(pages)
    assert res["jobs"] == [] and "uniform_titles" in res["flags"] and ("ats", "rejected") in layers(res)


def test_foreign_host_links_and_dead_links_are_dropped_anofm_not_checked():
    ld = LD.replace("https://beta.ro/cariere/contabil-1", "https://evil.com/cariere/contabil-1")
    pages = {"https://beta.ro": '<a href="/cariere">c</a>', "https://beta.ro/cariere": ld}
    assert run(pages, "Beta", "beta.ro")["jobs"] == []
    rows = [{"id": 7, "occupation": "Sofer", "address_locality_name": "Arges > Pitesti"}]
    checked = []
    res = run({"https://beta.ro": "<html></html>"}, "Beta", "beta.ro", rows=rows, status=lambda u: checked.append(u) or 404)
    assert [j["url"] for j in res["jobs"]] == ["https://mediere.anofm.ro/app/module/mediere/job/7"] and checked == []
    assert res["method"] == "anofm"


def test_history_canary_flags_sudden_zero():
    res = run({"https://beta.ro": "<html></html>"}, "Beta", "beta.ro", previous=12)
    assert "suspect_zero" in res["flags"] and res["jobs"] == []
    assert "suspect_zero" not in run({"https://beta.ro": "<html></html>"}, "Beta", "beta.ro", previous=2)["flags"]


def test_recipe_ats_and_recipe_link_regex_are_the_smallest_doll():
    pages = {J.ats_url("greenhouse", "alfa"): GH}
    res = run(pages, recipe={"ats": "greenhouse", "slug": "alfa"})
    assert res["method"] == "greenhouse" and res["requests"] == 1  # no crawl at all
    pages2 = {"https://x.ro/echipa": '<a href="/p/sofer-camion">a</a><a href="/blog/x">b</a>', "https://x.ro/p/sofer-camion": AD}
    res2 = run(pages2, "X", "x.ro", recipe={"careers_urls": ["https://x.ro/echipa"], "ad_link_regex": "/p/"})
    assert res2["method"] == "recipe" and res2["jobs"][0]["title"] == "Sofer camion"


def test_run_all_is_resumable_parallel_and_survives_engine_errors(tmp_path):
    docs = [{"id": f"0000000{i}", "company": f"Firma{i}", "website": []} for i in range(1, 6)]
    pages = {}
    anofm = lambda url, body: {"rows": [{"id": int(body["employer_tax_code"]), "occupation": "Sofer", "address_locality_name": "Cluj"}]}  # noqa: E731

    def bad_status(url):
        raise ValueError("boom")  # unexpected error inside a firm

    rep = E.run_all(docs, tmp_path, fetch=site(pages), status=bad_status, anofm_post=anofm, workers=4)
    assert rep["firms_done"] == 5 and rep["jobs_total"] == 5  # ANOFM jobs need no status check
    rep2 = E.run_all(docs, tmp_path, fetch=lambda u: pytest.fail("refetched"), status=OK, anofm_post=lambda u, b: pytest.fail("again"), workers=4)
    assert rep2["firms_done"] == 5 and rep2["firms_left"] == 0
    lines = (tmp_path / "jobs.jsonl").read_text().splitlines()
    assert len(lines) == 5 and len({json.loads(l)["cif"] for l in lines}) == 5


def test_unexpected_firm_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    def explode(*a, **k):
        raise ZeroDivisionError("bug in a layer helper")
    monkeypatch.setattr(E, "run_firm", explode)
    docs = [{"id": "00000001", "company": "Alfa", "website": ["alfa.ro"]}, {"id": "00000002", "company": "Beta", "website": []}]
    rep = E.run_all(docs, tmp_path, fetch=OK, status=OK, anofm_post=lambda u, b: {"rows": []})
    assert rep["firms_done"] == 2 and rep["flags"] == {"engine_error": 2}


AD_LONG = AD + "<p>" + "Descriere detaliata a postului. " * 30 + "</p>"  # real ad pages are not empty JS shells
SHELL = '<html><div id="root"></div><script src="/app.js"></script></html>'
RENDERED_CAREERS = '<a href="/cariere/sofer-camion">Sofer</a><a href="/cariere/despre-noi">Despre</a>'


def make_render(table, calls=None):
    def render(url):
        if calls is not None:
            calls.append(url)
        if url not in table:
            raise RuntimeError("render failed")
        return table[url]
    return render


def test_rendered_layer_takes_over_when_static_html_is_an_empty_shell():
    pages = {"https://alfa.ro": '<a href="/cariere">c</a>', "https://alfa.ro/cariere": SHELL, "https://alfa.ro/cariere/sofer-camion": AD_LONG}
    calls = []
    table = {"https://alfa.ro/cariere": RENDERED_CAREERS}
    res = run(pages, render=make_render(table, calls))
    assert res["method"] == "js:listing" and res["jobs"][0]["title"] == "Sofer camion" and res["renders"] == 1
    assert [t["layer"] for t in res["layers"]] == ["ats", "jsonld", "listing", "rendered"]
    assert run(pages)["method"] == "none"  # without a renderer nothing changes


def test_rendered_home_when_the_careers_link_is_injected_by_javascript():
    pages = {"https://alfa.ro": SHELL, "https://alfa.ro/cariere/sofer-camion": AD_LONG}
    table = {"https://alfa.ro": '<a href="/cariere">Cariere</a>', "https://alfa.ro/cariere": RENDERED_CAREERS}
    res = run(pages, render=make_render(table))
    assert res["method"] == "js:listing" and res["renders"] == 2 and len(res["jobs"]) == 1


def test_rendered_ats_iframe_is_followed_to_its_feed():
    pages = {"https://alfa.ro": '<a href="/cariere">c</a>', "https://alfa.ro/cariere": SHELL, J.ats_url("greenhouse", "alfa"): GH}
    table = {"https://alfa.ro/cariere": '<iframe src="https://boards.greenhouse.io/alfa"></iframe>'}
    res = run(pages, render=make_render(table))
    assert res["method"] == "js:greenhouse" and len(res["jobs"]) == 3


def test_render_budget_and_failures_are_contained():
    pages = {"https://alfa.ro": '<a href="/cariere">c</a><a href="/jobs">j</a>', "https://alfa.ro/cariere": SHELL, "https://alfa.ro/jobs": SHELL}
    calls = []
    res = run(pages, render=make_render({}, calls), max_renders=1)  # renderer always fails
    assert res["jobs"] == [] and len(calls) <= 1
    res2 = run(pages, render=make_render({"https://alfa.ro/cariere": "<html></html>", "https://alfa.ro/jobs": "<html></html>"}, calls), max_renders=1)
    assert "truncated" in res2["flags"] or res2["method"] == "none"


def test_website_is_guessed_and_verified_strictly_when_no_other_source():
    pages = {"https://egala.ro": "<html>Egala SRL CUI RO 00000001 Romania</html>"}
    res = run(pages, "EGALA SRL", None)
    assert res["site"] == "https://egala.ro" and res["site_source"] == "guess"
    assert run({"https://egala.ro": "<html>Egala fan club</html>"}, "EGALA SRL", None)["site"] is None
