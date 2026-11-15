import pandas as pd

from app.brief import to_markdown, to_pdf
from app.summarize import summarize_column, summarize_file

T = pd.Series(range(20), dtype=float)


def test_far_placeholder_is_stripped():
    # radiometer-style: long run of -4078 before the sensor is live
    v = pd.Series([-4078.0] * 12 + [-12.0, -12.5, -11.8, 40.0, 128.0, 90.0, 20.0, -12.0])
    r = summarize_column(v, T)
    assert r["min"] == -12.5 and r["max"] == 128.0


def test_genuine_off_baseline_is_kept():
    # igniter-style: -80.84 is the real "off" level, then it switches on at -161.7
    v = pd.Series([-80.84] * 15 + [-161.7, -161.5, -161.7, -161.6, -161.7])
    r = summarize_column(v, T)
    assert r["baseline"] == -80.84 and r["t_onset_s"] == 15


def test_sparse_zeros_and_constant():
    assert summarize_column(pd.Series([0.0] * 15 + [19.6, 19.7, 19.8, 20.1, 19.9]), T)["note"]
    assert summarize_column(pd.Series([-12.59] * 20), T)["kind"] == "constant"


def test_file_and_renderers(tmp_path):
    p = tmp_path / "run.csv"
    pd.DataFrame({"Relative Time (s)": range(30), "Temp (degC)": [20.0] * 10 + [60.0] * 20}).to_csv(p, index=False)
    s = summarize_file(p)
    col = s["sheets"][0]["columns"]["Temp (degC)"]
    assert col["t_onset_s"] == 10
    brief = {"title": "T", "generated_at": "x", "access_date": "2026-11-15", "source": "PSI", "search_terms": ["a"],
             "caveats": ["c"], "investigations": [], "datasets": [{"accession": "PSI-1", "acronym": "X", **s}]}
    assert "| Temp (degC) |" in to_markdown(brief)
    pdf, info = to_pdf(brief)
    assert pdf.startswith(b"%PDF") and info["pages"] == 1
