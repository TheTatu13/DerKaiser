"""ANAF stage: screen CIFs 100 at a time with the official VAT-payer web service.

``POST https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva`` takes up to 100 CUIs per request,
at most 1 request/second. One request replaces 100 CUIScan calls. Local files only, resumable.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date
from pathlib import Path
from typing import Callable

from .discovery import USER_AGENT, _load_jsonl
from .quality import assess_anaf
from .sources import SourceUnavailable, Throttle

ANAF_URL = "https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva"
BATCH = 100


def _http_post(payload: list[dict]) -> dict:
    req = urllib.request.Request(
        ANAF_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 429/5xx/timeouts/garbage
        raise SourceUnavailable(str(exc) or exc.__class__.__name__) from exc


def normalize(entry: dict) -> dict:
    """Flatten one ANAF `found` entry into the fields the quality gates read (status, legal form, CAEN, inactive flag...)."""
    dg = entry.get("date_generale") or {}
    cui = str(dg.get("cui", "")).strip()
    return {
        "cif": cui.zfill(8) if cui.isdigit() else cui,
        "name": dg.get("denumire") or "",
        "stare": dg.get("stare_inregistrare") or "",
        "forma_juridica": dg.get("forma_juridica") or "",
        "forma_organizare": dg.get("forma_organizare") or "",
        "caen": str(dg.get("cod_CAEN") or ""),
        "nr_reg_com": dg.get("nrRegCom") or "",
        "address": dg.get("adresa") or "",
        "inactive": bool((entry.get("stare_inactiv") or {}).get("statusInactivi")),
        "tva": bool((entry.get("inregistrare_scop_Tva") or {}).get("scpTVA")),
        "registered": dg.get("data_inregistrare") or "",
    }


def fetch_batch(cifs: list[str], today: str, poster: Callable[[list[dict]], dict] | None = None) -> tuple[dict[str, dict], set[str]]:
    """``({cif: record}, {cifs ANAF does not know})`` for up to 100 CIFs. Raises ``SourceUnavailable``."""
    data = (poster or _http_post)([{"cui": int(c), "data": today} for c in cifs])
    found = {}
    for entry in data.get("found") or []:
        rec = normalize(entry)
        if rec["cif"]:
            found[rec["cif"]] = rec
    missing = {str(c).zfill(8) for c in data.get("notFound") or []}
    return found, missing


def screen_cifs(
    cifs: list[str],
    out_dir: str | Path,
    *,
    poster: Callable[[list[dict]], dict] | None = None,
    throttle: Throttle | None = None,
    today: str | None = None,
    max_consecutive_unavailable: int = 3,
) -> dict:
    """Screen ``cifs`` through the ANAF gates; writes ``screened.jsonl`` and ``cifs_screened.txt``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "screened.jsonl"
    done = {r["cif"] for r in _load_jsonl(path)}
    # ANAF takes numeric CUIs only: a value with letters would crash int() mid-run, so it is dropped up front
    todo = [c for c in dict.fromkeys(cifs) if c not in done and str(c).isdigit()]
    throttle = throttle or Throttle(1.5)
    today = today or date.today().isoformat()
    fails = 0
    aborted = False
    with path.open("a", encoding="utf-8") as fh:
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            throttle.wait()
            try:
                found, missing = fetch_batch(chunk, today, poster)
            except SourceUnavailable:
                fails += 1
                if fails >= max_consecutive_unavailable:
                    aborted = True
                    break
                continue
            fails = 0
            for cif in chunk:
                rec = found.get(cif)
                reasons = assess_anaf(rec)
                fh.write(json.dumps({"cif": cif, "ok": not reasons, "reasons": reasons, "anaf": rec},
                                    ensure_ascii=False) + "\n")
            fh.flush()
    rows = _load_jsonl(path)
    screened = {r["cif"] for r in rows}  # built once: rebuilding it per CIF made the final step quadratic
    passed = sorted(r["cif"] for r in rows if r["ok"])
    (out / "cifs_screened.txt").write_text("\n".join(passed) + ("\n" if passed else ""), encoding="utf-8")
    why: dict[str, int] = {}
    for r in rows:
        for reason in r["reasons"]:
            key = reason.split(":")[0]
            why[key] = why.get(key, 0) + 1
    report = {"screened_total": len(rows), "passed": len(passed), "failed": len(rows) - len(passed),
              "fail_reasons": dict(sorted(why.items(), key=lambda kv: -kv[1])),
              "left_to_screen": sum(1 for c in todo if c not in screened),
              "aborted": aborted, "dry_run": True}
    (out / "screen_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
