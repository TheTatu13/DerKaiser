"""peViitor job model exactly as the Brewtality 3:16 template builds it (api.peviitor.ro/v1).

Job: {url, title, company, cif (8 digits), location[], workmode, expirationdate?, date, status:"scraped"}.
ANOFM gives each CIF's public postings for free (no website needed).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Callable

API_BASE = "https://api.peviitor.ro/v1"
ANOFM_URL = "https://mediere.anofm.ro/api/entity/vw_public_job_posting"

ROMANIAN_CITIES = [
    'Bucharest', 'București', 'Bucuresti', 'Cluj-Napoca', 'Cluj Napoca', 'Timișoara', 'Timisoara', 'Iași', 'Iasi',
    'Brașov', 'Brasov', 'Constanța', 'Constanta', 'Craiova', 'Bacău', 'Sibiu', 'Târgu Mureș', 'Targu Mures', 'Oradea',
    'Baia Mare', 'Satu Mare', 'Ploiești', 'Ploiesti', 'Pitești', 'Pitesti', 'Arad', 'Galați', 'Galati', 'Brăila',
    'Braila', 'Drobeta-Turnu Severin', 'Râmnicu Vâlcea', 'Ramnicu Valcea', 'Buzău', 'Buzau', 'Botoșani', 'Botosani',
    'Zalău', 'Zalau', 'Hunedoara', 'Deva', 'Suceava', 'Bistrița', 'Bistrita', 'Tulcea', 'Călărași', 'Calarasi',
    'Giurgiu', 'Alba Iulia', 'Slatina', 'Piatra Neamț', 'Piatra Neamt', 'Roman', 'Dumbrăvița', 'Dumbravita',
    'Voluntari', 'Popești-Leordeni', 'Popesti-Leordeni', 'Chitila', 'Mogoșoaia', 'Mogosoaia', 'Otopeni', 'Dâmbovița',
    'Dambovita', 'Sighișoara', 'Sighisoara', 'Sovata', 'Reghin', 'Târnăveni', 'Tarnaveni',
]
_CITY_SET = {c.lower() for c in ROMANIAN_CITIES}


def pad_cif(cif) -> str:
    return str(cif).zfill(8)


def validate_ro_locations(locations: list[str] | None) -> list[str]:
    valid = []
    for loc in locations or []:
        lower = (loc or "").lower().strip()
        if lower in ("romania", "românia"):
            valid.append("România")
        elif lower in _CITY_SET:
            valid.append(loc.strip())
    return valid or ["România"]


def normalize_workmode(workmode: str | None) -> str | None:
    if not workmode:
        return None
    lower = workmode.lower()
    if "remote" in lower:
        return "remote"
    if "office" in lower or "on-site" in lower or "site" in lower:
        return "on-site"
    return "hybrid"


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_job_model(raw: dict, cif, company: str, now: datetime | None = None) -> dict:
    """``raw`` = {url,title,location?,workmode?,expirationdate?}. None-valued keys are dropped."""
    job = {
        "url": raw["url"], "title": raw["title"], "company": company, "cif": pad_cif(cif),
        "location": validate_ro_locations(raw.get("location")),
        "workmode": normalize_workmode(raw.get("workmode")),
        "expirationdate": raw.get("expirationdate") or None,
        "date": iso_z(now or datetime.now(timezone.utc)), "status": "scraped",
    }
    return {k: v for k, v in job.items() if v is not None}


def from_legacy(job: dict) -> dict:
    """Own-site scraper output (jobs.py: job_title/job_link/city/remote) -> raw dict for ``to_job_model``."""
    remote = job.get("remote") or []
    return {"url": job["job_link"], "title": job["job_title"],
            "location": [job["city"]] if job.get("city") else None,
            "workmode": remote[0] if remote else None}


def anofm_rows(cif, post: Callable[[str, dict], dict]) -> list[dict]:
    """Raw ANOFM rows for one CIF (``post(url, json_body) -> dict`` is injected and throttled by the caller)."""
    body = {"current": 1, "rowCount": 250, "sort": {"created_at": "desc"}, "employer_tax_code": str(cif).lstrip("0") or "0"}
    return (post(ANOFM_URL, body) or {}).get("rows") or []


def parse_anofm(data: dict) -> list[dict]:
    out = []
    for row in (data or {}).get("rows") or []:
        parts = [p.strip() for p in (row.get("address_locality_name") or "").split(">")]
        location = parts[-1] if len(parts) > 1 else (parts[0] if parts else "")
        job_id = row.get("id")
        if not job_id or not row.get("occupation"):
            continue
        out.append({"url": f"https://mediere.anofm.ro/app/module/mediere/job/{job_id}",
                    "title": row["occupation"], "location": [location] if location else None})
    return out


def search_anofm(cif, post: Callable[[str, dict], dict]) -> list[dict]:
    return parse_anofm({"rows": anofm_rows(cif, post)})


# --- website discovery: the firm's own mail domain from ANOFM contact_email, verified on its homepage ---
FREE_MAIL = {"gmail.com", "yahoo.com", "yahoo.ro", "yahoo.co.uk", "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com",
             "icloud.com", "me.com", "mail.com", "mail.ru", "gmx.com", "gmx.net", "protonmail.com", "proton.me", "zoho.com",
             "aol.com", "k.ro", "email.ro", "clicknet.ro", "upcmail.ro", "rdslink.ro", "xnet.ro", "gmail.ro", "mailinator.com"}
_LEGAL = {"srl", "sa", "sc", "snc", "scs", "pfa", "ii", "if", "srld", "sra", "ra", "the", "com", "romania", "romanian", "group", "grup",
          "compania", "nationala", "societatea", "societate", "comercial", "comerciala", "filiala", "sucursala", "international"}


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def core_tokens(company: str) -> list[str]:
    """Distinctive words of a company name (no legal form), ascii, len >= 3."""
    return [t for t in re.findall(r"[a-z0-9]+", _ascii(company)) if t not in _LEGAL and len(t) >= 3]


def candidate_domains(rows: list[dict]) -> list[str]:
    """Own-domain candidates from ANOFM contact emails, most frequent first; free-mail domains excluded."""
    counts: dict[str, int] = {}
    for row in rows:
        for m in re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", (row.get("contact_email") or "").lower()):
            if m not in FREE_MAIL:
                counts[m] = counts.get(m, 0) + 1
    return sorted(counts, key=lambda d: (-counts[d], d))



SUBPAGES = ("/contact", "/contact/", "/despre-noi", "/despre", "/termeni-si-conditii", "/politica-de-confidentialitate")


def _cif_with_context(text: str, cif: str) -> bool:
    """CIF written as 'CUI/CIF/C.U.I./Cod fiscal/RO' + number (a bare number anywhere proves nothing)."""
    return re.search(r"(?:cui|cif|c\.u\.i\.?|cod\s+(?:de\s+)?(?:identificare\s+)?fiscal|\bro)\s*[:.\-]?\s*(?:ro)?\s*0*" + re.escape(cif) + r"(?!\d)", text) is not None


def _cif_on_subpages(base_url: str, cif: str, get: Callable[[str], str]) -> bool:
    if not cif:
        return False
    for path in SUBPAGES:
        try:
            page = get(base_url.rstrip("/") + path)
        except Exception:  # noqa: BLE001 - a missing page is the normal case
            continue
        text = _ascii(re.sub(r"<[^>]+>", " ", page))
        if _cif_with_context(text, cif):
            return True
    return False



def verify_site(domain: str, company: str, get: Callable[[str], str], *, strict_cif: str | None = None) -> str | None:
    """Return ``https://domain`` only if the homepage loads AND belongs to the company.

    Normal mode (domain comes from the firm's own contact email): the domain label or the page text contains the
    company's distinctive name. Strict mode (``strict_cif`` given: the domain was only GUESSED from the name): the
    label proves nothing, so the page must show the full distinctive name AND either the firm's CIF or a Romanian
    signal (``.ro`` domain + 'romania' in the text)."""
    tokens = core_tokens(company)
    if not tokens:
        return None
    label = _ascii(domain.split(".")[0])
    full = " ".join(tokens)
    for host in (domain, "www." + domain):
        url = f"https://{host}"
        try:
            html = get(url)
        except Exception:  # noqa: BLE001
            continue
        text = _ascii(re.sub(r"<[^>]+>", " ", html))
        flat = re.sub(r"\s+", " ", text)
        if strict_cif is not None:
            cif = str(strict_cif).lstrip("0")
            named = full in flat or (len(tokens) > 1 and all(t in text for t in tokens))
            has_cif = bool(cif) and re.search(r"(?<!\d)(ro)?\s*" + re.escape(cif) + r"(?!\d)", text) is not None
            others = {n.lstrip("0") for n in re.findall(r"(?<![\w])(?:cui|cif|c\.u\.i\.?|ro)\s*:?\s*(\d{3,10})(?!\d)", text)}
            foreign_cif = bool(others) and cif not in others  # the page states another firm's CUI: same name, other firm
            if named and not foreign_cif and (has_cif or (domain.endswith(".ro") and "romania" in text)):
                return url
            if not foreign_cif and (named or any(t in label for t in tokens)):
                # the CIF is rarely on the homepage: Romanian sites state it on contact / terms / privacy pages
                if _cif_on_subpages(url, cif, get):
                    return url
            continue
        if any(t in label for t in tokens) or full in flat or (len(tokens) > 1 and all(t in text for t in tokens)):
            return url
    return None


def guess_domains(company: str, limit: int = 4) -> list[str]:
    """Plausible own domains from the distinctive name only (verified strictly before use)."""
    tokens = core_tokens(company)
    if not tokens:
        return []
    joined, dashed = "".join(tokens), "-".join(tokens)
    names = [joined] + ([dashed] if dashed != joined else []) + ([tokens[0]] if len(tokens) > 1 and len(tokens[0]) >= 5 else [])
    out = []
    for n in names:
        for tld in (".ro", ".com"):
            d = n + tld
            if d not in out:
                out.append(d)
    return out[:limit]


def guess_website(company: str, cif: str, get: Callable[[str], str], max_tries: int = 3) -> str | None:
    for domain in guess_domains(company)[:max_tries]:
        site = verify_site(domain, company, get, strict_cif=cif)
        if site:
            return site
    return None


def discover_website(rows: list[dict], company: str, get: Callable[[str], str], max_tries: int = 2) -> str | None:
    for domain in candidate_domains(rows)[:max_tries]:
        site = verify_site(domain, company, get)
        if site:
            return site
    return None
