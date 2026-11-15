from scraper import portals as P

PROFILE = ('<a href="/loc-de-munca/back-office-cu-experienta" class="x" aria-label="Back Office (cu experienta)">'
           '<a href="/loc-de-munca/back-office-cu-experienta" aria-label="dup">'
           '<a href="/loc-de-munca/sofer-c-e" aria-label="Sofer C+E">')


def ad(loc="Strada Soarelui, Dumbravita, România", employer="Emerald Company", extra=""):
    return ('<meta property="og:title" content="Back Office , Emerald Company" />'
            '{\\"slug\\":\\"x\\",\\"locations\\":[{\\"name\\":\\"' + loc + '\\",\\"slug\\":\\"-\\"}],\\"employmentTypes\\":[],'
            '\\"remote\\":false,\\"partialRemote\\":false,\\"employer\\":{\\"employerName\\":\\"' + employer + '\\"}' + extra + '}')


def test_slugs_and_match():
    assert P.company_slug("EMERALD COMPANY SRL") == "emerald-company"
    assert P.slugify("Țânțar & Fiii") == "tantar-fiii"
    assert P.match_slug("Emerald Company SRL", {"emerald-company"}) == "emerald-company"
    assert P.match_slug("Emerald Group SRL", {"emerald-company"}) is None


def test_parse_profile_dedupes():
    got = P.parse_profile(PROFILE)
    assert [u for u, _ in got] == ["https://www.bestjobs.eu/loc-de-munca/back-office-cu-experienta",
                                    "https://www.bestjobs.eu/loc-de-munca/sofer-c-e"]


def test_parse_ad_ok_foreign_and_other_employer():
    url = "https://www.bestjobs.eu/loc-de-munca/x"
    job = P.parse_ad("Emerald Company SRL", url, "Back Office", ad())
    assert job and job["job_title"] == "Back Office" and job["job_link"] == url
    assert P.parse_ad("Emerald Company SRL", url, "Back Office", ad(loc="Berlin, Germany")) is None
    assert P.parse_ad("Emerald Company SRL", url, "Back Office", ad(employer="Alt Angajator")) is None
    assert P.parse_ad("Emerald Company SRL", url, "x", ad(extra=',\\"expired\\":true')) is None


def test_portal_jobs_end_to_end():
    pages = {"https://www.bestjobs.eu/company-profile/emerald-company": PROFILE,
             "https://www.bestjobs.eu/loc-de-munca/back-office-cu-experienta": ad(),
             "https://www.bestjobs.eu/loc-de-munca/sofer-c-e": ad(loc="Cluj-Napoca, România")}
    jobs = P.portal_jobs("EMERALD COMPANY SRL", {"emerald-company"}, pages.__getitem__)
    assert len(jobs) == 2 and [j["city"] for j in jobs if j["job_link"].endswith("sofer-c-e")] == ["Cluj-Napoca"]
    assert P.portal_jobs("Necunoscuta SRL", {"emerald-company"}, pages.__getitem__) == []


def test_load_slugs():
    idx = "<loc>https://www.bestjobs.eu/sitemap/sitemap.jobs_by_employer.ro_0.xml</loc><loc>https://www.bestjobs.eu/sitemap/sitemap.jobs.ro.xml</loc>"
    sm = "<loc>https://www.bestjobs.eu/company-profile/Emerald-Company</loc>"
    got = P.load_slugs(lambda u: idx if u.endswith("sitemap.xml") and "jobs_by" not in u else sm)
    assert got == {"emerald-company"}
