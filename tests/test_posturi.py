import json

from scraper import posturi as P

AD = "https://posturi.gov.ro/joburi/secretar-scoala/"


def page(org="Scoala Gimnaziala Nr. 1", locality="Pitesti", country="RO"):
    ld = {"@context": "https://schema.org/", "@type": "JobPosting", "title": "Secretar",
          "hiringOrganization": {"@type": "Organization", "name": org},
          "jobLocation": {"@type": "Place", "address": {"addressLocality": locality, "addressCountry": country}}}
    return f'<script type="application/ld+json">{json.dumps(ld)}</script>'


def test_ad_links_dedupes_and_absolutizes():
    html = '<a href="/joburi/a-b/">x</a><a href="https://posturi.gov.ro/joburi/a-b/">y</a><a href="/judete/">z</a>'
    assert P.ad_links(html) == ["https://posturi.gov.ro/joburi/a-b/"]


def test_listing_ads_tolerates_overlapping_pages_then_stops():
    pages = {1: "a", 2: "b", 3: "a", 4: "c", 5: "c", 6: "c"}

    def get(u):
        n = int(u.rsplit("=", 1)[1])
        return f'<a href="/joburi/{pages[n]}/">' if n in pages else '<a href="/joburi/c/">'
    got = P.listing_ads(get, max_stale=3)
    assert got == [f"https://posturi.gov.ro/joburi/{x}/" for x in "abc"]


def test_listing_ads_retries_failed_page():
    calls = {"n": 0}

    def get(u):
        if u.endswith("pg_page=2") and calls["n"] < 2:
            calls["n"] += 1
            raise OSError("boom")
        return {"1": '<a href="/joburi/a/">', "2": '<a href="/joburi/b/">'}.get(u[-1], "")
    assert P.listing_ads(get, pause=lambda s: None) == ["https://posturi.gov.ro/joburi/a/", "https://posturi.gov.ro/joburi/b/"]


def test_listing_ads_gives_up_after_repeated_failures():
    def get(u):
        raise OSError("down")
    assert P.listing_ads(get, max_failures=3, pause=lambda s: None) == []


def test_parse_ad():
    job = P.parse_ad(page(), AD)
    assert job and job["company"] == "Scoala Gimnaziala Nr. 1" and job["job_link"] == P.J.normalize_link(AD)
    assert P.parse_ad("<html></html>", AD) is None
