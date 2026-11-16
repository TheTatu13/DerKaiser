"""Known career boards (``config/ats_boards.json``) -> Romanian jobs, through the ATS public JSON endpoints.

Greenhouse / Lever / Workable / ... come from ``jobs.parse_ats``; Workday from ``workday.fetch_workday``.
Only postings with a Romanian location signal survive. Read-only; ``get(url) -> text`` and ``post(url, body) -> dict`` are injected.
"""
from __future__ import annotations

import json

from . import jobs as J
from . import workday as WD

ATS_KINDS = ("greenhouse", "lever", "workable", "recruitee", "smartrecruiters", "personio")


def fetch_boards(config: dict, get, post, *, on_board=None) -> list[dict]:
    out = []
    for kind in ATS_KINDS:
        for b in config.get(kind, []):
            try:
                jobs = J.parse_ats(kind, b["company"], b["slug"], get(J.ats_url(kind, b["slug"])))
            except Exception:  # noqa: BLE001 - one broken board never stops the others
                jobs = []
            out.extend({**j, "source": f"ats:{kind}"} for j in jobs)
            if on_board:
                on_board(kind, b["company"], len(jobs))
    for b in config.get("workday", []):
        try:
            jobs = WD.fetch_workday(b["company"], b["tenant"], b["wd"], b["site"], post)
        except Exception:  # noqa: BLE001
            jobs = []
        out.extend({**j, "source": "ats:workday"} for j in jobs)
        if on_board:
            on_board("workday", b["company"], len(jobs))
    return J.dedupe(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from . import net as N

    ap = argparse.ArgumentParser(description="ATS career boards -> Romanian jobs (read-only)")
    ap.add_argument("--config", default="config/ats_boards.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=3.0)
    args = ap.parse_args(argv)
    config = json.load(open(args.config, encoding="utf-8-sig"))
    net = N.Net(interval=args.interval, timeout=40, retries=1)
    jobs = fetch_boards(config, net.get, net.post_json, on_board=lambda k, c, n: print(f"{k:11} {c:22} {n}", flush=True))
    with open(args.out, "w", encoding="utf-8") as f:
        for j in jobs:
            f.write(json.dumps(j, ensure_ascii=False) + "\n")
    print(json.dumps({"boards_jobs_romania": len(jobs)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
