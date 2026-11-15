import json

from scraper import ejobs as E


def page(org="Acme SRL", locality="Bucuresti", country="RO"):
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "name": "eJobs"},
        {"@type": "JobPosting", "title": "Contabil", "hiringOrganization": {"@type": "Organization", "name": org},
         "jobLocation": {"@type": "Place", "address": {"addressLocality": locality, "addressCountry": country}}}]}
    return f'<script type="application/ld+json">{json.dumps(ld)}</script>'


URL = "https://www.ejobs.ro/user/locuri-de-munca/contabil/123"


def test_parse_ad_romania():
    job = E.parse_ad(page(), URL)
    assert job and job["company"] == "Acme SRL" and job["job_link"] == URL and job["city"] == "Bucuresti"


def test_parse_ad_foreign_or_anonymous():
    assert E.parse_ad(page(locality="Berlin", country="DE"), URL) is None
    assert E.parse_ad(page(org="Companie Confidențială"), URL) is None
    assert E.parse_ad("<html></html>", URL) is None


def test_sitemap_ads_skips_expired_and_dedupes():
    pages = {E.INDEX: "<loc>https://www.ejobs.ro/sitemap-listings-a.xml</loc><loc>https://www.ejobs.ro/sitemap-expired-listings.xml</loc>",
             "https://www.ejobs.ro/sitemap-listings-a.xml": f"<loc>{URL}</loc><loc>{URL}</loc><loc>https://www.ejobs.ro/despre</loc>"}
    assert E.sitemap_ads(pages.__getitem__) == [URL]
