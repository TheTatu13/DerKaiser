"""Third-party layer, firm first: for a KNOWN firm, find its own page on a job portal and read the ads listed there.

BestJobs only (robots.txt allows generic bots; checked). Public sitemap -> company slugs; the firm is matched by an EXACT
slug of its cleaned name (no fuzzy guesses: a wrong firm would put foreign ads under it); the profile page lists the firm's
active ads as links; each ad page carries its location/remote/expiry as embedded JSON. Only Romanian, non-expired ads
survive ``build_job``. Link = the ad on the portal (the peViitor model: aggregate + link to the source, no copied text).
All I/O is injected (``get(url) -> text``).
"""
from __future__ import annotations

import html as H
import json
import re
import unicodedata

from . import jobs as J
from . import model as M

BASE = "https://www.bestjobs.eu"
SITEMAP_INDEX = f"{BASE}/sitemap/sitemap.xml"
_PROFILE_SITEMAP = re.compile(r"sitemap\.jobs_by_employer\.ro[^/]*\.xml$")
_AD_HREF = re.compile(r'href="(?:https://www\.bestjobs\.eu)?(/loc-de-munca/[a-z0-9][a-z0-9\-+%.]*)"[^>]*?(?:aria-label="([^"]*)")?', re.I)


def slugify(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", t)).strip("-")


def company_slug(company: str) -> str:
    return slugify(" ".join(M.core_tokens(company)))


def load_slugs(get) -> set[str]:
    index = get(SITEMAP_INDEX)
    slugs: set[str] = set()
    for sm in re.findall(r"<loc>(.*?)</loc>", index):
        if _PROFILE_SITEMAP.search(sm):
            for u in re.findall(r"<loc>(.*?)</loc>", get(sm)):
                m = re.search(r"/company-profile/([^/?#]+)$", u)
                if m:
                    slugs.add(m.group(1).lower())
    return slugs


def match_slug(company: str, slugs: set[str]) -> str | None:
    slug = company_slug(company)
    return slug if slug and slug in slugs else None


def parse_profile(html: str) -> list[tuple[str, str]]:
    """``[(absolute ad url, title)]`` of the ads listed on a company profile."""
    out, seen = [], set()
    for m in _AD_HREF.finditer(html or ""):
        path, title = m.group(1), H.unescape(m.group(2) or "")
        if path not in seen:
            seen.add(path)
            out.append((BASE + path, title))
    return out


def parse_ad(company: str, url: str, title: str, html: str) -> dict | None:
    data = (html or "").replace('\\"', '"')
    if '"expired":true' in data or re.search(r'"state":"(?!active)[a-z_]+"', data[data.find('"workplaceJob"'):][:200] if '"workplaceJob"' in data else ""):
        return None
    m = re.search(r'"employer":\{"employerName":"(.*?)"', data)
    if m and not set(M.core_tokens(company)) & set(M.core_tokens(H.unescape(m.group(1)))):
        return None  # the ad belongs to another employer: never attach it to this firm
    names = re.findall(r'"locations":\[(.*?)\],"employmentTypes"', data, re.S)
    locs = re.findall(r'"name":"(.*?)"', names[0]) if names else []
    if not title:
        t = re.search(r'property="og:title" content="(.*?)"', data)
        title = H.unescape(t.group(1)).rsplit(",", 1)[0].strip() if t else ""
    remote = ["remote"] if '"remote":true' in data else (["hybrid"] if '"partialRemote":true' in data else [])
    job = J.build_job(company, title, url, [*(H.unescape(x) for x in locs)], [])
    if job and remote:
        job["remote"] = remote
    return job


def portal_jobs(company: str, slugs: set[str], get, *, max_ads: int = 60) -> list[dict]:
    slug = match_slug(company, slugs)
    if not slug:
        return []
    profile = get(f"{BASE}/company-profile/{slug}")
    jobs = []
    for url, title in parse_profile(profile)[:max_ads]:
        try:
            job = parse_ad(company, url, title, get(url))
        except Exception:  # noqa: BLE001 - one broken ad never stops the firm
            continue
        if job:
            jobs.append(job)
    return J.dedupe(jobs)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    from . import net as N

    ap = argparse.ArgumentParser(description="BestJobs company-profile layer for known firms (read-only)")
    ap.add_argument("--docs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--max-firms", type=int, default=0)
    ap.add_argument("--i-have-written-permission", action="store_true",
                    help="obligatoriu: termenii site-ului interzic colectarea automata fara acord scris (vezi docs/TOS.md)")
    args = ap.parse_args(argv)
    if not args.i_have_written_permission:
        print("Refuz: termenii BestJobs (termenii pct. 3) interzic colectarea automata fara acord scris. "
              "Vezi docs/TOS.md. Cu acord scris, adauga --i-have-written-permission.")
        return 2
    net = N.Net(interval=args.interval, timeout=40, retries=1)
    t0 = time.time()
    slugs = load_slugs(net.get)
    firms = []
    for line in open(args.docs, encoding="utf-8"):
        doc = json.loads(line)
        doc = doc if "id" in doc else doc.get("doc") or {}
        if doc.get("id") and doc.get("company"):
            firms.append(doc)
    matched = [d for d in firms if match_slug(d["company"], slugs)]
    print(f"slugs={len(slugs)} firms={len(firms)} matched={len(matched)}", flush=True)
    total = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for doc in matched[: args.max_firms or None]:
            try:
                jobs = portal_jobs(doc["company"], slugs, net.get)
            except Exception as exc:  # noqa: BLE001
                print(f"{doc['company'][:30]:30} ERR {exc.__class__.__name__}", flush=True)
                continue
            for j in jobs:
                out.write(json.dumps({**j, "cif": doc["id"], "source": "bestjobs"}, ensure_ascii=False) + "\n")
            out.flush()
            total += len(jobs)
            print(f"{doc['company'][:30]:30} jobs={len(jobs)}", flush=True)
    print(json.dumps({"matched": len(matched), "jobs": total, "seconds": round(time.time() - t0)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
