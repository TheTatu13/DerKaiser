"""DerKaiser jobs (JSONL) -> one self-contained PostgreSQL import script (``db/schema.sql`` + upserts).

    py -m scraper.pgexport --out import.sql run_all/final_distinct.jsonl [more.jsonl ...]
        [--companies docs.jsonl] [--deactivate-missing]

The result runs in ``psql -f import.sql``, pgAdmin or DBeaver. Nothing here connects to a database: it only writes a file.
Re-running the script is safe: companies are upserted by CIF and jobs by URL, so nothing is duplicated.
With ``--deactivate-missing`` the jobs of the sources present in this import that are NOT in it are marked inactive
(they disappeared from their source). Accepts both the model format (url/title/company/cif/location[]) and the own-site
format (job_link/job_title/city/county/remote). Jobs without a CIF are kept (employer name only); rows whose
CIF field holds a personal ID (CNP) are dropped. No description text is read or written (GDPR data minimisation).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
BATCH = 500  # rows per INSERT statement
WORK_MODES = ("remote", "hybrid", "on-site")


def q(value) -> str:
    """SQL string literal (or NULL for None/empty): quotes doubled, NUL bytes dropped (Postgres rejects them)."""
    if value is None or value == "":
        return "NULL"
    return "'" + str(value).replace("\x00", "").replace("'", "''") + "'"


def _first(value) -> str:
    """First non-empty string of a list, or the string itself."""
    if isinstance(value, (list, tuple)):
        for item in value:
            if item:
                return str(item).strip()
        return ""
    return str(value or "").strip()


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def normalize_cif(value) -> str | None:
    """Digits only (an 'RO' prefix is dropped), zero-padded to 8; None unless it has 2-10 digits."""
    digits = re.sub(r"\D", "", str(value or ""))
    if not 2 <= len(digits) <= 10:
        return None
    return digits.zfill(8)


def is_personal_id(value) -> bool:
    """11+ digits is not a CIF (max 10): it is a personal ID (CNP) of a natural person, which must never be stored."""
    return len(re.sub(r"\D", "", str(value or ""))) >= 11


def work_mode_of(value) -> str | None:
    """'remote' / 'hybrid' / 'on-site' only when the source says so clearly; otherwise None (never guessed)."""
    if value is True:
        return "remote"
    text = _fold(_first(value))
    if "hybrid" in text or "hibrid" in text:
        return "hybrid"
    if "remote" in text:
        return "remote"
    if text in ("on-site", "onsite", "on site", "office"):
        return "on-site"
    return None


def _timestamp(value) -> str | None:
    """ISO-8601 timestamp as given (validated), else None."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text


def normalize_job(raw: dict) -> tuple[dict | None, str]:
    """``(job, "")`` for a usable row, or ``(None, reason)``: no_url, no_title, no_company, personal_id, bad_cif.

    A missing CIF is allowed (the job keeps the employer's name, ``cif`` stays None); a CIF that is present but invalid is not.
    """
    url = str(raw.get("url") or raw.get("job_link") or "").strip()
    title = " ".join(str(raw.get("title") or raw.get("job_title") or "").split())
    company = " ".join(str(raw.get("company") or "").split())
    if not re.match(r"https?://", url):
        return None, "no_url"
    if not title:
        return None, "no_title"
    if not company:
        return None, "no_company"
    cif = None
    if str(raw.get("cif") or "").strip():
        if is_personal_id(raw.get("cif")):
            return None, "personal_id"  # GDPR: a CNP in the CIF field means the employer is a private person
        cif = normalize_cif(raw.get("cif"))
        if not cif:
            return None, "bad_cif"
    city = _first(raw.get("city"))
    if not city:
        # the model format lists locations; the bare country name is not a city
        for loc in raw.get("location") or []:
            if loc and _fold(str(loc)) not in ("romania", "ro"):
                city = str(loc).strip()
                break
    return {"url": url, "title": title, "company": company, "cif": cif, "city": city,
            "county": _first(raw.get("county")), "work_mode": work_mode_of(raw.get("workmode") or raw.get("remote")),
            "source": str(raw.get("source") or raw.get("_source") or ("site" if raw.get("job_link") else "unknown")).strip() or "unknown",
            "expires_at": _timestamp(raw.get("expirationdate")), "scraped_at": _timestamp(raw.get("date"))}, ""


def read_jsonl(path: str | Path) -> Iterator[dict]:
    """Rows of a jsonl file; blank and malformed lines are skipped."""
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def company_extras(rows: Iterable[dict]) -> dict[str, dict]:
    """``{cif: {employees, website}}`` from company documents (id/company/employees/website)."""
    out = {}
    for r in rows:
        cif = normalize_cif(r.get("id") or r.get("cif"))
        if cif:
            emp = r.get("employees")
            out[cif] = {"employees": int(emp) if isinstance(emp, (int, float)) and emp >= 0 else None,
                        "website": _first(r.get("website")) or None}
    return out


def _batches(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def build_sql(rows: Iterable[dict], *, extras: dict[str, dict] | None = None, deactivate_missing: bool = False,
              schema_sql: str | None = None) -> tuple[str, dict]:
    """The whole import script and its statistics. Jobs are deduplicated by URL (first wins), companies by CIF."""
    extras = extras or {}
    schema_sql = schema_sql if schema_sql is not None else SCHEMA_PATH.read_text(encoding="utf-8")
    jobs: dict[str, dict] = {}
    companies: dict[str, str] = {}
    stats = {"rows": 0, "skipped": {}, "duplicates": 0}
    for raw in rows:
        stats["rows"] += 1
        job, reason = normalize_job(raw)
        if job is None:
            stats["skipped"][reason] = stats["skipped"].get(reason, 0) + 1
        elif job["url"] in jobs:
            stats["duplicates"] += 1
        else:
            jobs[job["url"]] = job
            if job["cif"]:
                companies.setdefault(job["cif"], job["company"])
    stats.update(jobs=len(jobs), companies=len(companies))

    out = ["-- DerKaiser import (generated). Safe to run again: upserts by CIF and URL.",
           f"-- rows read: {stats['rows']}, jobs: {len(jobs)}, companies: {len(companies)}, "
           f"duplicates: {stats['duplicates']}, skipped: {stats['skipped'] or 0}",
           "SET client_encoding = 'UTF8';", "SET standard_conforming_strings = on;", "BEGIN;", "", schema_sql.strip(), ""]
    for chunk in _batches(sorted(companies.items()), BATCH):
        values = ",\n".join(
            f"  ({q(cif)}, {q(name)}, {q((extras.get(cif) or {}).get('website'))}, "
            f"{(extras.get(cif) or {}).get('employees') if (extras.get(cif) or {}).get('employees') is not None else 'NULL'})"
            for cif, name in chunk)
        out.append("INSERT INTO companies (cif, name, website, employees) VALUES\n" + values +
                   "\nON CONFLICT (cif) DO UPDATE SET name = EXCLUDED.name, website = COALESCE(EXCLUDED.website, companies.website),"
                   " employees = COALESCE(EXCLUDED.employees, companies.employees), updated_at = now();\n")
    for chunk in _batches(list(jobs.values()), BATCH):
        values = ",\n".join(
            f"  ({q(j['url'])}, {q(j['title'])}, {q(j['cif'])}, {q(j['company'])}, {q(j['city'])}, {q(j['county'])}, {q(j['work_mode'])}, "
            f"{q(j['source'])}, {q(j['expires_at'])}, {q(j['scraped_at'])})" for j in chunk)
        out.append("INSERT INTO jobs (url, title, cif, company_name, city, county, work_mode, source, expires_at, scraped_at) VALUES\n" + values +
                   "\nON CONFLICT (url) DO UPDATE SET title = EXCLUDED.title, cif = EXCLUDED.cif, company_name = EXCLUDED.company_name, city = EXCLUDED.city,"
                   " county = EXCLUDED.county, work_mode = EXCLUDED.work_mode, source = EXCLUDED.source,"
                   " expires_at = EXCLUDED.expires_at, scraped_at = EXCLUDED.scraped_at, last_seen = now(), active = true;\n")
    if deactivate_missing and jobs:
        sources = sorted({j["source"] for j in jobs.values()})
        out.append("-- jobs of these sources that were not in this import are no longer listed there\n"
                   "UPDATE jobs SET active = false WHERE active AND last_seen < now() AND source IN ("
                   + ", ".join(q(s) for s in sources) + ");\n")
    out.append("COMMIT;\n")
    return "\n".join(out), stats


def main(argv: list[str] | None = None) -> int:
    """CLI: write the import script and print the statistics."""
    ap = argparse.ArgumentParser(description="DerKaiser jobs (jsonl) -> PostgreSQL import script (writes a file only)")
    ap.add_argument("jobs", nargs="+", help="jobs jsonl files (later duplicates of a URL are ignored)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--companies", help="company documents jsonl (id, employees, website) to enrich the companies table")
    ap.add_argument("--deactivate-missing", action="store_true",
                    help="mark jobs of the sources present here that are missing from this import as inactive")
    args = ap.parse_args(argv)
    rows = (r for path in args.jobs for r in read_jsonl(path))
    extras = company_extras(read_jsonl(args.companies)) if args.companies else None
    sql, stats = build_sql(rows, extras=extras, deactivate_missing=args.deactivate_missing)
    Path(args.out).write_text(sql, encoding="utf-8", newline="\n")
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
