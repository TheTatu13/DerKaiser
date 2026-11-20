"""export_csv: GDPR guard (a personal ID in the CIF field must never reach the CSV)."""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import export_csv as E  # noqa: E402


def test_rows_with_a_cnp_in_the_cif_field_are_not_exported(tmp_path):
    src = tmp_path / "j.jsonl"
    rows = [{"url": "https://a.ro/1", "title": "Operator", "company": "A SRL", "cif": "12345678", "_source": "anofm"},
            {"url": "https://a.ro/2", "title": "Ingrijitor", "company": "ION MARIA", "cif": "2850425440010", "_source": "anofm"},
            {"url": "https://a.ro/3", "title": "Asistent", "company": "Spital", "cif": None, "_source": "posturi.gov.ro"}]
    src.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = tmp_path / "o.csv"
    assert E.export([str(src)], str(out)) == 2
    text = out.read_text(encoding="utf-8-sig")
    assert "2850425440010" not in text and "ION MARIA" not in text and "Spital" in text
    assert [r["link"] for r in csv.DictReader(text.splitlines())] == ["https://a.ro/1", "https://a.ro/3"]
