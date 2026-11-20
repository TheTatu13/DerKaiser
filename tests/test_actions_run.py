"""The Actions orchestrator: repair of cut files, site extraction, time boxing, finishing, safety limits."""
import gzip
import json
import sys
import time
from pathlib import Path

import pytest

from scraper import actions_run as A

PY = sys.executable


def seed(state: Path):
    state.mkdir(parents=True, exist_ok=True)
    with gzip.open(state / "firms_active.jsonl.gz", "wb") as fh:
        fh.write(b'{"cif": "00000001", "company": "A SRL", "employees": 5}\n')


def script(code: str) -> list[str]:
    return [PY, "-c", code]


def test_ensure_newline_only_when_needed(tmp_path):
    f = tmp_path / "d.jsonl"
    assert A.ensure_newline(f) is False  # missing file
    f.write_bytes(b'{"a": 1}\n{"b"')  # killed mid-write
    assert A.ensure_newline(f) is True
    assert f.read_bytes() == b'{"a": 1}\n{"b"\n'
    assert A.ensure_newline(f) is False
    f.write_bytes(b"")
    assert A.ensure_newline(f) is False


def test_cut_line_does_not_swallow_the_next_record(tmp_path):
    f = tmp_path / "d.jsonl"
    f.write_bytes(b'{"id": "1"}\n{"id": "2", "web')
    A.ensure_newline(f)
    with f.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "3"}\n')
    good = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            good.append(json.loads(line)["id"])
        except ValueError:
            pass
    assert good == ["1", "3"]


def test_extract_sites_filters_dedupes_and_skips_garbage(tmp_path):
    docs = tmp_path / "docs.jsonl"
    docs.write_text('{"id": "1", "website": ["https://a.ro"]}\n{"id": "2", "website": []}\nnot json\n'
                    '{"id": "1", "website": ["https://dup.ro"]}\n{"id": "3", "website": ["https://c.ro"]}\n{"id": "4", "web', encoding="utf-8")
    out = tmp_path / "site.jsonl"
    assert A.extract_sites(docs, out) == 2
    assert [json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines()] == ["1", "3"]
    assert A.extract_sites(tmp_path / "missing.jsonl", out) == 0


def test_unpack_firms_and_missing_seed(tmp_path):
    with pytest.raises(FileNotFoundError, match="seed"):
        A.unpack_firms(tmp_path)
    seed(tmp_path)
    p = A.unpack_firms(tmp_path)
    assert p.read_text(encoding="utf-8").startswith('{"cif": "00000001"')
    p.write_text("changed\n", encoding="utf-8")
    assert A.unpack_firms(tmp_path).read_text(encoding="utf-8") == "changed\n"  # never overwritten


def test_run_for_returns_code_and_kills_on_timeout():
    assert A.run_for(script("import sys; sys.exit(3)"), 20, poll=0.02) == 3
    t0 = time.monotonic()
    assert A.run_for(script("import time; time.sleep(60)"), 0.5, poll=0.02) is None
    assert time.monotonic() - t0 < 15  # stopped, not waited for


def fake_bridge(state: Path, sleep_after: float = 0.0, code: int = 0) -> list[str]:
    return script(
        "import json, time, sys\n"
        f"f = open(r'{state / 'docs.jsonl'}', 'a', encoding='utf-8')\n"
        "for i, w in ((1, ['https://a.ro']), (2, []), (3, ['https://c.ro'])):\n"
        "    f.write(json.dumps({'id': str(i), 'company': 'F', 'website': w}) + '\\n'); f.flush()\n"
        f"time.sleep({sleep_after})\n"
        f"sys.exit({code})\n")


def fake_jobs(state: Path, code: int = 0, sleep_for: float = 0.0) -> list[str]:
    return script(
        "import json, time, sys\n"
        f"docs = [json.loads(l) for l in open(r'{state / 'docs_site.jsonl'}', encoding='utf-8') if l.strip()]\n"
        f"out = r'{state / 'out' / 'jobs_by_firm.jsonl'}'\n"
        "done = set()\n"
        "try:\n    done = {json.loads(l)['cif'] for l in open(out, encoding='utf-8') if l.strip()}\nexcept FileNotFoundError:\n    pass\n"
        "with open(out, 'a', encoding='utf-8') as f:\n"
        "    for d in docs:\n"
        "        if d['id'] not in done:\n            f.write(json.dumps({'cif': d['id'], 'jobs': 1}) + '\\n')\n"
        f"time.sleep({sleep_for})\n"
        f"sys.exit({code})\n")


KW = dict(safety=0, pass_every=0.2, poll=0.05)


def test_full_run_finishes_when_bridge_is_done_and_last_pass_completed(tmp_path):
    st = tmp_path / "state"
    seed(st)
    (st / "out").mkdir()
    res = A.run(st, 1, bridge_cmd=fake_bridge(st), jobs_cmd=fake_jobs(st), **KW)
    assert res["finished"] is True and res["failed"] is False and res["reason"] == "all firms checked"
    assert res["firms_checked"] == 3 and res["firms_with_site"] == 2 and res["firms_jobs_done"] == 2
    assert json.loads((st / "run_log.json").read_text())["runs"] == 1


def test_budget_ends_a_slow_run_and_the_bridge_is_stopped(tmp_path):
    st = tmp_path / "state"
    seed(st)
    t0 = time.monotonic()
    res = A.run(st, 0.1, bridge_cmd=fake_bridge(st, sleep_after=120), jobs_cmd=fake_jobs(st), **KW)  # 6 s budget
    assert res["finished"] is False and res["failed"] is False and res["reason"] == "time budget used"
    assert time.monotonic() - t0 < 60
    assert res["firms_with_site"] == 2  # the work done before the stop is kept and counted


def test_second_run_resumes_and_does_not_redo_firms(tmp_path):
    st = tmp_path / "state"
    seed(st)
    A.run(st, 0.1, bridge_cmd=fake_bridge(st, sleep_after=120), jobs_cmd=fake_jobs(st), **KW)
    again = A.run(st, 1, bridge_cmd=script("pass"), jobs_cmd=fake_jobs(st), **KW)  # bridge has nothing left
    assert again["finished"] is True and again["run"] == 2
    assert again["firms_jobs_done"] == 2  # still 2, not 4


def test_bridge_crash_is_reported_as_failure(tmp_path):
    st = tmp_path / "state"
    seed(st)
    res = A.run(st, 1, bridge_cmd=fake_bridge(st, code=2), jobs_cmd=fake_jobs(st), **KW)
    assert res["failed"] is True and res["finished"] is False and "bridge exited with 2" in res["reason"]


def test_jobs_pass_crash_is_reported_as_failure(tmp_path):
    st = tmp_path / "state"
    seed(st)
    res = A.run(st, 1, bridge_cmd=fake_bridge(st, sleep_after=30), jobs_cmd=fake_jobs(st, code=5), **KW)
    assert res["failed"] is True and "jobs pass exited with 5" in res["reason"]


def test_stop_file_and_max_runs_end_the_chain(tmp_path):
    st = tmp_path / "state"
    seed(st)
    (st / "STOP").write_text("", encoding="utf-8")
    assert A.run(st, 1, bridge_cmd=fake_bridge(st), jobs_cmd=fake_jobs(st), **KW)["reason"] == "STOP file"
    (st / "STOP").unlink()
    (st / "run_log.json").write_text('{"runs": 40}', encoding="utf-8")
    res = A.run(st, 1, bridge_cmd=fake_bridge(st), jobs_cmd=fake_jobs(st), max_runs=40, **KW)
    assert res["finished"] is True and "max_runs" in res["reason"] and res["firms_checked"] == 0


def test_cut_files_are_repaired_before_the_run(tmp_path):
    st = tmp_path / "state"
    seed(st)
    (st / "docs.jsonl").write_bytes(b'{"id": "9", "company": "X", "website": []}\n{"id": "8", "comp')
    A.run(st, 1, bridge_cmd=fake_bridge(st), jobs_cmd=fake_jobs(st), **KW)
    good = []
    for l in (st / "docs.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            good.append(json.loads(l)["id"])
        except ValueError:
            pass
    assert good == ["9", "1", "2", "3"]  # the three new records were not glued onto the cut line


def test_main_writes_summary_and_github_output(tmp_path, monkeypatch):
    st = tmp_path / "state"
    seed(st)
    (st / "STOP").write_text("", encoding="utf-8")
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert A.main(["--state", str(st), "--minutes", "1"]) == 0
    assert "finished=true" in out.read_text(encoding="utf-8")
    assert json.loads((st / "run_summary.json").read_text(encoding="utf-8"))["reason"] == "STOP file"
