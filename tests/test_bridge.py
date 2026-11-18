import json

from scraper import bridge as B


def test_firms_from_rows_filters_employees_and_dedupes():
    rows = [{"cif": "123", "name": "Acme Soft SRL", "employees": "5"},
            {"id": "00000124", "company": "Solo SRL", "salariati": 1},
            {"id": "125", "company": "Fara Date SRL"},
            {"doc": {"id": "126", "company": "Nested SA", "employees": 40}},
            {"cif": "123", "name": "Acme Soft SRL", "employees": 5}]
    got = list(B.firms_from_rows(rows, min_employees=2))
    assert [f["company"] for f in got] == ["Acme Soft SRL", "Nested SA"]
    assert got[0]["id"] == "00000123"


def test_more_domains_includes_two_word_variant():
    d = B.more_domains("Acme Soft Solutions SRL")
    assert "acmesoft.ro" in d and "acme-soft.com" in d


def page(name, cif):
    return f"<html><body><footer>{name} CUI RO{cif} Romania</footer></body></html>"


def test_find_site_requires_name_and_cif():
    firm = {"id": "00000123", "company": "Acme Soft SRL", "employees": 5}

    def get(url):
        if "acmesoft.ro" in url:
            return page("Acme Soft", "123")
        if "acmesoft.com" in url:
            return page("Acme Soft", "999")  # same name, other firm: rejected
        raise OSError("nope")
    assert B.find_site(firm, get) == "https://acmesoft.ro"
    assert B.find_site(firm, lambda u: page("Acme Soft", "999") if "acme" in u else (_ for _ in ()).throw(OSError())) is None


def test_run_is_resumable_and_writes_docs(tmp_path):
    out = tmp_path / "docs.jsonl"
    firms = [{"id": "00000123", "company": "Acme Soft SRL", "employees": 5},
             {"id": "00000200", "company": "Niciun Site SRL", "employees": 9}]
    get = lambda u: page("Acme Soft", "123") if "acmesoft.ro" in u else (_ for _ in ()).throw(OSError())
    first = B.run(firms, get, str(out), workers=2)
    assert first["with_site"] == 1 and first["firms"] == 2
    assert B.run(firms, get, str(out))["firms"] == 0  # nothing left
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["website"] == ["https://acmesoft.ro"] and rows[1]["website"] == []


def test_find_site_skips_domains_that_do_not_resolve():
    firm = {"id": "00000123", "company": "Acme Soft SRL", "employees": 5}
    asked = []

    def get(url):
        asked.append(url)
        return page("Acme Soft", "123")
    assert B.find_site(firm, get, resolves=lambda d: d == "acmesoft.com") == "https://acmesoft.com"
    assert all("acmesoft.com" in u for u in asked)
