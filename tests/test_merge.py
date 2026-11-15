from scraper import merge as M


def test_dedupe_by_url_and_soft_key_and_priority():
    anofm = [{"url": "https://mediere.anofm.ro/job/1", "title": "Contabil", "company": "Acme SRL", "location": ["Bucuresti"]}]
    ejobs = [
        {"job_link": "https://www.ejobs.ro/x/1", "job_title": "Contabil", "company": "ACME SRL", "city": "Bucuresti"},  # same ad, other portal
        {"job_link": "https://www.ejobs.ro/x/2", "job_title": "Sofer", "company": "Acme SRL", "city": "Cluj-Napoca"},
        {"job_link": "https://ejobs.ro/x/2/", "job_title": "Sofer", "company": "Acme SRL", "city": "Cluj-Napoca"},  # same url
    ]
    jobs, stats = M.merge([("anofm", anofm), ("ejobs", ejobs)])
    assert len(jobs) == 2 and {j["_source"] for j in jobs} == {"anofm", "ejobs"}
    assert stats["ejobs"] == {"read": 3, "new_distinct": 1}


def test_norm_url():
    assert M.norm_url("https://www.Site.ro/a/b/?utm=1#x") == "site.ro/a/b"
