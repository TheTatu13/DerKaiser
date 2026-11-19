"""Jooble REST API source (dry-run, resumable). Needs a free API key from https://ro.jooble.org/api/about

    py -m scraper.jooble --key KEY --out run_jooble/jooble.jsonl [--max-requests 90] [--pages 5]

POST https://jooble.org/api/<key>  {"keywords","location","page"}  ->  {"totalCount","jobs":[...]}
Writes normalized rows {url,title,company,location,source,date,snippet}. Never writes to peViitor.
Check Jooble's API terms before republishing the data (see docs/TOS.md).
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

from .net import Net, NetError

ENDPOINT = "https://jooble.org/api/{key}"
KEYWORDS = ["", "vanzari", "contabil", "sofer", "programator", "inginer", "operator productie", "asistent", "manager",
            "administrativ", "medic", "inginer software", "livrator", "muncitor", "receptioner", "agent", "tehnician",
            "bucatar", "ospatar", "magazin", "depozit", "curatenie", "paza", "suport clienti", "marketing", "HR", "IT"]
CITIES = ["", "Bucuresti", "Cluj-Napoca", "Timisoara", "Iasi", "Brasov", "Constanta", "Craiova", "Pitesti", "Sibiu",
          "Oradea", "Arad", "Ploiesti", "Galati", "Bacau", "Targu Mures", "Baia Mare", "Suceava", "Ramnicu Valcea"]


def normalize(job: dict) -> dict | None:
    """Map one Jooble result to our job fields; None when it has no link or title."""
    url = (job.get("link") or "").strip()
    title = (job.get("title") or "").strip()
    if not url or not title:
        return None
    return {"url": url, "title": title, "company": (job.get("company") or "").strip(),
            "location": (job.get("location") or "").strip(), "source": "jooble:" + (job.get("source") or ""),
            "date": job.get("updated") or "", "snippet": (job.get("snippet") or "")[:300]}


def run(post, key: str, out: Path, *, max_requests: int = 90, pages: int = 5, queries=None) -> dict:
    """Query Jooble within a request quota; resumable via a `.done` file, deduplicated by URL."""
    out.parent.mkdir(parents=True, exist_ok=True)
    done_file = out.with_suffix(".done")
    done = set(done_file.read_text(encoding="utf-8").splitlines()) if done_file.exists() else set()
    seen = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line)["url"])
            except Exception:  # noqa: BLE001
                pass
    stats = {"requests": 0, "new": 0, "total_seen": len(seen), "stopped": ""}
    queries = queries or list(itertools.product(KEYWORDS, CITIES))
    with out.open("a", encoding="utf-8") as fh, done_file.open("a", encoding="utf-8") as dh:
        for kw, city in queries:
            qid = f"{kw}|{city}"
            if qid in done:
                continue
            for page in range(1, pages + 1):
                if stats["requests"] >= max_requests:
                    stats["stopped"] = "max_requests"
                    return stats
                stats["requests"] += 1
                try:
                    resp = post(ENDPOINT.format(key=key), {"keywords": kw, "location": city or "Romania", "page": page})
                except NetError as exc:
                    stats["stopped"] = f"error:{exc}"
                    return stats
                jobs = resp.get("jobs") or []
                for j in jobs:
                    row = normalize(j)
                    if row and row["url"] not in seen:
                        seen.add(row["url"])
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                        stats["new"] += 1
                fh.flush()
                if len(jobs) < 20:
                    break
            dh.write(qid + "\n")
            dh.flush()
    stats["total_seen"] = len(seen)
    return stats


def main(argv=None) -> int:
    """CLI: needs a Jooble API key (`--key`); stays under `--max-requests` per run."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True)
    ap.add_argument("--out", default="run_jooble/jooble.jsonl")
    ap.add_argument("--max-requests", type=int, default=90, help="stay under the daily quota")
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--interval", type=float, default=1.5)
    a = ap.parse_args(argv)
    net = Net(interval=a.interval, timeout=20, retries=1)
    stats = run(net.post_json, a.key, Path(a.out), max_requests=a.max_requests, pages=a.pages)
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
