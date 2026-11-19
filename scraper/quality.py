"""DerKaiser quality gates: only real, active, hiring-capable companies go on.

Stage 1 (this module, from ANAF data, free and fast): a firm fails if ANY gate fails.
Stage 2 (CUIScan, slow): employees >= 1 and a recent balance sheet (see ``assess_financials``).

Decided with Mihai (2026-11-13): sole traders (PFA/II/IF/AF/PF) are out; holdings (CAEN 6420)
are out (own-property rental, CAEN 6820, was re-admitted 2026-11-19); firms without jobs are kept (they may publish later).
Every gate returns a short reason string, so a report can say *why* a firm was dropped.
"""

from __future__ import annotations

import re
import unicodedata

EXCLUDED_CAEN = {"6420"}  # 6820 (own-property rental) was excluded until 2026-11-19: Mihai decided to include it
# ANAF "stare_inregistrare" texts that mean the firm operates.
# TRANSFER(SOSIRE) = the firm moved its registered office into a new county and keeps operating (Catena Pharma, Nemak, Nobel Automotive ...)
ACTIVE_PREFIXES = ("INREGISTRAT", "REINREGISTRAT", "RELUARE ACTIVITATE", "TRANSFER(SOSIRE)")
# forma_juridica must look like a company, not a person.
# 2026-11-19: cooperatives and "alte forme juridice" (research institutes, agro-industrial combines ...) are real employers: admitted
COMPANY_WORDS = ("SOCIETATE", "COMPANIE", "REGIE", "ORGANIZATIE COOPERATISTA", "ALTE FORME JURIDICE")
_NAME_PERSON = re.compile(
    r"(INTREPRINDERE\s+(INDIVIDUALA|FAMILIALA)|PERSOANA\s+FIZICA|\bPFA\b|\bP\.F\.A\b)"
    r"|(?:^|\s)(PF|AF|II|IF)\s*$"
)


def _norm(text) -> str:
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", folded).strip().upper()


def name_reasons(name: str) -> list[str]:
    """['name_sole_trader'] when the name is a PFA/II/IF-type person, else []."""
    return ["name_sole_trader"] if _NAME_PERSON.search(_norm(name)) else []


def assess_anaf(rec: dict | None, *, excluded_caen: set[str] | frozenset[str] = frozenset(EXCLUDED_CAEN)) -> list[str]:
    """Reasons a firm fails the ANAF gates; an empty list means it passes."""
    if not rec:
        return ["anaf_not_found"]
    reasons = name_reasons(rec.get("name", ""))
    stare = _norm(rec.get("stare"))
    if not stare.startswith(ACTIVE_PREFIXES):
        reasons.append("stare:" + (stare.split(" DIN ")[0] or "necunoscuta"))
    if rec.get("inactive"):
        reasons.append("inactiv_fiscal")
    forma = _norm(rec.get("forma_juridica"))
    if not any(w in forma for w in COMPANY_WORDS):
        reasons.append("forma_juridica:" + (forma or "lipsa"))
    caen = str(rec.get("caen") or "").strip()
    if caen in excluded_caen:
        reasons.append("caen_exclus:" + caen)
    return reasons


def assess_financials(employees, turnover, *, min_employees: int = 1) -> list[str]:
    """Stage 2. ``None`` means "no balance sheet": reported as ``fara_bilant`` (kept apart, not dropped silently)."""
    if employees is None and turnover is None:
        return ["fara_bilant"]
    reasons = []
    if (employees or 0) < min_employees:
        reasons.append("salariati:%s" % (employees or 0))
    if not turnover or turnover <= 0:
        reasons.append("cifra_afaceri_zero")
    return reasons
