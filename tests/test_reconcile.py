"""Tests for scraper.reconcile (phase-4 planning logic) and its wiring in batch/CLI."""

import json
from datetime import date

import pytest

from scraper import batch, derkaiser, reconcile as rc
from scraper import sources as src
from scraper.company_doc import build_company_doc

TODAY = date(2026, 11, 13)


def ours(cui=1559737, **kw):
    anaf = {"cui": cui, "name": "CONTINENTAL HOTELS SA", "address": "Municipiul Bucuresti, Sector 1", "inactive": False}
    return build_company_doc(anaf, today=TODAY, **kw)


def stored(doc=None, **over):
    base = dict(doc or ours())
    base.update(over)
    return base


# --- decide -----------------------------------------------------------------


def test_no_existing_document_means_create():
    assert rc.decide(ours(), []).action == rc.CREATE


def test_documents_of_other_firms_are_ignored():
    other = stored(ours(5), id="00000005")
    assert rc.decide(ours(), [other]).action == rc.CREATE


def test_only_unpadded_legacy_document_is_a_conflict_not_a_create():
    legacy = stored(id="1559737", scraperFile="inviitor-ro-nodejs-scraper")
    d = rc.decide(ours(), [legacy])
    assert d.action == rc.CONFLICT_UNPADDED
    assert "1559737" in d.reason and d.existing_id == "1559737"


def test_two_documents_with_same_exact_id_need_review():
    a = stored(scraperFile="derkaiser-python-scraper")
    assert rc.decide(ours(), [a, dict(a)]).action == rc.REVIEW


def test_document_without_scraperfile_needs_review():
    existing = stored()
    existing.pop("scraperFile")
    assert rc.decide(ours(), [existing]).action == rc.REVIEW


def test_document_owned_by_a_fleet_scraper_is_never_overwritten():
    fleet = stored(scraperFile="https://github.com/peviitor-scrapers/continental-hotels-srl-nodejs-scraper/actions/workflows/scrape.yml")
    d = rc.decide(ours(), [fleet])
    assert d.action == rc.SKIP_FOREIGN and "continental-hotels" in d.reason


def test_own_unchanged_document_is_skipped_even_if_lastscraped_and_jobs_differ():
    existing = stored(scraperFile="derkaiser-python-scraper", lastScraped="2020-01-01", existingJobsCount=[99])
    assert rc.decide(ours(), [existing]).action == rc.SKIP_UNCHANGED


def test_own_document_that_differs_is_updated_and_lists_the_fields():
    existing = stored(scraperFile="derkaiser-python-scraper", location=["Altundeva"], brand="OLD BRAND")
    d = rc.decide(ours(), [existing])
    assert d.action == rc.UPDATE and d.changed == ["brand", "location"]


def test_inviitor_documents_are_updatable_by_default_but_configurable():
    existing = stored(scraperFile="inviitor-ro-nodejs-scraper", brand="OLD")
    assert rc.decide(ours(), [existing]).action == rc.UPDATE
    assert rc.decide(ours(), [existing], updatable={"derkaiser-python-scraper"}).action == rc.SKIP_FOREIGN


def test_solr_style_single_element_lists_do_not_cause_false_differences():
    existing = stored(scraperFile=["derkaiser-python-scraper"], company=[ours()["company"]], status=["activ"])
    assert rc.decide(ours(), [existing]).action == rc.SKIP_UNCHANGED


def test_exact_document_next_to_a_legacy_one_is_noted():
    exact = stored(scraperFile="derkaiser-python-scraper")
    legacy = stored(id="1559737", scraperFile="inviitor-ro-nodejs-scraper")
    d = rc.decide(ours(), [exact, legacy])
    assert d.action == rc.SKIP_UNCHANGED and "also unpadded: 1559737" in d.reason


# --- load_existing_index ----------------------------------------------------


def test_load_existing_index_groups_by_integer_cui_and_skips_junk(tmp_path):
    f = tmp_path / "existing.jsonl"
    f.write_text(
        "﻿" + json.dumps({"id": "01559737"}) + "\n\n" + json.dumps({"id": "1559737"}) + "\nnot json\n"
        + json.dumps({"id": "abc"}) + "\n" + json.dumps({"id": "5"}) + "\n",
        encoding="utf-8",
    )
    index = rc.load_existing_index(f)
    assert sorted(index) == [5, 1559737] and len(index[1559737]) == 2


# --- batch + CLI wiring -----------------------------------------------------


def record(cui):
    return {"cui": int(cui), "name": f"FIRMA {int(cui)} SRL", "address": "Municipiul Sibiu, Sibiu", "inactive": False}


class Lookup:
    def __call__(self, cif):
        return record(cif)


def test_batch_writes_a_plan_and_counts_actions(tmp_path):
    index = {
        2: [{"id": "2", "scraperFile": "inviitor-ro-nodejs-scraper"}],  # legacy unpadded
        3: [{"id": "00000003", "scraperFile": "https://github.com/x/y/actions/workflows/scrape.yml"}],  # foreign
    }
    report = batch.run_batch([1, 2, 3], tmp_path, lookup=Lookup(), today=TODAY, existing_index=index)
    assert report["plan"] == {"create": 1, "conflict_unpadded": 1, "skip_foreign": 1}
    rows = [json.loads(x) for x in (tmp_path / "plan.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["action"] for r in rows] == ["create", "conflict_unpadded", "skip_foreign"]
    assert report["dry_run"] is True


def test_batch_without_existing_index_has_no_plan(tmp_path):
    report = batch.run_batch([1], tmp_path, lookup=Lookup(), today=TODAY)
    assert report["plan"] is None and not (tmp_path / "plan.jsonl").exists()


def test_cli_existing_flag_produces_a_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(src, "_default_lookup", Lookup())
    cifs = tmp_path / "cifs.txt"
    cifs.write_text("10\n20\n", encoding="utf-8")
    existing = tmp_path / "existing.jsonl"
    existing.write_text(json.dumps({"id": "00000010", "scraperFile": "derkaiser-python-scraper"}) + "\n", encoding="utf-8")
    code = derkaiser.main(["--cifs", str(cifs), "--out", str(tmp_path / "out"), "--pause", "0", "--existing", str(existing)])
    assert code == 0
    plan = json.loads(capsys.readouterr().out)["plan"]
    assert plan["create"] == 1 and sum(plan.values()) == 2
