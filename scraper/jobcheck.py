"""DerKaiser job checks. Brewtality's own ``validate`` rules are reused as they are; this adds the sanity net."""

from __future__ import annotations

from collections import Counter

from .validate import filter_valid_jobs, validate_job  # noqa: F401  (re-exported: the Brewtality rules)


def is_sane(jobs: list[dict], max_jobs: int = 1000) -> tuple[bool, str]:
    """Reject a layer's output that looks like a scraping accident: absurdly many jobs, or ten+ with the same title."""
    if len(jobs) > max_jobs:
        return False, f"too_many_jobs:{len(jobs)}"
    if len(jobs) >= 10:
        top = Counter((j.get("job_title") or j.get("title") or "").lower() for j in jobs).most_common(1)[0][1]
        if top / len(jobs) >= 0.9:
            return False, "uniform_titles"
    return True, ""
