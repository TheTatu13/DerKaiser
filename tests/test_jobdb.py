"""SQLite job database: build, search (diacritics ignored), stats and the read-only web page."""
import json
import sqlite3
import threading
import urllib.error
import urllib.request

import pytest

from scraper import jobdb as D


def job(i, **kw):
    base = {"url": f"https://x.ro/j/{i}", "title": "Operator", "company": "ACME SRL", "cif": "123456",
            "location": ["Pitești"], "_source": "anofm"}
    base.update(kw)
    return base


ROWS = [
    job(1, title="Asistent medical generalist", company="Spitalul Județean Argeș", cif=None, location=["Pitești"], _source="posturi.gov.ro"),
    job(2, title="Programator Python", company="DEV SRL", cif="777", location=["Cluj-Napoca"], _source="site", workmode="remote"),
    job(3, title="Șofer C+E", company="TRANS SRL", cif="888", location=["România"]),
    job(4, title="Inginer automatist", company="INOVA SRL", cif="999", location=["Brașov"], _source="ats:greenhouse"),
]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    D.build(path, ROWS)
    return path


def found(db, *a, **kw):
    con = D.connect_readonly(db)
    try:
        return D.search(con, *a, **kw)
    finally:
        con.close()


def test_build_counts_and_rules(tmp_path):
    rows = ROWS + [job(1), job(9, cif="2850425440010"), job(10, cif="abc"), job(11, title="")]
    st = D.build(tmp_path / "t.db", rows)
    assert st["jobs"] == 4 and st["duplicates"] == 1 and st["total_in_db"] == 4
    assert st["skipped"] == {"personal_id": 1, "bad_cif": 1, "no_title": 1}
    assert st["companies"] == 3  # the hospital ad has no CIF


def test_diacritics_case_and_prefix_are_ignored(db):
    assert [r["title"] for r in found(db, "pitesti")["rows"]] == ["Asistent medical generalist"]
    assert found(db, "SOFER")["total"] == 1 and found(db, "șofer")["total"] == 1
    assert found(db, "asist med")["total"] == 1  # prefixes
    assert found(db, "programator remote")["total"] == 0  # work mode is a filter, not text
    assert found(db, "argesului")["total"] == 0


def test_filters_and_combination(db):
    assert found(db, county="")["total"] == 4
    assert found(db, city="brasov")["rows"][0]["company"] == "INOVA SRL"
    assert found(db, source="site")["total"] == 1 and found(db, work_mode="remote")["total"] == 1
    assert found(db, "inginer", city="cluj")["total"] == 0


def test_hostile_input_never_breaks_the_query(db):
    for q in ['"', "*", "a OR b", "title:", "NEAR(", "'; DROP TABLE jobs;--", "( ) ^", "\\"]:
        found(db, q, city=q, county=q)  # must not raise
    assert found(db)["total"] == 4


def test_paging_and_limits(tmp_path):
    D.build(tmp_path / "p.db", [job(i) for i in range(1, 46)])
    con = D.connect_readonly(tmp_path / "p.db")
    assert len(D.search(con, limit=20)["rows"]) == 20
    assert len(D.search(con, limit=20, offset=40)["rows"]) == 5
    assert len(D.search(con, limit=10_000)["rows"]) == 45 <= D.MAX_LIMIT
    assert D.search(con, limit=0)["rows"] != []  # clamped to 1


def test_rebuild_is_idempotent_keeps_first_seen_and_updates(tmp_path):
    path = tmp_path / "t.db"
    D.build(path, ROWS)
    con = sqlite3.connect(path)
    first = dict(con.execute("SELECT url, first_seen FROM jobs").fetchall())
    D.build(path, [job(2, title="Programator Python senior", company="DEV SRL", cif="777")] + ROWS[:1] + ROWS[2:])
    assert con.execute("SELECT count(*) FROM jobs").fetchone()[0] == 4
    assert dict(con.execute("SELECT url, first_seen FROM jobs").fetchall()) == first
    assert found(path, "senior")["total"] == 1 and found(path, "python")["total"] == 1  # the index followed the update
    con.close()


def test_deactivate_missing_only_touches_sources_in_the_import(tmp_path):
    path = tmp_path / "t.db"
    D.build(path, ROWS)
    # job 3 (anofm) vanished; another anofm job is still listed, so anofm is "present" in this import
    D.build(path, [r for r in ROWS if r["url"] != "https://x.ro/j/3"] + [job(5, title="Zidar")], deactivate_missing=True)
    assert found(path, "sofer")["total"] == 0 and found(path, "sofer", only_active=False)["total"] == 1
    assert found(path, "programator")["total"] == 1  # other sources/jobs untouched
    D.build(path, ROWS)  # it came back
    assert found(path, "sofer")["total"] == 1
    # a source that is absent from an import is left alone (its site may simply have been skipped this time)
    D.build(path, [job(6, title="Tâmplar", _source="newsource")], deactivate_missing=True)
    assert found(path, "programator")["total"] == 1 and found(path, "sofer")["total"] == 1


def test_expired_jobs_are_hidden_by_default(tmp_path):
    path = tmp_path / "t.db"
    D.build(path, [job(1, expirationdate="2020-01-01T00:00:00Z"), job(2, expirationdate="2999-01-01T00:00:00Z"), job(3)])
    assert found(path)["total"] == 2 and found(path, only_active=False)["total"] == 3


def test_stats(db):
    st = D.stats(D.connect_readonly(db))
    assert st["jobs"] == 4 and st["active"] == 4 and st["without_city"] == 1
    assert {s["source"]: s["n"] for s in st["sources"]} == {"posturi.gov.ro": 1, "site": 1, "anofm": 1, "ats:greenhouse": 1}
    assert st["cities"][0]["n"] >= 1 and len(st["employers"]) == 4


def test_connection_is_read_only(db):
    con = D.connect_readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM jobs")


# ---------- web page ----------

@pytest.fixture
def server(db):
    srv = D.make_server(db, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.headers.get("Content-Type"), r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read().decode("utf-8")


def test_web_page_and_api(server):
    status, kind, body = get(server + "/")
    assert status == 200 and kind.startswith("text/html") and "<title>Joburi DerKaiser</title>" in body
    status, kind, body = get(server + "/api/jobs?q=pitesti")
    data = json.loads(body)
    assert status == 200 and data["total"] == 1 and data["rows"][0]["title"].startswith("Asistent")
    assert json.loads(get(server + "/api/stats")[2])["active"] == 4
    assert json.loads(get(server + "/api/jobs?city=cluj&source=site")[2])["total"] == 1


def test_web_errors_are_clean(server):
    assert get(server + "/api/jobs?limit=abc")[0] == 400
    assert get(server + "/nope")[0] == 404
    assert get(server + "/api/jobs?q=%22%2A%20OR")[0] == 200  # hostile text is just text


def test_web_server_is_get_only_and_never_writes(server, db):
    req = urllib.request.Request(server + "/api/jobs", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=5)
    assert err.value.code == 501
    assert sqlite3.connect(db).execute("SELECT count(*) FROM jobs").fetchone()[0] == 4


def test_cli_build_search_stats(tmp_path, capsys):
    src = tmp_path / "j.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in ROWS), encoding="utf-8")
    db = str(tmp_path / "c.db")
    assert D.main(["build", db, str(src)]) == 0
    assert D.main(["search", db, "programator"]) == 0
    out = capsys.readouterr().out
    assert "1 rezultate" in out and "Programator Python" in out
    assert D.main(["stats", db]) == 0
