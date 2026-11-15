"""Stage 2: employees + turnover from CUIScan ``financials`` (latest balance sheet).

Runs on firms that already passed ANAF, *before* the expensive ``company`` call, so a firm with
no employees never costs a second request. CUIScan allows 100 requests/hour/IP: the default pace
is one request per 60 s, hard budget 60/hour. Resumable, local files only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Callable

from .discovery import USER_AGENT, Budget, _load_jsonl
from .quality import assess_financials
from .sources import SourceUnavailable, Throttle

URL = "https://cuiscan.ro/api.php?action=financials&cui="
MAX_AGE_YEARS = 2  # latest balance sheet must be from this year or the two before


def _http_get(cui: str):
    req = urllib.request.Request(URL + cui, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 404, 422):
            return []  # a verdict on this CIF, not an outage
        raise SourceUnavailable(f"HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SourceUnavailable(str(exc) or exc.__class__.__name__) from exc


def latest_year(rows) -> dict | None:
    rows = [r for r in rows or [] if isinstance(r, dict) and isinstance(r.get("an"), int)]
    return max(rows, key=lambda r: r["an"]) if rows else None


def assess(rows, *, this_year: int, min_employees: int = 1) -> dict:
    last = latest_year(rows)
    if not last:
        return {"ok": False, "reasons": assess_financials(None, None), "year": None, "employees": None, "turnover": None}
    employees, turnover = last.get("nrAngajati"), last.get("cifraAfaceri")
    reasons = assess_financials(employees if employees is not None else 0, turnover, min_employees=min_employees)
    if last["an"] < this_year - MAX_AGE_YEARS:
        reasons.append("bilant_vechi:%s" % last["an"])
    return {"ok": not reasons, "reasons": reasons, "year": last["an"], "employees": employees, "turnover": turnover}


def screen_financials(
    cifs: list[str],
    out_dir: str | Path,
    *,
    fetcher: Callable[[str], list] | None = None,
    throttle: Throttle | None = None,
    budget: Budget | None = None,
    max_firms: int | None = None,
    min_employees: int = 1,
    today: date | None = None,
    max_consecutive_unavailable: int = 5,
) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "financials.jsonl"
    done = {r["cif"] for r in _load_jsonl(path)}
    todo = [c for c in dict.fromkeys(cifs) if c not in done]
    fetcher = fetcher or _http_get
    throttle = throttle or Throttle(60.0)
    budget = budget or Budget(60, 3600.0)
    year = (today or date.today()).year
    fails = spent = 0
    stop = "finished"
    with path.open("a", encoding="utf-8") as fh:
        for cif in todo:
            if max_firms is not None and spent >= max_firms:
                stop = "max_firms"
                break
            if not budget.available():
                stop = "hourly_budget_exhausted"
                break
            throttle.wait()
            budget.spend()
            spent += 1
            try:
                rows = fetcher(str(int(cif)))
            except SourceUnavailable:
                fails += 1
                if fails >= max_consecutive_unavailable:
                    stop = "source_down"
                    break
                continue
            fails = 0
            res = assess(rows, this_year=year, min_employees=min_employees)
            fh.write(json.dumps({"cif": cif, **res}) + "\n")
            fh.flush()
    rows = _load_jsonl(path)
    passed = sorted(r["cif"] for r in rows if r["ok"])
    (out / "cifs_final.txt").write_text("\n".join(passed) + ("\n" if passed else ""), encoding="utf-8")
    why: dict[str, int] = {}
    for r in rows:
        for reason in r["reasons"]:
            why[reason.split(":")[0]] = why.get(reason.split(":")[0], 0) + 1
    report = {"checked_total": len(rows), "passed": len(passed), "fail_reasons": dict(sorted(why.items(), key=lambda kv: -kv[1])),
              "left_to_check": len([c for c in todo if c not in {r["cif"] for r in rows}]),
              "stop_reason": stop, "dry_run": True}
    (out / "financials_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
