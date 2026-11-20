"""PostgreSQL export: unit tests always; the integration tests run when `psql` and DERKAISER_PG_URL are available."""
import json
import os
import shutil
import subprocess

import pytest

from scraper import pgexport as P


def job(**kw):
    base = {"url": "https://x.ro/j/1", "title": "Operator", "company": "ACME SRL", "cif": "123456", "location": ["Pitești"],
            "_source": "anofm"}
    base.update(kw)
    return base


# ---------- normalisation ----------

def test_cif_digits_only_and_padded():
    assert P.normalize_cif("RO 123456") == "00123456"
    assert P.normalize_cif(33936517) == "33936517"
    assert P.normalize_cif("1") is None and P.normalize_cif("12345678901") is None and P.normalize_cif("") is None


def test_model_format_city_from_location_and_bare_country_is_not_a_city():
    j, _ = P.normalize_job(job())
    assert j["city"] == "Pitești" and j["source"] == "anofm" and j["cif"] == "00123456"
    j, _ = P.normalize_job(job(location=["România"]))
    assert j["city"] == ""  # becomes NULL in SQL
    j, _ = P.normalize_job(job(location=["Romania", "Cluj-Napoca"]))
    assert j["city"] == "Cluj-Napoca"


def test_own_site_format():
    j, _ = P.normalize_job({"job_link": "https://a.ro/c/1", "job_title": "  Sofer   C+E ", "company": "A SRL", "cif": "99",
                            "city": "Brasov", "county": ["Brasov"], "remote": ["hybrid"], "_source": "site"})
    assert (j["title"], j["city"], j["county"], j["work_mode"], j["source"]) == ("Sofer C+E", "Brasov", "Brasov", "hybrid", "site")
    j, _ = P.normalize_job({"job_link": "https://a.ro/c/2", "job_title": "X", "company": "A SRL", "cif": "99"})
    assert j["source"] == "site"  # own-site rows carry no label; the model rows without one stay 'unknown'
    j, _ = P.normalize_job(job(_source=None))
    assert j["source"] == "unknown"


@pytest.mark.parametrize("raw, mode", [(True, "remote"), (["Remote"], "remote"), ("Hibrid", "hybrid"), ("on-site", "on-site"),
                                       ("flexibil", None), ("", None), (None, None), (False, None)])
def test_work_mode_is_never_guessed(raw, mode):
    assert P.work_mode_of(raw) == mode


@pytest.mark.parametrize("kw, reason", [({"url": "ftp://x"}, "no_url"), ({"title": " "}, "no_title"),
                                        ({"company": ""}, "no_company"), ({"cif": "abc"}, "bad_cif"),
                                        ({"cif": "2850425440010"}, "personal_id")])
def test_unusable_rows_are_rejected_with_a_reason(kw, reason):
    assert P.normalize_job(job(**kw)) == (None, reason)


def test_job_without_cif_is_kept_with_the_employer_name():
    j, why = P.normalize_job(job(cif=None, company="Spitalul Județean"))
    assert why == "" and j["cif"] is None and j["company"] == "Spitalul Județean"
    sql, st = P.build_sql([job(cif="", company="Spitalul Județean")], schema_sql="-- s")
    assert st["jobs"] == 1 and st["companies"] == 0 and "INSERT INTO companies" not in sql


def test_personal_id_in_cif_field_is_never_exported():
    sql, st = P.build_sql([job(cif="2850425440010", company="ION LAVINIA MIRELA")], schema_sql="-- s")
    assert st["jobs"] == 0 and st["skipped"] == {"personal_id": 1}
    assert "2850425440010" not in sql and "LAVINIA" not in sql


def test_bad_timestamps_become_null():
    j, _ = P.normalize_job(job(expirationdate="not a date", date="2026-11-15T01:10:26.437Z"))
    assert j["expires_at"] is None and j["scraped_at"] == "2026-11-15T01:10:26.437Z"


# ---------- SQL text ----------

def test_quotes_backslashes_and_nul_are_escaped():
    assert P.q("O'Neil \\ x\x00y") == "'O''Neil \\ xy'"
    assert P.q(None) == "NULL" and P.q("") == "NULL"


def test_dedupe_by_url_stats_and_batching():
    rows = [job(), job(title="same url again"), job(url="https://x.ro/j/2", cif="x")]
    sql, st = P.build_sql(rows, schema_sql="-- schema")
    assert st["jobs"] == 1 and st["duplicates"] == 1 and st["skipped"] == {"bad_cif": 1} and st["companies"] == 1
    many = [job(url=f"https://x.ro/j/{i}", cif=str(10 + i % 7)) for i in range(1203)]
    sql, st = P.build_sql(many, schema_sql="-- schema")
    assert sql.count("INSERT INTO jobs") == 3 and st["jobs"] == 1203 and st["companies"] == 7


def test_script_is_transactional_and_has_no_description_column():
    sql, _ = P.build_sql([job()])
    assert sql.index("BEGIN;") < sql.index("INSERT INTO companies") < sql.index("INSERT INTO jobs") < sql.rindex("COMMIT;")
    import re
    code = re.sub(r"--.*", "", P.SCHEMA_PATH.read_text(encoding="utf-8")).lower()  # comments may mention the word
    assert "description" not in code and "cv" not in re.findall(r"\b\w+\b", code)


def test_deactivate_missing_is_opt_in_and_scoped_to_present_sources():
    sql, _ = P.build_sql([job()])
    assert "UPDATE jobs SET active = false" not in sql
    sql, _ = P.build_sql([job(), job(url="https://x.ro/j/2", _source="site")], deactivate_missing=True)
    assert "source IN ('anofm', 'site')" in sql


def test_company_extras_enrich_the_companies_table():
    extras = P.company_extras([{"id": "123456", "employees": 30, "website": ["https://acme.ro"]}, {"id": "bad"}])
    sql, _ = P.build_sql([job()], extras=extras)
    assert "('00123456', 'ACME SRL', 'https://acme.ro', 30)" in sql


def test_cli_roundtrip(tmp_path):
    src = tmp_path / "jobs.jsonl"
    src.write_text(json.dumps(job()) + "\n\nnot json\n" + json.dumps(job(url="https://x.ro/j/9")) + "\n", encoding="utf-8")
    out = tmp_path / "import.sql"
    assert P.main(["--out", str(out), str(src)]) == 0
    assert out.read_text(encoding="utf-8").count("https://x.ro/j/") == 2


# ---------- integration against a real PostgreSQL ----------

PG = os.environ.get("DERKAISER_PG_URL")
needs_pg = pytest.mark.skipif(not (PG and shutil.which("psql")), reason="set DERKAISER_PG_URL and install psql")


def psql(*args, sql=None):
    cmd = ["psql", PG, "-v", "ON_ERROR_STOP=1", "-X", "-q", "-t", "-A", *args]
    return subprocess.run(cmd, input=sql, capture_output=True, text=True, encoding="utf-8")


@pytest.fixture
def clean_db():
    psql(sql="DROP VIEW IF EXISTS active_jobs; DROP TABLE IF EXISTS jobs; DROP TABLE IF EXISTS companies;")
    yield
    psql(sql="DROP VIEW IF EXISTS active_jobs; DROP TABLE IF EXISTS jobs; DROP TABLE IF EXISTS companies;")


def run_import(tmp_path, rows, **kw):
    sql, st = P.build_sql(rows, **kw)
    f = tmp_path / "i.sql"
    f.write_text(sql, encoding="utf-8")
    r = psql("-f", str(f))
    assert r.returncode == 0, r.stderr
    return st


def count(table, where="true"):
    return int(psql("-c", f"SELECT count(*) FROM {table} WHERE {where}").stdout.strip())


@needs_pg
def test_import_is_idempotent_and_handles_awkward_text(clean_db, tmp_path):
    rows = [job(title="Chef de rang \\ O'Brien \"bar\" 😀 ăîșțâ"), job(url="https://x.ro/j/2", cif="777", company="B SRL", location=["România"])]
    rows.append(job(url="https://x.ro/j/3", cif=None, company="Spitalul Județean"))  # ad without a CIF
    run_import(tmp_path, rows)
    run_import(tmp_path, rows)  # second run: nothing duplicated
    assert count("jobs") == 3 and count("companies") == 2 and count("active_jobs") == 3
    assert psql("-c", "SELECT company FROM active_jobs WHERE url='https://x.ro/j/3'").stdout.strip() == "Spitalul Județean"
    assert psql("-c", "SELECT title FROM jobs WHERE url='https://x.ro/j/1'").stdout.strip() == rows[0]["title"]
    assert count("jobs", "city IS NULL") == 1  # "România" alone is not a city


@needs_pg
def test_update_in_place_and_deactivate_missing(clean_db, tmp_path):
    run_import(tmp_path, [job(), job(url="https://x.ro/j/2")])
    run_import(tmp_path, [job(title="Operator CNC")], deactivate_missing=True)  # j/2 vanished from anofm
    assert psql("-c", "SELECT title FROM jobs WHERE url='https://x.ro/j/1'").stdout.strip() == "Operator CNC"
    assert count("jobs", "active") == 1 and count("jobs") == 2
    assert count("active_jobs") == 1
    run_import(tmp_path, [job(url="https://x.ro/j/2")])  # it came back: active again
    assert count("jobs", "active") == 2


@needs_pg
def test_database_rejects_bad_rows_even_if_the_exporter_is_bypassed(clean_db):
    assert psql(sql=P.SCHEMA_PATH.read_text(encoding="utf-8")).returncode == 0
    assert psql(sql="INSERT INTO companies (cif, name) VALUES ('abc', 'X');").returncode != 0
    assert psql(sql="INSERT INTO jobs (url, title, cif, company_name) VALUES ('https://a.ro/1', 't', '00000001', 'A');").returncode != 0  # no such company
    assert psql(sql="INSERT INTO companies VALUES ('00000001','A',NULL,NULL,now(),now()); "
                    "INSERT INTO jobs (url,title,cif,company_name,work_mode) VALUES ('https://a.ro/1','t','00000001','A','whenever');").returncode != 0
    assert psql(sql="INSERT INTO jobs (url,title,company_name) VALUES ('https://a.ro/2','t','Spital');").returncode == 0  # no CIF is allowed
