"""Self-adapting scrapers: DerKaiser LEARNS a recipe per firm (data, not code) and repairs it when the site changes.

A recipe is a small JSON the engine's smallest doll executes (see engine.layer_recipe):

    {"careers_urls": [...], "ad_link_regex": "...", "pagination": {"kind": "query", "param": "page"} | null,
     "render": false, "expected_min": 24, "max_pages": 25, "budget": 60, "learned": "2026-11-15", "source": "learned"}

Learning = look at the careers pages the engine already fetched, find the FAMILY of links that are job ads (same path
prefix + same tail shape: ``/cariere/detalii/\\d+``, ``/joburi/[^/]+``), PROVE it by confirming sampled ads on their own
pages, detect how the list is paginated, and write it down. Drift = a later run finds far fewer ads than the recipe
expected (or the sampled ads stop confirming) -> the recipe is relearned and the old one kept as ``<cif>.prev.json``.
"""

from __future__ import annotations

import datetime
import re
import urllib.parse
from collections import defaultdict
from typing import Callable

from . import jobs as J

GENERIC_ANCHOR = re.compile(
    r"^(detalii|vezi|vezi detalii|vezi job|details|view|view job|view details|apply|aplica|aplica acum|apply now|citeste|"
    r"citeste mai mult|read more|mai multe|more|see more|click aici|afla mai multe|learn more)\W*$", re.I)
JOBISH_PREFIX = re.compile(r"(cariere|careers?|joburi|jobs?|locuri-de-munca|kariera|recrutare|pozitii|posturi|vacan|oferte?-de-munca)", re.I)
PAGE_KEYS = ("page", "pagina", "p", "pg", "pageno", "paged")


def anchors(html: str, base: str, site: str) -> list[tuple[str, str, str]]:
    """Same-site ``(normalized url, anchor text, raw tag attributes)`` for every real link on the page."""
    out = []
    for m in re.finditer(r'<a\b([^>]*?)href=["\']([^"\'#][^"\']*)["\']([^>]*)>(.*?)</a>', html or "", re.I | re.S):
        href = m.group(2)
        if href.lower().startswith(("mailto:", "tel:", "javascript:")) or J._FILE_EXT.search(href):  # noqa: SLF001
            continue
        url = J.normalize_link(urllib.parse.urljoin(base, href))
        if url and J._same_site(url, site):  # noqa: SLF001
            out.append((url, J.html_to_text(m.group(4))[:160], (m.group(1) + " " + m.group(3)).lower()))
    return out


def trusted_title(text: str) -> bool:
    t = (text or "").strip()
    return 3 <= len(t) <= 90 and not GENERIC_ANCHOR.match(t) and not t.endswith(".")


def signature(url: str) -> tuple[str, str] | None:
    segs = [s for s in urllib.parse.urlsplit(url).path.split("/") if s]
    if not segs:
        return None
    last = segs[-1]
    kind = "num" if last.isdigit() else ("slugid" if re.search(r"-\d{3,}$", last) else "slug")
    return "/" + "/".join(segs[:-1]), kind


def regex_for(prefix: str, kind: str) -> str:
    tail = {"num": r"\d+", "slugid": r"[^/?#]*-\d{3,}", "slug": r"[^/?#]+"}[kind]
    return rf"^https://[^/]+{re.escape(prefix)}/{tail}(?:\?.*)?$"


def detect_pagination(pages: dict[str, str], site: str) -> dict | None:
    for url, html in pages.items():
        found = anchors(html, url, site)
        if any(re.search(r'rel=["\']?next', attrs) for _, _, attrs in found):
            return {"kind": "next"}
        for u, _, _ in found:
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query))
            for key in PAGE_KEYS:
                if q.get(key, "").isdigit():
                    return {"kind": "query", "param": key}
            m = re.search(r"/(page|pagina|pagini)/(\d+)/?$", urllib.parse.urlsplit(u).path)
            if m:
                return {"kind": "path", "segment": m.group(1)}
    return None


def next_page_url(base: str, current: str, pagination: dict | None, n: int, html: str, site: str) -> str | None:
    """URL of page ``n + 1`` of the list that starts at ``base`` (None when there is no next page)."""
    if not pagination:
        return None
    kind = pagination.get("kind")
    parts = urllib.parse.urlsplit(base)
    if kind == "query":
        q = dict(urllib.parse.parse_qsl(parts.query))
        q[pagination["param"]] = str(n + 1)
        return J.normalize_link(urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(q), "")))
    if kind == "path":
        path = re.sub(rf"/{pagination['segment']}/\d+/?$", "", parts.path.rstrip("/"))
        return J.normalize_link(urllib.parse.urlunsplit((parts.scheme, parts.netloc, f"{path}/{pagination['segment']}/{n + 1}", parts.query, "")))
    if kind == "next":
        for u, _, attrs in anchors(html, current, site):
            if re.search(r'rel=["\']?next', attrs):
                return u
    return None


def learn_recipe(company: str, pages: dict[str, str], site: str, get_ad: Callable[[str], str], *,
                 min_group: int = 3, sample: int = 5, today: datetime.date | None = None) -> dict | None:
    """Recipe for this firm from its careers pages, or ``None`` if no family of links can be PROVEN to be job ads."""
    groups: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    sources: dict[tuple[str, str], set[str]] = defaultdict(set)
    for page_url, html in pages.items():
        for url, text, _ in anchors(html, page_url, site):
            path = urllib.parse.urlsplit(url).path
            sig = signature(url)
            if not sig or url == J.normalize_link(page_url) or J._is_stop_path(path) or not J.is_ad_link(url):  # noqa: SLF001
                continue
            groups[sig].setdefault(url, text)
            sources[sig].add(page_url)
    for sig in sorted((g for g in groups if JOBISH_PREFIX.search(g[0])), key=lambda s: -len(groups[s]))[:3]:
        links = list(groups[sig])  # only families under a careers/jobs path: /credite/<slug> (products) can never be a recipe
        if len(links) < min_group:
            break
        step = max(1, len(links) // sample)
        picked = links[::step][:sample]
        ok = 0
        for url in picked:
            try:
                ok += J.confirm_ad(company, url, get_ad(url)) is not None
            except Exception:  # noqa: BLE001
                continue
        if ok < max(2, -(-len(picked) * 6 // 10)):  # at least 60% of the sample (and >= 2) must prove to be ads
            continue
        pagination = detect_pagination({u: pages[u] for u in sources[sig]}, site)
        max_pages = 25 if pagination else 1
        return {"careers_urls": sorted(sources[sig]), "ad_link_regex": regex_for(*sig), "pagination": pagination,
                "render": False, "expected_min": len(links), "max_pages": max_pages,
                "budget": min(500, 12 + max_pages * 2 + len(picked)),
                "learned": (today or datetime.date.today()).isoformat(), "source": "learned"}
    return None
