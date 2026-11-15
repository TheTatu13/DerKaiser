"""DerKaiser company document: the full peViitor company-core contract.

DerKaiser is a multi-company scraper, so unlike the single-company template
modules it builds the *whole* company-core document itself and refuses to
emit one that is missing any field. The contract (11 fields) was read from a
real document (Inviitor-created) and from the company.json snapshots in the
fleet:

    id, company, brand, status, location[], website[], lastScraped,
    scraperFile, address[], anafData[<one JSON string>], existingJobsCount[<int>]

Alignment with the Brewtality fleet: ``id`` is the CIF zero-padded to 8
digits (``01559737``), NOT the bare CUI (``1559737``). Inviitor's older
documents are unpadded, which is what creates duplicate company docs; this
module never produces an unpadded id.

Pure functions only (no network, no config import) so they are trivially
testable and safe to call from any adapter.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

SCRAPER_FILE = "derkaiser-python-scraper"
CIF_WIDTH = 8
STATUSES = ("activ", "suspendat", "inactiv", "radiat")

REQUIRED_FIELDS = (
    "id",
    "company",
    "brand",
    "status",
    "location",
    "website",
    "lastScraped",
    "scraperFile",
    "address",
    "anafData",
    "existingJobsCount",
)

_LOCALITY_RE = re.compile(r"^(municipiul|ora[sș]ul|comuna|satul|sat|sectorul)\b", re.IGNORECASE)
_ABBREVIATIONS = (
    (re.compile(r"^MUN\.?\s+", re.IGNORECASE), "Municipiul "),
    (re.compile(r"^OR[SȘ]\.?\s+", re.IGNORECASE), "Orașul "),
    (re.compile(r"^COM\.?\s+", re.IGNORECASE), "Comuna "),
)


def pad_cif(cif: str | int) -> str:
    """CIF/CUI as the fleet writes it: digits only, ``RO`` prefix dropped, zero-padded to 8.

    Longer values (9-10 digit CUIs) are kept whole; nothing is ever truncated.
    """
    text = str(cif).strip().upper()
    if text.startswith("RO"):
        text = text[2:]
    text = text.strip()
    if not text.isdigit():
        raise ValueError(f"CIF must be numeric, got {cif!r}")
    # "0001559737" and "01559737" are the same firm: drop surplus zeros, then pad to 8.
    return (text.lstrip("0") or "0").zfill(CIF_WIDTH)


def status_from_anaf(anaf: dict) -> str:
    """``activ`` unless ANAF flags the company inactive."""
    return "inactiv" if anaf.get("inactive") else "activ"


# "Sat Recea Com. Vârșolț" / "Sat Rahău Mun. Sebeș": the administrative unit is the
# abbreviated tail (commune / municipality), not the village in front of it.
_INNER_ABBREV = re.compile(r"\b(mun|com|or[sș])\.\s+(.+)$", re.IGNORECASE)

_CEDILLA = str.maketrans({"ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț"})


def _title_ro(text: str) -> str:
    return text.strip().title()


def _normalize_place(text: str) -> str:
    """peViitor place style: comma-below diacritics (ș/ț, not ş/ţ) and Title Case.

    ANAF/CUIScan return e.g. ``MUNICIPIUL BUCUREŞTI`` (upper case, cedilla). Text that
    is already mixed case is left alone apart from the diacritic fix.
    """
    text = text.strip().translate(_CEDILLA)
    return text.title() if text.isupper() else text


def locality_from_address(address: str | None) -> tuple[str, str]:
    """Best-effort ``(localitate, adresa)`` from an ANAF/CUIScan address string.

    ``"DUMBRĂVII, 101, Municipiul Sibiu, Sibiu"`` -> ``("Municipiul Sibiu", "Municipiul Sibiu, Sibiu")``.
    Also understands the older abbreviated form (``"JUD. CLUJ, MUN. CLUJ-NAPOCA, STR. X"``).
    Returns ``("", "")`` when no locality can be found: the caller must then
    supply one explicitly, because ``location``/``address`` may not be empty.
    """
    if not address:
        return "", ""
    parts = [_normalize_place(p) for p in address.split(",") if p.strip()]

    county = ""
    for part in parts:
        match = re.match(r"^JUD\.?\s+(.+)$", part, re.IGNORECASE)
        if match:
            county = _title_ro(match.group(1))

    for i in range(len(parts) - 1, -1, -1):
        part = parts[i]
        inner = _INNER_ABBREV.search(part)
        if inner and inner.start() > 0:
            raw = inner.group(1).lower()
            full = "Municipiul" if raw == "mun" else "Comuna" if raw == "com" else "Orașul"
            part = f"{full} {inner.group(2).strip()}"
        for pattern, full in _ABBREVIATIONS:
            if pattern.match(part):
                part = full + _title_ro(pattern.sub("", part))
                break
        if _LOCALITY_RE.match(part):
            if not county and i + 1 < len(parts) and not _LOCALITY_RE.match(parts[i + 1]):
                county = parts[i + 1]
            return part, (f"{part}, {county}" if county else part)
    return "", ""


def build_company_doc(
    anaf: dict,
    *,
    brand: str | None = None,
    website: str | None = None,
    jobs_count: int = 0,
    today: date | None = None,
    localitate: str | None = None,
    adresa: str | None = None,
) -> dict[str, Any]:
    """Assemble the 11-field company document from an ANAF-shaped record."""
    cui = anaf.get("cui")
    name = (anaf.get("name") or "").strip()
    if cui is None or not name:
        raise ValueError("ANAF record needs both 'cui' and 'name'")

    guessed_loc, guessed_addr = locality_from_address(anaf.get("address"))
    localitate = localitate or guessed_loc
    adresa = adresa or guessed_addr or localitate

    company = name.upper()
    brand = (brand or company).strip()
    status = status_from_anaf(anaf)
    site = (website or "").strip()
    number = int(pad_cif(cui))

    anaf_data = json.dumps(
        {
            "cif": number,
            "cui": number,
            "denumire": name,
            "company": company,
            "brand": brand,
            "statusImpozit": status,
            "adresa": adresa,
            "localitate": localitate,
            "website": site,
        },
        ensure_ascii=False,
    )

    return {
        "id": pad_cif(cui),
        "company": company,
        "brand": brand,
        "status": status,
        "location": [localitate] if localitate else [],
        "website": [site] if site else [],
        "lastScraped": (today or date.today()).isoformat(),
        "scraperFile": SCRAPER_FILE,
        "address": [adresa] if adresa else [],
        "anafData": [anaf_data],
        "existingJobsCount": [int(jobs_count)],
    }


def _problems(doc: dict) -> list[tuple[str, str]]:
    """``(field, message)`` for every condition the document fails."""
    problems: list[tuple[str, str]] = []
    for name in REQUIRED_FIELDS:
        if name not in doc or doc[name] is None:
            problems.append((name, f"Missing field: {name}"))
    if problems:
        return problems

    cif = doc["id"]
    if not (isinstance(cif, str) and cif.isdigit() and len(cif) >= CIF_WIDTH):
        problems.append(("id", f"id must be a numeric string padded to {CIF_WIDTH} digits, got {cif!r}"))

    for name in ("company", "brand", "scraperFile"):
        if not (isinstance(doc[name], str) and doc[name].strip()):
            problems.append((name, f"{name} must be a non-empty string"))

    if doc["status"] not in STATUSES:
        problems.append(("status", f"status {doc['status']!r} not in {', '.join(STATUSES)}"))

    for name in ("location", "address"):
        value = doc[name]
        if not (isinstance(value, list) and value and all(isinstance(v, str) and v.strip() for v in value)):
            problems.append((name, f"{name} must be a non-empty list of non-empty strings"))

    if not (isinstance(doc["website"], list) and all(isinstance(v, str) for v in doc["website"])):
        problems.append(("website", "website must be a list of strings (may be empty)"))

    try:
        datetime.strptime(str(doc["lastScraped"]), "%Y-%m-%d")
    except ValueError:
        problems.append(("lastScraped", f"lastScraped must be YYYY-MM-DD, got {doc['lastScraped']!r}"))

    count = doc["existingJobsCount"]
    if not (
        isinstance(count, list)
        and len(count) == 1
        and isinstance(count[0], int)
        and not isinstance(count[0], bool)
        and count[0] >= 0
    ):
        problems.append(("existingJobsCount", "existingJobsCount must be a list with one non-negative integer"))

    blob = doc["anafData"]
    if not (isinstance(blob, list) and len(blob) == 1 and isinstance(blob[0], str)):
        problems.append(("anafData", "anafData must be a list holding one JSON string"))
    else:
        try:
            parsed = json.loads(blob[0])
        except ValueError:
            problems.append(("anafData", "anafData[0] is not valid JSON"))
        else:
            if not isinstance(parsed, dict) or "cui" not in parsed:
                problems.append(("anafData", "anafData[0] must be a JSON object with 'cui'"))
            elif isinstance(cif, str) and cif.isdigit() and int(cif) != parsed.get("cui"):
                problems.append(("anafData", "anafData cui does not match id"))
    return problems


def validate_company_doc(doc: dict | None) -> dict[str, Any]:
    """Strict check: every one of the 11 fields must be present and well-formed.

    Returns ``{"valid": bool, "errors": [str, ...], "failed_fields": [str, ...]}``.
    """
    if not isinstance(doc, dict):
        return {"valid": False, "errors": ["No company document provided"], "failed_fields": []}
    problems = _problems(doc)
    return {
        "valid": not problems,
        "errors": [message for _, message in problems],
        "failed_fields": sorted({field for field, _ in problems}),
    }


def meets_conditions(doc: dict | None) -> dict[str, Any]:
    """The keep/ignore rule: a firm is kept only if EVERY condition holds.

    Failing even one condition means the firm is ignored. On top of the
    well-formedness checks, ``status`` must be exactly ``"activ"``: an inactive
    company is ignored, not written.

    Returns ``{"ok": bool, "failed": [field, ...], "reasons": [str, ...]}``.
    """
    result = validate_company_doc(doc)
    failed = list(result["failed_fields"])
    reasons = list(result["errors"])
    if isinstance(doc, dict) and doc.get("status") in STATUSES and doc["status"] != "activ":
        failed.append("status")
        reasons.append(f"status must be 'activ', got {doc['status']!r}")
    return {"ok": not reasons, "failed": sorted(set(failed)), "reasons": reasons}
