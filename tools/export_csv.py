"""Merged jobs (JSONL, any DerKaiser source format) -> CSV: titlu, firma, cif, locatie, judet, remote, link, sursa.

    py tools/export_csv.py jobs.csv run_all/final_distinct.jsonl [more.jsonl ...]

Accepts both the model format (url/title/company/cif/location[]) and the own-site format (job_link/job_title/city/county/remote).
Duplicate links are written once. UTF-8 with BOM so Excel opens the diacritics correctly.
"""
import csv
import json
import sys

COLUMNS = ["titlu", "firma", "cif", "locatie", "judet", "remote", "link", "sursa"]


def _join(value) -> str:
    if isinstance(value, (list, tuple)):
        return ";".join(str(v) for v in value if v)
    return str(value or "")


def row_of(job: dict) -> dict:
    return {"titlu": job.get("title") or job.get("job_title") or "", "firma": job.get("company") or "",
            "cif": job.get("cif") or "", "locatie": _join(job.get("location") or job.get("city")),
            "judet": _join(job.get("county")), "remote": _join(job.get("remote") or job.get("workmode")),
            "link": job.get("url") or job.get("job_link") or "", "sursa": job.get("source") or job.get("_source") or ""}


def export(paths: list[str], out: str) -> int:
    seen, rows = set(), []
    for path in paths:
        for line in open(path, encoding="utf-8"):
            if not line.strip():
                continue
            row = row_of(json.loads(line))
            if row["link"] and row["titlu"] and row["link"] not in seen:
                seen.add(row["link"])
                rows.append(row)
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    print(export(sys.argv[2:], sys.argv[1]), "rows")
