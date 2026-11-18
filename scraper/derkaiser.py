"""DerKaiser command line.  Dry-run only: it writes local files, never Solr/peViitor.

    py -m scraper.derkaiser --cifs cifs.txt --out out_run1 [--max 200] [--pause 3]

Exit codes: 0 finished, 2 aborted because a source looked down (re-run later to resume).
"""

from __future__ import annotations

import argparse
import json
import sys

from . import batch, reconcile
from . import sources as src


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="derkaiser",
        description="Build company documents for many CIFs (dry-run: local files only).",
    )
    parser.add_argument("--discover", action="store_true", help="grow the CIF list: CUIFirma and CUIScan feed each other (writes discovered.jsonl, cifs.txt in --out)")
    parser.add_argument("--screen", action="store_true", help="screen discovered CIFs through ANAF (100 per request): writes screened.jsonl, cifs_screened.txt in --out")
    parser.add_argument("--financials", action="store_true", help="employees/turnover check (CUIScan financials) on cifs_screened.txt: writes financials.jsonl, cifs_final.txt in --out")
    parser.add_argument("--jobs", default=None, metavar="DOCS_JSONL", help="find each firm's own job ads (ATS feeds, JobPosting) for the documents in DOCS_JSONL: writes jobs.jsonl, docs_with_jobs.jsonl in --out")
    parser.add_argument("--websites", default=None, help="CSV cif,website to use for --jobs when a document has no website")
    parser.add_argument("--workers", type=int, default=6, help="--jobs: firms processed in parallel (each HOST is still hit one request at a time)")
    parser.add_argument("--interval", type=float, default=3.0, help="--jobs: minimum seconds between two requests to the same host")
    parser.add_argument("--learn", action="store_true", help="--jobs: LEARN a recipe per firm (list pages, ad-link pattern, pagination) and repair it when a site changes; recipes are written to --recipes (default: recipes)")
    parser.add_argument("--no-render", action="store_true", help="--jobs: do NOT render JavaScript pages (default: render with the Chrome/Edge already on the PC when static layers find nothing)")
    parser.add_argument("--render-parallel", type=int, default=2, help="--jobs: browsers running at once")
    parser.add_argument("--recipes", default=None, help="--jobs: folder with per-firm recipes <cif>.json (the smallest doll)")
    parser.add_argument("--cache", default=None, help="--jobs: folder to cache fetched pages (reruns do not refetch)")
    parser.add_argument("--seeds", default=None, help="file with seed queries for --discover (default: the 42 counties)")
    parser.add_argument("--cifs", required=False, help="file with one CIF per line (# comments allowed)")
    parser.add_argument("--out", required=True, help="output folder (docs.jsonl, progress.jsonl, report.json)")
    parser.add_argument("--max", type=int, default=None, help="process at most N new firms this run")
    parser.add_argument("--pause", type=float, default=60.0, help="seconds between CUIScan requests (default 60: the site allows 100/hour)")
    parser.add_argument("--max-unavailable", type=int, default=5, help="abort after N consecutive source failures")
    parser.add_argument("--existing", default=None, help="JSONL of existing company documents: adds plan.jsonl (create/update/skip/conflict)")
    args = parser.parse_args(argv)

    if args.jobs:
        import csv

        from . import publish

        sites = {}
        if args.websites:
            with open(args.websites, encoding="utf-8", newline="") as fh:
                sites = {r[0].strip().zfill(8): r[1].strip() for r in csv.reader(fh) if len(r) >= 2 and r[0].strip().isdigit()}
        from .net import Net

        from .render import Renderer

        net = Net(interval=args.interval, cache_dir=args.cache, max_bytes=40_000_000)  # big ATS feeds must not be cut (invalid JSON)
        renderer = None if args.no_render else Renderer(allowed=net.allowed, max_parallel=args.render_parallel)
        if renderer is not None and not renderer.available:
            print("no Chrome/Edge found: JavaScript rendering is off (set DERKAISER_BROWSER to a browser .exe)")
            renderer = None
        rep = publish.run_jobs(args.jobs, args.out, websites=sites, max_firms=args.max, net=net, workers=args.workers,
                               recipes_dir=args.recipes or ("recipes" if args.learn else None), render=renderer, learn=args.learn)
        rep["net"] = net.stats
        if renderer is not None:
            rep["render"] = renderer.stats
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return 0
    if args.financials:
        import pathlib

        from . import financials, sources

        cifs = batch.read_cif_list(str(pathlib.Path(args.out) / "cifs_screened.txt"))
        rep = financials.screen_financials(cifs, args.out, throttle=sources.Throttle(args.pause), max_firms=args.max)
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return 0 if rep["stop_reason"] in ("finished", "max_firms", "hourly_budget_exhausted") else 2
    if args.screen:
        from . import anaf_batch, discovery

        rows = discovery._load_jsonl(__import__("pathlib").Path(args.out) / "discovered.jsonl")
        cifs = [r["cif"] for r in rows if r.get("active") is not False]
        rep = anaf_batch.screen_cifs(cifs, args.out)
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return 2 if rep["aborted"] else 0
    if args.discover:
        from . import discovery

        seeds = [l.strip() for l in open(args.seeds, encoding="utf-8")] if args.seeds else None
        rep = discovery.run_discovery(args.out, seeds=seeds, max_queries=args.max,
                                      max_consecutive_unavailable=args.max_unavailable)
        rep["cifs_exported"] = discovery.export_cifs(args.out)
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return 0
    if not args.cifs:
        parser.error("--cifs is required unless --discover is used")
    cifs = batch.read_cif_list(args.cifs)
    report = batch.run_batch(
        cifs,
        args.out,
        throttle=src.Throttle(args.pause),
        max_firms=args.max,
        max_consecutive_unavailable=args.max_unavailable,
        existing_index=reconcile.load_existing_index(args.existing) if args.existing else None,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 2 if report["aborted"] else 0


if __name__ == "__main__":
    sys.exit(main())
