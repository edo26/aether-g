from pathlib import Path

import pytest

from app.advisor.rag import Rag, facts_from_manual, tokenize

ROOT = Path(__file__).resolve().parents[2] / "data" / "rag"


@pytest.fixture(scope="module")
def rag():
    return Rag.load(ROOT / "psi_fire_safety_brief.json", ROOT / "facts_manual.json")


def test_chunks_one_per_investigation_and_sheet(rag):
    kinds = [f.kind for f in rag.facts]
    assert kinds.count("investigation") == 10 and kinds.count("sheet") == 5 and kinds.count("manual") == 2
    ids = [f.id for f in rag.facts]
    assert len(set(ids)) == len(ids)
    assert "PSI-98" in ids
    sheet = next(f for f in rag.facts if f.kind == "sheet")
    assert sheet.id.startswith("PSI-98:PSI-98_Raw Data_Saffire-I Telemetry Data Good Process Version.xlsx:")


def test_every_fact_carries_a_citation_source(rag):
    for f in rag.facts:
        c = f.citation()
        assert c["url"], f.id
        if f.kind != "manual":
            assert c["doi"] and c["license"], f.id
    assert rag.caveats


def test_smoldering_query_finds_gas_fact_first(rag):
    top = rag.retrieve("smoldering cable overheating carbon monoxide carbon dioxide gas temperature rise", 4)
    assert top and top[0].id == "NTRS-20240002981"
    assert len(top) <= 4


def test_smoke_query_finds_smoke_investigations(rag):
    ids = [f.id for f in rag.retrieve("smoke aerosol particulate detection", 4)]
    assert {"PSI-101", "PSI-102"} & set(ids)


def test_no_match_returns_nothing_and_order_is_stable(rag):
    assert rag.retrieve("zzzz qqqq", 4) == []
    assert rag.retrieve("smoke", 4) == rag.retrieve("smoke", 4)


def test_missing_files_do_not_crash(tmp_path):
    r = Rag.load(tmp_path / "nope.json", tmp_path / "nope2.json")
    assert r.facts == [] and r.retrieve("smoke") == []


def test_manual_fact_without_url_is_refused():
    with pytest.raises(ValueError):
        facts_from_manual({"facts": [{"id": "x", "title": "t", "text": "t", "source": "s"}]})


def test_tokenize_basics():
    assert tokenize("The Smoldering cables, CO2!") == ["smoldering", "cable", "co2"]
