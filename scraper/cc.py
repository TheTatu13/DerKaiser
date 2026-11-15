"""Common Crawl source: schema.org ``JobPosting`` pages of Romanian sites, found through the public CDX index.

Nothing here touches the firms' own sites: it reads the index (``index.commoncrawl.org``) and fetches single WARC
records by byte range (``data.commoncrawl.org``). Records are weeks/months old, so every candidate ad link is
re-checked live by the caller (``status``) before it counts. All I/O is injected (``get`` text, ``get_range`` bytes).
"""
from __future__ import annotations

import gzip
import json
import re
import urllib.parse
import urllib.request
from typing import Callable, Iterator

from . import jobs as J

INDEX = "https://index.commoncrawl.org"
DATA = "https://data.commoncrawl.org"
PATH_HINT = r".*(cariere|career|jobs?/|joburi|locuri-de-munca|loc-de-munca|angaj|recrut|vacan|pozitii|posturi|oferta).*"
UA = "DerKaiser/0.1 (peViitor volunteer; read-only)"


def latest_crawls(get: Callable[[str], str], n: int = 2) -> list[str]:
    return [c["id"] for c in json.loads(get(f"{INDEX}/collinfo.json"))[:n]]


def cdx_url(crawl: str, domain: str, *, limit: int = 300, page: int | None = None) -> str:
    q = [("url", f"{domain}/*"), ("output", "json"), ("limit", str(limit)),
         ("fl", "url,filename,offset,length,status,mime"),
         ("filter", "status:200"), ("filter", "mime:text/html"), ("filter", f"url:{PATH_HINT}")]
    if domain.startswith("*."):
        q[0] = ("url", f"{domain}")
    if page is not None:
        q.append(("page", str(page)))
    return f"{INDEX}/{crawl}-index?" + urllib.parse.urlencode(q)


def cdx_records(get: Callable[[str], str], crawl: str, domain: str, *, limit: int = 300) -> list[dict]:
    out = []
    for line in get(cdx_url(crawl, domain, limit=limit)).splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("filename") and r.get("offset") is not None and r.get("length") is not None:
            out.append(r)
    return out


def warc_body(raw: bytes) -> str:
    """HTML of one WARC response record (gzip member): WARC headers, HTTP headers, then the body."""
    try:
        data = gzip.decompress(raw)
    except OSError:
        return ""
    parts = data.split(b"\r\n\r\n", 2)
    if len(parts) < 3:
        return ""
    return parts[2].decode("utf-8", "replace")


def fetch_record(get_range: Callable[[str, int, int], bytes], rec: dict) -> str:
    off, length = int(rec["offset"]), int(rec["length"])
    return warc_body(get_range(f"{DATA}/{rec['filename']}", off, off + length - 1))


def jobs_from_records(company: str, records: list[dict], get_range, *, max_records: int = 200,
                      seen: set | None = None) -> Iterator[dict]:
    seen = seen if seen is not None else set()
    for rec in records[:max_records]:
        url = rec.get("url") or ""
        if url in seen or len(seen) >= max_records:
            continue
        seen.add(url)
        try:
            html = fetch_record(get_range, rec)
        except Exception:  # noqa: BLE001 - one broken record never stops the sweep
            continue
        if "JobPosting" not in html:
            continue
        yield from J.extract_jobpostings(company, html, url)


def urllib_get_range(url: str, start: int, end: int, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return f.read()


# ---------- index straight from the data server (when the CDX API times out) ----------
# cluster.idx (~100 MB, sorted by SURT key) is binary-searched with small byte-range reads; the matching cdx-NNNNN.gz
# blocks (~250 KB each) are then read and filtered. No API server involved, nothing large downloaded.

def surt_host(domain: str) -> str:
    return ",".join(reversed(domain.lower().strip(".").split(".")))


def idx_url(crawl: str, name: str = "cluster.idx") -> str:
    return f"{DATA}/cc-index/collections/{crawl}/indexes/{name}"


def remote_size(url: str, timeout: float = 40.0) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return int(f.headers["Content-Range"].rsplit("/", 1)[1])


def _lines_from(get_range, url: str, pos: int, size: int, span: int) -> list[str]:
    raw = get_range(url, pos, min(size, pos + span) - 1).decode("utf-8", "replace")
    lines = raw.split("\n")
    return lines[1:-1] if pos > 0 else lines[:-1]  # first/last may be cut


def local_blocks(get_range, crawl: str, domain: str, size: int, *, max_blocks: int = 8) -> list[tuple[str, int, int]]:
    url, host = idx_url(crawl), surt_host(domain)
    start, stop = host + ")", host + "-"
    lo, hi = 0, size
    while hi - lo > 16384:
        mid = (lo + hi) // 2
        got = _lines_from(get_range, url, mid, size, 4096)
        if not got:
            hi = mid
            continue
        if got[0].split(" ", 1)[0] < start:
            lo = mid
        else:
            hi = mid
    window = _lines_from(get_range, url, max(0, lo - 1), size, 400_000)
    blocks, prev = [], None
    for line in window:
        key = line.split(" ", 1)[0]
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        if key >= stop:
            break
        if key < start:
            prev = (parts[1], int(parts[2]), int(parts[3]))
            continue
        if prev:
            blocks.append(prev)
            prev = None
        blocks.append((parts[1], int(parts[2]), int(parts[3])))
    if prev and not blocks:  # the domain sits inside the block that starts before it
        blocks.append(prev)
    seen, out = set(), []
    for b in blocks:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out[:max_blocks]


def local_records(get_range, crawl: str, domain: str, size: int, *, max_blocks: int = 8) -> list[dict]:
    host, out = surt_host(domain), []
    rx = re.compile(PATH_HINT, re.I)
    for name, off, length in local_blocks(get_range, crawl, domain, size, max_blocks=max_blocks):
        try:
            text = gzip.decompress(get_range(idx_url(crawl, name), off, off + length - 1)).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        for line in text.splitlines():
            key = line.split(" ", 1)[0]
            if not (key.startswith(host + ")") or key.startswith(host + ",")):
                continue
            brace = line.find("{")
            try:
                rec = json.loads(line[brace:]) if brace >= 0 else None
            except ValueError:
                continue
            if rec and str(rec.get("status")) == "200" and "html" in str(rec.get("mime", "")) \
                    and rx.match(rec.get("url") or "") and rec.get("filename"):
                out.append(rec)
    return out


# ---------- sweep over known firm domains ----------

def domain_of(site) -> str | None:
    if isinstance(site, (list, tuple)):  # company docs keep "website" as a list
        return next((d for d in (domain_of(x) for x in site) if d), None)
    try:
        host = urllib.parse.urlsplit(site if "://" in (site or "") else f"https://{site or ''}").hostname or ""
    except ValueError:  # malformed site value in the docs
        return None
    host = host.lower().removeprefix("www.")
    return host if "." in host else None


def sweep(firms: list[dict], get, get_range, status, *, crawls: list[str], max_records: int = 40,
          limit: int = 300, size_of=None, on_firm=None) -> list[dict]:
    """``firms``: ``[{cif, company, site}]`` -> legacy jobs (+ ``cif``, ``cc_crawl``) whose ad link is alive NOW."""
    out, sizes = [], {}
    size_of = size_of or remote_size
    for firm in firms:
        domain = domain_of(firm.get("site"))
        if not domain:
            continue
        found: dict[str, dict] = {}
        tried: set = set()  # a page seen in several crawls is fetched once
        for crawl in crawls:
            try:  # index files first (the CDX API server times out most of the time), API as fallback
                size = sizes.get(crawl) or sizes.setdefault(crawl, size_of(idx_url(crawl)))
                records = local_records(get_range, crawl, domain, size)
            except Exception:  # noqa: BLE001
                try:
                    records = cdx_records(get, crawl, domain, limit=limit)
                except Exception:  # noqa: BLE001 - skips that crawl only
                    continue
            for job in jobs_from_records(firm["company"], records, get_range, max_records=max_records, seen=tried):
                found.setdefault(job["job_link"], {**job, "cif": firm["cif"], "cc_crawl": crawl})
        alive = [j for j in found.values() if 0 < status(j["job_link"]) < 400]
        out.extend(alive)
        if on_firm:
            on_firm(firm, len(found), len(alive))
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    from . import net as N
    from . import publish as P

    ap = argparse.ArgumentParser(description="Common Crawl JobPosting sweep over known firm sites (read-only)")
    ap.add_argument("--docs", required=True, help="docs_with_jobs.jsonl / docs.jsonl (id, company, website)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--crawls", type=int, default=2)
    ap.add_argument("--max-firms", type=int, default=0)
    args = ap.parse_args(argv)

    firms = []
    for line in open(args.docs, encoding="utf-8"):
        row = json.loads(line)
        doc = row if "id" in row else row.get("doc") or {}
        site = row.get("website") or doc.get("website") or doc.get("site")
        if doc.get("id") and site:
            firms.append({"cif": doc["id"], "company": doc.get("company") or "", "site": site})
    if args.max_firms:
        firms = firms[:args.max_firms]
    net = N.Net(interval=1.0, respect_robots=False, timeout=60, retries=0)
    crawls = latest_crawls(net.get, args.crawls)
    t0 = time.time()
    jobs = sweep(firms, net.get, urllib_get_range, P.make_status_checker(P.Pace(0.5)), crawls=crawls,
                 on_firm=lambda f, n, a: print(f"{f['company'][:28]:28} candidates={n} alive={a}", flush=True))
    out = open(args.out, "w", encoding="utf-8")
    for j in jobs:
        out.write(json.dumps(j, ensure_ascii=False) + "\n")
    print(json.dumps({"firms": len(firms), "crawls": crawls, "jobs_alive": len(jobs),
                      "seconds": round(time.time() - t0)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
