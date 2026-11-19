import json

from scraper import discovery as d
from scraper.sources import SourceUnavailable, Throttle


def _cand(cui, name, active=None, source="x"):
    return d.Candidate(d.pad_cif(cui), name, "Arges", "Pitesti", active, source)


def _nowait():
    return Throttle(0, sleep=lambda s: None)


def _fast():
    return {d.CUIFIRMA: _nowait(), d.CUISCAN: _nowait()}


def _big():
    return {d.CUIFIRMA: d.Budget(999), d.CUISCAN: d.Budget(999)}


def test_tokens_drop_legal_noise():
    assert d.tokens("PITESTI DIVERS S.R.L.") == ["PITESTI", "DIVERS"]
    assert d.tokens("ABEL ABEL INTREPRINDERE INDIVIDUALA") == ["ABEL"]


def test_parse_cuifirma_pads_and_keeps_active_flag():
    rows = d.parse_cuifirma({"results": [
        {"cui": "143949", "name": "A SRL", "county": "Arges", "is_active": False},
        {"cui": "RO12", "name": "bad"}, {"cui": "5", "name": ""}]})
    assert [(c.cif, c.active) for c in rows] == [("00143949", False)]


def test_parse_cuiscan_never_trusts_status():
    rows = d.parse_cuiscan([{"cui": 143949, "name": "A SRL", "activa": False}, "junk"])
    assert [(c.cif, c.active) for c in rows] == [("00143949", None)]


def test_budget_sliding_window():
    t = [0.0]
    b = d.Budget(2, 10, clock=lambda: t[0])
    b.spend()
    b.spend()
    assert not b.available()
    t[0] = 10
    assert b.available()


def test_snowball_each_source_feeds_the_other(tmp_path):
    asked = {d.CUIFIRMA: [], d.CUISCAN: []}

    def firma(q):
        asked[d.CUIFIRMA].append(q)
        return [_cand(1111, "ALFA BETA SRL", True, d.CUIFIRMA)] if q == "seed" else []

    def scan(q):
        asked[d.CUISCAN].append(q)
        return [_cand(2222, "GAMMA TRANS SRL", None, d.CUISCAN)] if q == "ALFA" else []

    rep = d.run_discovery(tmp_path, seeds=["seed"], searchers={d.CUIFIRMA: firma, d.CUISCAN: scan},
                          throttles=_fast(), budgets=_big())
    assert rep["found_total"] == 2 and rep["stop_reason"] == "frontier_empty"
    assert "ALFA" in asked[d.CUISCAN]  # found by CUIFirma, searched on CUIScan
    assert "GAMMA" in asked[d.CUIFIRMA]  # found by CUIScan, searched on CUIFirma
    assert rep["active_true"] == 1 and rep["active_unknown"] == 1


def test_resume_does_not_repeat_queries(tmp_path):
    calls = []

    def firma(q):
        calls.append(q)
        return [_cand(1, "ONE SRL", True, d.CUIFIRMA)]

    kw = dict(searchers={d.CUIFIRMA: firma}, throttles={d.CUIFIRMA: _nowait()}, budgets={d.CUIFIRMA: d.Budget(99)})
    d.run_discovery(tmp_path, seeds=["zz"], **kw)
    first = len(calls)
    rep = d.run_discovery(tmp_path, seeds=["zz"], **kw)
    assert len(calls) == first and rep["queries_this_run"] == 0 and rep["found_total"] == 1


def test_hourly_budget_stops_run_cleanly(tmp_path):
    rep = d.run_discovery(tmp_path, seeds=["aa", "bb", "cc"], searchers={d.CUIFIRMA: lambda q: []},
                          throttles={d.CUIFIRMA: _nowait()}, budgets={d.CUIFIRMA: d.Budget(2, 3600)})
    assert rep["queries_this_run"] == 2 and rep["stop_reason"] == "hourly_budget_exhausted"


def test_failing_source_is_switched_off_and_query_kept(tmp_path):
    def boom(q):
        raise SourceUnavailable("429")

    rep = d.run_discovery(tmp_path, seeds=["aa"], searchers={d.CUIFIRMA: boom},
                          throttles={d.CUIFIRMA: _nowait()}, budgets={d.CUIFIRMA: d.Budget(99)},
                          max_consecutive_unavailable=3)
    assert rep["sources_off"] == [d.CUIFIRMA] and rep["queries_this_run"] == 3
    assert rep["pending_queries"][d.CUIFIRMA] == 1 and rep["stop_reason"] == "all_sources_off"


def test_inactive_upgrade_and_export(tmp_path):
    def scan(q):
        return [_cand(7, "X SRL", None, d.CUISCAN), _cand(8, "DEAD SRL", None, d.CUISCAN)]

    def firma(q):
        return [_cand(8, "DEAD SRL", False, d.CUIFIRMA)]

    d.run_discovery(tmp_path, seeds=["qq"], searchers={d.CUIFIRMA: firma, d.CUISCAN: scan},
                    throttles=_fast(), budgets=_big())
    assert d.export_cifs(tmp_path) == 1
    assert (tmp_path / "cifs.txt").read_text().split() == ["00000007"]  # 8 is known inactive


def test_truncated_candidate_line_tolerated(tmp_path):
    (tmp_path / "discovered.jsonl").write_text(
        json.dumps({"cif": "00000001", "name": "A", "county": "", "locality": "", "active": True, "source": "x"})
        + '\n{"cif": "000', encoding="utf-8")
    rep = d.run_discovery(tmp_path, seeds=[], searchers={d.CUIFIRMA: lambda q: []},
                          throttles={d.CUIFIRMA: _nowait()}, budgets={d.CUIFIRMA: d.Budget(9)})
    assert rep["found_total"] == 1

