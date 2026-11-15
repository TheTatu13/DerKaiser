from datetime import date

from scraper import financials as fi
from scraper.discovery import Budget
from scraper.sources import SourceUnavailable, Throttle

TODAY = date(2026, 11, 13)


def _row(an, ang, ca=1000):
    return {"an": an, "nrAngajati": ang, "cifraAfaceri": ca}


def _fast():
    return Throttle(0, sleep=lambda s: None)


def test_latest_year_picks_newest_not_first():
    assert fi.latest_year([_row(2023, 1), _row(2025, 4), _row(2024, 2)])["an"] == 2025
    assert fi.latest_year([]) is None and fi.latest_year(None) is None


def test_assess_pass_and_threshold():
    assert fi.assess([_row(2025, 4)], this_year=2026)["ok"] is True
    assert fi.assess([_row(2025, 1)], this_year=2026)["ok"] is True  # decided: >= 1
    assert fi.assess([_row(2025, 0)], this_year=2026)["reasons"] == ["salariati:0"]
    assert fi.assess([_row(2025, 4)], this_year=2026, min_employees=5)["reasons"] == ["salariati:4"]


def test_assess_no_balance_and_old_balance():
    assert fi.assess([], this_year=2026)["reasons"] == ["fara_bilant"]
    old = fi.assess([_row(2022, 9)], this_year=2026)
    assert old["ok"] is False and old["reasons"] == ["bilant_vechi:2022"]
    assert fi.assess([_row(2024, 9)], this_year=2026)["ok"] is True


def test_assess_zero_turnover_fails():
    assert fi.assess([_row(2025, 3, ca=0)], this_year=2026)["reasons"] == ["cifra_afaceri_zero"]


def test_screen_writes_final_list_resumes_and_uses_bare_cui(tmp_path):
    asked = []

    def fetcher(cui):
        asked.append(cui)
        return [_row(2025, 5)] if cui == "1111" else [_row(2025, 0)]

    kw = dict(fetcher=fetcher, throttle=_fast(), budget=Budget(99), today=TODAY)
    rep = fi.screen_financials(["00001111", "00002222"], tmp_path, **kw)
    assert asked == ["1111", "2222"]
    assert rep["passed"] == 1 and (tmp_path / "cifs_final.txt").read_text().split() == ["00001111"]
    fi.screen_financials(["00001111", "00002222"], tmp_path, **kw)
    assert asked == ["1111", "2222"]  # resume: nothing repeated


def test_screen_respects_hourly_budget_and_max(tmp_path):
    kw = dict(fetcher=lambda c: [_row(2025, 2)], throttle=_fast(), today=TODAY)
    rep = fi.screen_financials(["00000001", "00000002", "00000003"], tmp_path / "a", budget=Budget(2), **kw)
    assert rep["checked_total"] == 2 and rep["stop_reason"] == "hourly_budget_exhausted" and rep["left_to_check"] == 1
    rep = fi.screen_financials(["00000001", "00000002", "00000003"], tmp_path / "b", budget=Budget(99), max_firms=1, **kw)
    assert rep["checked_total"] == 1 and rep["stop_reason"] == "max_firms"


def test_source_down_stops_and_keeps_firms_for_next_run(tmp_path):
    def down(cui):
        raise SourceUnavailable("502")

    rep = fi.screen_financials(["00000001", "00000002", "00000003"], tmp_path, fetcher=down, throttle=_fast(),
                               budget=Budget(99), today=TODAY, max_consecutive_unavailable=2)
    assert rep["stop_reason"] == "source_down" and rep["checked_total"] == 0 and rep["left_to_check"] == 3
