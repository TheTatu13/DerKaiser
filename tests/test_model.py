from datetime import datetime, timezone

from scraper import model as M


def test_locations_fall_back_to_romania():
    assert M.validate_ro_locations(["Pitesti", "Berlin"]) == ["Pitesti"]
    assert M.validate_ro_locations(["romania"]) == ["România"]
    assert M.validate_ro_locations(["Berlin"]) == ["România"] and M.validate_ro_locations(None) == ["România"]


def test_workmode_and_iso_z():
    assert [M.normalize_workmode(x) for x in ("Remote", "on-site", "office", "x", None)] == ["remote", "on-site", "on-site", "hybrid", None]
    assert M.iso_z(datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc)) == "2026-09-30T23:59:59.000Z"


def test_job_model_drops_none_and_pads_cif():
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    j = M.to_job_model({"url": "https://a/1", "title": "Dev", "location": ["Iasi"]}, "2816464", "Dedeman", now)
    assert j == {"url": "https://a/1", "title": "Dev", "company": "Dedeman", "cif": "02816464", "location": ["Iasi"],
                 "date": "2026-01-02T00:00:00.000Z", "status": "scraped"}


def test_from_legacy():
    assert M.from_legacy({"job_link": "u", "job_title": "t", "city": "Cluj-Napoca", "remote": ["hybrid"]}) == \
        {"url": "u", "title": "t", "location": ["Cluj-Napoca"], "workmode": "hybrid"}


def test_candidate_domains_skip_freemail_and_rank():
    rows = [{"contact_email": "hr@dedeman.ro"}, {"contact_email": "x@gmail.com"}, {"contact_email": "a@dedeman.ro"},
            {"contact_email": "b@contabil.ro"}, {"contact_email": None}]
    assert M.candidate_domains(rows) == ["dedeman.ro", "contabil.ro"]


def test_core_tokens_drop_legal_forms():
    assert M.core_tokens("DEDEMAN S.R.L.") == ["dedeman"] and M.core_tokens("Ana-Maria Șarpe SRL") == ["ana", "maria", "sarpe"]


def test_verify_site_needs_name_match():
    page = {"https://dedeman.ro": "<html>Dedeman</html>", "https://contabil.ro": "<html>Servicii contabilitate</html>"}

    def get(url):
        if url not in page:
            raise OSError("down")
        return page[url]

    assert M.verify_site("dedeman.ro", "DEDEMAN SRL", get) == "https://dedeman.ro"
    assert M.verify_site("contabil.ro", "DEDEMAN SRL", get) is None  # an accountant's domain is rejected
    assert M.verify_site("nope.ro", "DEDEMAN SRL", get) is None
    rows = [{"contact_email": "b@contabil.ro"}, {"contact_email": "a@dedeman.ro"}, {"contact_email": "c@dedeman.ro"}]
    assert M.discover_website(rows, "DEDEMAN SRL", get) == "https://dedeman.ro"


def test_guess_domains_from_distinctive_name():
    assert M.guess_domains("CARREFOUR ROMANIA SA") == ["carrefour.ro", "carrefour.com"]
    assert M.guess_domains("COMPANIA NATIONALA POSTA ROMANA SA")[:2] == ["posta.ro", "posta.com"] or "postaromana" in "".join(M.guess_domains("POSTA ROMANA SRL"))
    assert M.guess_domains("S.C. SRL") == []


def test_guessed_domain_needs_strict_proof():
    def site(html):
        return lambda url: html if url == "https://egala.ro" else (_ for _ in ()).throw(OSError("x"))
    ok = "<html>Egala SRL, CUI RO 12345678, Romania</html>"
    assert M.verify_site("egala.ro", "EGALA SRL", site(ok), strict_cif="12345678") == "https://egala.ro"
    assert M.verify_site("egala.ro", "EGALA SRL", site("<html>Egala fan page</html>"), strict_cif="12345678") is None  # no CIF / Romania
    assert M.verify_site("egala.ro", "EGALA SRL", site("<html>Altceva, CUI 12345678</html>"), strict_cif="12345678") is None  # name missing
    assert M.verify_site("egala.ro", "EGALA SRL", site("<html>Egala - Romania</html>"), strict_cif="12345678") == "https://egala.ro"  # .ro + Romania
    assert M.guess_website("EGALA SRL", "12345678", site(ok)) == "https://egala.ro"
