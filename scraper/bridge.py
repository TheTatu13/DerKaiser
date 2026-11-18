"""Bridge: firm list (CIF + name + employees) -> website finder -> documents the job scraper reads.

``python -m scraper.bridge --firms financials.jsonl --out bridge_docs.jsonl`` finds each firm's own website, then
``python -m scraper.derkaiser --jobs bridge_docs.jsonl ...`` scrapes its jobs. A site is accepted only when the page
proves it belongs to the firm (``model.verify_site``: full distinctive name AND the firm's CIF, or ``.ro`` + 'romania').
Resumable (append-only, skips CIFs already written), read-only, slow on purpose. ``get(url) -> text`` is injected.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, Iterator

from . import model as M

_KEYS = {
    "cif": ("id", "cif", "cui"),
    "company": ("company", "name", "denumire"),
    "employees": ("employees", "salariati", "num_employees", "numar_mediu_salariati", "nr_salariati"),
}


def _pick(row: dict, field: str):
    for k in _KEYS[field]:
        if row.get(k) not in (None, ""):
            return row[k]
    doc = row.get("doc")
    if isinstance(doc, dict):
        return _pick(doc, field)
    return None


def _num(value) -> float | None:
    try:
        return float(str(value).replace(".", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def firms_from_rows(rows: Iterable[dict], *, min_employees: int = 2) -> Iterator[dict]:
    """``{"id", "company", "employees"}`` for firms with at least ``min_employees`` (unknown employees are skipped)."""
    seen = set()
    for row in rows:
        cif, company, emp = _pick(row, "cif"), _pick(row, "company"), _num(_pick(row, "employees"))
        cif = str(cif or "").strip().lstrip("0")
        if not cif or not company or emp is None or emp < min_employees or cif in seen:
            continue
        seen.add(cif)
        yield {"id": cif.zfill(8) if len(cif) < 8 else cif, "company": str(company), "employees": int(emp)}


def more_domains(company: str, limit: int = 8) -> list[str]:
    """Name-based guesses: ``model.guess_domains`` plus first-two-words and 'grup'/'group' variants."""
    tokens = M.core_tokens(company)
    out = list(M.guess_domains(company, limit=limit))
    if len(tokens) >= 2:
        for n in (tokens[0] + tokens[1], tokens[0] + "-" + tokens[1]):
            for tld in (".ro", ".com"):
                if n + tld not in out:
                    out.append(n + tld)
    return out[:limit]


def dns_resolves(domain: str) -> bool:
    """True when the name exists in DNS (a guessed domain that does not resolve is skipped without any request)."""
    import socket
    try:
        socket.getaddrinfo(domain, 443)
        return True
    except OSError:
        return False


def find_site(firm: dict, get: Callable[[str], str], *, max_tries: int = 6,
              resolves: Callable[[str], bool] | None = None) -> str | None:
    for domain in more_domains(firm["company"], limit=max_tries):
        if resolves is not None and not resolves(domain):
            continue
        site = M.verify_site(domain, firm["company"], get, strict_cif=firm["id"])
        if site:
            return site
    return None


def run(firms: list[dict], get, out_path: str, *, workers: int = 4, on_firm=None, resolves=None) -> dict:
    done = set()
    try:
        done = {json.loads(l)["id"] for l in open(out_path, encoding="utf-8") if l.strip()}
    except FileNotFoundError:
        pass
    todo = [f for f in firms if f["id"] not in done]
    found = 0
    chunk = max(1, workers * 10)  # write results every chunk, so a crash or sleep loses at most one chunk
    with open(out_path, "a", encoding="utf-8") as out, ThreadPoolExecutor(max_workers=workers) as pool:
        for i in range(0, len(todo), chunk):
            part = todo[i:i + chunk]
            for firm, site in zip(part, pool.map(lambda f: _safe_find(f, get, resolves), part)):
                doc = {"id": firm["id"], "company": firm["company"], "employees": firm["employees"],
                       "website": [site] if site else []}
                out.write(json.dumps(doc, ensure_ascii=False) + "\n")
                found += bool(site)
                if on_firm:
                    on_firm(firm, site)
            out.flush()
    return {"firms": len(todo), "skipped_done": len(done), "with_site": found}


def _safe_find(firm, get, resolves=None):
    try:
        return find_site(firm, get, resolves=resolves)
    except Exception:  # noqa: BLE001 - one firm never stops the run
        return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    from . import net as N

    ap = argparse.ArgumentParser(description="firms (CIF, name, employees) -> verified website -> docs for derkaiser --jobs")
    ap.add_argument("--firms", required=True, help="jsonl with cif/id, company/name, employees (e.g. financials.jsonl)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-employees", type=int, default=2)
    ap.add_argument("--max-firms", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--interval", type=float, default=3.0)
    args = ap.parse_args(argv)
    rows = (json.loads(l) for l in open(args.firms, encoding="utf-8") if l.strip())
    firms = list(firms_from_rows(rows, min_employees=args.min_employees))
    firms.sort(key=lambda f: -f["employees"])  # biggest employers first: they are the ones with career pages
    if args.max_firms:
        firms = firms[: args.max_firms]
    net = N.Net(interval=args.interval, timeout=8, retries=0)
    n = {"i": 0}

    def progress(firm, site):
        n["i"] += 1
        if n["i"] % 25 == 0:
            print(f"{n['i']}/{len(firms)}", flush=True)
    print(json.dumps(run(firms, net.get, args.out, workers=args.workers, on_firm=progress, resolves=dns_resolves)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
