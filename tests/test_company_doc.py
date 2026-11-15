"""Tests for scraper.company_doc: the DerKaiser 11-field company contract."""

import json
from datetime import date

import pytest

from scraper import company_doc as cd

TODAY = date(2026, 11, 13)

SOBIS = {
    "cui": 52200796,
    "name": "SOBIS AP S.R.L.",
    "address": "DUMBRĂVII, 101, Municipiul Sibiu, Sibiu",
    "inactive": False,
}


def build(**kw):
    return cd.build_company_doc(SOBIS, today=TODAY, **kw)


# --- pad_cif ----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (1559737, "01559737"),  # 7 digits -> padded (the Rapel/Continental case)
        ("1559737", "01559737"),
        ("RO1559737", "01559737"),
        (" ro 1559737 ", "01559737"),
        (39167242, "39167242"),  # already 8 digits
        ("1697454", "01697454"),  # Inviitor's unpadded Farmec id
        (1234567890, "1234567890"),  # longer is never truncated
    ],
)
def test_pad_cif(raw, expected):
    assert cd.pad_cif(raw) == expected


@pytest.mark.parametrize("bad", ["", "abc", "12-34", None])
def test_pad_cif_rejects_non_numeric(bad):
    with pytest.raises(ValueError):
        cd.pad_cif(bad)


# --- locality_from_address --------------------------------------------------


def test_locality_cuiscan_style():
    assert cd.locality_from_address("DUMBRĂVII, 101, Municipiul Sibiu, Sibiu") == (
        "Municipiul Sibiu",
        "Municipiul Sibiu, Sibiu",
    )


def test_locality_abbreviated_anaf_style():
    loc, addr = cd.locality_from_address("JUD. CLUJ, MUN. CLUJ-NAPOCA, STR. HENRI BARBUSSE, NR.16")
    assert loc == "Municipiul Cluj-Napoca"
    assert addr == "Municipiul Cluj-Napoca, Cluj"


def test_locality_commune():
    assert cd.locality_from_address("Sat Racu, Comuna Racu, Harghita")[0] == "Comuna Racu"


@pytest.mark.parametrize("empty", [None, "", "STRADA X NR 5"])
def test_locality_unknown_returns_empty(empty):
    assert cd.locality_from_address(empty) == ("", "")


# --- build_company_doc ------------------------------------------------------


def test_build_has_all_eleven_fields_and_is_valid():
    doc = build(brand="SOBIS", website="https://sobis.ro", jobs_count=3)
    assert set(doc) == set(cd.REQUIRED_FIELDS)
    assert cd.validate_company_doc(doc) == {"valid": True, "errors": [], "failed_fields": []}


def test_build_values():
    doc = build(brand="SOBIS", website="https://sobis.ro", jobs_count=3)
    assert doc["id"] == "52200796"
    assert doc["company"] == "SOBIS AP S.R.L."
    assert doc["brand"] == "SOBIS"
    assert doc["status"] == "activ"
    assert doc["location"] == ["Municipiul Sibiu"]
    assert doc["address"] == ["Municipiul Sibiu, Sibiu"]
    assert doc["website"] == ["https://sobis.ro"]
    assert doc["lastScraped"] == "2026-11-13"
    assert doc["scraperFile"] == "derkaiser-python-scraper"
    assert doc["existingJobsCount"] == [3]


def test_build_id_is_padded_never_bare_cui():
    doc = cd.build_company_doc({"cui": 1559737, "name": "X SRL", "address": "Municipiul Arad, Arad"}, today=TODAY)
    assert doc["id"] == "01559737"
    assert json.loads(doc["anafData"][0])["cui"] == 1559737  # the JSON keeps the numeric CUI


def test_build_anafdata_is_list_with_one_json_string():
    doc = build()
    assert isinstance(doc["anafData"], list) and len(doc["anafData"]) == 1
    parsed = json.loads(doc["anafData"][0])
    assert parsed["cif"] == parsed["cui"] == 52200796
    assert parsed["denumire"] == "SOBIS AP S.R.L."
    assert parsed["statusImpozit"] == "activ"
    assert parsed["localitate"] == "Municipiul Sibiu"


def test_build_inactive_company():
    doc = cd.build_company_doc({**SOBIS, "inactive": True}, today=TODAY)
    assert doc["status"] == "inactiv"
    assert cd.validate_company_doc(doc)["valid"] is True


def test_build_brand_defaults_to_company():
    assert build()["brand"] == "SOBIS AP S.R.L."


def test_build_explicit_locality_overrides_guess():
    doc = build(localitate="Municipiul Brașov", adresa="Municipiul Brașov, Brașov")
    assert doc["location"] == ["Municipiul Brașov"]
    assert doc["address"] == ["Municipiul Brașov, Brașov"]


def test_build_without_website_gives_empty_list_but_stays_valid():
    doc = build()
    assert doc["website"] == []
    assert cd.validate_company_doc(doc)["valid"] is True


@pytest.mark.parametrize("anaf", [{}, {"cui": 1}, {"name": "X"}, {"cui": None, "name": "X"}])
def test_build_requires_cui_and_name(anaf):
    with pytest.raises(ValueError):
        cd.build_company_doc(anaf, today=TODAY)


def test_build_without_locality_is_flagged_by_validator():
    doc = cd.build_company_doc({"cui": 5, "name": "X SRL", "address": "STRADA X"}, today=TODAY)
    result = cd.validate_company_doc(doc)
    assert result["valid"] is False
    assert any("location" in e for e in result["errors"])


# --- validate_company_doc ---------------------------------------------------


def good():
    return build(website="https://sobis.ro")


@pytest.mark.parametrize("field", cd.REQUIRED_FIELDS)
def test_every_field_is_required(field):
    doc = good()
    del doc[field]
    result = cd.validate_company_doc(doc)
    assert result["valid"] is False
    assert f"Missing field: {field}" in result["errors"]


@pytest.mark.parametrize("field", cd.REQUIRED_FIELDS)
def test_null_field_counts_as_missing(field):
    doc = good()
    doc[field] = None
    assert f"Missing field: {field}" in cd.validate_company_doc(doc)["errors"]


@pytest.mark.parametrize("bad", ["1559737", "0155973", "ABC12345", 1559737, ""])
def test_id_must_be_padded_numeric_string(bad):
    doc = good()
    doc["id"] = bad
    assert any(e.startswith("id must be") for e in cd.validate_company_doc(doc)["errors"])


def test_unpadded_inviitor_id_is_rejected():
    # the real Inviitor-created Farmec doc has id "1697454"
    doc = good()
    doc["id"] = "1697454"
    assert cd.validate_company_doc(doc)["valid"] is False


def test_status_must_be_known():
    doc = good()
    doc["status"] = "bogus"
    assert any("status" in e for e in cd.validate_company_doc(doc)["errors"])


@pytest.mark.parametrize("field", ["location", "address"])
@pytest.mark.parametrize("bad", [[], "Sibiu", [""], [1]])
def test_location_and_address_need_non_empty_string_lists(field, bad):
    doc = good()
    doc[field] = bad
    assert any(field in e for e in cd.validate_company_doc(doc)["errors"])


@pytest.mark.parametrize("bad", ["13-11-2026", "2026/11/13", "", 20261113])
def test_last_scraped_must_be_iso_date(bad):
    doc = good()
    doc["lastScraped"] = bad
    assert any("lastScraped" in e for e in cd.validate_company_doc(doc)["errors"])


@pytest.mark.parametrize("bad", [3, [], [1, 2], ["3"], [-1], [True], [1.5]])
def test_existing_jobs_count_is_a_list_with_one_non_negative_int(bad):
    doc = good()
    doc["existingJobsCount"] = bad
    assert any("existingJobsCount" in e for e in cd.validate_company_doc(doc)["errors"])


@pytest.mark.parametrize("bad", ["{}", [], ["{}", "{}"], [{"cui": 1}], ["not json"], ['{"x": 1}']])
def test_anafdata_must_be_one_json_string_with_cui(bad):
    doc = good()
    doc["anafData"] = bad
    assert any("anafData" in e for e in cd.validate_company_doc(doc)["errors"])


def test_anafdata_cui_must_match_id():
    doc = good()
    doc["anafData"] = [json.dumps({"cui": 111})]
    assert "anafData cui does not match id" in cd.validate_company_doc(doc)["errors"]


@pytest.mark.parametrize("bad", [None, "doc", [], 5])
def test_validate_rejects_non_dict(bad):
    assert cd.validate_company_doc(bad)["valid"] is False


def test_real_inviitor_shaped_doc_validates_once_id_is_padded():
    # shape of the real Inviitor-created doc (Farmec SRL, Arad), with the id padded
    doc = {
        "id": "01697454",
        "company": "FARMEC SRL",
        "brand": "FARMEC SA",
        "status": "activ",
        "location": ["Municipiul Arad"],
        "website": ["https://www.farmec.ro"],
        "lastScraped": "2026-08-07",
        "scraperFile": "inviitor-ro-nodejs-scraper",
        "address": ["Municipiul Arad, Arad"],
        "anafData": [json.dumps({"cif": 1697454, "cui": 1697454, "denumire": "FARMEC SRL"})],
        "existingJobsCount": [0],
    }
    assert cd.validate_company_doc(doc) == {"valid": True, "errors": [], "failed_fields": []}


# --- place normalization (found live on Continental Hotels, CIF 1559737) -----


def test_locality_normalizes_uppercase_and_cedilla():
    # CUIScan returned "MUNICIPIUL BUCUREŞTI, SECTOR 1" (upper case, cedilla ş)
    assert cd.locality_from_address("MUNICIPIUL BUCUREŞTI, SECTOR 1") == (
        "Municipiul București",
        "Municipiul București, Sector 1",
    )


def test_locality_cedilla_fixed_even_when_mixed_case():
    assert cd.locality_from_address("Strada X 5, Municipiul Târgu Mureş, Mureş")[0] == "Municipiul Târgu Mureș"


def test_locality_mixed_case_is_not_retitled():
    assert cd.locality_from_address("Municipiul Piatra Neamț, Neamț")[1] == "Municipiul Piatra Neamț, Neamț"


def test_built_doc_has_no_cedilla_or_shouting_in_places():
    anaf = {"cui": 1559737, "name": "CONTINENTAL HOTELS SA", "address": "MUNICIPIUL BUCUREŞTI, SECTOR 1"}
    doc = cd.build_company_doc(anaf, today=TODAY)
    assert doc["id"] == "01559737"
    assert doc["location"] == ["Municipiul București"]
    assert doc["address"] == ["Municipiul București, Sector 1"]
    assert cd.validate_company_doc(doc)["valid"] is True


# --- keep/ignore rule: failing even one condition means ignored --------------


def test_meets_conditions_ok_for_a_complete_active_firm():
    assert cd.meets_conditions(good()) == {"ok": True, "failed": [], "reasons": []}


@pytest.mark.parametrize("field", [f for f in cd.REQUIRED_FIELDS if f != "website"])
def test_breaking_any_single_condition_ignores_the_firm(field):
    doc = good()
    del doc[field]
    result = cd.meets_conditions(doc)
    assert result["ok"] is False and field in result["failed"]


def test_website_is_not_a_condition_when_empty():
    doc = good()
    doc["website"] = []
    assert cd.meets_conditions(doc)["ok"] is True


@pytest.mark.parametrize("status", ["inactiv", "suspendat", "radiat"])
def test_non_active_status_is_ignored(status):
    doc = good()
    doc["status"] = status
    result = cd.meets_conditions(doc)
    assert result["ok"] is False and result["failed"] == ["status"]


def test_meets_conditions_reports_every_failed_field():
    doc = good()
    doc["location"] = []
    doc["existingJobsCount"] = "3"
    assert cd.meets_conditions(doc)["failed"] == ["existingJobsCount", "location"]


@pytest.mark.parametrize("bad", [None, "x", []])
def test_meets_conditions_rejects_non_dict(bad):
    assert cd.meets_conditions(bad)["ok"] is False


# --- "Sat X Com. Y" addresses (found live on Cemacon, Rapel, Cris-Tim) --------


@pytest.mark.parametrize(
    "address, expected",
    [
        ("Sat Recea Com. Vârșolț, Sălaj", "Comuna Vârșolț"),
        ("Sat Rahău Mun. Sebeș, Alba", "Municipiul Sebeș"),
        ("Sat Filipeștii De Pădure Com. Filipeștii De Pădure, Prahova", "Comuna Filipeștii De Pădure"),
        ("Sat Valea Ors. Otopeni, Ilfov", "Orașul Otopeni"),
    ],
)
def test_village_with_abbreviated_unit_resolves_to_the_unit(address, expected):
    assert cd.locality_from_address(address)[0] == expected


def test_village_with_unit_keeps_county_in_address():
    assert cd.locality_from_address("Sat Rahău Mun. Sebeș, Alba")[1] == "Municipiul Sebeș, Alba"


def test_plain_village_without_unit_is_unchanged():
    assert cd.locality_from_address("Sat Racu, Harghita")[0] == "Sat Racu"


@pytest.mark.parametrize(
    "raw, expected",
    [("0001559737", "01559737"), ("000000001", "00000001"), ("0", "00000000"), ("00199150", "00199150")],
)
def test_pad_cif_strips_surplus_leading_zeros(raw, expected):
    assert cd.pad_cif(raw) == expected


def test_padded_variants_are_the_same_key():
    assert len({cd.pad_cif(x) for x in ("1559737", "01559737", "0001559737", "RO1559737", 1559737)}) == 1
