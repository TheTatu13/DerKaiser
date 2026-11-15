"""DerKaiser engine: nested scrapers ("matryoshka") with safety nets, run in parallel across hosts.

Nesting, smallest doll first. Each layer is a complete scraper on its own; when it finds nothing (or fails, or its
output looks wrong) the next, broader one takes over. ANOFM is not a layer: it is always added on top.

    recipe   a per-firm JSON recipe (recipes/<cif>.json): exact ATS+slug, or careers URLs + ad-link regex
    ats      public ATS feeds found on the careers pages (Greenhouse, Lever, Workable, Recruitee, ...)
    jsonld   schema.org JobPosting in the careers pages
    listing  generic: ad links on careers pages, each CONFIRMED on its own ad page

Safety nets: every layer runs in its own try/except; a per-firm request budget and time limit (never hangs);
robots.txt / retries / circuit breaker live in ``net``; each layer's output must be sane (no uniform-title floods,
no absurd counts); job URLs must belong to the firm's site, a known ATS or ANOFM; Brewtality validation rules;
live check of every own-site ad link (ANOFM ads are current by definition); a history canary flags a firm that
suddenly drops to 0 jobs (flagged, never treated as deletion); resumable, append-only output; dry-run only.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from . import jobs as J
from . import learn as L
from . import model as M
from . import jobcheck as V
from . import workday as WD

ATS_HOSTS = ("greenhouse.io", "lever.co", "workable.com", "recruitee.com", "smartrecruiters.com", "personio.de", "personio.com", "myworkdayjobs.com")
ANOFM_HOST = "mediere.anofm.ro"


class BudgetExceeded(Exception):
    """The firm used up its request budget or time limit."""


class FirmCtx:
    """Per-firm state: page cache + request budget + deadline. ``get`` is the ONLY way layers touch the network."""

    def __init__(self, cif, company, site, fetch, recipe, max_requests, max_seconds, clock=time.monotonic,
                 render=None, max_renders=6, post=None):
        self.post = post
        self.cif, self.company, self.site, self.recipe = cif, company, site, recipe or {}
        self._fetch, self.max_requests, self._clock = fetch, max_requests, clock
        self.deadline = clock() + max_seconds
        self.pages: dict[str, str] = {}
        self.requests = 0
        self.crawled = False
        self.render, self.max_renders, self.renders = render, max_renders, 0
        self.flags: list[str] = []
        self.rendered: dict[str, str] = {}

    def get(self, url: str) -> str:
        if url in self.pages:
            return self.pages[url]
        if self.requests >= self.max_requests or self._clock() > self.deadline:
            raise BudgetExceeded(url)
        self.requests += 1
        text = self._fetch(url)
        self.pages[url] = text
        return text


    def render_page(self, url: str) -> str:
        """Same page after JavaScript ran (headless browser). Own budget, shares the firm's deadline."""
        if not self.render:
            raise RuntimeError("rendering unavailable")
        if url in self.rendered:
            return self.rendered[url]
        if self.renders >= self.max_renders or self._clock() > self.deadline:
            raise BudgetExceeded(url)
        self.renders += 1
        html = self.render(url)
        self.rendered[url] = html
        return html

    def counted_post(self, url: str, body: dict) -> dict:
        """POST (JSON) under the same request budget and deadline as ``get``."""
        if self.requests >= self.max_requests or self._clock() > self.deadline:
            raise BudgetExceeded(url)
        self.requests += 1
        return self.post(url, body)

    def get_ad(self, url: str) -> str:
        """Ad page: static first; if it is an empty JavaScript shell, the rendered DOM."""
        html = self.get(url)
        if self.render and len(J.html_to_text(re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I))) < 400:
            try:
                return self.render_page(url)
            except BudgetExceeded:
                raise
            except Exception:  # noqa: BLE001
                return html
        return html


# ---------- crawl (shared by the broader layers) ----------

def crawl(ctx: FirmCtx, max_careers: int = 4, max_second_level: int = 3) -> None:
    if ctx.crawled:
        return
    ctx.crawled = True
    home = ctx.get(ctx.site)
    urls = list(ctx.recipe.get("careers_urls") or []) or J.find_careers_links(home, ctx.site, limit=max_careers)
    for url in urls:
        try:
            ctx.get(url)
        except BudgetExceeded:
            raise
        except Exception:  # noqa: BLE001 - a dead careers link is not fatal
            continue
    fetched = 0
    for url, text in list(ctx.pages.items()):
        if url == ctx.site:
            continue
        for link in J.listing_page_links(text, url, ctx.site):
            if link in ctx.pages or fetched >= max_second_level:
                continue
            try:
                ctx.get(link)
                fetched += 1
            except BudgetExceeded:
                raise
            except Exception:  # noqa: BLE001
                continue


# ---------- layers: each returns (label, legacy_jobs) or None when it does not apply ----------

def run_link_recipe(ctx: FirmCtx, r: dict) -> list[dict]:
    """Walk the recipe's careers pages (all list pages, if paginated), collect ad links by the recipe regex, build the
    jobs. Titles come from the link text when it looks like a title, else from the ad page. Sampled ads must still
    confirm: if not, the recipe has drifted (flag) and returns nothing so the broader dolls take over."""
    rx = re.compile(r["ad_link_regex"], re.I)
    fetch = ctx.render_page if r.get("render") and ctx.render else ctx.get
    links: dict[str, str] = {}
    for base in r["careers_urls"]:
        url, n, seen = base, 1, set()
        while n <= int(r.get("max_pages", 1)) and url not in seen:
            seen.add(url)
            try:
                html = fetch(url)
            except BudgetExceeded:
                raise
            except Exception:  # noqa: BLE001
                break
            fresh = 0
            for u, text, _ in L.anchors(html, url, ctx.site or url):
                if rx.search(u) and J.is_ad_link(u) and u not in links:
                    links[u] = text
                    fresh += 1
            nxt = L.next_page_url(base, url, r.get("pagination"), n, html, ctx.site or url) if fresh else None
            if not nxt:
                break
            url, n = nxt, n + 1
    if not links:
        return []
    urls = list(links)
    step = max(1, len(urls) // 3)
    probe = urls[::step][:3]
    confirmed = {}
    for u in probe:
        try:
            confirmed[u] = J.confirm_ad(ctx.company, u, ctx.get_ad(u))
        except BudgetExceeded:
            raise
        except Exception:  # noqa: BLE001
            confirmed[u] = None
    if len(probe) >= 2 and sum(1 for v in confirmed.values() if v) < 2:
        ctx.flags.append("recipe_drift")
        return []
    jobs, fetched = [], 0
    for u in urls:
        text = links[u]
        job = confirmed.get(u)
        if job is None and L.trusted_title(text):
            city = J.infer_city(text)
            job = {"job_title": text, "job_link": u, "company": ctx.company, "country": J.COUNTRY, "city": city or [],
                   "county": J.county_of(city), "remote": J.infer_remote(text)}
        elif job is None and fetched < 60:
            fetched += 1
            try:
                job = J.confirm_ad(ctx.company, u, ctx.get_ad(u))
            except BudgetExceeded:
                raise
            except Exception:  # noqa: BLE001
                job = None
        if job:
            jobs.append(job)
    if r.get("expected_min") and len(jobs) < 0.5 * r["expected_min"]:
        ctx.flags.append("recipe_drift")
    return J.dedupe(jobs)


def layer_recipe(ctx: FirmCtx):
    r = ctx.recipe
    if not r:
        return None
    if r.get("ats") and r.get("slug"):
        jobs = J.parse_ats(r["ats"], ctx.company, r["slug"], ctx.get(J.ats_url(r["ats"], r["slug"])))
        return r["ats"], jobs
    if r.get("careers_urls") and r.get("ad_link_regex"):
        return "recipe", run_link_recipe(ctx, r)
    return None


def layer_ats(ctx: FirmCtx):
    crawl(ctx)
    for text in list(ctx.pages.values()):
        wd = WD.detect_workday(text) if ctx.post else None
        if wd:
            try:
                jobs = WD.fetch_workday(ctx.company, *wd, ctx.counted_post)
            except BudgetExceeded:
                raise
            except Exception:  # noqa: BLE001
                jobs = []
            if jobs:
                return "workday", jobs
        hit = J.detect_ats(text)
        if not hit:
            continue
        ats, slug = hit
        try:
            jobs = J.parse_ats(ats, ctx.company, slug, ctx.get(J.ats_url(ats, slug)))
        except BudgetExceeded:
            raise
        except Exception:  # noqa: BLE001 - broken feed: try the next doll
            continue
        if jobs:
            return ats, jobs
    return "ats", []


def layer_jsonld(ctx: FirmCtx):
    crawl(ctx)
    return "jsonld", J.dedupe(j for url, text in ctx.pages.items() for j in J.extract_jobpostings(ctx.company, text, url))


def layer_listing(ctx: FirmCtx):
    crawl(ctx)
    return "listing", J.extract_listing_ads(ctx.company, ctx.pages, ctx.site, ctx.get)


def layer_rendered(ctx: FirmCtx):
    """Last doll: the careers pages AFTER JavaScript ran, then the same three extractions on the rendered DOM."""
    if not ctx.render:
        return None
    crawl(ctx)
    targets = [u for u in ctx.pages if u != ctx.site][:3]
    pages: dict[str, str] = {}
    if not targets:  # careers link itself is injected by JavaScript: render the home page to find it
        home = ctx.render_page(ctx.site)
        pages[ctx.site] = home
        targets = [u for u in J.find_careers_links(home, ctx.site, limit=3) if u != ctx.site]
    for url in targets:
        try:
            pages[url] = ctx.render_page(url)
        except BudgetExceeded:
            raise
        except Exception:  # noqa: BLE001
            continue
    for url, html in list(pages.items()):
        for link in J.listing_page_links(html, url, ctx.site, limit=2):
            if link not in pages:
                try:
                    pages[link] = ctx.render_page(link)
                except BudgetExceeded:
                    raise
                except Exception:  # noqa: BLE001
                    continue
    for html in pages.values():
        hit = J.detect_ats(html)
        if hit:
            try:
                jobs = J.parse_ats(hit[0], ctx.company, hit[1], ctx.get(J.ats_url(*hit)))
            except BudgetExceeded:
                raise
            except Exception:  # noqa: BLE001
                continue
            if jobs:
                return f"js:{hit[0]}", jobs
    jobs = J.dedupe(j for url, html in pages.items() for j in J.extract_jobpostings(ctx.company, html, url))
    if jobs:
        return "js:jsonld", jobs
    return "js:listing", J.extract_listing_ads(ctx.company, pages, ctx.site, ctx.get_ad)


def learn_from_ctx(ctx: FirmCtx) -> dict | None:
    """Try to LEARN a recipe from what the engine already fetched (static pages first, then rendered ones)."""
    static = {u: h for u, h in ctx.pages.items() if u != ctx.site}
    rec = L.learn_recipe(ctx.company, static, ctx.site, ctx.get_ad) if static else None
    if rec:
        return rec
    rendered = {u: h for u, h in ctx.rendered.items() if u != ctx.site}
    rec = L.learn_recipe(ctx.company, rendered, ctx.site, ctx.get_ad) if rendered else None
    if rec:
        rec["render"] = True
    return rec


LAYERS: list[tuple[str, Callable]] = [("recipe", layer_recipe), ("ats", layer_ats), ("jsonld", layer_jsonld), ("listing", layer_listing), ("rendered", layer_rendered)]


# ---------- one firm ----------

def allowed_url(url: str, site: str | None) -> bool:
    host = urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")
    if host == ANOFM_HOST or any(host == h or host.endswith("." + h) for h in ATS_HOSTS):
        return True
    return bool(site) and J._same_site(url, site)  # noqa: SLF001


def run_firm(cif: str, company: str, site: str | None, rows: list[dict], *, fetch: Callable[[str], str],
             status: Callable[[str], int], recipe: dict | None = None, previous: int | None = None,
             max_requests: int = 40, max_checks: int = 60, max_seconds: float = 120.0, max_jobs: int = 1000,
             clock: Callable[[], float] = time.monotonic, render: Callable[[str], str] | None = None, max_renders: int = 6,
             learn: bool = False, post: Callable[[str, dict], dict] | None = None) -> dict:
    t0 = clock()
    site = J.normalize_link(site if "://" in (site or "") else f"https://{site}") if site else None
    if recipe and recipe.get("budget"):  # a learned recipe knows how many pages it needs (big employers, paginated lists)
        max_requests = max(max_requests, min(500, int(recipe["budget"])))
        max_seconds = max(max_seconds, min(1800.0, int(recipe["budget"]) * 4.0))
    ctx = FirmCtx(cif, company, site, fetch, recipe, max_requests, max_seconds, clock, render, max_renders, post)
    trace, flags = [], []
    site_source = "given" if site else None
    method, own = "none", []

    def note(layer, state, n=0, err=""):
        trace.append({"layer": layer, "state": state, "n": n, **({"err": err[:120]} if err else {})})

    try:
        if not site and rows:
            ctx.site = site = M.discover_website(rows, company, ctx.get)
            site_source = "anofm_email" if site else None
        if not site:
            ctx.site = site = M.guess_website(company, cif, ctx.get)
            site_source = "guess" if site else None
        for name, fn in LAYERS:
            if name != "recipe" and not site:
                note(name, "skipped", err="no_website")
                continue
            if name == "recipe" and not ctx.recipe:
                continue
            try:
                out = fn(ctx)
            except BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - one broken doll never stops the cascade
                note(name, "error", err=f"{exc.__class__.__name__}: {exc}")
                continue
            if out is None:
                continue
            label, jobs = out
            if not jobs:
                note(name, "empty")
                continue
            ok, why = V.is_sane(jobs, max_jobs)
            if not ok:
                note(name, "rejected", len(jobs), why)
                flags.append(why)
                continue
            note(name, "ok", len(jobs))
            method, own = label, jobs
            break
    except BudgetExceeded:
        flags.append("truncated")
        note("budget", "exceeded", err=f"{ctx.requests} requests")

    learned = None
    if learn and site and "truncated" not in flags and (not own or method in ("listing", "js:listing") or "recipe_drift" in ctx.flags):
        try:  # self-adaptation: learn a recipe (or relearn a drifted one) from the pages already fetched
            crawl(ctx)
            rec = learn_from_ctx(ctx)
            if rec:
                if "recipe_drift" in ctx.flags:
                    flags.append("recipe_drift")  # record that the old recipe broke, then judge the new one on its own
                old_recipe, ctx.recipe, ctx.flags = ctx.recipe, rec, [f for f in ctx.flags if f != "recipe_drift"]
                label, jobs = layer_recipe(ctx) or ("recipe", [])
                ok, why = V.is_sane(jobs, max_jobs)
                if jobs and ok and "recipe_drift" not in ctx.flags and len(jobs) >= len(own):
                    note("learn", "ok", len(jobs))
                    method, own, learned = "recipe", jobs, rec
                    flags.append("recipe_relearned" if old_recipe else "recipe_learned")
                else:
                    ctx.recipe = old_recipe
                    note("learn", "rejected", len(jobs), why or "no_better")
            else:
                note("learn", "empty")
        except BudgetExceeded:
            flags.append("truncated")
        except Exception as exc:  # noqa: BLE001 - learning is an extra, never fatal
            note("learn", "error", err=f"{exc.__class__.__name__}: {exc}")
    flags.extend(ctx.flags)

    anofm_raw = M.parse_anofm({"rows": rows})
    anofm_urls = {r["url"] for r in anofm_raw}
    seen, merged = set(), []
    for r in [M.from_legacy(j) for j in own] + anofm_raw:
        if r["url"] not in seen:
            seen.add(r["url"])
            merged.append(r)
    models = [M.to_job_model(r, cif, company) for r in merged]
    models, bad = V.filter_valid_jobs(models)
    models = [j for j in models if j["url"] in anofm_urls or allowed_url(j["url"], site)]
    alive, dead, checks = [], [], 0
    for job in models:
        if job["url"] in anofm_urls:
            alive.append(job)
        elif checks >= max_checks:
            flags.append("checks_capped")
            dead.append(job)  # unverifiable -> not published
        else:
            checks += 1
            (alive if 0 < status(job["url"]) < 400 else dead).append(job)
    if anofm_raw and method == "none":
        method = "anofm"
    if previous is not None and previous >= 5 and not alive:
        flags.append("suspect_zero")
    return {"cif": cif, "company": company, "site": site, "site_source": site_source, "method": method,
            "layers": trace, "jobs": alive, "anofm": len(anofm_raw), "dead_links": len(dead), "invalid": len(bad),
            "learned_recipe": learned, "requests": ctx.requests, "renders": ctx.renders, "seconds": round(clock() - t0, 2), "flags": sorted(set(flags))}


# ---------- many firms ----------

def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def load_recipe(recipes_dir: str | Path | None, cif: str) -> dict | None:
    if not recipes_dir:
        return None
    p = Path(recipes_dir) / f"{cif}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None


def save_recipe(recipes_dir: str | Path, cif: str, recipe: dict) -> None:
    """Write ``<cif>.json`` atomically; the previous version is kept as ``<cif>.prev.json`` (undo for a bad relearn)."""
    d = Path(recipes_dir)
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{cif}.json"
    if target.exists():
        (d / f"{cif}.prev.json").write_bytes(target.read_bytes())
    tmp = d / f"{cif}.json.tmp"
    tmp.write_text(json.dumps(recipe, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)


def run_all(docs: list[dict], out_dir: str | Path, *, fetch, status, anofm_post, websites: dict | None = None,
            recipes_dir=None, workers: int = 1, max_firms: int | None = None, previous: dict | None = None,
            first_site: Callable[[dict], str] = lambda d: "", **limits) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    firms_path, jobs_path = out / "jobs_by_firm.jsonl", out / "jobs.jsonl"
    done = {r["cif"] for r in _read(firms_path)}
    todo = [d for d in docs if d["id"] not in done]
    if max_firms is not None:
        todo = todo[:max_firms]
    websites, previous = websites or {}, previous or {}
    lock = threading.Lock()

    def work(doc):
        try:
            rows = M.anofm_rows(doc["id"], anofm_post)
        except Exception:  # noqa: BLE001 - supplementary source, never fatal
            rows = []
        site = websites.get(doc["id"]) or first_site(doc) or None
        try:
            res = run_firm(doc["id"], doc.get("company") or "", site, rows, fetch=fetch, status=status,
                           recipe=load_recipe(recipes_dir, doc["id"]), previous=previous.get(doc["id"]), post=anofm_post, **limits)
        except Exception as exc:  # noqa: BLE001 - last net: a firm can never kill the run
            res = {"cif": doc["id"], "company": doc.get("company"), "site": site, "site_source": None, "method": "none",
                   "layers": [{"layer": "engine", "state": "error", "n": 0, "err": f"{exc.__class__.__name__}: {exc}"[:120]}],
                   "jobs": [], "anofm": 0, "dead_links": 0, "invalid": 0, "requests": 0, "seconds": 0, "flags": ["engine_error"]}
        if res.get("learned_recipe") and recipes_dir:
            save_recipe(recipes_dir, doc["id"], res["learned_recipe"])
        with lock:
            with jobs_path.open("a", encoding="utf-8") as jf:
                for job in res["jobs"]:
                    jf.write(json.dumps(job, ensure_ascii=False) + "\n")
            row = {k: v for k, v in res.items() if k not in ("jobs", "learned_recipe")}
            row["jobs"] = len(res["jobs"])
            with firms_path.open("a", encoding="utf-8") as ff:
                ff.write(json.dumps(row, ensure_ascii=False) + "\n")

    if workers <= 1:
        for d in todo:
            work(d)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, todo))
    return report(docs, out)


def report(docs: list[dict], out: Path) -> dict:
    firms = _read(out / "jobs_by_firm.jsonl")
    methods: dict[str, int] = defaultdict(int)
    flags: dict[str, int] = defaultdict(int)
    for r in firms:
        methods[r["method"]] += 1
        for f in r.get("flags") or []:
            flags[f] += 1
    counts = {r["cif"] for r in firms}
    rep = {"firms_done": len(firms), "firms_with_jobs": sum(1 for r in firms if r["jobs"]),
           "jobs_total": sum(r["jobs"] for r in firms), "dead_links": sum(r["dead_links"] for r in firms),
           "by_method": dict(methods), "flags": dict(flags), "requests": sum(r.get("requests", 0) for r in firms), "renders": sum(r.get("renders", 0) for r in firms),
           "firms_left": len([d for d in docs if d["id"] not in counts]), "dry_run": True}
    (out / "jobs_report.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    return rep
