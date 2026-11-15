"""DerKaiser jobs run + publish payloads. Dry-run by default: local files only.

``run_jobs`` takes the company documents (``docs.jsonl`` from the batch), finds each firm's own job
ads through ``scraper.jobs``, checks every ad link is alive, writes ``jobs.jsonl`` and gives each
document its real ``existingJobsCount``. Firms with no jobs are kept (count 0): they may publish later.

Jobs come from the firm's own site (scraper.jobs) plus ANOFM per CIF (no website needed), and are
written in the Brewtality 3:16 model (scraper.model): {url,title,company,cif,location[],workmode,date,status}.
Sending (``send_jobs`` -> POST api.peviitor.ro/v1/scraper/jobs/upload/, ``send_company`` -> PUT
/firme/company/add/) is separate and refuses to run without ``confirm=True``; untested against the live API.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Callable

from . import jobs as J
from . import model as M

USER_AGENT = "DerKaiser-peViitor/0.1 (volunteer project; github.com/thetatu13)"


class Pace:
    """Minimum gap between requests (injectable clock/sleep)."""

    def __init__(self, min_interval: float = 2.0, clock=time.monotonic, sleep=time.sleep):
        self.min_interval, self._clock, self._sleep, self._last = min_interval, clock, sleep, None

    def wait(self) -> None:
        if self._last is not None:
            gap = self.min_interval - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
        self._last = self._clock()


def make_getter(pace: Pace | None = None, max_bytes: int = 2_000_000) -> Callable[[str], str]:
    pace = pace or Pace()

    def get(url: str) -> str:
        pace.wait()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json,*/*"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.read(max_bytes).decode(resp.headers.get_content_charset() or "utf-8", errors="replace")

    return get


def make_status_checker(pace: Pace | None = None) -> Callable[[str], int]:
    """GET (not HEAD, as in Brewtality): unreachable counts as 0 = dead."""
    pace = pace or Pace()

    def status(url: str) -> int:
        pace.wait()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                resp.read(2048)
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception:  # noqa: BLE001
            return 0

    return status


def make_anofm_poster(pace: Pace | None = None) -> Callable[[str, dict], dict]:
    pace = pace or Pace()

    def post(url: str, body: dict) -> dict:
        pace.wait()
        return _post_json(url, body, None)

    return post


def alive_jobs(jobs: list[dict], status: Callable[[str], int]) -> tuple[list[dict], list[dict]]:
    """``(alive, dead)``: a job (Brewtality model, ``url``) whose ad link does not answer < 400 is dropped, never published."""
    alive, dead = [], []
    for job in jobs:
        (alive if 0 < status(job["url"]) < 400 else dead).append(job)
    return alive, dead


def _doc_of(row: dict) -> dict | None:
    doc = row if "id" in row else row.get("doc")
    return doc if isinstance(doc, dict) and doc.get("id") else None


def _first(value) -> str:
    return (value[0] if isinstance(value, list) and value else value) or ""


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


def run_jobs(
    docs_path: str | Path,
    out_dir: str | Path,
    *,
    websites: dict[str, str] | None = None,
    get: Callable[[str], str] | None = None,
    status: Callable[[str], int] | None = None,
    anofm: Callable[[str, dict], dict] | None = None,
    recipes_dir: str | Path | None = None,
    workers: int = 1,
    max_firms: int | None = None,
    net: "Net | None" = None,
    **limits,
) -> dict:
    """Find every firm's jobs via the nested engine and give each document its real ``existingJobsCount``."""
    from . import engine
    from .net import Net

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    net = net or (Net() if not (get and status and anofm) else None)
    get, status = get or net.get, status or net.status
    anofm = anofm or net.post_json
    docs = [d for d in (_doc_of(r) for r in _read(Path(docs_path))) if d]
    previous = {r["cif"]: r["jobs"] for r in _read(out / "previous_by_firm.jsonl")}
    engine.run_all(docs, out, fetch=get, status=status, anofm_post=anofm, websites=websites, recipes_dir=recipes_dir,
                   workers=workers, max_firms=max_firms, previous=previous, first_site=lambda d: _first(d.get("website")), **limits)
    firms = _read(out / "jobs_by_firm.jsonl")
    counts = {r["cif"]: r["jobs"] for r in firms}
    found_sites = {r["cif"]: r["site"] for r in firms if r.get("site_source") == "anofm_email"}
    with (out / "docs_with_jobs.jsonl").open("w", encoding="utf-8") as fh:
        for doc in docs:
            if doc["id"] in counts:
                doc = {**doc, "existingJobsCount": [counts[doc["id"]]]}
                if not doc.get("website") and found_sites.get(doc["id"]):
                    doc["website"] = [found_sites[doc["id"]]]
                fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
    return engine.report(docs, out)


def payloads_by_company(jobs_path: str | Path) -> list[list[dict]]:
    """One list per CIF, in the upload body shape (Brewtality model, cif padded to 8)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in _read(Path(jobs_path)):
        groups[row["cif"]].append(row)
    return list(groups.values())


def _need_confirm(confirm: bool) -> None:
    if not confirm:
        raise RuntimeError("send is dry-run by default: pass confirm=True once writing to peViitor is approved")


def send_jobs(payloads: list[list[dict]], *, confirm: bool = False, post: Callable | None = None) -> int:
    """POST each company's jobs to /v1/scraper/jobs/upload/ (no token, as Brewtality). Returns jobs sent."""
    _need_confirm(confirm)
    post = post or (lambda url, body: _post_json(url, body, None))
    sent = 0
    for batch in payloads:
        if batch:  # the API rejects an empty array
            post(f"{M.API_BASE}/scraper/jobs/upload/", batch)
            sent += len(batch)
    return sent


def send_company(doc: dict, *, confirm: bool = False, put: Callable | None = None) -> None:
    """PUT one company document to /v1/firme/company/add/ (must answer success:true)."""
    _need_confirm(confirm)
    put = put or _put_json
    data = put(f"{M.API_BASE}/firme/company/add/", {**doc, "id": M.pad_cif(doc["id"])})
    if not data.get("success"):
        raise RuntimeError(f"company upsert failed: {data}")


def _put_json(url: str, body, token=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="PUT",
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def existing_jobs_count(cif, get: Callable[[str], str]) -> int:
    """numFound for this CIF straight from peViitor (read-only): GET /v1/scraper/jobs/?cif=&rows=500 -> total."""
    return int(json.loads(get(f"{M.API_BASE}/scraper/jobs/?cif={M.pad_cif(cif)}&rows=500")).get("total", 0))


def _post_json(url: str, body, token: str | None):
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}
