import json

from scraper import anaf_batch as ab
from scraper import quality as q
from scraper.sources import SourceUnavailable, Throttle


def _entry(cui, name="ALFA SRL", stare="INREGISTRAT din data 01.01.2010", forma="SOCIETATE COMERCIALĂ CU RĂSPUNDERE LIMITATĂ",
           caen="6201", inactive=False):
    return {"date_generale": {"cui": cui, "denumire": name, "stare_inregistrare": stare, "forma_juridica": forma,
                              "forma_organizare": "PERSOANA JURIDICA", "cod_CAEN": caen, "nrRegCom": "J1/1/2010"},
            "stare_inactiv": {"statusInactivi": inactive}, "inregistrare_scop_Tva": {"scpTVA": True}}


def _rec(**kw):
    return ab.normalize(_entry(1, **kw))


def test_active_company_passes():
    assert q.assess_anaf(_rec()) == []
    assert q.assess_anaf(_rec(stare="RELUARE ACTIVITATE din data 04.02.2022")) == []


def test_radiated_and_suspended_fail():
    assert q.assess_anaf(_rec(stare="RADIERE din data 02.02.2002"))[0].startswith("stare:RADIERE")
    assert any(r.startswith("stare:SUSPENDARE") for r in q.assess_anaf(_rec(stare="SUSPENDARE ACTIVITATE din data 1.1.2020")))


def test_inactive_fiscal_fails_even_if_reluare_activitate():
    reasons = q.assess_anaf(_rec(stare="RELUARE ACTIVITATE din data 04.02.2022", inactive=True))
    assert reasons == ["inactiv_fiscal"]


def test_sole_traders_fail_by_form_and_by_name():
    assert any(r.startswith("forma_juridica") for r in q.assess_anaf(_rec(forma="PERSOANA FIZICA AUTORIZATA")))
    assert any(r.startswith("forma_juridica") for r in q.assess_anaf(_rec(forma="")))
    assert "name_sole_trader" in q.assess_anaf(_rec(name="POPESCU ANA PFA"))
    assert "name_sole_trader" in q.assess_anaf(_rec(name="ABEL INTREPRINDERE INDIVIDUALA"))
    assert "name_sole_trader" in q.assess_anaf(_rec(name='ABRUDAN MONICA "ABRUDAN MONICA" AF'))
    assert q.assess_anaf(_rec(name="ELITE INVEST SRL")) == []


def test_sa_and_regie_pass():
    assert q.assess_anaf(_rec(forma="SOCIETATE COMERCIALĂ PE ACŢIUNI")) == []
    assert q.assess_anaf(_rec(forma="REGIE AUTONOMA")) == []


def test_excluded_caen_configurable():
    assert q.assess_anaf(_rec(caen="6420")) == ["caen_exclus:6420"]
    assert q.assess_anaf(_rec(caen="6820")) == []  # re-admitted 2026-11-19
    assert q.assess_anaf(_rec(caen="6420")) == ["caen_exclus:6420"]
    assert q.assess_anaf(_rec(caen="6420"), excluded_caen=frozenset()) == []


def test_not_found_fails():
    assert q.assess_anaf(None) == ["anaf_not_found"]


def test_financials_gate():
    assert q.assess_financials(None, None) == ["fara_bilant"]
    assert q.assess_financials(0, 1000) == ["salariati:0"]
    assert q.assess_financials(1, 1000) == []
    assert q.assess_financials(3, 0) == ["cifra_afaceri_zero"]
    assert q.assess_financials(1, 5, min_employees=5) == ["salariati:1"]


def test_normalize_pads_and_reads_flags():
    r = ab.normalize(_entry(143949, inactive=True))
    assert r["cif"] == "00143949" and r["inactive"] is True and r["tva"] is True and r["caen"] == "6201"


def test_fetch_batch_found_and_missing():
    sent = []

    def poster(payload):
        sent.append(payload)
        return {"found": [_entry(1111)], "notFound": [2222]}

    found, missing = ab.fetch_batch(["00001111", "00002222"], "2026-11-13", poster)
    assert sent[0] == [{"cui": 1111, "data": "2026-11-13"}, {"cui": 2222, "data": "2026-11-13"}]
    assert list(found) == ["00001111"] and missing == {"00002222"}


def test_screen_batches_of_100_resume_and_report(tmp_path):
    cifs = ["%08d" % n for n in range(1, 251)]
    calls = []

    def poster(payload):
        calls.append(len(payload))
        found = []
        for p in payload:
            n = p["cui"]
            found.append(_entry(n, stare="RADIERE din data 1.1.2000") if n % 2 == 0 else _entry(n))
        return {"found": found, "notFound": []}

    kw = dict(poster=poster, throttle=Throttle(0, sleep=lambda s: None), today="2026-11-13")
    rep = ab.screen_cifs(cifs, tmp_path, **kw)
    assert calls == [100, 100, 50]
    assert rep["screened_total"] == 250 and rep["passed"] == 125 and rep["fail_reasons"] == {"stare": 125}
    assert len((tmp_path / "cifs_screened.txt").read_text().split()) == 125
    rep2 = ab.screen_cifs(cifs, tmp_path, **kw)
    assert calls == [100, 100, 50] and rep2["screened_total"] == 250  # nothing repeated


def test_screen_aborts_when_anaf_down_and_keeps_rest_for_resume(tmp_path):
    def down(payload):
        raise SourceUnavailable("429")

    rep = ab.screen_cifs(["00000001", "00000002"], tmp_path, poster=down,
                         throttle=Throttle(0, sleep=lambda s: None), today="2026-11-13", max_consecutive_unavailable=1)
    assert rep["screened_total"] == 0 and rep["left_to_screen"] == 2 and rep["aborted"] is True
