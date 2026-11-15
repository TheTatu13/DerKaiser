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


def test_listing_ads_stops_on_empty_or_repeated_page():
    pages = {P.LISTING.format(page=1): '<a href="/joburi/a/">', P.LISTING.format(page=2): '<a href="/joburi/b/">',
             P.LISTING.format(page=3): '<a href="/joburi/b/">'}
    assert P.listing_ads(lambda u: pages[u]) == ["https://posturi.gov.ro/joburi/a/", "https://posturi.gov.ro/joburi/b/"]


def test_listing_ads_survives_fetch_error():
    def get(u):
        if u.endswith("pg_page=2"):
            raise OSError("boom")
        return '<a href="/joburi/a/">'
    assert P.listing_ads(get) == ["https://posturi.gov.ro/joburi/a/"]


def test_parse_ad():
    job = P.parse_ad(page(), AD)
    assert job and job["company"] == "Scoala Gimnaziala Nr. 1" and job["job_link"] == P.J.normalize_link(AD)
    assert P.parse_ad("<html></html>", AD) is None
