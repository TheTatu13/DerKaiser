"""Regression tests for the ANAF screening stage (bugs found in the 2026-11-20 bug hunt)."""
import json
import time

from scraper import anaf_batch as ab
from scraper.sources import SourceUnavailable, Throttle

NO_WAIT = dict(throttle=Throttle(0, sleep=lambda s: None), today="2026-11-20")


def test_non_numeric_cif_is_dropped_and_does_not_crash_the_run(tmp_path):
    seen = []

    def poster(payload):
        seen.extend(p["cui"] for p in payload)
        return {"found": [], "notFound": [p["cui"] for p in payload]}

    rep = ab.screen_cifs(["00000007", "RO123X", "", "00000009"], tmp_path, poster=poster, **NO_WAIT)
    assert seen == [7, 9]  # the two bad values never reached int()
    assert rep["screened_total"] == 2


def test_final_report_is_linear_on_large_resumed_runs(tmp_path):
    """Before the fix, `left_to_screen` rebuilt a set of all screened CIFs for every CIF still to do: it hung for minutes."""
    n = 30_000
    done = [{"cif": str(i).zfill(8), "ok": False, "reasons": ["x"], "anaf": None} for i in range(1, n + 1)]
    (tmp_path / "screened.jsonl").write_text("\n".join(json.dumps(r) for r in done) + "\n", encoding="utf-8")

    def down(payload):
        raise SourceUnavailable("429")

    new = [str(i).zfill(8) for i in range(n + 1, 2 * n + 1)]
    t0 = time.monotonic()
    rep = ab.screen_cifs(new, tmp_path, poster=down, max_consecutive_unavailable=1, **NO_WAIT)
    assert time.monotonic() - t0 < 10
    assert rep["left_to_screen"] == n and rep["aborted"] is True
