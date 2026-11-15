import json

import pytest

from scraper import jobs as J
from scraper import publish as P


def _doc(cif, name, website=None):
    return {"id": cif, "company": name, "website": [website] if website else []}


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


GH = json.dumps({"jobs": [{"title": "QA", "absolute_url": "https://boards.greenhouse.io/alfa/jobs/1", "location": {"name": "Cluj"}},
                          {"title": "Dev", "absolute_url": "https://boards.greenhouse.io/alfa/jobs/2", "location": {"name": "Iasi"}}]})
PAGES = {"https://alfa.ro": '<a href="/cariere">Cariere</a>',
         "https://alfa.ro/cariere": "https://boards.greenhouse.io/alfa",
         J.ats_url("greenhouse", "alfa"): GH,
         "https://beta.ro": "<html></html>"}


NO_ANOFM = lambda url, body: {"rows": []}  # noqa: E731


def _get(url):
    if url not in PAGES:
        raise OSError("404")
    return PAGES[url]


def test_pace_waits_between_calls():
    t, slept = [0.0], []
    p = P.Pace(2.0, clock=lambda: t[0], sleep=slept.append)
    p.wait()
    t[0] = 0.5
    p.wait()
    assert slept == [1.5]


def test_alive_jobs_drops_dead_links():
    jobs = [{"url": "https://a/1"}, {"url": "https://a/2"}, {"url": "https://a/3"}]
    alive, dead = P.alive_jobs(jobs, {"https://a/1": 200, "https://a/2": 404, "https://a/3": 0}.get)
    assert alive == [jobs[0]] and dead == jobs[1:]


def test_run_jobs_end_to_end_with_counts_and_resume(tmp_path):
    docs = tmp_path / "docs.jsonl"
    _write(docs, [_doc("00000001", "Alfa", "alfa.ro"), _doc("00000002", "Beta", "beta.ro"), _doc("00000003", "Gamma")])
    status = lambda url: 404 if url.endswith("/2") else 200  # noqa: E731
    rep = P.run_jobs(docs, tmp_path / "out", get=_get, status=status, anofm=NO_ANOFM)
    assert rep["firms_done"] == 3 and rep["firms_with_jobs"] == 1 and rep["jobs_total"] == 1 and rep["dead_links"] == 1
    assert rep["by_method"] == {"greenhouse": 1, "none": 2}
    out = {json.loads(l)["id"]: json.loads(l) for l in (tmp_path / "out" / "docs_with_jobs.jsonl").read_text().splitlines()}
    assert out["00000001"]["existingJobsCount"] == [1] and out["00000002"]["existingJobsCount"] == [0]
    assert out["00000003"]["existingJobsCount"] == [0]  # firm kept with 0 jobs
    calls = []
    P.run_jobs(docs, tmp_path / "out", get=lambda u: calls.append(u) or _get(u), status=status, anofm=NO_ANOFM)
    assert calls == []  # resume: nothing fetched twice


def test_websites_override_and_max_firms(tmp_path):
    docs = tmp_path / "docs.jsonl"
    _write(docs, [_doc("00000001", "Alfa"), _doc("00000002", "Beta")])
    rep = P.run_jobs(docs, tmp_path / "o", websites={"00000001": "alfa.ro"}, get=_get, status=lambda u: 200, anofm=NO_ANOFM, max_firms=1)
    assert rep["firms_done"] == 1 and rep["jobs_total"] == 2 and rep["firms_left"] == 1


def test_wrapped_doc_rows_are_understood(tmp_path):
    docs = tmp_path / "docs.jsonl"
    _write(docs, [{"doc": _doc("00000001", "Alfa", "alfa.ro"), "status": "ok"}])
    assert P.run_jobs(docs, tmp_path / "o", get=_get, status=lambda u: 200, anofm=NO_ANOFM)["jobs_total"] == 2


def test_payload_is_brewtality_model_and_send_is_guarded(tmp_path):
    docs = tmp_path / "docs.jsonl"
    _write(docs, [_doc("00000001", "Alfa", "alfa.ro")])
    P.run_jobs(docs, tmp_path / "o", get=_get, status=lambda u: 200, anofm=NO_ANOFM)
    payloads = P.payloads_by_company(tmp_path / "o" / "jobs.jsonl")
    j = payloads[0][0]
    assert len(payloads) == 1 and len(payloads[0]) == 2
    assert {"url", "title", "company", "cif", "location", "date", "status"} <= set(j)
    assert j["cif"] == "00000001" and j["status"] == "scraped" and j["date"].endswith("Z")
    with pytest.raises(RuntimeError, match="dry-run"):
        P.send_jobs(payloads)
    seen = []
    assert P.send_jobs(payloads + [[]], confirm=True, post=lambda u, b: seen.append((u, len(b)))) == 2
    assert seen == [("https://api.peviitor.ro/v1/scraper/jobs/upload/", 2)]


def test_send_company_needs_confirm_and_success():
    with pytest.raises(RuntimeError, match="dry-run"):
        P.send_company({"id": "1"})
    sent = []
    P.send_company({"id": "2816464"}, confirm=True, put=lambda u, b: sent.append((u, b["id"])) or {"success": True})
    assert sent == [("https://api.peviitor.ro/v1/firme/company/add/", "02816464")]
    with pytest.raises(RuntimeError, match="failed"):
        P.send_company({"id": "1"}, confirm=True, put=lambda u, b: {"success": False})


def test_existing_jobs_count_reads_total():
    assert P.existing_jobs_count("2816464", lambda u: json.dumps({"total": 7, "data": []}) if "cif=02816464" in u else "{}") == 7


def test_anofm_jobs_added_without_website_and_deduped(tmp_path):
    docs = tmp_path / "docs.jsonl"
    _write(docs, [_doc("00000003", "Gamma")])
    rows = {"rows": [{"id": 9, "occupation": "Sofer", "address_locality_name": "Arges > Pitesti"},
                     {"id": 9, "occupation": "Sofer", "address_locality_name": "Arges > Pitesti"},
                     {"id": 10, "occupation": ""}]}
    seen = []
    anofm = lambda url, body: seen.append(body["employer_tax_code"]) or rows  # noqa: E731
    rep = P.run_jobs(docs, tmp_path / "o", get=_get, status=lambda u: 200, anofm=anofm)
    assert seen == ["3"] and rep["jobs_total"] == 1 and rep["by_method"] == {"anofm": 1}
    row = json.loads((tmp_path / "o" / "jobs.jsonl").read_text().splitlines()[0])
    assert row["url"].endswith("/job/9") and row["location"] == ["Pitesti"]


def test_anofm_failure_is_not_fatal(tmp_path):
    docs = tmp_path / "docs.jsonl"
    _write(docs, [_doc("00000003", "Gamma")])

    def boom(url, body):
        raise OSError("down")

    assert P.run_jobs(docs, tmp_path / "o", get=_get, status=lambda u: 200, anofm=boom)["firms_done"] == 1


def test_website_discovered_from_anofm_email_then_site_jobs_scraped(tmp_path):
    docs = tmp_path / "docs.jsonl"
    _write(docs, [_doc("00000001", "Alfa")])  # no website in the doc
    rows = {"rows": [{"id": 5, "occupation": "Sofer", "address_locality_name": "Cluj", "contact_email": "hr@alfa.ro"}]}
    rep = P.run_jobs(docs, tmp_path / "o", get=_get, status=lambda u: 200, anofm=lambda u, b: rows)
    assert rep["jobs_total"] == 3  # 2 greenhouse (alfa.ro careers) + 1 ANOFM
    firm = json.loads((tmp_path / "o" / "jobs_by_firm.jsonl").read_text().splitlines()[0])
    assert firm["site_source"] == "anofm_email" and firm["site"] == "https://alfa.ro"
    out = json.loads((tmp_path / "o" / "docs_with_jobs.jsonl").read_text().splitlines()[0])
    assert out["website"] == ["https://alfa.ro"]
