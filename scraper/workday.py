"""Workday career sites (``<tenant>.wd<N>.myworkdayjobs.com/<site>``) -> fleet-format jobs.

Public JSON endpoint (no login), verified live on a real tenant: ``POST /wday/cxs/<tenant>/<site>/jobs`` with
``{"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "Romania"}`` -> ``{total, jobPostings: [{title,
externalPath, locationsText, postedOn, bulletFields}]}``. The ad link is ``https://<host>/<site><externalPath>``.
Only Romanian postings survive ``build_job`` (location signal check). ``post(url, body) -> dict`` is injected.
"""
from __future__ import annotations

import re
from typing import Callable

from . import jobs as J

WD_RX = re.compile(r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)", re.I)
_NOT_SITE = {"wday", "job", "details", "login"}
PAGE = 20  # Workday rejects a larger "limit" on most tenants


def detect_workday(text: str) -> tuple[str, str, str] | None:
    for m in WD_RX.finditer(text or ""):
        tenant, wd, site = m.group(1), m.group(2).lower(), m.group(3)
        if site.lower() not in _NOT_SITE:
            return tenant, wd, site
    return None


def endpoint(tenant: str, wd: str, site: str) -> str:
    return f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"


def _path_location(path: str) -> str:
    parts = [p for p in (path or "").split("/") if p]
    return parts[1].replace("-", " ") if len(parts) > 2 and parts[0] == "job" else ""


def parse_workday(company: str, tenant: str, wd: str, site: str, data: dict) -> list[dict]:
    jobs = []
    for p in (data or {}).get("jobPostings") or []:
        path = p.get("externalPath") or ""
        link = f"https://{tenant}.{wd}.myworkdayjobs.com/{site}{path}" if path else ""
        jobs.append(J.build_job(company, p.get("title") or "", link,
                                [p.get("locationsText") or "", _path_location(path)], [p.get("locationsText") or ""]))
    return J.dedupe(jobs)


def fetch_workday(company: str, tenant: str, wd: str, site: str, post: Callable[[str, dict], dict], *,
                  term: str = "Romania", max_pages: int = 40) -> list[dict]:
    """All postings matching ``term`` (Workday free-text search), paged until the reported total or ``max_pages``."""
    url, jobs, offset, total = endpoint(tenant, wd, site), [], 0, None
    for _ in range(max_pages):
        data = post(url, {"appliedFacets": {}, "limit": PAGE, "offset": offset, "searchText": term})
        batch = (data or {}).get("jobPostings") or []
        if total is None:
            total = int((data or {}).get("total") or 0)  # only the first page reports the real total
        jobs += parse_workday(company, tenant, wd, site, data)
        offset += PAGE
        if not batch or offset >= total:
            break
    return J.dedupe(jobs)
