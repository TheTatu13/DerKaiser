"""DerKaiser batch runner (phase 2): many firms, dry-run only, resumable.

Writes ONLY local files; there is no Solr/peViitor write anywhere in this module.

Output folder:

* ``docs.jsonl``      one company document per line (firms that met every condition)
* ``progress.jsonl``  one line per firm: ``{"cif", "status", "detail"}``
* ``report.json``     summary, rewritten at the end of every run

Crash safety / resume:

* each line is flushed as it is written, so a kill loses at most the firm in flight;
* the document is written BEFORE its progress line, so a crash can leave a document
  without progress (the firm is simply re-done on resume) but never progress without
  its document; duplicates are collapsed by ``id`` when the report is built;
* a truncated last line (crash mid-write) is ignored on resume;
* ``unavailable`` is not terminal: a firm skipped because a site was down is retried
  on the next run. ``ok / not_found / ignored / invalid`` are final.

Memory stays flat: nothing is accumulated, everything is streamed to disk.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

from . import reconcile, sources as src
from .company_doc import pad_cif

log = logging.getLogger("derkaiser.batch")

TERMINAL = {src.OK, src.NOT_FOUND, src.IGNORED, src.INVALID}


def read_cif_list(path: str | Path) -> list[str]:
    """One CIF per line (first column if CSV). Blank lines and ``#`` comments are skipped."""
    entries: list[str] = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line.split(",")[0].split(";")[0].strip())
    return entries


def _append(handle, row: dict) -> None:
    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    handle.flush()


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # truncated last line after a crash
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _latest_progress(path: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in _read_jsonl(path):
        if "cif" in row:
            latest[str(row["cif"])] = row
    return latest


def run_batch(
    cifs: Iterable[str | int],
    out_dir: str | Path,
    *,
    lookup: Callable[[str], dict] | None = None,
    jobs_count_fn: Callable[[str], int] | None = None,
    today: date | None = None,
    throttle: src.Throttle | None = None,
    max_firms: int | None = None,
    max_consecutive_unavailable: int = 5,
    existing_index: dict[int, list[dict]] | None = None,
) -> dict[str, Any]:
    """Process firms, streaming results to ``out_dir``. Returns (and writes) the report.

    With ``existing_index`` (CUI -> existing documents) every ``ok`` firm also gets a
    planning row in ``plan.jsonl`` (create / update / skip / conflict). Planning only.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    docs_path = out / "docs.jsonl"
    progress_path = out / "progress.jsonl"
    plan_path = out / "plan.jsonl"

    throttle = throttle or src.Throttle(0)
    done = {cif for cif, row in _latest_progress(progress_path).items() if row.get("status") in TERMINAL}

    this_run: Counter[str] = Counter()
    seen: set[str] = set()
    processed = 0
    resumed = 0
    streak = 0
    aborted = False
    started = time.monotonic()

    plan_cm = plan_path.open("a", encoding="utf-8") if existing_index is not None else nullcontext()
    with docs_path.open("a", encoding="utf-8") as docs_f, progress_path.open("a", encoding="utf-8") as prog_f, plan_cm as plan_f:
        for raw in cifs:
            try:
                key = pad_cif(raw)
            except ValueError as exc:
                key = str(raw).strip()
                if key in seen or key in done:
                    continue
                seen.add(key)
                _append(prog_f, {"cif": key, "status": src.INVALID, "detail": str(exc)})
                this_run[src.INVALID] += 1
                continue

            if key in seen:
                continue
            seen.add(key)
            if key in done:
                resumed += 1
                continue
            if max_firms is not None and processed >= max_firms:
                break

            throttle.wait()
            count = jobs_count_fn(key) if jobs_count_fn else 0
            outcome = src.company_doc_for_cif(key, jobs_count=count, today=today, lookup=lookup)
            if outcome.status == src.OK:
                _append(docs_f, outcome.doc)  # type: ignore[arg-type]
                if plan_f is not None and existing_index is not None:
                    decision = reconcile.decide(outcome.doc, existing_index.get(int(key), []))  # type: ignore[arg-type]
                    _append(plan_f, decision.as_row(key))
            _append(prog_f, {"cif": key, "status": outcome.status, "detail": outcome.detail})
            processed += 1
            this_run[outcome.status] += 1

            streak = streak + 1 if outcome.status == src.UNAVAILABLE else 0
            if streak >= max_consecutive_unavailable:
                aborted = True
                log.error("aborting: %d consecutive unavailable results (source likely down)", streak)
                break

    latest = _latest_progress(progress_path)
    doc_ids = [str(d.get("id")) for d in _read_jsonl(docs_path)]
    report: dict[str, Any] = {
        "firms_seen_overall": len(latest),
        "counts_overall": dict(Counter(row.get("status") for row in latest.values())),
        "this_run": dict(this_run),
        "processed_this_run": processed,
        "skipped_already_done": resumed,
        "aborted": aborted,
        "docs_unique": len(set(doc_ids)),
        "docs_duplicate_lines": len(doc_ids) - len(set(doc_ids)),
        "to_retry_unavailable": sorted(c for c, r in latest.items() if r.get("status") == src.UNAVAILABLE),
        "existingJobsCount": "placeholder 0 until jobs/Solr are wired (phases 3-4)" if jobs_count_fn is None else "from jobs_count_fn",
        "elapsed_s": round(time.monotonic() - started, 2),
        "dry_run": True,
        "plan": None,
    }
    if existing_index is not None:
        plan_rows = {str(r["cif"]): r for r in _read_jsonl(plan_path) if "cif" in r}
        report["plan"] = dict(Counter(r.get("action") for r in plan_rows.values()))
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
