"""posturi.gov.ro source (ANFP): public-sector vacancies (town halls, hospitals, schools, agencies).

Official state portal (H.G. 1336/2022); ``robots.txt`` allows everything for bots. Listing pages
``/toate-posturile/?pg_page=N`` link to ad pages ``/joburi/<slug>/``; each ad page carries schema.org ``JobPosting``
JSON-LD. The link is the ad on posturi.gov.ro (aggregate + link, no copied description). ``get(url) -> text`` is injected.
The site publishes no reuse terms: confirm with the association before using the output in production.
"""
from __future__ import annotations

import json
import re

from . import ejobs as E
from . import jobs as J

BASE = "https://posturi.gov.ro"
LISTING = BASE + "/toate-posturile/?pg_page={page}"
_AD = re.compile(r'(?:https://posturi\.gov\.ro)?(/joburi/[a-z0-9-]+/)')


def ad_links(listing_html: str) -> list[str]:
    seen, out = set(), []
    for path in _AD.findall(listing_html or ""):
        if path not in seen:
            seen.add(path)
            out.append(BASE + path)
    return out


def listing_ads(get, *, max_pages: int = 400, max_failures: int = 5, max_stale: int = 8, pause=None) -> list[str]:
    """Ad URLs from every listing page. The site's pages overlap, so the walk ends only after ``max_stale`` pages in a
    row bring nothing new; a failed page is retried (``max_failures`` in a row end it)."""
    import time
    pause = pause if pause is not None else time.sleep
    seen, out, failures, stale, page = set(), [], 0, 0, 1
    while page <= max_pages:
        try:
            links = ad_links(get(LISTING.format(page=page)))
        except Exception:  # noqa: BLE001
            failures += 1
            if failures >= max_failures:
                break
            pause(10 * failures)
            continue
        failures = 0
        fresh = [u for u in links if u not in seen]
        stale = 0 if fresh else stale + 1
        if stale >= max_stale:
            break
        seen.update(fresh)
        out.extend(fresh)
        page += 1
    return out


def parse_ad(page_html: str, url: str) -> dict | None:
    company = E.employer_of(page_html)
    if not company:
        return None
    jobs = J.extract_jobpostings(company, page_html, url)
    return jobs[0] if jobs else None


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    from . import net as N

    ap = argparse.ArgumentParser(description="posturi.gov.ro listing -> JobPosting jobs (read-only, slow on purpose)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--max-ads", type=int, default=0)
    ap.add_argument("--max-pages", type=int, default=400)
    ap.add_argument("--skip-urls", help="file with URLs already done (one per line): resume")
    args = ap.parse_args(argv)
    net = N.Net(interval=args.interval, timeout=40, retries=1)
    t0 = time.time()
    urls = listing_ads(net.get, max_pages=args.max_pages)
    done = set(open(args.skip_urls, encoding="utf-8").read().split()) if args.skip_urls else set()
    todo = [u for u in urls if u not in done][: args.max_ads or None]
    print(f"ads_listed={len(urls)} todo={len(todo)}", flush=True)
    kept = 0
    with open(args.out, "a", encoding="utf-8") as out, open(args.out + ".done", "a", encoding="utf-8") as seen:
        for i, u in enumerate(todo, 1):
            try:
                job = parse_ad(net.get(u), u)
            except Exception:  # noqa: BLE001
                job = None
            seen.write(u + "\n")
            if job:
                out.write(json.dumps({**job, "source": "posturi.gov.ro"}, ensure_ascii=False) + "\n")
                kept += 1
            if i % 25 == 0:
                out.flush(); seen.flush()
                print(f"{i}/{len(todo)} kept={kept}", flush=True)
    print(json.dumps({"ads": len(todo), "kept": kept, "seconds": round(time.time() - t0)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
