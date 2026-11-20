"""DerKaiser job database: one local SQLite file, searchable (diacritics ignored), with a read-only web page.

Zero install (Python standard library only). Typical use::

    py -m scraper.jobdb build derkaiser.db run_all/final_distinct.jsonl [more.jsonl ...] [--companies docs.jsonl] [--deactivate-missing]
    py -m scraper.jobdb search derkaiser.db "asistent medical pitesti" [--source anofm] [--limit 20]
    py -m scraper.jobdb stats derkaiser.db
    py -m scraper.jobdb serve derkaiser.db --open        # search page on http://127.0.0.1:8765 (this computer only)

``build`` can be repeated on the same file: jobs are upserted by URL (no duplicates), first_seen is kept and
``--deactivate-missing`` marks the jobs of the sources present in the import that are no longer listed as inactive.
The row rules are the same as for the PostgreSQL export (``scraper.pgexport``): no description text, a missing CIF is
allowed, a personal ID (CNP) in the CIF field drops the row. The web page never writes: the file is opened read-only.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from . import pgexport as P

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    cif TEXT PRIMARY KEY, name TEXT NOT NULL, website TEXT, employees INTEGER
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
    cif TEXT REFERENCES companies (cif), company_name TEXT NOT NULL,
    city TEXT, county TEXT,
    work_mode TEXT CHECK (work_mode IN ('remote', 'hybrid', 'on-site')),
    source TEXT NOT NULL DEFAULT 'unknown', expires_at TEXT, scraped_at TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS jobs_cif_idx ON jobs (cif);
CREATE INDEX IF NOT EXISTS jobs_city_idx ON jobs (city);
CREATE INDEX IF NOT EXISTS jobs_source_idx ON jobs (source);
CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
    title, company_name, city, county, content='jobs', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
"""
MAX_LIMIT = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def build(db_path: str | Path, rows: Iterable[dict], *, extras: dict[str, dict] | None = None,
          deactivate_missing: bool = False) -> dict:
    """Upsert the jobs (and their companies) into ``db_path``; returns the statistics of this run."""
    extras = extras or {}
    stats = {"rows": 0, "skipped": {}, "duplicates": 0}
    jobs: dict[str, dict] = {}
    for raw in rows:
        stats["rows"] += 1
        job, reason = P.normalize_job(raw)
        if job is None:
            stats["skipped"][reason] = stats["skipped"].get(reason, 0) + 1
        elif job["url"] in jobs:
            stats["duplicates"] += 1
        else:
            jobs[job["url"]] = job
    run = _now()
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(SCHEMA)
        with con:  # one transaction: all or nothing
            companies = {}
            for j in jobs.values():
                if j["cif"]:
                    companies.setdefault(j["cif"], j["company"])
            con.executemany(
                "INSERT INTO companies (cif, name, website, employees) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (cif) DO UPDATE SET name = excluded.name, website = COALESCE(excluded.website, website), "
                "employees = COALESCE(excluded.employees, employees)",
                [(cif, name, (extras.get(cif) or {}).get("website"), (extras.get(cif) or {}).get("employees"))
                 for cif, name in companies.items()])
            con.executemany(
                "INSERT INTO jobs (url, title, cif, company_name, city, county, work_mode, source, expires_at, scraped_at, "
                "first_seen, last_seen, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1) "
                "ON CONFLICT (url) DO UPDATE SET title = excluded.title, cif = excluded.cif, company_name = excluded.company_name, "
                "city = excluded.city, county = excluded.county, work_mode = excluded.work_mode, source = excluded.source, "
                "expires_at = excluded.expires_at, scraped_at = excluded.scraped_at, last_seen = excluded.last_seen, active = 1",
                [(j["url"], j["title"], j["cif"], j["company"], j["city"] or None, j["county"] or None, j["work_mode"],
                  j["source"], j["expires_at"], j["scraped_at"], run, run) for j in jobs.values()])
            if deactivate_missing and jobs:
                sources = sorted({j["source"] for j in jobs.values()})
                con.execute("UPDATE jobs SET active = 0 WHERE active = 1 AND last_seen < ? AND source IN (%s)"
                            % ", ".join("?" * len(sources)), [run, *sources])
            con.execute("INSERT INTO jobs_fts (jobs_fts) VALUES ('rebuild')")
        stats["jobs"] = len(jobs)
        stats["companies"] = len(companies)
        stats["total_in_db"] = con.execute("SELECT count(*) FROM jobs").fetchone()[0]
        stats["active_in_db"] = con.execute("SELECT count(*) FROM jobs WHERE active = 1").fetchone()[0]
    finally:
        con.close()
    return stats


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Read-only connection with dict-like rows."""
    con = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _terms(text: str) -> list[str]:
    return re.findall(r"\w+", text or "", re.UNICODE)


def match_expression(q: str = "", city: str = "", county: str = "") -> str | None:
    """FTS5 MATCH text: every word is a quoted prefix (so user input can never break the query syntax)."""
    parts = [f'"{t}"*' for t in _terms(q)]
    parts += [f'city : "{t}"*' for t in _terms(city)]
    parts += [f'county : "{t}"*' for t in _terms(county)]
    return " ".join(parts) or None


def search(con: sqlite3.Connection, q: str = "", *, city: str = "", county: str = "", source: str = "", work_mode: str = "",
           only_active: bool = True, limit: int = 20, offset: int = 0) -> dict:
    """``{"total", "rows"}``: best matches first (or newest first without a text query). Diacritics and case are ignored."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    match = match_expression(q, city, county)
    where, args = [], []
    if match:
        source_sql = "FROM jobs_fts JOIN jobs j ON j.id = jobs_fts.rowid"
        where.append("jobs_fts MATCH ?")
        args.append(match)
        order = "ORDER BY rank, j.id DESC"
    else:
        source_sql = "FROM jobs j"
        order = "ORDER BY j.last_seen DESC, j.id DESC"
    if source:
        where.append("j.source = ?")
        args.append(source)
    if work_mode:
        where.append("j.work_mode = ?")
        args.append(work_mode)
    if only_active:
        where.append("j.active = 1 AND (j.expires_at IS NULL OR j.expires_at > ?)")
        args.append(datetime.now(timezone.utc).isoformat(timespec="seconds"))
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = con.execute(f"SELECT count(*) {source_sql} {clause}", args).fetchone()[0]
    rows = con.execute(
        f"SELECT j.id, j.url, j.title, j.company_name AS company, j.cif, j.city, j.county, j.work_mode, j.source, j.last_seen "
        f"{source_sql} {clause} {order} LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
    return {"total": total, "rows": [dict(r) for r in rows]}


def stats(con: sqlite3.Connection) -> dict:
    """Totals plus the biggest sources, cities and employers."""
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "jobs": one("SELECT count(*) FROM jobs"), "active": one("SELECT count(*) FROM jobs WHERE active = 1"),
        "companies": one("SELECT count(DISTINCT company_name) FROM jobs WHERE active = 1"),
        "without_city": one("SELECT count(*) FROM jobs WHERE active = 1 AND city IS NULL"),
        "sources": [dict(r) for r in con.execute(
            "SELECT source, count(*) AS n FROM jobs WHERE active = 1 GROUP BY source ORDER BY n DESC")],
        "cities": [dict(r) for r in con.execute(
            "SELECT city, count(*) AS n FROM jobs WHERE active = 1 AND city IS NOT NULL GROUP BY city ORDER BY n DESC LIMIT 15")],
        "employers": [dict(r) for r in con.execute(
            "SELECT company_name AS company, count(*) AS n FROM jobs WHERE active = 1 GROUP BY company_name ORDER BY n DESC LIMIT 15")],
    }


# ---------- web page (read-only, this computer only) ----------

INDEX_HTML = """<!doctype html>
<html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Joburi DerKaiser</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--ink:#1b1b1b;--mute:#6b6b6b;--line:#e3e3df;--accent:#0b5fff}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--card:#1e1e1e;--ink:#ececec;--mute:#9a9a9a;--line:#2e2e2e;--accent:#6ea0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,Segoe UI,sans-serif}
main{max-width:860px;margin:0 auto;padding:16px}h1{font-size:1.3rem;margin:.2rem 0 .8rem}
form{display:grid;grid-template-columns:1fr 1fr;gap:8px}form input,form select{padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font:inherit;min-width:0}
#q{grid-column:1/-1;font-size:1.05rem}label{display:flex;gap:6px;align-items:center;color:var(--mute);font-size:.9rem}
#info{margin:12px 0 4px;color:var(--mute);font-size:.9rem}.job{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0}
.job a{color:var(--accent);font-weight:600;text-decoration:none;word-break:break-word}.job a:hover{text-decoration:underline}
.meta{color:var(--mute);font-size:.9rem;margin-top:3px}.tag{border:1px solid var(--line);border-radius:99px;padding:0 8px;font-size:.78rem;margin-left:4px}
.pager{display:flex;gap:8px;justify-content:center;margin:14px 0}.pager button{padding:8px 14px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);cursor:pointer}
.pager button:disabled{opacity:.4;cursor:default}
@media (max-width:520px){form{grid-template-columns:1fr}}
</style></head><body><main>
<h1>Joburi DerKaiser <span id="count" class="tag"></span></h1>
<form id="f" autocomplete="off">
<input id="q" type="search" placeholder="Caută: ex. asistent medical, programator, șofer…" autofocus>
<input id="county" placeholder="Județ (ex. Argeș)"><input id="city" placeholder="Oraș (ex. Pitești)">
<select id="source"><option value="">Orice sursă</option></select>
<select id="work_mode"><option value="">Orice mod de lucru</option><option value="remote">Remote</option><option value="hybrid">Hibrid</option><option value="on-site">La birou</option></select>
</form>
<div id="info"></div><div id="list"></div>
<div class="pager"><button id="prev">&larr; Înapoi</button><button id="next">Înainte &rarr;</button></div>
</main><script>
const $=id=>document.getElementById(id),PER=20;let page=0,timer=null;
const SAFE=u=>/^https?:\\/\\//i.test(u);
async function load(){
  const p=new URLSearchParams({q:$('q').value,county:$('county').value,city:$('city').value,source:$('source').value,work_mode:$('work_mode').value,limit:PER,offset:page*PER});
  const r=await fetch('/api/jobs?'+p);const d=await r.json();const list=$('list');list.replaceChildren();
  $('info').textContent=d.total?`${d.total} rezultate, pagina ${page+1} din ${Math.ceil(d.total/PER)}`:'Niciun rezultat.';
  for(const j of d.rows){
    const div=document.createElement('div');div.className='job';
    const a=document.createElement('a');a.textContent=j.title;if(SAFE(j.url)){a.href=j.url;a.target='_blank';a.rel='noopener noreferrer'}div.appendChild(a);
    const m=document.createElement('div');m.className='meta';m.textContent=[j.company,j.city||j.county||'România'].filter(Boolean).join(' · ');
    for(const t of [j.work_mode,j.source]){if(t){const s=document.createElement('span');s.className='tag';s.textContent=t;m.appendChild(s)}}
    div.appendChild(m);list.appendChild(div)}
  $('prev').disabled=page===0;$('next').disabled=(page+1)*PER>=d.total;
}
function again(){page=0;clearTimeout(timer);timer=setTimeout(load,250)}
for(const id of ['q','county','city'])$(id).addEventListener('input',again);
for(const id of ['source','work_mode'])$(id).addEventListener('change',again);
$('f').addEventListener('submit',e=>{e.preventDefault();again()});
$('prev').onclick=()=>{page--;load();scrollTo(0,0)};$('next').onclick=()=>{page++;load();scrollTo(0,0)};
fetch('/api/stats').then(r=>r.json()).then(s=>{$('count').textContent=s.active+' active';
  for(const x of s.sources){const o=document.createElement('option');o.value=x.source;o.textContent=`${x.source} (${x.n})`;$('source').appendChild(o)}});
load();
</script></body></html>"""


def make_server(db_path: str | Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """HTTP server for the search page and its JSON API (GET only; the database is opened read-only per request)."""
    db = Path(db_path).resolve()
    if not db.exists():
        raise FileNotFoundError(db)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the console quiet
            pass

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802 - http.server API
            url = urlparse(self.path)
            args = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path in ("/", "/index.html"):
                return self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            if url.path not in ("/api/jobs", "/api/stats"):
                return self._json(404, {"error": "not found"})
            try:
                con = connect_readonly(db)
                try:
                    if url.path == "/api/stats":
                        return self._json(200, stats(con))
                    return self._json(200, search(
                        con, args.get("q", ""), city=args.get("city", ""), county=args.get("county", ""),
                        source=args.get("source", ""), work_mode=args.get("work_mode", ""),
                        only_active=args.get("all") != "1", limit=int(args.get("limit", 20)), offset=int(args.get("offset", 0))))
                finally:
                    con.close()
            except ValueError:
                return self._json(400, {"error": "limit and offset must be numbers"})
            except sqlite3.Error as exc:
                return self._json(500, {"error": str(exc)})

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: list[str] | None = None) -> int:
    """CLI: build | search | stats | serve."""
    ap = argparse.ArgumentParser(prog="jobdb", description="DerKaiser job database (SQLite): build, search, serve")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="create/update the database from jobs jsonl files")
    b.add_argument("db")
    b.add_argument("jobs", nargs="+")
    b.add_argument("--companies", help="company documents jsonl (id, employees, website)")
    b.add_argument("--deactivate-missing", action="store_true")
    s = sub.add_parser("search", help="search from the terminal")
    s.add_argument("db")
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--city", default="")
    s.add_argument("--county", default="")
    s.add_argument("--source", default="")
    s.add_argument("--limit", type=int, default=20)
    t = sub.add_parser("stats", help="totals, sources, cities, employers")
    t.add_argument("db")
    w = sub.add_parser("serve", help="search page on this computer only")
    w.add_argument("db")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("--open", action="store_true", help="open the page in the browser")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        rows = (r for path in a.jobs for r in P.read_jsonl(path))
        extras = P.company_extras(P.read_jsonl(a.companies)) if a.companies else None
        print(json.dumps(build(a.db, rows, extras=extras, deactivate_missing=a.deactivate_missing), ensure_ascii=False))
    elif a.cmd == "search":
        con = connect_readonly(a.db)
        res = search(con, a.query, city=a.city, county=a.county, source=a.source, limit=a.limit)
        print(f"{res['total']} rezultate")
        for r in res["rows"]:
            print(f"- {r['title']} | {r['company']} | {r['city'] or r['county'] or 'România'} | {r['source']}\n  {r['url']}")
    elif a.cmd == "stats":
        print(json.dumps(stats(connect_readonly(a.db)), ensure_ascii=False, indent=2))
    else:
        server = make_server(a.db, port=a.port)
        url = f"http://127.0.0.1:{server.server_address[1]}"
        print(f"Cautare joburi: {url}  (Ctrl+C pentru oprire)", flush=True)
        if a.open:
            threading.Timer(0.5, webbrowser.open, args=(url,)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
