"""eJobs source: the sitemaps eJobs itself publishes for bots (robots.txt: ``sitemap-listings-index.xml``) list every active
ad; each ad page carries schema.org ``JobPosting`` JSON-LD (inside an ``@graph``). Only Romanian postings survive
``build_job``; the link is the ad on eJobs (the peViitor model: aggregate + link, no copied description).
``get(url) -> text`` is injected. The expired-ads sitemap is never read. Pages are large (~450 KB): keep the pace slow.
"""
from __future__ import annotations

import html as _h
import json
import re

from . import jobs as J

INDEX = "https://www.ejobs.ro/sitemap-listings-index.xml"
AD_URL = re.compile(r"^https://www\.ejobs\.ro/user/locuri-de-munca/[^/?#]+/\d+$")
_LD = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)


def sitemap_ads(get) -> list[str]:
    """All active ad URLs (deduplicated, stable order) from the category sitemaps."""
    seen, out = set(), []
    for sm in re.findall(r"<loc>(.*?)</loc>", get(INDEX)):
        if "expired" in sm:
            continue
        try:
            body = get(sm)
        except Exception:  # noqa: BLE001 - one broken category does not stop the others
            continue
        for u in re.findall(r"<loc>(.*?)</loc>", body):
            if AD_URL.match(u) and u not in seen:
                seen.add(u)
                out.append(u)
    return out


def employer_of(page_html: str) -> str:
    for block in _LD.findall(page_html or ""):
        try:
            data = json.loads(_h.unescape(block.strip()))
        except ValueError:
            continue
        for node in J._walk(data):
            kind = node.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                org = node.get("hiringOrganization")
                name = org.get("name") if isinstance(org, dict) else org
                if isinstance(name, str) and name.strip():
                    return _h.unescape(name).strip()
    return ""


def parse_ad(page_html: str, url: str) -> dict | None:
    company = employer_of(page_html)
    if not company or re.search(r"confiden", company, re.I):
        return None  # anonymous employer: nothing to attach the ad to
    jobs = J.extract_jobpostings(company, page_html, url)
    return jobs[0] if jobs else None


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    from . import net as N

    ap = argparse.ArgumentParser(description="eJobs sitemap -> JobPosting jobs (read-only, slow on purpose)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--max-ads", type=int, default=0)
    ap.add_argument("--skip-urls", help="file with URLs already done (one per line): resume")
    ap.add_argument("--i-have-written-permission", action="store_true",
                    help="obligatoriu: termenii site-ului interzic colectarea automata fara acord scris (vezi docs/TOS.md)")
    args = ap.parse_args(argv)
    if not args.i_have_written_permission:
        print("Refuz: termenii eJobs (termenii 7.2, 7.3, 7.5) interzic colectarea automata fara acord scris. "
              "Vezi docs/TOS.md. Cu acord scris, adauga --i-have-written-permission.")
        return 2
    net = N.Net(interval=args.interval, timeout=40, retries=1)
    t0 = time.time()
    urls = sitemap_ads(net.get)
    done = set(open(args.skip_urls, encoding="utf-8").read().split()) if args.skip_urls else set()
    todo = [u for u in urls if u not in done][: args.max_ads or None]
    print(f"ads_in_sitemaps={len(urls)} todo={len(todo)}", flush=True)
    kept = 0
    with open(args.out, "a", encoding="utf-8") as out, open(args.out + ".done", "a", encoding="utf-8") as seen:
        for i, u in enumerate(todo, 1):
            try:
                job = parse_ad(net.get(u), u)
            except Exception:  # noqa: BLE001
                job = None
            seen.write(u + "\n")
            if job:
                out.write(json.dumps({**job, "source": "ejobs"}, ensure_ascii=False) + "\n")
                kept += 1
            if i % 25 == 0:
                out.flush(); seen.flush()
                print(f"{i}/{len(todo)} kept={kept}", flush=True)
    print(json.dumps({"ads": len(todo), "kept_romania": kept, "seconds": round(time.time() - t0)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
