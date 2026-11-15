"""Tests for scraper.sources: CUIScan + CUIFirma behaviour, with fakes (no network)."""

from datetime import date

import pytest

from scraper import sources as src

TODAY = date(2026, 11, 13)


def anaf_record(cui=52200796, name="SOBIS AP S.R.L.", address="DUMBRĂVII, 101, Municipiul Sibiu, Sibiu", **kw):
    return {"cui": cui, "name": name, "address": address, "inactive": False, **kw}


class FakeLookup:
    """Per-bare-CUI canned results; an Exception value is raised."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, cif):
        self.calls.append(cif)
        result = self.table[cif]
        if isinstance(result, Exception):
            raise result
        return result


# --- lookup_by_cif ----------------------------------------------------------


def test_lookup_sends_bare_cui_not_padded_id():
    fake = FakeLookup({"1559737": anaf_record(cui=1559737, name="X SRL")})
    src.lookup_by_cif("01559737", fake)
    assert fake.calls == ["1559737"]


def test_lookup_returns_record():
    fake = FakeLookup({"52200796": anaf_record()})
    assert src.lookup_by_cif(52200796, fake)["name"] == "SOBIS AP S.R.L."


@pytest.mark.parametrize("msg", ["CUIScan returned no data", "CUIScan API error: 404"])
def test_lookup_unknown_cif_is_none_not_an_outage(msg):
    assert src.lookup_by_cif(5, FakeLookup({"5": RuntimeError(msg)})) is None


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("CUIScan API error: 500"),
        RuntimeError("CUIScan API error: 429"),
        RuntimeError("CUIScan API error: 403"),
        TimeoutError("timed out"),
        ConnectionError("connection reset"),
        ValueError("Expecting value: line 1 column 1 (char 0)"),  # non-JSON body
    ],
)
def test_lookup_outage_is_source_unavailable(exc):
    with pytest.raises(src.SourceUnavailable):
        src.lookup_by_cif(5, FakeLookup({"5": exc}))


@pytest.mark.parametrize("empty", [None, {}, {"cui": 5}, {"name": "X"}, {"cui": None, "name": "X"}])
def test_lookup_unusable_record_is_none(empty):
    assert src.lookup_by_cif(5, FakeLookup({"5": empty})) is None


# --- search_by_name / find_cif_by_name --------------------------------------


def test_search_wraps_failures():
    def boom(_):
        raise RuntimeError("CUIFirma search error: 503")

    with pytest.raises(src.SourceUnavailable):
        src.search_by_name("X", boom)


def test_find_cif_picks_exact_match_not_first_result():
    # the FARMEC SRL (Arad) vs FARMEC SA (Cluj-Napoca) trap
    rows = [{"cui": "1697454", "name": "FARMEC SRL"}, {"cui": "199150", "name": "FARMEC SA"}]
    assert src.find_cif_by_name("FARMEC SA", lambda _: rows) == ("00199150", "")


def test_find_cif_is_case_and_space_insensitive():
    rows = [{"cui": 199150, "name": "  Farmec sa "}]
    assert src.find_cif_by_name("FARMEC SA", lambda _: rows)[0] == "00199150"


def test_find_cif_no_exact_match():
    rows = [{"cui": "1", "name": "FARMEC SRL"}]
    assert src.find_cif_by_name("FARMEC SA", lambda _: rows) == (None, src.NOT_FOUND)


def test_find_cif_two_different_exact_matches_is_ambiguous():
    rows = [{"cui": "11", "name": "ACME SRL"}, {"cui": "22", "name": "ACME SRL"}]
    assert src.find_cif_by_name("ACME SRL", lambda _: rows) == (None, src.AMBIGUOUS)


def test_find_cif_same_cui_twice_is_not_ambiguous():
    rows = [{"cui": "11", "name": "ACME SRL"}, {"cui": 11, "name": "ACME SRL"}]
    assert src.find_cif_by_name("ACME SRL", lambda _: rows)[0] == "00000011"


def test_find_cif_ignores_non_numeric_cui():
    rows = [{"cui": "abc", "name": "ACME SRL"}]
    assert src.find_cif_by_name("ACME SRL", lambda _: rows) == (None, src.NOT_FOUND)


@pytest.mark.parametrize("name", ["", "   ", None])
def test_find_cif_empty_name(name):
    assert src.find_cif_by_name(name, lambda _: [{"cui": "1", "name": ""}]) == (None, src.NOT_FOUND)


# --- Throttle ---------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 100.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_throttle_first_call_does_not_sleep_then_paces():
    clock = Clock()
    throttle = src.Throttle(1.0, clock=clock, sleep=clock.sleep)
    throttle.wait()
    assert clock.slept == []
    clock.now += 0.25
    throttle.wait()
    assert clock.slept == [pytest.approx(0.75)]


def test_throttle_no_sleep_when_gap_already_elapsed():
    clock = Clock()
    throttle = src.Throttle(1.0, clock=clock, sleep=clock.sleep)
    throttle.wait()
    clock.now += 5
    throttle.wait()
    assert clock.slept == []


# --- company_doc_for_cif ----------------------------------------------------


def test_doc_for_cif_ok_and_padded():
    fake = FakeLookup({"1559737": anaf_record(cui=1559737, name="X SRL", address="Municipiul Arad, Arad")})
    out = src.company_doc_for_cif(1559737, lookup=fake, today=TODAY, jobs_count=2)
    assert out.status == src.OK
    assert out.doc["id"] == "01559737"
    assert out.doc["existingJobsCount"] == [2]
    assert out.doc["scraperFile"] == "derkaiser-python-scraper"


def test_doc_for_cif_outage_is_unavailable_and_writes_nothing():
    out = src.company_doc_for_cif(5, lookup=FakeLookup({"5": RuntimeError("CUIScan API error: 503")}))
    assert (out.status, out.doc) == (src.UNAVAILABLE, None)


def test_doc_for_cif_unknown_is_not_found():
    out = src.company_doc_for_cif(5, lookup=FakeLookup({"5": RuntimeError("CUIScan returned no data")}))
    assert (out.status, out.doc) == (src.NOT_FOUND, None)


def test_doc_for_cif_rejects_cui_mismatch():
    out = src.company_doc_for_cif(5, lookup=FakeLookup({"5": anaf_record(cui=999)}))
    assert out.status == src.INVALID and "mismatch" in out.detail


def test_doc_for_cif_bad_cif_is_invalid_without_calling_the_site():
    fake = FakeLookup({})
    out = src.company_doc_for_cif("abc", lookup=fake)
    assert out.status == src.INVALID and fake.calls == []


def test_doc_for_cif_missing_locality_is_ignored_not_half_written():
    fake = FakeLookup({"5": anaf_record(cui=5, address="STRADA X NR 5")})
    out = src.company_doc_for_cif(5, lookup=fake, today=TODAY)
    assert out.status == src.IGNORED and out.doc is None and "location" in out.detail


def test_doc_for_cif_inactive_company_is_ignored():
    fake = FakeLookup({"5": anaf_record(cui=5, address="Municipiul Sibiu, Sibiu", inactive=True)})
    out = src.company_doc_for_cif(5, lookup=fake, today=TODAY)
    assert out.status == src.IGNORED and out.doc is None and "activ" in out.detail


def test_ignored_firms_do_not_count_toward_abort():
    bad = anaf_record(cui=1, address="STRADA X")
    fake = FakeLookup({str(i): {**bad, "cui": i} for i in range(1, 9)})
    report = src.process_cifs(range(1, 9), lookup=fake, max_consecutive_unavailable=3)
    assert report["aborted"] is False and report["counts"] == {"ignored": 8}
    assert all(row["status"] == "ignored" for row in report["skipped"])


def test_doc_for_cif_explicit_locality_rescues_unparsable_address():
    fake = FakeLookup({"5": anaf_record(cui=5, address="STRADA X NR 5")})
    out = src.company_doc_for_cif(
        5, lookup=fake, today=TODAY, localitate="Municipiul Brașov", adresa="Municipiul Brașov, Brașov"
    )
    assert out.status == src.OK and out.doc["location"] == ["Municipiul Brașov"]


# --- process_cifs -----------------------------------------------------------


def table(*cuis):
    return {str(c): anaf_record(cui=c, name=f"FIRMA {c} SRL", address="Municipiul Sibiu, Sibiu") for c in cuis}


def test_batch_collects_docs_and_counts():
    fake = FakeLookup({**table(10, 20), "30": RuntimeError("CUIScan returned no data")})
    report = src.process_cifs([10, 20, 30], lookup=fake, today=TODAY)
    assert [d["id"] for d in report["docs"]] == ["00000010", "00000020"]
    assert report["counts"] == {"ok": 2, "not_found": 1}
    assert report["skipped"][0]["cif"] == "00000030"
    assert report["aborted"] is False


def test_batch_dedupes_after_padding():
    fake = FakeLookup(table(1559737))
    report = src.process_cifs([1559737, "01559737", "RO1559737"], lookup=fake, today=TODAY)
    assert len(report["docs"]) == 1 and fake.calls == ["1559737"]


def test_batch_invalid_entries_are_skipped_without_a_request():
    fake = FakeLookup(table(10))
    report = src.process_cifs(["abc", 10, ""], lookup=fake, today=TODAY)
    assert report["counts"] == {"invalid": 2, "ok": 1}
    assert fake.calls == ["10"]


def test_batch_aborts_after_consecutive_outages():
    down = RuntimeError("CUIScan API error: 503")
    fake = FakeLookup({str(i): down for i in range(1, 20)})
    report = src.process_cifs(range(1, 20), lookup=fake, today=TODAY, max_consecutive_unavailable=5)
    assert report["aborted"] is True
    assert len(fake.calls) == 5  # stopped hammering the dead site
    assert report["docs"] == []


def test_batch_outage_streak_resets_on_success():
    down = RuntimeError("CUIScan API error: 503")
    data = {"1": down, "2": down, "3": anaf_record(cui=3, name="OK SRL", address="Municipiul Sibiu, Sibiu"), "4": down, "5": down}
    report = src.process_cifs([1, 2, 3, 4, 5], lookup=FakeLookup(data), today=TODAY, max_consecutive_unavailable=3)
    assert report["aborted"] is False and report["counts"] == {"unavailable": 4, "ok": 1}


def test_batch_not_found_does_not_count_toward_abort():
    gone = RuntimeError("CUIScan returned no data")
    fake = FakeLookup({str(i): gone for i in range(1, 11)})
    report = src.process_cifs(range(1, 11), lookup=fake, max_consecutive_unavailable=3)
    assert report["aborted"] is False and report["counts"] == {"not_found": 10}


def test_batch_uses_jobs_count_fn_and_throttle():
    clock = Clock()
    throttle = src.Throttle(2.0, clock=clock, sleep=clock.sleep)
    fake = FakeLookup(table(10, 20))
    report = src.process_cifs([10, 20], lookup=fake, today=TODAY, throttle=throttle, jobs_count_fn=lambda c: int(c))
    assert [d["existingJobsCount"] for d in report["docs"]] == [[10], [20]]
    assert clock.slept == [pytest.approx(2.0)]  # paced between the two requests


@pytest.mark.parametrize("code", [400, 404, 422])
def test_client_error_verdicts_are_not_found_not_outage(code):
    # live: CUIScan answers 400 for a CIF that fails its checksum
    assert src.lookup_by_cif(5, FakeLookup({"5": RuntimeError(f"CUIScan API error: {code}")})) is None


@pytest.mark.parametrize("code", [403, 408, 429, 500, 502, 503])
def test_other_http_errors_stay_outages(code):
    with pytest.raises(src.SourceUnavailable):
        src.lookup_by_cif(5, FakeLookup({"5": RuntimeError(f"CUIScan API error: {code}")}))


def test_rejected_cifs_do_not_abort_a_batch():
    bad = RuntimeError("CUIScan API error: 400")
    fake = FakeLookup({str(i): bad for i in range(1, 12)})
    report = src.process_cifs(range(1, 12), lookup=fake, max_consecutive_unavailable=3)
    assert report["aborted"] is False and report["counts"] == {"not_found": 11}
