"""Tests for scraper.batch + scraper.derkaiser: resumable, dry-run, streaming."""

import json
from datetime import date

import pytest

from scraper import batch, derkaiser
from scraper import sources as src

TODAY = date(2026, 11, 13)


def record(cui, address="Municipiul Sibiu, Sibiu", **kw):
    return {"cui": int(cui), "name": f"FIRMA {int(cui)} SRL", "address": address, "inactive": False, **kw}


class Lookup:
    """Canned per-bare-CUI results; Exception instances are raised; unknown CUIs return a record."""

    def __init__(self, table=None):
        self.table = table or {}
        self.calls = []

    def __call__(self, cif):
        self.calls.append(cif)
        result = self.table.get(cif, record(cif))
        if isinstance(result, BaseException):
            raise result
        return result


def lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# --- read_cif_list ----------------------------------------------------------


def test_read_cif_list_skips_blanks_comments_and_takes_first_column(tmp_path):
    f = tmp_path / "cifs.txt"
    f.write_text("﻿# header comment\n\n 1559737 \n199150, SOME NAME\n52200796;x\n", encoding="utf-8")
    assert batch.read_cif_list(f) == ["1559737", "199150", "52200796"]


# --- run_batch basics -------------------------------------------------------


def test_writes_docs_progress_and_report(tmp_path):
    lk = Lookup({"30": RuntimeError("CUIScan returned no data")})
    report = batch.run_batch([10, 20, 30], tmp_path, lookup=lk, today=TODAY)
    assert [d["id"] for d in lines(tmp_path / "docs.jsonl")] == ["00000010", "00000020"]
    assert [r["status"] for r in lines(tmp_path / "progress.jsonl")] == ["ok", "ok", "not_found"]
    saved = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert saved == report
    assert report["counts_overall"] == {"ok": 2, "not_found": 1}
    assert report["dry_run"] is True and report["aborted"] is False


def test_report_is_honest_that_jobs_count_is_a_placeholder(tmp_path):
    report = batch.run_batch([10], tmp_path, lookup=Lookup(), today=TODAY)
    assert "placeholder" in report["existingJobsCount"]


def test_dirty_entries_are_recorded_invalid_without_any_request(tmp_path):
    lk = Lookup()
    report = batch.run_batch(["abc", "", 10, "abc"], tmp_path, lookup=lk, today=TODAY)
    assert lk.calls == ["10"]
    assert report["counts_overall"] == {"invalid": 2, "ok": 1}  # "abc" and "" once each


def test_duplicates_after_padding_are_processed_once(tmp_path):
    lk = Lookup()
    batch.run_batch([1559737, "01559737", "RO1559737"], tmp_path, lookup=lk, today=TODAY)
    assert lk.calls == ["1559737"]


def test_inactive_firm_is_ignored_and_not_written(tmp_path):
    lk = Lookup({"10": record(10, inactive=True)})
    report = batch.run_batch([10], tmp_path, lookup=lk, today=TODAY)
    assert report["counts_overall"] == {"ignored": 1}
    assert not lines(tmp_path / "docs.jsonl")


def test_max_firms_limits_new_work(tmp_path):
    lk = Lookup()
    report = batch.run_batch(range(1, 11), tmp_path, lookup=lk, today=TODAY, max_firms=4)
    assert lk.calls == ["1", "2", "3", "4"] and report["processed_this_run"] == 4


# --- resume -----------------------------------------------------------------


def test_resume_after_a_crash_neither_loses_nor_duplicates(tmp_path):
    crashing = Lookup({"3": KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        batch.run_batch([1, 2, 3, 4, 5], tmp_path, lookup=crashing, today=TODAY)
    assert [r["cif"] for r in lines(tmp_path / "progress.jsonl")] == ["00000001", "00000002"]

    healthy = Lookup()
    report = batch.run_batch([1, 2, 3, 4, 5], tmp_path, lookup=healthy, today=TODAY)
    assert healthy.calls == ["3", "4", "5"]  # 1 and 2 are not redone
    assert report["skipped_already_done"] == 2
    ids = [d["id"] for d in lines(tmp_path / "docs.jsonl")]
    assert sorted(ids) == [f"0000000{i}" for i in range(1, 6)] and len(ids) == len(set(ids))
    assert report["docs_unique"] == 5 and report["docs_duplicate_lines"] == 0


def test_crash_between_doc_and_progress_redoes_the_firm_and_collapses_duplicates(tmp_path):
    # first run finished firm 1; simulate a crash right after writing firm 2's document
    batch.run_batch([1], tmp_path, lookup=Lookup(), today=TODAY)
    doc2 = src.company_doc_for_cif(2, lookup=Lookup(), today=TODAY).doc
    with (tmp_path / "docs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(doc2) + "\n")  # doc written, progress line never was

    report = batch.run_batch([1, 2], tmp_path, lookup=Lookup(), today=TODAY)
    assert report["processed_this_run"] == 1  # firm 2 redone
    assert report["docs_duplicate_lines"] == 1 and report["docs_unique"] == 2  # collapsed in the report


def test_truncated_last_progress_line_is_ignored(tmp_path):
    batch.run_batch([1, 2], tmp_path, lookup=Lookup(), today=TODAY)
    with (tmp_path / "progress.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"cif": "00000003", "sta')  # killed mid-write
    lk = Lookup()
    batch.run_batch([1, 2, 3], tmp_path, lookup=lk, today=TODAY)
    assert lk.calls == ["3"]


def test_unavailable_is_retried_on_resume_but_final_statuses_are_not(tmp_path):
    down = Lookup({"2": RuntimeError("CUIScan API error: 503"), "3": RuntimeError("CUIScan returned no data")})
    first = batch.run_batch([1, 2, 3], tmp_path, lookup=down, today=TODAY)
    assert first["to_retry_unavailable"] == ["00000002"]

    up = Lookup()
    second = batch.run_batch([1, 2, 3], tmp_path, lookup=up, today=TODAY)
    assert up.calls == ["2"]  # only the one that was unavailable; not_found (3) stays final
    assert second["counts_overall"] == {"ok": 2, "not_found": 1}
    assert second["to_retry_unavailable"] == []


def test_source_down_aborts_then_resume_finishes(tmp_path):
    down = Lookup({str(i): RuntimeError("CUIScan API error: 503") for i in range(1, 9)})
    first = batch.run_batch(range(1, 9), tmp_path, lookup=down, today=TODAY, max_consecutive_unavailable=3)
    assert first["aborted"] is True and len(down.calls) == 3

    up = Lookup()
    second = batch.run_batch(range(1, 9), tmp_path, lookup=up, today=TODAY)
    assert second["aborted"] is False
    assert second["counts_overall"] == {"ok": 8} and second["docs_unique"] == 8


# --- CLI --------------------------------------------------------------------


def test_cli_runs_a_list_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(src, "_default_lookup", Lookup())
    cifs = tmp_path / "cifs.txt"
    cifs.write_text("# test\n10\n20\n", encoding="utf-8")
    code = derkaiser.main(["--cifs", str(cifs), "--out", str(tmp_path / "out"), "--pause", "0"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["counts_overall"] == {"ok": 2}


def test_cli_exits_two_when_aborted(tmp_path, monkeypatch):
    down = Lookup({str(i): RuntimeError("CUIScan API error: 503") for i in range(1, 6)})
    monkeypatch.setattr(src, "_default_lookup", down)
    cifs = tmp_path / "cifs.txt"
    cifs.write_text("\n".join(str(i) for i in range(1, 6)), encoding="utf-8")
    code = derkaiser.main(["--cifs", str(cifs), "--out", str(tmp_path / "out"), "--pause", "0", "--max-unavailable", "3"])
    assert code == 2


def test_over_padded_cif_is_the_same_firm_as_the_padded_one(tmp_path):
    lk = Lookup()
    batch.run_batch(["01234567", "0001234567", 1234567], tmp_path, lookup=lk, today=TODAY)
    assert lk.calls == ["1234567"]


def test_checksum_rejected_cif_is_final_and_not_retried_on_resume(tmp_path):
    bad = Lookup({"1": RuntimeError("CUIScan API error: 400")})
    first = batch.run_batch([1, 2], tmp_path, lookup=bad, today=TODAY)
    assert first["counts_overall"] == {"not_found": 1, "ok": 1} and first["to_retry_unavailable"] == []
    again = Lookup()
    batch.run_batch([1, 2], tmp_path, lookup=again, today=TODAY)
    assert again.calls == []
