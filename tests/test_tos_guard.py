import pytest

from scraper import ejobs, portals


@pytest.mark.parametrize("mod,args", [(ejobs, ["--out", "x.jsonl"]), (portals, ["--docs", "d.jsonl", "--out", "x.jsonl"])])
def test_runs_refuse_without_written_permission(mod, args, capsys):
    assert mod.main(args) == 2
    assert "docs/TOS.md" in capsys.readouterr().out
