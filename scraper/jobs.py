"""DerKaiser jobs layer: find a firm's own job ads, each with its own ad link.

Independent of peViitor and of the fleet. Sources, cleanest first:

1. public ATS feeds (Greenhouse, Lever, Workable, Recruitee, SmartRecruiters, Personio),
2. schema.org ``JobPosting`` JSON-LD in the firm's careers pages,
3. generic listing scraper: links on the careers pages that lead to ad pages, each CONFIRMED by fetching the
   ad page (JobPosting JSON-LD, or an h1 title plus job-ad wording); static HTML only, no JavaScript rendering,
4. nothing else: a job without a valid ad link is dropped, never guessed.

Output format = the fleet's job format (``job_title``, ``job_link``, ``company``, ``country``,
``city``, ``county``, ``remote``) so the jobs are accepted wherever fleet jobs are.

Everything here is pure (text in, jobs out) except ``scrape_company_jobs``, whose network access
is injected (``get``), so the whole module is testable offline. ATS payload shapes other than
Greenhouse/Lever follow public community references and must be re-checked on a live response
before relying on them (see DERKAISER.md).
"""

from __future__ import annotations

import html as _html
import json
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Callable, Iterable

COUNTRY = "Romania"
TRACKING = re.compile(r"^(utm_|gclid|fbclid|gh_src|lever-source|source$|ref$|trk)", re.I)

# city (ascii, lower) -> (display name, county). Romanian cities that actually show up in job ads.
CITIES = {
    "bucuresti": ("Bucuresti", "Bucuresti"), "bucharest": ("Bucuresti", "Bucuresti"),
    "cluj-napoca": ("Cluj-Napoca", "Cluj"), "cluj napoca": ("Cluj-Napoca", "Cluj"), "cluj": ("Cluj-Napoca", "Cluj"),
    "timisoara": ("Timisoara", "Timis"), "iasi": ("Iasi", "Iasi"), "brasov": ("Brasov", "Brasov"),
    "constanta": ("Constanta", "Constanta"), "craiova": ("Craiova", "Dolj"), "sibiu": ("Sibiu", "Sibiu"),
    "oradea": ("Oradea", "Bihor"), "arad": ("Arad", "Arad"), "pitesti": ("Pitesti", "Arges"),
    "ploiesti": ("Ploiesti", "Prahova"), "galati": ("Galati", "Galati"), "braila": ("Braila", "Braila"),
    "bacau": ("Bacau", "Bacau"), "suceava": ("Suceava", "Suceava"), "baia mare": ("Baia Mare", "Maramures"),
    "targu mures": ("Targu-Mures", "Mures"), "targu-mures": ("Targu-Mures", "Mures"),
    "satu mare": ("Satu Mare", "Satu Mare"), "ramnicu valcea": ("Ramnicu Valcea", "Valcea"),
    "buzau": ("Buzau", "Buzau"), "botosani": ("Botosani", "Botosani"), "piatra neamt": ("Piatra-Neamt", "Neamt"),
    "deva": ("Deva", "Hunedoara"), "alba iulia": ("Alba Iulia", "Alba"), "focsani": ("Focsani", "Vrancea"),
    "targoviste": ("Targoviste", "Dambovita"), "bistrita": ("Bistrita", "Bistrita-Nasaud"),
    "resita": ("Resita", "Caras-Severin"), "slatina": ("Slatina", "Olt"), "tulcea": ("Tulcea", "Tulcea"),
    "zalau": ("Zalau", "Salaj"), "vaslui": ("Vaslui", "Vaslui"), "giurgiu": ("Giurgiu", "Giurgiu"),
    "slobozia": ("Slobozia", "Ialomita"), "calarasi": ("Calarasi", "Calarasi"), "targu jiu": ("Targu Jiu", "Gorj"),
    "miercurea ciuc": ("Miercurea Ciuc", "Harghita"), "sfantu gheorghe": ("Sfantu Gheorghe", "Covasna"),
    "alexandria": ("Alexandria", "Teleorman"), "drobeta-turnu severin": ("Drobeta-Turnu Severin", "Mehedinti"),
    "voluntari": ("Voluntari", "Ilfov"), "otopeni": ("Otopeni", "Ilfov"), "popesti-leordeni": ("Popesti-Leordeni", "Ilfov"),
    "floresti": ("Floresti", "Cluj"), "sectorul 1": ("Bucuresti", "Bucuresti"),
}
_LISTING_PATHS = {"", "/", "/jobs", "/job", "/careers", "/cariere", "/joburi", "/locuri-de-munca", "/kariera"}
_CAREER_WORDS = re.compile(r"(cariere|careers?|joburi|jobs?|locuri[-_ ]de[-_ ]munca|kariera|recrutare|work-with-us)", re.I)


def _ascii(text) -> str:
    return unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower().strip()


def html_to_text(value) -> str:
    """Strip tags and entities and collapse whitespace."""
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


# ---------- location / remote ----------

def infer_city(*values) -> str | None:
    """Return the display name of the longest known Romanian city mentioned in the given texts, or None."""
    text = " ".join(_ascii(v) for v in values if v)
    best = None
    for alias in sorted(CITIES, key=len, reverse=True):
        if re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", text):
            best = CITIES[alias][0]
            break
    return best


def single_city(*values) -> str | None:
    """The city only if exactly ONE distinct city is mentioned (a page listing many cities gives no location)."""
    text = " ".join(_ascii(v) for v in values if v)
    hits = {CITIES[a][0] for a in CITIES if re.search(r"(?<![a-z])" + re.escape(a) + r"(?![a-z])", text)}
    return hits.pop() if len(hits) == 1 else None


def county_of(city: str | None) -> list[str]:
    """County (as a one-item list) of a known city, else an empty list."""
    if not city:
        return []
    for display, county in CITIES.values():
        if display == city:
            return [county]
    return []


def has_romania_signal(*values) -> bool:
    """True when the texts mention Romania or a known Romanian city."""
    text = " ".join(_ascii(v) for v in values if v)
    return "romania" in text or infer_city(*values) is not None


def infer_remote(*values) -> list[str]:
    """Work mode from free text: ['hybrid'], ['remote'] or []."""
    text = " ".join(_ascii(v) for v in values if v)
    if "hybrid" in text or "hibrid" in text:
        return ["hybrid"]
    if re.search(r"remote|telecommute|work from home|la distanta", text):
        return ["remote"]
    return []


# ---------- links ----------

def normalize_link(link: str) -> str:
    """https, no fragment, no tracking parameters, no trailing slash noise."""
    parts = urllib.parse.urlsplit((link or "").strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)]
    path = parts.path.rstrip("/") if parts.path != "/" else "/"
    return urllib.parse.urlunsplit(("https", parts.netloc.lower(), path, urllib.parse.urlencode(query), ""))


def is_ad_link(link: str) -> bool:
    """A link to ONE ad: valid, and not a bare listing page like /careers."""
    clean = normalize_link(link)
    if not clean:
        return False
    parts = urllib.parse.urlsplit(clean)
    return parts.path.lower() not in _LISTING_PATHS


# ---------- job building ----------

def build_job(company, title, link, location_signals, remote_signals=None) -> dict | None:
    """Fleet-format job, or ``None`` (no title, not an ad link, or not in Romania)."""
    link = normalize_link(link)
    if not (title or "").strip() or not is_ad_link(link) or not has_romania_signal(*location_signals):
        return None
    city = infer_city(*location_signals)
    return {
        "job_title": title.strip(), "job_link": link, "company": company, "country": COUNTRY,
        "city": city or [], "county": county_of(city), "remote": infer_remote(*(remote_signals or [])),
    }


def dedupe(jobs: Iterable[dict | None]) -> list[dict]:
    """Drop jobs with a repeated `job_link`, keeping the first."""
    seen, out = set(), []
    for job in jobs:
        if job and job["job_link"] not in seen:
            seen.add(job["job_link"])
            out.append(job)
    return out


# ---------- ATS parsers (payload -> jobs) ----------

def parse_greenhouse(company: str, data: dict) -> list[dict]:
    """Jobs from a Greenhouse board JSON."""
    jobs = []
    for j in (data or {}).get("jobs", []):
        meta = [m.get("value", "") for m in j.get("metadata") or [] if isinstance(m, dict)
                and any(w in _ascii(m.get("name")) for w in ("location", "office"))]
        jobs.append(build_job(company, j.get("title") or "", j.get("absolute_url") or "",
                              [(j.get("location") or {}).get("name") or "", *meta], [html_to_text(j.get("content"))]))
    return dedupe(jobs)


def parse_lever(company: str, data: list) -> list[dict]:
    """Jobs from a Lever postings JSON."""
    jobs = []
    for j in data or []:
        cat = j.get("categories") or {}
        jobs.append(build_job(company, j.get("text") or "", j.get("hostedUrl") or "",
                              [cat.get("location") or "", " ; ".join(cat.get("allLocations") or [])],
                              [j.get("workplaceType") or "", html_to_text(j.get("description"))]))
    return dedupe(jobs)


def parse_workable(company: str, data: dict) -> list[dict]:
    """Jobs from a Workable widget JSON."""
    jobs = []
    for j in (data or {}).get("jobs", []):
        remote = ["remote"] if j.get("telecommuting") else []
        job = build_job(company, j.get("title") or "", j.get("url") or j.get("shortlink") or "",
                        [j.get("city") or "", j.get("country") or "", j.get("state") or ""], [])
        if job and remote:
            job["remote"] = remote
        jobs.append(job)
    return dedupe(jobs)


def parse_recruitee(company: str, data: dict) -> list[dict]:
    """Jobs from a Recruitee offers JSON."""
    jobs = []
    for j in (data or {}).get("offers", []):
        remote = ["remote"] if j.get("remote") else []
        job = build_job(company, j.get("title") or "", j.get("careers_url") or "",
                        [j.get("city") or "", j.get("country") or "", j.get("location") or ""], [])
        if job and remote:
            job["remote"] = remote
        jobs.append(job)
    return dedupe(jobs)


def parse_smartrecruiters(company: str, slug: str, data: dict) -> list[dict]:
    """Jobs from a SmartRecruiters postings JSON."""
    jobs = []
    for j in (data or {}).get("content", []):
        loc = j.get("location") or {}
        link = j.get("postingUrl") or (f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}" if j.get("id") else "")
        job = build_job(company, j.get("name") or "", link, [loc.get("city") or "", loc.get("country") or ""], [])
        if job and loc.get("remote"):
            job["remote"] = ["remote"]
        jobs.append(job)
    return dedupe(jobs)


def parse_personio(company: str, slug: str, xml_text: str) -> list[dict]:
    """Jobs from a Personio XML feed."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    jobs = []
    for pos in root.iter("position"):
        pid = (pos.findtext("id") or "").strip()
        jobs.append(build_job(company, pos.findtext("name") or "",
                              f"https://{slug}.jobs.personio.de/job/{pid}" if pid else "",
                              [pos.findtext("office") or ""], [pos.findtext("schedule") or ""]))
    return dedupe(jobs)


# ---------- ATS detection + endpoints ----------

_ATS_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([a-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([a-z0-9_-]+)", re.I)),
    ("recruitee", re.compile(r"([a-z0-9-]+)\.recruitee\.com", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)")),
    ("personio", re.compile(r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)", re.I)),
]
_NOT_SLUG = {"embed", "api", "www", "jobs", "careers", "static"}


def detect_ats(text: str) -> tuple[str, str] | None:
    """Find a known ATS and its slug referenced in a page; returns (name, slug) or None."""
    for name, pattern in _ATS_PATTERNS:
        for m in pattern.finditer(text or ""):
            slug = m.group(1)
            if slug.lower() not in _NOT_SLUG:
                return name, slug
    return None


def ats_url(ats: str, slug: str) -> str:
    """Public API/feed URL of a board for the given ATS and slug."""
    return {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        "lever": f"https://api.lever.co/v0/postings/{slug}?mode=json",
        "workable": f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
        "recruitee": f"https://{slug}.recruitee.com/api/offers/",
        "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
        "personio": f"https://{slug}.jobs.personio.de/xml",
    }[ats]


def parse_ats(ats: str, company: str, slug: str, body: str) -> list[dict]:
    """Dispatch to the parser of the given ATS (the body is JSON, or XML for Personio)."""
    if ats == "personio":
        return parse_personio(company, slug, body)
    try:
        data = json.loads(body)
    except ValueError:
        return []
    if ats == "smartrecruiters":
        return parse_smartrecruiters(company, slug, data)
    return {"greenhouse": parse_greenhouse, "lever": parse_lever,
            "workable": parse_workable, "recruitee": parse_recruitee}[ats](company, data)


# ---------- JobPosting JSON-LD ----------

_LD = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)


def _walk(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for value in node.values():
            if isinstance(value, (list, dict)):
                yield from _walk(value)


def extract_jobpostings(company: str, page_html: str, page_url: str) -> list[dict]:
    """Jobs from ``JobPosting`` JSON-LD. A posting without its own ``url`` uses the page URL, but only
    if that page is a single-ad page (``is_ad_link``); never a listing."""
    jobs = []
    for block in _LD.findall(page_html or ""):
        try:
            data = json.loads(_html.unescape(block.strip()))
        except ValueError:
            continue
        for node in _walk(data):
            kind = node.get("@type")
            if kind != "JobPosting" and not (isinstance(kind, list) and "JobPosting" in kind):
                continue
            locs = node.get("jobLocation") or []
            locs = locs if isinstance(locs, list) else [locs]
            signals = []
            for loc in locs:
                addr = (loc or {}).get("address") or {}
                if isinstance(addr, dict):
                    country = addr.get("addressCountry")
                    country = country.get("name") if isinstance(country, dict) else country
                    signals += [addr.get("addressLocality") or "", addr.get("addressRegion") or "", country or ""]
            remote = ["remote"] if str(node.get("jobLocationType") or "").upper() == "TELECOMMUTE" else []
            link = node.get("url") or page_url
            job = build_job(company, node.get("title") or "", link, signals, [])
            if job and remote:
                job["remote"] = remote
            jobs.append(job)
    return dedupe(jobs)


# ---------- careers discovery ----------

def find_careers_links(page_html: str, base_url: str, limit: int = 5) -> list[str]:
    """Links on a page whose URL or label looks like a careers page, resolved against `base_url`."""
    found = []
    for m in re.finditer(r'<a\b[^>]*?href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>', page_html or "", re.I | re.S):
        href, label = m.group(1), html_to_text(m.group(2))
        if not (_CAREER_WORDS.search(href) or _CAREER_WORDS.search(label)):
            continue
        url = normalize_link(urllib.parse.urljoin(base_url, href))
        if url and url not in found:
            found.append(url)
    return found[:limit]


# ---------- generic listing scraper (confirmed on the ad page) ----------

_STOP_TOKENS = {
    "despre", "about", "beneficii", "benefits", "contact", "politica", "policy", "gdpr", "cookie", "cookies", "termeni", "terms",
    "angajator", "employer", "echipa", "team", "programul", "programe", "programs", "faq", "intrebari", "blog", "news", "stiri",
    "login", "cont", "account", "search", "cauta", "categorie", "category", "tag", "page", "pagina", "feed", "rss",
    "why", "culture", "cultura", "values", "valori", "diversity", "diversitate", "press", "presa", "events", "evenimente",
    "noutati", "parcursul", "parcurs", "proces", "process", "recrutare", "recruitment",
}
_STOP_LAST = {"cariera", "cariere", "career", "careers"}  # only as the LAST segment ('cariera-la-penny'); the parent /cariere/ is fine
_STOP_PHRASES = ("oferte-de-joburi", "joburi-disponibile", "toate-joburile", "all-jobs", "pozitii-disponibile", "posturi-vacante",
                 "locuri-vacante", "open-positions", "current-openings", "aplica-pentru", "apply-for", "how-we-hire", "cum-aplici")


_LISTING_SLUG = re.compile(r"(oferte-de-joburi|joburi-disponibile|toate-joburile|all-jobs|pozitii-disponibile|posturi-vacante|"
                           r"locuri-vacante|locuri-de-munca|open-positions|openings|vacancies|joburi|jobs|pozitii|posturi)", re.I)


def _is_stop_path(path: str) -> bool:
    """True if ANY path segment is a non-ad section (about-us, candidate-faq, programul-...). Whole-word match only,
    so 'contabil-sef' is fine while 'cont' or 'about-us' are not."""
    segs = path.lower().strip("/").split("/")
    if any(ph in segs[-1] for ph in _STOP_PHRASES):  # a listing page itself, not an ad under one
        return True
    if any(t in _STOP_LAST for t in re.split(r"[-_.]+", segs[-1])):
        return True
    return any(t in _STOP_TOKENS for seg in segs for t in re.split(r"[-_.]+", seg))



_FILE_EXT = re.compile(r"\.(pdf|docx?|xlsx?|jpe?g|png|gif|svg|webp|zip|mp4|css|js)(\?|$)", re.I)
_JOB_PATH = re.compile(r"/(job|jobs|joburi|cariere|careers?|pozitii?|posturi?|oferta|oferte|vacancies|vacancy|locuri-de-munca)/[^/]+", re.I)
_AD_WORDS = ("responsabilit", "responsibilit", "cerinte", "requirements", "qualifications", "oferim", "we offer",
             "beneficii", "aplica", "apply", "candidat", "experienta", "experience", "program de lucru", "salariu",
             "contract de munca", "sarcini", "atributii", "descrierea jobului", "job description")
_GENERIC_TITLES = {"cariere", "careers", "joburi", "jobs", "career", "job", "home", "acasa", "aplica acum", "apply now"}


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")


def _same_site(url: str, site: str) -> bool:
    a, b = _host(url), _host(site)
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def candidate_ad_links(page_html: str, page_url: str, site: str) -> list[str]:
    """Same-site links from a careers page that look like ONE ad page (child of the page, or under a jobs path)."""
    base_path = urllib.parse.urlsplit(page_url).path.rstrip("/").lower()
    found = []
    for m in re.finditer(r'<a\b[^>]*?href=["\']([^"\'#][^"\']*)["\']', page_html or "", re.I):
        href = m.group(1)
        if href.lower().startswith(("mailto:", "tel:", "javascript:")) or _FILE_EXT.search(href):
            continue
        url = normalize_link(urllib.parse.urljoin(page_url, href))
        if not url or not _same_site(url, site) or url == normalize_link(page_url):
            continue
        path = urllib.parse.urlsplit(url).path.lower()
        child = bool(base_path) and path.startswith(base_path + "/") and path != base_path
        if not (child or _JOB_PATH.search(path)) or not is_ad_link(url):
            continue
        if _is_stop_path(path):
            continue
        if url not in found:
            found.append(url)
    return found


def listing_page_links(page_html: str, page_url: str, site: str, limit: int = 3) -> list[str]:
    """Same-site links to a second-level LISTING page (e.g. /cariere/pozitii-disponibile), to be fetched as pages."""
    found = []
    for m in re.finditer(r'<a\b[^>]*?href=["\']([^"\'#][^"\']*)["\']', page_html or "", re.I):
        href = m.group(1)
        if href.lower().startswith(("mailto:", "tel:", "javascript:")) or _FILE_EXT.search(href):
            continue
        url = normalize_link(urllib.parse.urljoin(page_url, href))
        if not url or not _same_site(url, site) or url == normalize_link(page_url):
            continue
        path = urllib.parse.urlsplit(url).path.lower()
        if _CAREER_WORDS.search(path) and _LISTING_SLUG.search(path.rsplit("/", 1)[-1]) and url not in found:
            found.append(url)
    return found[:limit]


def _h1(page_html: str) -> str:
    m = re.search(r"<h1\b[^>]*>(.*?)</h1>", page_html or "", re.I | re.S)
    if m:
        return html_to_text(m.group(1))
    m = re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']+)', page_html or "", re.I)
    return html_to_text(m.group(1)) if m else ""


def confirm_ad(company: str, ad_url: str, ad_html: str) -> dict | None:
    """Accept a page as a job ad only with proof: JobPosting JSON-LD, or an h1 title that matches the URL slug plus >= 2
    job-ad words (or, for numeric-id URLs, >= 5 words). Site banners, generic titles and articles are rejected."""
    ld = extract_jobpostings(company, ad_html, ad_url)
    if ld:
        return ld[0]
    title = _h1(ad_html)
    if not title or len(title) > 120 or _ascii(title) in _GENERIC_TITLES or "|" in title or title.rstrip().endswith(".") or re.search(r"\.\s*(ro|com|eu|net|org)\b|www\.", title, re.I):
        return None  # empty, generic or a site banner ("EDU . REGINAMARIA . RO"), not an ad title
    text = _ascii(html_to_text(re.sub(r"<(script|style)\b.*?</\1>", " ", ad_html or "", flags=re.S | re.I)))
    words = sum(1 for w in _AD_WORDS if w in text)
    slug = {t for t in re.findall(r"[a-z0-9]+", _ascii(urllib.parse.urlsplit(ad_url).path.rsplit("/", 1)[-1])) if len(t) >= 3}
    tokens = {t for t in re.findall(r"[a-z0-9]+", _ascii(title)) if len(t) >= 3}
    shared = tokens & slug
    overlap = bool(shared) and (len(shared) / len(tokens) >= 0.6 or slug <= tokens)
    id_slug = not re.search(r"[a-z]{4,}", _ascii(urllib.parse.urlsplit(ad_url).path.rsplit("/", 1)[-1]))  # /job/12345
    careers_ctx = bool(_CAREER_WORDS.search(urllib.parse.urlsplit(ad_url).path))  # /corporate/cariere/detalii/6886
    if not ((overlap and words >= 2) or (id_slug and (words >= 5 or (careers_ctx and words >= 3)))):
        return None
    city = infer_city(title) or single_city(text[:6000])
    return {"job_title": title, "job_link": normalize_link(ad_url), "company": company, "country": COUNTRY,
            "city": city or [], "county": county_of(city), "remote": infer_remote(title, text[:6000])}


def extract_listing_ads(company: str, pages: dict[str, str], site: str, get: Callable[[str], str], max_ads: int = 30) -> list[dict]:
    """Follow the job-ad links of a careers listing and parse each ad page (at most `max_ads`)."""
    cands: list[str] = []
    careers_pages = {u: h for u, h in pages.items() if u != site} or pages
    for url, html in careers_pages.items():
        for link in candidate_ad_links(html, url, site):
            if link not in cands:
                cands.append(link)
    jobs = []
    for link in cands[:max_ads]:
        try:
            job = confirm_ad(company, link, pages[link] if link in pages else get(link))
        except Exception:  # noqa: BLE001 - a dead candidate is not fatal
            continue
        jobs.append(job)
    return dedupe(jobs)


# ---------- orchestration (network injected) ----------

def scrape_company_jobs(company: str, website: str, *, get: Callable[[str], str], max_pages: int = 4) -> dict:
    """``{"jobs": [...], "method": ats|jsonld|none, "detail": str}``. ``get(url) -> text`` raises on failure."""
    site = normalize_link(website if "://" in (website or "") else f"https://{website}")
    if not site:
        return {"jobs": [], "method": "none", "detail": "no_website"}
    try:
        home = get(site)
    except Exception as exc:  # noqa: BLE001
        return {"jobs": [], "method": "none", "detail": f"site_unreachable:{exc.__class__.__name__}"}
    pages = {site: home}
    for link in find_careers_links(home, site, limit=max_pages):
        try:
            pages[link] = get(link)
        except Exception:  # noqa: BLE001 - a dead careers link is not fatal
            continue
    for url, text in list(pages.items()):
        if url == site:
            continue
        for link in listing_page_links(text, url, site):
            if link not in pages and len(pages) < max_pages + 4:
                try:
                    pages[link] = get(link)
                except Exception:  # noqa: BLE001
                    continue
    for text in pages.values():
        hit = detect_ats(text)
        if not hit:
            continue
        ats, slug = hit
        try:
            jobs = parse_ats(ats, company, slug, get(ats_url(ats, slug)))
        except Exception:  # noqa: BLE001
            continue
        if jobs:
            return {"jobs": jobs, "method": ats, "detail": slug}
    jobs = dedupe(j for url, text in pages.items() for j in extract_jobpostings(company, text, url))
    if jobs:
        return {"jobs": jobs, "method": "jsonld", "detail": f"{len(pages)} pages"}
    jobs = extract_listing_ads(company, pages, site, get)
    if jobs:
        return {"jobs": jobs, "method": "listing", "detail": f"{len(jobs)} confirmed ads"}
    return {"jobs": [], "method": "none", "detail": "no_structured_jobs"}
