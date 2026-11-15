"""Merge the per-source job files into one deduplicated count (and file). Read-only on the inputs.

A job is a duplicate of another when its normalized link is the same, or when company + title + city are the same
(the same ad reached through two portals). Sources are kept in priority order: the first file wins.
Formats accepted: Brewtality model (``url``/``title``/``location``) and the legacy engine format
(``job_link``/``job_title``/``city``).
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse


def _ascii(s) -> str:
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()


def norm_url(url: str) -> str:
    p = urllib.parse.urlsplit((url or "").strip())
    return f"{p.hostname or ''}{p.path.rstrip('/')}".lower().removeprefix("www.")


def _tokens(s) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", _ascii(s)))


def soft_key(job: dict) -> tuple[str, str, str]:
    title = job.get("title") or job.get("job_title") or ""
    city = job.get("city")
    if city is None:
        loc = job.get("location") or []
        city = loc[0] if isinstance(loc, list) and loc else loc
    city = ", ".join(city) if isinstance(city, list) else city
    return _tokens(job.get("company")), _tokens(title), _tokens(city)


def link_of(job: dict) -> str:
    return job.get("url") or job.get("job_link") or ""


def merge(sources: list[tuple[str, list[dict]]]) -> tuple[list[dict], dict]:
    """``sources``: ``[(name, jobs)]`` in priority order -> (distinct jobs, per-source stats)."""
    seen_url, seen_soft, out = set(), set(), []
    stats = {}
    for name, jobs in sources:
        total = kept = 0
        for job in jobs:
            total += 1
            u, k = norm_url(link_of(job)), soft_key(job)
            if not u or u in seen_url or (all(k) and k in seen_soft):
                continue
            seen_url.add(u)
            if all(k):
                seen_soft.add(k)
            out.append({**job, "_source": name})
            kept += 1
        stats[name] = {"read": total, "new_distinct": kept}
    return out, stats


def read_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("{"):
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Deduplicate jobs from several sources (read-only)")
    ap.add_argument("--src", action="append", required=True, help="name=path.jsonl (priority = order)")
    ap.add_argument("--out", help="write the distinct jobs here (jsonl)")
    args = ap.parse_args(argv)
    sources = [(s.split("=", 1)[0], read_jsonl(s.split("=", 1)[1])) for s in args.src]
    jobs, stats = merge(sources)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            for j in jobs:
                f.write(json.dumps(j, ensure_ascii=False) + "\n")
    print(json.dumps({"distinct_total": len(jobs), "by_source": stats}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
