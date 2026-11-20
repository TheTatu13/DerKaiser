"""Long, resumable run for GitHub Actions: firm websites (bridge) + their jobs (derkaiser --jobs) in time-boxed slices.

    python -m scraper.actions_run --state state --minutes 330

A GitHub job lives at most 6 hours, so one run works for ``--minutes`` and stops cleanly; the workflow saves ``state/``
on the ``run-state`` branch and starts the next run until everything is done (``finished=true`` in ``$GITHUB_OUTPUT``).
Everything is read-only against the outside world and writes local files only (dry-run): nothing is sent to peViitor,
inviitor.ro or any database.

``state/`` holds: ``firms_active.jsonl.gz`` (the firms to check: cif, company, employees), ``docs.jsonl`` (website found
per firm), ``docs_site.jsonl`` (only the firms with a website), ``out/`` (jobs.jsonl, jobs_by_firm.jsonl), ``recipes/``,
``run_log.json`` (how many runs so far). Both stages resume where they stopped, so a killed run loses at most a few
seconds of work. A file that was cut in the middle of a line is repaired before every start.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

MAX_MINUTES = 340          # a GitHub job is killed at 360; the rest is for saving the state
SAFETY_SECONDS = 4 * 60    # kept free at the end of the budget for the last pass and the summary
JOBS_PASS_MAX = 25 * 60    # one jobs pass never runs longer than this
PASS_EVERY = 10 * 60       # a jobs pass starts every 10 minutes while the bridge keeps finding websites


def ensure_newline(path: str | Path) -> bool:
    """Make a jsonl file end with a newline (a run killed mid-write leaves half a line: the next append would glue to it).

    Returns True when a newline had to be added. The half line stays; readers skip lines that are not valid JSON.
    """
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return False
    with p.open("rb+") as fh:
        fh.seek(-1, os.SEEK_END)
        if fh.read(1) == b"\n":
            return False
        fh.write(b"\n")
    return True


def unpack_firms(state: str | Path) -> Path:
    """``firms_active.jsonl`` from ``firms_active.jsonl.gz`` (only when missing); returns its path."""
    st = Path(state)
    plain, packed = st / "firms_active.jsonl", st / "firms_active.jsonl.gz"
    if not plain.exists():
        if not packed.exists():
            raise FileNotFoundError(f"{packed} is missing: seed the run-state branch first (tools/seed_run_state.ps1)")
        with gzip.open(packed, "rb") as src, plain.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    return plain


def extract_sites(docs_path: str | Path, out_path: str | Path) -> int:
    """Write the documents that have a website (one per CIF) to ``out_path``; returns how many."""
    seen, rows = set(), []
    p = Path(docs_path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and d.get("website") and d.get("id") and d["id"] not in seen:
                seen.add(d["id"])
                rows.append(d)
    Path(out_path).write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in rows), encoding="utf-8")
    return len(rows)


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as fh:
        return sum(1 for line in fh if line.strip())


def summarize(state: str | Path) -> dict:
    """Progress numbers read from the state files."""
    st = Path(state)
    firms = _count_lines(st / "firms_active.jsonl") if (st / "firms_active.jsonl").exists() else None
    return {"firms_total": firms, "firms_checked": _count_lines(st / "docs.jsonl"),
            "firms_with_site": _count_lines(st / "docs_site.jsonl"),
            "firms_jobs_done": _count_lines(st / "out" / "jobs_by_firm.jsonl"), "job_lines": _count_lines(st / "out" / "jobs.jsonl")}


def run_for(cmd: list[str], seconds: float, *, spawn=subprocess.Popen, clock=time.monotonic, sleep=time.sleep,
            poll: float = 1.0, log=None) -> int | None:
    """Run ``cmd``; ``returncode``, or None when it was still running after ``seconds`` and had to be stopped."""
    proc = spawn(cmd, stdout=log, stderr=subprocess.STDOUT)
    end = clock() + seconds
    while proc.poll() is None:
        if clock() >= end:
            stop(proc)
            return None
        sleep(poll)
    return proc.returncode


def stop(proc, grace: float = 20.0) -> None:
    """Terminate politely, kill after ``grace`` seconds."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def default_commands(state: Path) -> tuple[list[str], list[str]]:
    """The real bridge and jobs commands (same settings as the local full_pipeline.ps1)."""
    py = sys.executable
    bridge = [py, "-u", "-m", "scraper.bridge", "--firms", str(state / "firms_active.jsonl"), "--out", str(state / "docs.jsonl"),
              "--min-employees", "1", "--workers", "40", "--interval", "2"]
    jobs = [py, "-u", "-m", "scraper.derkaiser", "--jobs", str(state / "docs_site.jsonl"), "--out", str(state / "out"),
            "--workers", "6", "--interval", "3", "--learn", "--no-render", "--recipes", str(state / "recipes")]
    return bridge, jobs


def run(state: str | Path, minutes: float, *, bridge_cmd: list[str] | None = None, jobs_cmd: list[str] | None = None,
        max_runs: int = 40, safety: float = SAFETY_SECONDS, pass_every: float = PASS_EVERY, poll: float = 1.0,
        spawn=subprocess.Popen, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Work for ``minutes`` (at most MAX_MINUTES) and report. ``finished`` is True when there is nothing left to do
    (or the safety limits ``max_runs`` / a ``STOP`` file say stop); ``failed`` is True when the bridge crashed."""
    st = Path(state)
    st.mkdir(parents=True, exist_ok=True)
    (st / "out").mkdir(exist_ok=True)
    (st / "logs").mkdir(exist_ok=True)
    log_path = st / "run_log.json"
    try:
        log = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log = {"runs": 0}
    log["runs"] = int(log.get("runs", 0)) + 1
    log["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log_path.write_text(json.dumps(log), encoding="utf-8")

    result = {"run": log["runs"], "finished": False, "failed": False, "reason": ""}
    if (st / "STOP").exists():
        result.update(finished=True, reason="STOP file")
        return {**result, **summarize(st)}
    if log["runs"] > max_runs:
        result.update(finished=True, reason=f"max_runs ({max_runs}) reached")
        return {**result, **summarize(st)}

    unpack_firms(st)
    default_bridge, default_jobs = default_commands(st)
    bridge_cmd, jobs_cmd = bridge_cmd or default_bridge, jobs_cmd or default_jobs
    start = clock()
    deadline = start + min(float(minutes), MAX_MINUTES) * 60 - safety
    docs, docs_site = st / "docs.jsonl", st / "docs_site.jsonl"
    jsonls = [docs, st / "out" / "jobs.jsonl", st / "out" / "jobs_by_firm.jsonl"]

    for f in jsonls:
        ensure_newline(f)
    bridge_log = (st / "logs" / "bridge.log").open("ab")
    jobs_log = (st / "logs" / "jobs.log").open("ab")
    bridge = spawn(bridge_cmd, stdout=bridge_log, stderr=subprocess.STDOUT)
    try:
        while True:
            bridge_was_done = bridge.poll() is not None
            if bridge_was_done and bridge.returncode != 0:
                result.update(failed=True, reason=f"bridge exited with {bridge.returncode}")
                break
            extract_sites(docs, docs_site)
            remaining = deadline - clock()
            if remaining <= 0:
                result["reason"] = "time budget used"
                break
            rc = run_for(jobs_cmd, min(remaining, JOBS_PASS_MAX), spawn=spawn, clock=clock, sleep=sleep, poll=poll, log=jobs_log)
            for f in jsonls:
                ensure_newline(f)
            if bridge_was_done and rc == 0:
                # the bridge had finished BEFORE this pass read docs_site, and the pass ran to the end: nothing is left
                result.update(finished=True, reason="all firms checked")
                break
            if rc not in (0, None):
                result.update(failed=True, reason=f"jobs pass exited with {rc}")
                break
            wake = clock() + max(0.0, min(pass_every, deadline - clock()))
            while clock() < wake and bridge.poll() is None:
                sleep(min(poll, 5.0))
    finally:
        stop(bridge)
        bridge_log.close()
        jobs_log.close()
        for f in jsonls:
            ensure_newline(f)
        extract_sites(docs, docs_site)
    return {**result, **summarize(st), "seconds": int(clock() - start)}


def main(argv: list[str] | None = None) -> int:
    """CLI used by the workflow: prints the result as JSON and sets ``finished`` for the next step."""
    ap = argparse.ArgumentParser(description="time-boxed, resumable DerKaiser run for GitHub Actions (dry-run)")
    ap.add_argument("--state", required=True)
    ap.add_argument("--minutes", type=float, default=330)
    ap.add_argument("--max-runs", type=int, default=40, help="stop continuing after this many runs (safety net)")
    args = ap.parse_args(argv)
    res = run(args.state, args.minutes, max_runs=args.max_runs)
    Path(args.state, "run_summary.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(res, indent=2, ensure_ascii=False), flush=True)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"finished={'true' if res['finished'] else 'false'}\nfailed={'true' if res['failed'] else 'false'}\n")
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
