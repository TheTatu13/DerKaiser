import json
import re

from scraper import engine as E
from scraper import learn as L

AD_BODY = ("<p>Responsabilități: coordonare. Cerințe: experiență. Oferim beneficii. Aplică acum.</p>"
           + "<p>" + "Descriere detaliata a postului. " * 30 + "</p>")


def ad(n, title="Operator"):
    return f"<h1>{title} {n}</h1>" + AD_BODY


def listing(page, ids, pages_total, nav="query"):
    links = "".join(f'<a href="/cariere/detalii/{i}">Vezi detalii</a>' for i in ids)
    if nav == "query":
        links += "".join(f'<a href="/cariere?page={p}">{p}</a>' for p in range(1, pages_total + 1))
    elif nav == "path":
        links += "".join(f'<a href="/cariere/pagina/{p}">{p}</a>' for p in range(2, pages_total + 1))
    elif nav == "next" and page < pages_total:
        links += f'<a rel="next" href="/cariere?page={page + 1}">Urmatoarea</a>'
    return links


def paginated_site(total_pages=3, per_page=4, nav="query"):
    pages = {"https://alfa.ro": '<a href="/cariere">Cariere</a>'}
    n = 0
    for p in range(1, total_pages + 1):
        ids = [1000 + n + i for i in range(per_page)]
        n += per_page
        url = "https://alfa.ro/cariere" if p == 1 else (f"https://alfa.ro/cariere?page={p}" if nav != "path" else f"https://alfa.ro/cariere/pagina/{p}")
        pages[url] = listing(p, ids, total_pages, nav)
        for i in ids:
            pages[f"https://alfa.ro/cariere/detalii/{i}"] = ad(i)
    return pages


def getter(pages):
    def get(url):
        if url not in pages:
            raise OSError("404")
        return pages[url]
    return get


def test_signature_and_regex_shapes():
    assert L.signature("https://a.ro/cariere/detalii/6886") == ("/cariere/detalii", "num")
    assert L.signature("https://a.ro/joburi/sofer-camion") == ("/joburi", "slug")
    assert L.signature("https://a.ro/jobs/sofer-camion-48213") == ("/jobs", "slugid")
    rx = re.compile(L.regex_for("/cariere/detalii", "num"))
    assert rx.search("https://a.ro/cariere/detalii/6886") and not rx.search("https://a.ro/cariere/detalii/abc")


def test_learn_recipe_finds_the_ad_family_and_the_pagination():
    pages = paginated_site()
    rec = L.learn_recipe("Alfa", {"https://alfa.ro/cariere": pages["https://alfa.ro/cariere"]}, "https://alfa.ro", getter(pages))
    assert rec["ad_link_regex"] == L.regex_for("/cariere/detalii", "num")
    assert rec["pagination"] == {"kind": "query", "param": "page"} and rec["max_pages"] == 25 and rec["expected_min"] == 4
    assert rec["careers_urls"] == ["https://alfa.ro/cariere"] and rec["budget"] >= 40


def test_learner_refuses_a_family_that_is_not_ads():
    page = "".join(f'<a href="/cariere/echipa/{i}">Membru</a>' for i in range(5))
    pages = {"https://alfa.ro/cariere": page, **{f"https://alfa.ro/cariere/echipa/{i}": "<h1>Ana</h1><p>Despre mine</p>" for i in range(5)}}
    assert L.learn_recipe("Alfa", {"https://alfa.ro/cariere": page}, "https://alfa.ro", getter(pages)) is None


def test_next_page_builders():
    assert L.next_page_url("https://a.ro/c", "https://a.ro/c", {"kind": "query", "param": "page"}, 1, "", "https://a.ro") == "https://a.ro/c?page=2"
    assert L.next_page_url("https://a.ro/c?x=1", "", {"kind": "query", "param": "p"}, 2, "", "https://a.ro") == "https://a.ro/c?x=1&p=3"
    assert L.next_page_url("https://a.ro/c", "", {"kind": "path", "segment": "pagina"}, 1, "", "https://a.ro") == "https://a.ro/c/pagina/2"
    assert L.next_page_url("https://a.ro/c", "https://a.ro/c", {"kind": "next"}, 1, '<a rel="next" href="/c?x=9">n</a>', "https://a.ro") == "https://a.ro/c?x=9"
    assert L.next_page_url("https://a.ro/c", "", None, 1, "", "https://a.ro") is None


def run(pages, **kw):
    return E.run_firm("00000001", "Alfa", "alfa.ro", [], fetch=getter(pages), status=lambda u: 200, **kw)


def test_engine_learns_walks_every_page_and_returns_the_recipe():
    res = run(paginated_site(3, 4), learn=True)
    assert res["method"] == "recipe" and len(res["jobs"]) == 12 and "recipe_learned" in res["flags"]
    assert res["learned_recipe"]["pagination"]["param"] == "page"
    assert len(run(paginated_site(3, 4))["jobs"]) == 4  # without learning only the first page is seen


def test_all_three_pagination_styles_are_followed():
    for nav in ("query", "path", "next"):
        res = run(paginated_site(3, 4, nav), learn=True)
        assert len(res["jobs"]) == 12, nav


def test_saved_recipe_is_used_directly_and_gets_its_own_bigger_budget():
    pages = paginated_site(3, 4)
    rec = run(pages, learn=True)["learned_recipe"]
    res = run(pages, recipe=rec, max_requests=2)  # recipe budget overrides the tiny default limit
    assert res["method"] == "recipe" and len(res["jobs"]) == 12 and "truncated" not in res["flags"]


def test_drift_is_detected_and_the_recipe_is_relearned(tmp_path):
    pages = paginated_site(2, 4)
    stale = {"careers_urls": ["https://alfa.ro/cariere"], "ad_link_regex": r"^https://[^/]+/cariere/vechi/\d+$", "expected_min": 8,
             "max_pages": 1, "budget": 20}
    pages["https://alfa.ro/cariere"] += "".join(f'<a href="/cariere/vechi/{i}">x</a>' for i in range(4))
    for i in range(4):
        pages[f"https://alfa.ro/cariere/vechi/{i}"] = "<h1>Pagina veche</h1><p>nimic</p>"
    (tmp_path / "00000001.json").write_text(json.dumps(stale))
    docs = [{"id": "00000001", "company": "Alfa", "website": ["alfa.ro"]}]
    rep = E.run_all(docs, tmp_path / "o", fetch=getter(pages), status=lambda u: 200, anofm_post=lambda u, b: {"rows": []},
                    recipes_dir=tmp_path, learn=True, websites={"00000001": "alfa.ro"})
    assert rep["jobs_total"] == 8 and rep["flags"].get("recipe_relearned") == 1 and rep["flags"].get("recipe_drift") == 1
    new = json.loads((tmp_path / "00000001.json").read_text())
    assert "detalii" in new["ad_link_regex"] and json.loads((tmp_path / "00000001.prev.json").read_text())["ad_link_regex"] == stale["ad_link_regex"]
    # next run: the repaired recipe wins on its own, nothing to relearn
    rep2 = E.run_all(docs, tmp_path / "o2", fetch=getter(pages), status=lambda u: 200, anofm_post=lambda u, b: {"rows": []},
                     recipes_dir=tmp_path, learn=True, websites={"00000001": "alfa.ro"})
    assert rep2["by_method"] == {"recipe": 1} and rep2["jobs_total"] == 8 and "recipe_relearned" not in rep2["flags"]


def test_a_family_outside_a_careers_path_is_never_a_recipe():
    page = "".join(f'<a href="/credite/credit-{i}">Credit</a>' for i in range(5))
    pages = {"https://alfa.ro/cariere": page, **{f"https://alfa.ro/credite/credit-{i}": f"<h1>Credit {i}</h1>" + AD_BODY for i in range(5)}}
    assert L.learn_recipe("Alfa", {"https://alfa.ro/cariere": page}, "https://alfa.ro", getter(pages)) is None


def test_numeric_id_ads_under_a_careers_path_need_only_three_ad_words():
    from scraper import jobs as J
    page = "<h1>Casier</h1><p>Responsabilități: casa. Cerințe: atenție. Beneficii: tichete.</p>" + "<p>" + "text " * 120 + "</p>"
    assert J.confirm_ad("A", "https://a.ro/corporate/cariere/detalii/6880", page)["job_title"] == "Casier"
    assert J.confirm_ad("A", "https://a.ro/produse/detalii/6880", page) is None  # same page, not under a careers path
