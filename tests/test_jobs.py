import json

from scraper import jobs as J


def test_normalize_link_strips_tracking_fragment_and_forces_https():
    assert J.normalize_link("http://Firma.ro/jobs/abc/?utm_source=x&id=7#apply") == "https://firma.ro/jobs/abc?id=7"
    assert J.normalize_link("javascript:void(0)") == "" and J.normalize_link("") == ""


def test_listing_pages_are_not_ad_links():
    assert not J.is_ad_link("https://firma.ro/careers")
    assert not J.is_ad_link("https://firma.ro/")
    assert J.is_ad_link("https://firma.ro/careers/dev-123")


def test_city_county_and_romania_signal():
    assert J.infer_city("Cluj-Napoca, Romania") == "Cluj-Napoca"
    assert J.county_of("Cluj-Napoca") == ["Cluj"]
    assert J.infer_city("Bucharest") == "Bucuresti"
    assert J.infer_city("Clujul") is None  # no partial-word hits
    assert J.has_romania_signal("Remote, Romania") and not J.has_romania_signal("Berlin, Germany")


def test_build_job_needs_title_link_and_romania():
    ok = J.build_job("Alfa", " Dev ", "https://alfa.ro/j/1", ["Iasi"], ["hybrid"])
    assert ok == {"job_title": "Dev", "job_link": "https://alfa.ro/j/1", "company": "Alfa", "country": "Romania",
                  "city": "Iasi", "county": ["Iasi"], "remote": ["hybrid"]}
    assert J.build_job("Alfa", "", "https://alfa.ro/j/1", ["Iasi"]) is None
    assert J.build_job("Alfa", "Dev", "https://alfa.ro/careers", ["Iasi"]) is None  # listing, not an ad
    assert J.build_job("Alfa", "Dev", "https://alfa.ro/j/1", ["Berlin"]) is None


def test_greenhouse_and_lever_shapes():
    gh = {"jobs": [{"title": "QA", "absolute_url": "https://boards.greenhouse.io/alfa/jobs/1", "location": {"name": "Bucharest"},
                    "content": "&lt;p&gt;Remote friendly&lt;/p&gt;"},
                   {"title": "Dup", "absolute_url": "https://boards.greenhouse.io/alfa/jobs/1", "location": {"name": "Bucharest"}},
                   {"title": "Abroad", "absolute_url": "https://boards.greenhouse.io/alfa/jobs/2", "location": {"name": "Paris"}}]}
    out = J.parse_greenhouse("Alfa", gh)
    assert [j["job_title"] for j in out] == ["QA"] and out[0]["remote"] == ["remote"]
    lv = [{"text": "Dev", "hostedUrl": "https://jobs.lever.co/alfa/abc", "categories": {"location": "Cluj-Napoca"},
           "workplaceType": "hybrid", "description": ""}]
    assert J.parse_lever("Alfa", lv)[0]["remote"] == ["hybrid"]


def test_other_ats_shapes():
    wk = {"jobs": [{"title": "Ops", "url": "https://apply.workable.com/alfa/j/AB12", "city": "Timisoara", "telecommuting": True}]}
    assert J.parse_workable("Alfa", wk)[0]["remote"] == ["remote"]
    rc = {"offers": [{"title": "HR", "careers_url": "https://alfa.recruitee.com/o/hr", "city": "Brasov", "country": "Romania"}]}
    assert J.parse_recruitee("Alfa", rc)[0]["county"] == ["Brasov"]
    sr = {"content": [{"id": "77", "name": "Sales", "location": {"city": "Oradea", "country": "ro"}}]}
    assert J.parse_smartrecruiters("Alfa", "Alfa1", sr)[0]["job_link"] == "https://jobs.smartrecruiters.com/Alfa1/77"
    xml = "<positions><position><id>5</id><name>Dev</name><office>Sibiu</office></position></positions>"
    assert J.parse_personio("Alfa", "alfa", xml)[0]["job_link"] == "https://alfa.jobs.personio.de/job/5"
    assert J.parse_personio("Alfa", "alfa", "<broken") == []


def test_detect_ats():
    assert J.detect_ats('<a href="https://boards.greenhouse.io/alfa/jobs/1">') == ("greenhouse", "alfa")
    assert J.detect_ats("see https://jobs.lever.co/beta") == ("lever", "beta")
    assert J.detect_ats("https://gamma.recruitee.com/") == ("recruitee", "gamma")
    assert J.detect_ats("https://delta.jobs.personio.de/") == ("personio", "delta")
    assert J.detect_ats("https://boards.greenhouse.io/embed/job_board?for=eps") == ("greenhouse", "eps")
    assert J.detect_ats("nothing here") is None


LD_PAGE = """<html><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"JobPosting","title":"Contabil","url":"https://alfa.ro/cariere/contabil-1",
 "jobLocation":{"@type":"Place","address":{"addressLocality":"Pitesti","addressCountry":"RO"}}},
 {"@type":"JobPosting","title":"Sofer","jobLocation":[{"address":{"addressLocality":"Pitesti","addressCountry":{"name":"Romania"}}}],
  "jobLocationType":"TELECOMMUTE"}]}</script></html>"""


def test_jobposting_jsonld_url_fallback_only_for_single_ad_pages():
    on_listing = J.extract_jobpostings("Alfa", LD_PAGE, "https://alfa.ro/cariere")
    assert [j["job_title"] for j in on_listing] == ["Contabil"]  # 2nd has no url and the page is a listing
    on_ad = J.extract_jobpostings("Alfa", LD_PAGE, "https://alfa.ro/cariere/sofer-2")
    assert {j["job_title"] for j in on_ad} == {"Contabil", "Sofer"}
    assert [j for j in on_ad if j["job_title"] == "Sofer"][0]["remote"] == ["remote"]
    assert J.extract_jobpostings("Alfa", "<script type='application/ld+json'>{bad</script>", "https://x.ro/a/b") == []


def test_find_careers_links():
    page = '<a href="/despre">Despre</a><a href="/cariere">Cariere</a><a href="https://x.ro/jobs?utm_x=1">Joburi</a>'
    assert J.find_careers_links(page, "https://alfa.ro") == ["https://alfa.ro/cariere", "https://x.ro/jobs"]


def _site(pages):
    def get(url):
        if url not in pages:
            raise OSError("404")
        return pages[url]
    return get


def test_scrape_prefers_ats_then_jsonld_then_none():
    gh = json.dumps({"jobs": [{"title": "QA", "absolute_url": "https://boards.greenhouse.io/alfa/jobs/1", "location": {"name": "Cluj"}}]})
    pages = {"https://alfa.ro": '<a href="/cariere">Cariere</a>',
             "https://alfa.ro/cariere": '<iframe src="https://boards.greenhouse.io/alfa"></iframe>',
             J.ats_url("greenhouse", "alfa"): gh}
    res = J.scrape_company_jobs("Alfa", "alfa.ro", get=_site(pages))
    assert res["method"] == "greenhouse" and res["jobs"][0]["job_link"].endswith("/jobs/1")

    pages2 = {"https://beta.ro": '<a href="/cariere">Cariere</a>', "https://beta.ro/cariere": LD_PAGE}
    res2 = J.scrape_company_jobs("Beta", "https://beta.ro", get=_site(pages2))
    assert res2["method"] == "jsonld" and len(res2["jobs"]) == 1

    res3 = J.scrape_company_jobs("Gamma", "gamma.ro", get=_site({"https://gamma.ro": "<html></html>"}))
    assert res3 == {"jobs": [], "method": "none", "detail": "no_structured_jobs"}


def test_scrape_edge_cases():
    assert J.scrape_company_jobs("X", "", get=_site({}))["detail"] == "no_website"
    assert J.scrape_company_jobs("X", "down.ro", get=_site({}))["detail"].startswith("site_unreachable")
    bad = {"https://x.ro": "https://x.recruitee.com", J.ats_url("recruitee", "x"): "not json"}
    assert J.scrape_company_jobs("X", "x.ro", get=_site(bad))["method"] == "none"  # broken ATS feed is not fatal


AD = """<html><h1>Consultant de credit</h1><p>Responsabilități: analiza dosarelor. Cerințe: experiență în vânzări.
Oferim program flexibil, beneficii. Locație: Pitești. Aplică acum.</p></html>"""
CAREERS = ('<a href="/cariere/consultant-de-credit">Aplica acum</a><a href="/cariere/icredit-ca-angajator">Angajator</a>'
           '<a href="/cariere/oferte-de-joburi">Toate</a><a href="/despre">Despre</a><a href="/cariere/brosura.pdf">PDF</a>'
           '<a href="https://alt.ro/cariere/x-y">alt</a><a href="/cariere/sofer-camion">gol</a>')


def test_candidate_ad_links_keeps_only_same_site_ad_like_links():
    links = J.candidate_ad_links(CAREERS, "https://alfa.ro/cariere", "https://alfa.ro")
    assert links == ["https://alfa.ro/cariere/consultant-de-credit", "https://alfa.ro/cariere/sofer-camion"]


def test_listing_scraper_confirms_each_ad_page():
    pages = {"https://alfa.ro": '<a href="/cariere">Cariere</a>', "https://alfa.ro/cariere": CAREERS,
             "https://alfa.ro/cariere/consultant-de-credit": AD, "https://alfa.ro/cariere/sofer-camion": "<h1>Cariere</h1><p>nimic</p>"}
    res = J.scrape_company_jobs("Alfa", "alfa.ro", get=_site(pages))
    assert res["method"] == "listing"
    assert res["jobs"] == [{"job_title": "Consultant de credit", "job_link": "https://alfa.ro/cariere/consultant-de-credit",
                            "company": "Alfa", "country": "Romania", "city": "Pitesti", "county": ["Arges"], "remote": []}]


def test_listing_scraper_rejects_pages_without_ad_proof():
    assert J.confirm_ad("A", "https://a.ro/cariere/x", "<h1>Despre noi</h1><p>suntem o firma</p>") is None
    assert J.confirm_ad("A", "https://a.ro/cariere/x", "<h1>Cariere</h1>" + AD) is None  # generic title


def test_second_level_listing_page_is_followed():
    pages = {"https://alfa.ro": '<a href="/cariere">Cariere</a>',
             "https://alfa.ro/cariere": '<a href="/cariere/pozitii-disponibile">Pozitii disponibile</a>',
             "https://alfa.ro/cariere/pozitii-disponibile": '<a href="/cariere/pozitii-disponibile/asistent-medical">Asistent</a>',
             "https://alfa.ro/cariere/pozitii-disponibile/asistent-medical": AD.replace("Consultant de credit", "Asistent medical")}
    res = J.scrape_company_jobs("Alfa", "alfa.ro", get=_site(pages))
    assert res["method"] == "listing" and res["jobs"][0]["job_title"] == "Asistent medical"


def test_site_banner_and_articles_are_not_ads():
    article = "<h1>EDU . REGINAMARIA . RO</h1>" + AD
    assert J.confirm_ad("A", "https://a.ro/cariere/sfaturi-din-interior", article) is None
    unrelated = "<h1>Povesti de succes</h1>" + AD  # 5 ad words but title unrelated to slug and < 5 words? still needs overlap
    assert J.confirm_ad("A", "https://a.ro/cariere/sfaturi-din-interior", unrelated) is None


def test_ad_page_already_fetched_as_careers_page_is_still_confirmed():
    pages = {"https://alfa.ro": '<a href="/cariere">Cariere</a>',
             "https://alfa.ro/cariere": '<a href="/cariere/consultant-de-credit">Aplica</a>',
             "https://alfa.ro/cariere/consultant-de-credit": AD}
    res = J.scrape_company_jobs("Alfa", "alfa.ro", get=_site(pages))
    assert [j["job_title"] for j in res["jobs"]] == ["Consultant de credit"]


def test_numeric_id_url_needs_strong_ad_wording():
    assert J.confirm_ad("A", "https://a.ro/job/48213", AD)["job_title"] == "Consultant de credit"
    assert J.confirm_ad("A", "https://a.ro/job/48213", "<h1>Consultant</h1><p>Aplica acum</p>") is None


def test_single_city_refuses_ambiguous_pages():
    assert J.single_city("Locatie: Pitești. Aplică.") == "Pitesti"
    assert J.single_city("Avem birouri în Cluj, Iași și Brașov") is None
    assert J.single_city("nimic") is None


def test_careers_subdomain_counts_as_same_site():
    html = '<a href="https://cariere.alfa.ro/cariere/sofer-camion">x</a><a href="https://evil.ro/cariere/sofer-camion">y</a>'
    assert J.candidate_ad_links(html, "https://cariere.alfa.ro/cariere", "https://alfa.ro") == ["https://cariere.alfa.ro/cariere/sofer-camion"]


def test_program_pages_and_branded_titles_are_not_ads():
    assert J.confirm_ad("A", "https://a.ro/careers/military-path", "<h1>Military / Veteran | AECOM</h1>" + AD) is None
    assert J.confirm_ad("A", "https://a.ro/careers/military-path", "<h1>Military Veteran Program</h1>" + AD) is None
    assert J.confirm_ad("A", "https://a.ro/cariere/consultant-de-credit", AD)["job_title"] == "Consultant de credit"


def test_stop_paths_use_whole_words_on_every_segment():
    assert not J._is_stop_path("/cariere/contabil-sef")          # 'cont' must NOT match inside 'contabil'
    assert not J._is_stop_path("/joburi/reprezentant-vanzari-alba-sibiu")
    assert J._is_stop_path("/about-us/career/professionals")     # section, not an ad
    assert J._is_stop_path("/careers/candidate-faq/ai_in_recruitment")
    assert J._is_stop_path("/cariere/programul-internship-pentru-asistenti")
    assert J._is_stop_path("/cariere/oferte-de-joburi")


def test_news_process_and_career_section_pages_are_not_ads():
    assert J._is_stop_path("/corporate/cariere/noutati")
    assert J._is_stop_path("/joburi/parcursul-candidatilor")
    assert J._is_stop_path("/joburi/cariera-la-penny")
    assert not J._is_stop_path("/corporate/cariere/detalii/6886")      # real ad URL (numeric id)
    assert not J._is_stop_path("/cariere/consultant-de-credit")        # parent /cariere/ must not trigger
    assert J.confirm_ad("A", "https://a.ro/cariere/sofer-camion", "<h1>Sofer camion .</h1>" + AD) is None  # banner-style title
