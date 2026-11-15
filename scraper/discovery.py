"""DerKaiser CIF discovery: CUIFirma and CUIScan feed each other.

Neither site lists companies, but both search by name. So the list is grown like a
snowball: every firm found by one site gives names/words that become queries for
the *other* site, which finds more firms, and so on.

* CUIFirma search : 10 hits/query, has ``is_active`` -> cheap active pre-filter
  (limit seen: 60 requests/minute).
* CUIScan search  : up to 30 hits/query, no reliable status (limit: 100/hour/IP).

Defaults are deliberately slow (CUIFirma 1 request/6 s, CUIScan 1/60 s = 60/hour, leaving
headroom under its 100/hour cap for the detail lookups, which share the same IP).

Rules: own User-Agent, one request per throttle slot, an hourly budget per source
(never provoke a 429), a source that fails N times in a row is switched off, and
everything is resumable from the local files. Local files only, no Solr.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .company_doc import pad_cif
from .sources import SourceUnavailable, Throttle

USER_AGENT = "DerKaiser-peViitor/0.1 (volunteer project; github.com/thetatu13)"
CUIFIRMA, CUISCAN = "cuifirma", "cuiscan"

_STOP = {
    "SRL", "SA", "SC", "PFA", "II", "IF", "SNC", "SCS", "SRLD", "RA", "ONG",
    "INTREPRINDERE", "INDIVIDUALA", "INTREPRINDEREA", "FAMILIALA", "PERSOANA",
    "FIZICA", "AUTORIZATA", "ASOCIATIA", "COMPANIA", "SOCIETATEA", "ROMANIA",
}

COUNTIES = [
    "Alba", "Arad", "Arges", "Bacau", "Bihor", "Bistrita", "Botosani", "Braila", "Brasov",
    "Bucuresti", "Buzau", "Calarasi", "Caras", "Cluj", "Constanta", "Covasna", "Dambovita",
    "Dolj", "Galati", "Giurgiu", "Gorj", "Harghita", "Hunedoara", "Ialomita", "Iasi", "Ilfov",
    "Maramures", "Mehedinti", "Mures", "Neamt", "Olt", "Prahova", "Salaj", "Satu Mare",
    "Sibiu", "Suceava", "Teleorman", "Timis", "Tulcea", "Valcea", "Vaslui", "Vrancea",
]


@dataclass
class Candidate:
    cif: str  # padded to 8
    name: str
    county: str = ""
    locality: str = ""
    active: bool | None = None  # None = source cannot tell
    source: str = ""


def _ascii_upper(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().upper()


def tokens(name: str) -> list[str]:
    """Words worth searching: 4+ letters, not legal-form noise."""
    words = re.findall(r"[A-Z]{4,}", _ascii_upper(name))
    return [w for w in dict.fromkeys(words) if w not in _STOP]


def parse_cuifirma(payload: dict) -> list[Candidate]:
    out = []
    for r in (payload or {}).get("results") or []:
        cui = str(r.get("cui", "")).strip()
        if not cui.isdigit() or not r.get("name"):
            continue
        active = r.get("is_active")
        out.append(Candidate(pad_cif(cui), r["name"], r.get("county") or "", r.get("locality") or "",
                             active if isinstance(active, bool) else None, CUIFIRMA))
    return out


def parse_cuiscan(rows: list) -> list[Candidate]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        cui = str(r.get("cui", "")).strip()
        if not cui.isdigit() or not r.get("name"):
            continue
        # CUIScan's search rows carry no trustworthy status (activa is false for live firms).
        out.append(Candidate(pad_cif(cui), r["name"], r.get("county") or "", r.get("locality") or "", None, CUISCAN))
    return out


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 429/5xx/timeouts/garbage = source unavailable
        raise SourceUnavailable(str(exc) or exc.__class__.__name__) from exc


def search_cuifirma(q: str) -> list[Candidate]:
    return parse_cuifirma(_get_json("https://cuifirma.ro/api/search?q=" + urllib.parse.quote(q)))


def search_cuiscan(q: str) -> list[Candidate]:
    return parse_cuiscan(_get_json("https://cuiscan.ro/api.php?action=search&q=" + urllib.parse.quote(q)))


class Budget:
    """At most ``limit`` calls per sliding ``window`` seconds."""

    def __init__(self, limit: int, window: float = 3600.0, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self._clock = limit, window, clock
        self._calls: deque[float] = deque()

    def _trim(self) -> None:
        now = self._clock()
        while self._calls and now - self._calls[0] >= self.window:
            self._calls.popleft()

    def available(self) -> bool:
        self._trim()
        return len(self._calls) < self.limit

    def spend(self) -> None:
        self._calls.append(self._clock())


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # truncated last line after a crash
    return rows


def run_discovery(
    out_dir: str | Path,
    *,
    seeds: list[str] | None = None,
    searchers: dict[str, Callable[[str], list[Candidate]]] | None = None,
    throttles: dict[str, Throttle] | None = None,
    budgets: dict[str, Budget] | None = None,
    max_queries: int | None = None,
    max_consecutive_unavailable: int = 5,
) -> dict:
    """Snowball until the frontier is empty, ``max_queries`` is spent or all sources are off/exhausted."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cand_path, query_path = out / "discovered.jsonl", out / "queries.jsonl"
    searchers = searchers or {CUIFIRMA: search_cuifirma, CUISCAN: search_cuiscan}
    throttles = throttles or {CUIFIRMA: Throttle(6.0), CUISCAN: Throttle(60.0)}
    budgets = budgets or {CUIFIRMA: Budget(10, 60.0), CUISCAN: Budget(60, 3600.0)}

    found: dict[str, dict] = {}
    for row in _load_jsonl(cand_path):
        found[row["cif"]] = row
    done = {(r["source"], r["q"]) for r in _load_jsonl(query_path)}

    queues: dict[str, deque[str]] = {s: deque() for s in searchers}
    queued: set[tuple[str, str]] = set(done)

    def enqueue(source: str, q: str) -> None:
        q = q.strip()
        if len(q) >= 2 and (source, q.upper()) not in queued:
            queued.add((source, q.upper()))
            queues[source].append(q)

    def feed(c: dict | Candidate) -> None:
        name = c["name"] if isinstance(c, dict) else c.name
        for source in queues:
            enqueue(source, name)
            for t in tokens(name):
                enqueue(source, t)

    for s in seeds if seeds is not None else COUNTIES:
        for source in queues:
            enqueue(source, s)
    for row in found.values():  # resume: names already found still feed the other queues
        feed(row)

    fails = {s: 0 for s in searchers}
    off: set[str] = set()
    spent = new_total = 0
    stop_reason = "frontier_empty"

    with cand_path.open("a", encoding="utf-8") as cf, query_path.open("a", encoding="utf-8") as qf:
        while True:
            if max_queries is not None and spent >= max_queries:
                stop_reason = "max_queries"
                break
            live = [s for s in queues if s not in off and queues[s]]
            if not live:
                stop_reason = "all_sources_off" if off else "frontier_empty"
                break
            ready = [s for s in live if budgets[s].available()]
            if not ready:
                stop_reason = "hourly_budget_exhausted"
                break
            # work for the source with the longest queue; the two feed each other anyway
            source = max(ready, key=lambda s: len(queues[s]))
            q = queues[source].popleft()
            throttles[source].wait()
            budgets[source].spend()
            spent += 1
            try:
                hits = searchers[source](q)
            except SourceUnavailable:
                fails[source] += 1
                queues[source].appendleft(q)  # not done: retried on resume
                if fails[source] >= max_consecutive_unavailable:
                    off.add(source)
                continue
            fails[source] = 0
            new = 0
            for c in hits:
                known = found.get(c.cif)
                if known and not (known.get("active") is None and c.active is not None):
                    continue
                row = {"cif": c.cif, "name": c.name, "county": c.county, "locality": c.locality,
                       "active": c.active, "source": c.source}
                found[c.cif] = row
                cf.write(json.dumps(row, ensure_ascii=False) + "\n")
                if known is None:
                    new += 1
                    feed(c)
            cf.flush()
            new_total += new
            qf.write(json.dumps({"source": source, "q": q.upper(), "hits": len(hits), "new": new}) + "\n")
            qf.flush()

    report = {
        "stop_reason": stop_reason,
        "queries_this_run": spent,
        "new_this_run": new_total,
        "found_total": len(found),
        "active_true": sum(1 for r in found.values() if r["active"] is True),
        "active_false": sum(1 for r in found.values() if r["active"] is False),
        "active_unknown": sum(1 for r in found.values() if r["active"] is None),
        "sources_off": sorted(off),
        "pending_queries": {s: len(q) for s, q in queues.items()},
        "dry_run": True,
    }
    (out / "discovery_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def export_cifs(out_dir: str | Path, path: str | Path | None = None) -> int:
    """Write ``cifs.txt`` for the batch: every firm not known to be inactive."""
    out = Path(out_dir)
    rows = {r["cif"]: r for r in _load_jsonl(out / "discovered.jsonl")}
    cifs = sorted(c for c, r in rows.items() if r["active"] is not False)
    Path(path or out / "cifs.txt").write_text("\n".join(cifs) + ("\n" if cifs else ""), encoding="utf-8")
    return len(cifs)
