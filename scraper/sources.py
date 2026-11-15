"""DerKaiser data sources: CUIScan (full details by CIF) + CUIFirma (search by name).

Division of labour (the two sites are not interchangeable):

* CUIScan  -> full company record by CIF. This is what fills ``anafData``,
  ``company``, ``address`` and ``status``.
* CUIFirma -> name search only (cui / name / status). Used to discover the CIF
  when only a name is known. It cannot fill a document on its own.

Rules this module enforces:

* one attempt per source, no retries (same policy as ``scraper.anaf``);
* a source being down/blocked is *not* "company not found": the firm is
  skipped and reported, never written half-filled;
* name search accepts only an exact (case-insensitive) name match, never "the
  first result" (the bug that once picked FARMEC SRL Arad for FARMEC SA Cluj);
* the CUI CUIScan returns must equal the one requested;
* a batch aborts after N consecutive "unavailable" results instead of hammering
  a site that is down;
* requests are paced by ``Throttle``.

Network calls go through ``scraper.anaf`` by default and are injectable, so
every path is testable without the network.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable

from .company_doc import build_company_doc, meets_conditions, pad_cif

log = logging.getLogger("derkaiser.sources")

OK = "ok"
UNAVAILABLE = "unavailable"
NOT_FOUND = "not_found"
AMBIGUOUS = "ambiguous"
INVALID = "invalid"
IGNORED = "ignored"


class SourceUnavailable(Exception):
    """The upstream site is down, blocked, rate-limited or returned garbage."""


@dataclass
class Outcome:
    cif: str | None
    status: str
    doc: dict | None = None
    detail: str = ""


def _default_lookup(cif: str) -> dict:
    from . import anaf

    return anaf._fetch_from_cuiscan(cif)


def _default_search(name: str) -> list[dict]:
    from . import anaf

    return anaf._search_from_cuifirma(name)


def lookup_by_cif(cif: str | int, fetcher: Callable[[str], dict] | None = None) -> dict | None:
    """CUIScan record (ANAF-shaped) for ``cif``; ``None`` when the CIF is unknown.

    Raises ``SourceUnavailable`` when the site itself failed.
    """
    fetcher = fetcher or _default_lookup
    bare = str(int(pad_cif(cif)))  # CUIScan wants the bare CUI, not the padded id
    try:
        data = fetcher(bare)
    except Exception as exc:  # noqa: BLE001 - every transport/parse failure is "unavailable"
        message = str(exc)
        lowered = message.lower()
        # CUIScan answers 400 for a CIF that fails its checksum (found live with CIF "1").
        # That is a verdict on this CIF, not an outage: retrying it forever, or counting it
        # toward the batch abort, would be wrong. 403/408/429/5xx stay "unavailable".
        if "no data" in lowered or re.search(r"api error: (400|404|422)\b", lowered):
            return None
        raise SourceUnavailable(message or exc.__class__.__name__) from exc
    if not data or not data.get("name") or data.get("cui") is None:
        return None
    return data


def search_by_name(name: str, searcher: Callable[[str], list[dict]] | None = None) -> list[dict]:
    """CUIFirma search results (``cui``, ``name``, ``statusLabel``)."""
    searcher = searcher or _default_search
    try:
        results = searcher(name)
    except Exception as exc:  # noqa: BLE001
        raise SourceUnavailable(str(exc) or exc.__class__.__name__) from exc
    return [r for r in (results or []) if isinstance(r, dict)]


def find_cif_by_name(name: str, searcher: Callable[[str], list[dict]] | None = None) -> tuple[str | None, str]:
    """``(padded_cif, "")`` on one exact name match, else ``(None, reason)``.

    ``reason`` is ``NOT_FOUND`` or ``AMBIGUOUS``. Raises ``SourceUnavailable``.
    """
    target = (name or "").strip().upper()
    if not target:
        return None, NOT_FOUND
    exact: set[str] = set()
    for row in search_by_name(name, searcher):
        row_name = row.get("name")
        cui = str(row.get("cui", "")).strip()
        if isinstance(row_name, str) and row_name.strip().upper() == target and cui.isdigit():
            exact.add(pad_cif(cui))
    if len(exact) == 1:
        return exact.pop(), ""
    return None, (AMBIGUOUS if exact else NOT_FOUND)


class Throttle:
    """Minimum gap between requests. ``clock``/``sleep`` are injectable for tests."""

    def __init__(
        self,
        min_interval: float = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            gap = self.min_interval - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
        self._last = self._clock()


def company_doc_for_cif(
    cif: str | int,
    *,
    brand: str | None = None,
    website: str | None = None,
    jobs_count: int = 0,
    today: date | None = None,
    localitate: str | None = None,
    adresa: str | None = None,
    lookup: Callable[[str], dict] | None = None,
) -> Outcome:
    """Build the 11-field document for one CIF; ``IGNORED`` if any condition fails."""
    try:
        padded = pad_cif(cif)
    except ValueError as exc:
        return Outcome(None, INVALID, detail=str(exc))

    try:
        anaf = lookup_by_cif(padded, lookup)
    except SourceUnavailable as exc:
        return Outcome(padded, UNAVAILABLE, detail=str(exc))
    if anaf is None:
        return Outcome(padded, NOT_FOUND, detail="CUIScan has no record for this CIF")

    try:
        returned = int(pad_cif(anaf["cui"]))
    except (ValueError, KeyError):
        return Outcome(padded, INVALID, detail=f"CUIScan returned an unusable cui: {anaf.get('cui')!r}")
    if returned != int(padded):
        return Outcome(padded, INVALID, detail=f"CUI mismatch: asked {int(padded)}, CUIScan returned {returned}")

    try:
        doc = build_company_doc(
            anaf,
            brand=brand,
            website=website,
            jobs_count=jobs_count,
            today=today,
            localitate=localitate,
            adresa=adresa,
        )
    except ValueError as exc:
        return Outcome(padded, INVALID, detail=str(exc))

    # Keep/ignore rule: failing even one condition means the firm is ignored.
    check = meets_conditions(doc)
    if not check["ok"]:
        return Outcome(padded, IGNORED, detail="; ".join(check["reasons"]))
    return Outcome(padded, OK, doc=doc)


def process_cifs(
    cifs: Iterable[str | int],
    *,
    lookup: Callable[[str], dict] | None = None,
    jobs_count_fn: Callable[[str], int] | None = None,
    today: date | None = None,
    throttle: Throttle | None = None,
    max_consecutive_unavailable: int = 5,
) -> dict[str, Any]:
    """Run a batch. Returns ``{"docs", "skipped", "counts", "aborted"}``.

    Duplicates (after padding) are processed once. The batch stops early after
    ``max_consecutive_unavailable`` consecutive ``UNAVAILABLE`` outcomes.
    """
    throttle = throttle or Throttle(0)
    docs: list[dict] = []
    skipped: list[dict] = []
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    streak = 0
    aborted = False

    for raw in cifs:
        try:
            key = pad_cif(raw)
        except ValueError as exc:
            counts[INVALID] += 1
            skipped.append({"cif": str(raw), "status": INVALID, "detail": str(exc)})
            continue
        if key in seen:
            continue
        seen.add(key)

        throttle.wait()
        count = jobs_count_fn(key) if jobs_count_fn else 0
        outcome = company_doc_for_cif(key, jobs_count=count, today=today, lookup=lookup)
        counts[outcome.status] += 1

        if outcome.status == OK:
            docs.append(outcome.doc)  # type: ignore[arg-type]
            streak = 0
            continue

        skipped.append({"cif": outcome.cif, "status": outcome.status, "detail": outcome.detail})
        streak = streak + 1 if outcome.status == UNAVAILABLE else 0
        if streak >= max_consecutive_unavailable:
            aborted = True
            log.error("aborting batch: %d consecutive unavailable results (source likely down)", streak)
            break

    return {"docs": docs, "skipped": skipped, "counts": dict(counts), "aborted": aborted}
