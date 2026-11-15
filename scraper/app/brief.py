"""One-page RAG brief: every PSI fire-safety investigation + statistics of every locally downloaded data file.

Outputs the same content as JSON (structured), Markdown (for RAG ingestion) and a single-page PDF
(for humans). Text is extractive (no LLM): summaries are trimmed source sentences, data facts are
computed statistics.
"""

import asyncio
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .psi_client import PSIClient
from .summarize import TABULAR_EXT, summarize_cached

TITLE = "AETHER-G RAG Brief: NASA PSI Microgravity Fire-Safety Data"
CAVEATS = [
    "Extracted from NASA Physical Sciences Informatics (PSI) metadata and data files; no values are invented.",
    "Channel units are as given in the source column headers; channels without units are raw, uncalibrated sensor values.",
    "Times are in the source file's own time base (seconds). Onset = first clear departure from the initial baseline; peak = largest departure.",
    "Leading placeholder values logged before a sensor was live were removed; channels marked * had exact zeros treated as no-sample.",
]


# ----------------------------------------------------------------------------- search
async def fire_safety_investigations(psi: PSIClient, terms: list[str]) -> list[dict]:
    results = await asyncio.gather(*(psi.search(t, 50) for t in terms))
    seen: dict[str, dict] = {}
    for term, res in zip(terms, results):
        for r in res["results"]:
            seen.setdefault(r["accession"], {**r, "matched_terms": []})["matched_terms"].append(term)
    combustion = [r for r in seen.values() if (r.get("research_area") or "").lower().startswith("combustion")]
    others = [r for r in seen.values() if r not in combustion]
    return combustion + others


# ----------------------------------------------------------------------------- helpers
def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _trim(text: str | None, limit: int) -> str:
    t = _clean(text)
    if len(t) <= limit:
        return t
    cut = t[:limit]
    for sep in (". ", "; "):
        i = cut.rfind(sep)
        if i > limit * 0.5:
            return cut[: i + 1]
    return cut[: cut.rfind(" ")].rstrip(",;:") + "..."


def _year(d: str | None) -> str:
    m = re.search(r"(19|20)\d\d", d or "")
    return m.group(0) if m else "?"


def _fmt(x) -> str:
    if x is None:
        return "-"
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e6:
        return str(int(x))
    return f"{x:g}"


def _timeline(sheet: dict, window_s: float = 30.0) -> list[tuple[float, list[str]]]:
    events = sorted((v["t_onset_s"], k) for k, v in sheet["columns"].items() if v.get("kind") == "signal" and v.get("t_onset_s") is not None)
    groups: list[tuple[float, list[str]]] = []
    for t, name in events:
        if groups and t - groups[-1][0] <= window_s:
            groups[-1][1].append(name)
        else:
            groups.append((t, [name]))
    return groups


# ----------------------------------------------------------------------------- build
async def build_brief(psi: PSIClient, terms: list[str], raw_dir: Path, cache_dir: Path, combustion_only: bool = True) -> dict:
    found = await fire_safety_investigations(psi, terms)
    if combustion_only:
        found = [r for r in found if (r.get("research_area") or "").lower().startswith("combustion")]

    sem = asyncio.Semaphore(4)

    async def one(hit: dict) -> dict:
        acc = hit["accession"]
        async with sem:
            inv, files = await asyncio.gather(psi.investigation(acc), psi.files(acc))
        by_cat: dict[str, int] = {}
        for f in files:
            by_cat[f["category"] or "Other"] = by_cat.get(f["category"] or "Other", 0) + 1
        return {
            "accession": acc,
            "acronym": inv.get("acronym") or hit.get("acronym"),
            "title": inv.get("title"),
            "research_area": inv.get("research_area"),
            "sub_research_area": inv.get("sub_research_area"),
            "period": f"{_year(inv.get('start_date'))}-{_year(inv.get('end_date'))}",
            "flight_program": inv.get("flight_program"),
            "nasa_center": inv.get("nasa_center"),
            "hardware": _clean(inv.get("hardware")),
            "doi": inv.get("doi"),
            "license": inv.get("license"),
            "url": inv.get("url"),
            "related": inv.get("related", []),
            "objective": _trim(inv.get("objective"), 300),
            "impact": _trim(inv.get("research_impacts"), 200),
            "files": {"count": len(files), "total_bytes": int(sum(f["size_bytes"] or 0 for f in files)), "by_category": by_cat},
        }

    investigations = await asyncio.gather(*(one(h) for h in found))

    datasets = []
    for inv in investigations:
        folder = raw_dir / inv["accession"]
        if not folder.exists():
            continue
        for p in sorted(folder.iterdir()):
            if p.suffix.lower() in TABULAR_EXT:
                summary = await asyncio.to_thread(summarize_cached, p, cache_dir)
                datasets.append({"accession": inv["accession"], "acronym": inv["acronym"], **summary})

    now = datetime.now(UTC)
    return {
        "title": TITLE,
        "generated_at": now.isoformat(timespec="seconds"),
        "access_date": now.date().isoformat(),
        "source": "NASA Physical Sciences Informatics (PSI), https://psi.nasa.gov/",
        "search_terms": terms,
        "caveats": CAVEATS,
        "investigations": list(investigations),
        "datasets": datasets,
    }


# ----------------------------------------------------------------------------- markdown
def to_markdown(b: dict) -> str:
    L = [f"# {b['title']}", "",
         f"Source: {b['source']}. Accessed {b['access_date']}. Search terms: {', '.join(b['search_terms'])}.", ""]
    L += [f"- {c}" for c in b["caveats"]]
    L += ["", f"## Investigations ({len(b['investigations'])})", ""]
    for i in b["investigations"]:
        name = f"{i['accession']} {i['acronym']}" if i["acronym"] else i["accession"]
        L.append(f"### {name}: {i['title']}")
        meta = [i["sub_research_area"] or i["research_area"], i["period"], i["flight_program"], i["nasa_center"]]
        L.append(" | ".join(x for x in meta if x and x != "?-?"))
        if i["hardware"]:
            L.append(f"Hardware: {i['hardware']}.")
        if i["objective"]:
            L.append(f"Objective: {i['objective']}")
        if i["impact"]:
            L.append(f"Impact: {i['impact']}")
        cats = ", ".join(f"{k} {v}" for k, v in sorted(i["files"]["by_category"].items(), key=lambda kv: -kv[1]))
        L.append(f"Files: {i['files']['count']} ({i['files']['total_bytes'] / 1e9:.2f} GB): {cats}.")
        L.append(f"DOI {i['doi'] or '-'}; license {i['license'] or '-'}; related {', '.join(i['related']) or '-'}; {i['url']}")
        L.append("")
    L += [f"## Data files ({len(b['datasets'])})", ""]
    if not b["datasets"]:
        L.append("No data files downloaded yet. Use POST /api/psi/investigations/{accession}/download, then rebuild.")
    for d in b["datasets"]:
        L.append(f"### {d['accession']} {d['acronym'] or ''}: {d['file']}")
        L.append(f"{d['size_bytes'] / 1e6:.2f} MB, sha256 {d['sha256'][:16]}...")
        for s in d["sheets"]:
            span = f", t = {_fmt(s['time_span_s'][0])} to {_fmt(s['time_span_s'][1])} s" if s["time_span_s"] else ""
            L += ["", f"Sheet {s['sheet']} ({s['rows']} rows{span}):"]
            tl = _timeline(s)
            if tl:
                L.append("Timeline: " + "; ".join(f"t={_fmt(t)} s {', '.join(n)} depart baseline" for t, n in tl) + ".")
            signals = {k: v for k, v in s["columns"].items() if v.get("kind") == "signal"}
            if signals:
                L += ["", "| Channel | Baseline | Min | Max | Onset t (s) | Peak t (s) |", "|---|---|---|---|---|---|"]
                for k, v in signals.items():
                    star = "*" if v.get("note") else ""
                    L.append(f"| {k}{star} | {_fmt(v['baseline'])} | {_fmt(v['min'])} | {_fmt(v['max'])} | {_fmt(v.get('t_onset_s'))} | {_fmt(v.get('t_peak_s'))} |")
            consts = [f"{k}={_fmt(v['value'])}" for k, v in s["columns"].items() if v.get("kind") == "constant"]
            if consts:
                L += ["", "Constant (no activity): " + ", ".join(consts) + "."]
        L.append("")
    return "\n".join(L).rstrip() + "\n"


def to_json(b: dict) -> str:
    return json.dumps(b, indent=2, ensure_ascii=False)


# ----------------------------------------------------------------------------- pdf
def _pdf_story(b: dict, fs: float, objective_chars: int):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    lead = fs * 1.18
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=fs, leading=lead, spaceAfter=fs * 0.25)
    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=fs * 1.7, leading=fs * 2.0, spaceAfter=fs * 0.4)
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=fs * 1.2, leading=fs * 1.45, spaceBefore=fs * 0.5, spaceAfter=fs * 0.2,
                        textColor=colors.HexColor("#1d3557"))
    small = ParagraphStyle("s", parent=body, fontSize=fs * 0.9, leading=fs * 1.05, textColor=colors.HexColor("#444444"))
    e = lambda s: escape(str(s))

    st = [Paragraph(e(b["title"]), h1),
          Paragraph(e(f"Source: {b['source']}. Accessed {b['access_date']}."), small),
          Paragraph(e(" ".join(b["caveats"])), small),
          Paragraph(f"Investigations ({len(b['investigations'])})", h2)]
    for i in b["investigations"]:
        name = f"{i['accession']} {i['acronym']}" if i["acronym"] else i["accession"]
        meta = " | ".join(x for x in [i["sub_research_area"] or i["research_area"], i["period"], i["flight_program"]] if x and x != "?-?")
        obj = _trim(i["objective"], objective_chars)
        txt = (f"<b>{e(name)}: {e(i['title'])}</b>. {e(meta)}. "
               + (f"Hardware: {e(i['hardware'])}. " if i["hardware"] else "")
               + (f"Objective: {e(obj)} " if obj else "")
               + f"<i>{i['files']['count']} files, {i['files']['total_bytes'] / 1e9:.2f} GB; DOI {e(i['doi'] or '-')}; {e(i['license'] or '-')}.</i>")
        st.append(Paragraph(txt, body))

    st.append(Paragraph(f"Data files ({len(b['datasets'])})", h2))
    if not b["datasets"]:
        st.append(Paragraph("No data files downloaded yet.", body))
    for d in b["datasets"]:
        st.append(Paragraph(f"<b>{e(d['accession'])} {e(d['acronym'] or '')}: {e(d['file'])}</b> "
                            f"({d['size_bytes'] / 1e6:.2f} MB, sha256 {d['sha256'][:12]}...)", body))
        for s in d["sheets"]:
            span = f", t {_fmt(s['time_span_s'][0])}-{_fmt(s['time_span_s'][1])} s" if s["time_span_s"] else ""
            st.append(Paragraph(f"<b>{e(s['sheet'])}</b> ({s['rows']} rows{span})", small))
            tl = _timeline(s)
            if tl:
                st.append(Paragraph("Timeline: " + e("; ".join(f"t={_fmt(t)} {', '.join(n)}" for t, n in tl)), small))
            rows = [["Channel", "Base", "Min", "Max", "Onset", "Peak"]]
            for k, v in s["columns"].items():
                if v.get("kind") == "signal":
                    rows.append([k + ("*" if v.get("note") else ""), _fmt(v["baseline"]), _fmt(v["min"]), _fmt(v["max"]),
                                 _fmt(v.get("t_onset_s")), _fmt(v.get("t_peak_s"))])
            if len(rows) > 1:
                tsz = fs * 0.85
                t = Table(rows, repeatRows=1, hAlign="LEFT")
                t.setStyle(TableStyle([
                    ("FONT", (0, 0), (-1, -1), "Helvetica", tsz, tsz * 1.12),
                    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", tsz, tsz * 1.12),
                    ("TOPPADDING", (0, 0), (-1, -1), 0.4), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 1.5), ("RIGHTPADDING", (0, 0), (-1, -1), 1.5),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.HexColor("#1d3557")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f4f8")]),
                ]))
                st.append(t)
            consts = [k for k, v in s["columns"].items() if v.get("kind") == "constant"]
            if consts:
                st.append(Paragraph("Constant (no activity): " + e(", ".join(consts)), small))
            st.append(Spacer(1, fs * 0.3))
    return st


def to_pdf(b: dict) -> tuple[bytes, dict]:
    """Render on ONE A4 page in two columns, shrinking the font until everything fits."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    W, H = A4
    m, gap = 0.8 * cm, 0.5 * cm
    col_w = (W - 2 * m - gap) / 2

    def render(fs: float, obj_chars: int) -> tuple[bytes, int]:
        buf = io.BytesIO()
        doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=m, rightMargin=m, topMargin=m, bottomMargin=m,
                              title=b["title"], author="AETHER-G", subject="NASA PSI fire-safety RAG brief")
        frames = [Frame(m, m, col_w, H - 2 * m, id="c1", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
                  Frame(m + col_w + gap, m, col_w, H - 2 * m, id="c2", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)]
        doc.addPageTemplates([PageTemplate(id="two", frames=frames)])
        doc.build(_pdf_story(b, fs, obj_chars))
        return buf.getvalue(), doc.page

    attempt = None
    for obj_chars in (300, 200, 130):
        fs = 8.0
        while fs >= 4.5:
            data, pages = render(fs, obj_chars)
            attempt = (data, pages, fs, obj_chars)
            if pages == 1:
                return data, {"pages": 1, "font_size": fs, "objective_chars": obj_chars}
            fs = round(fs - 0.25, 2)
    data, pages, fs, obj_chars = attempt
    return data, {"pages": pages, "font_size": fs, "objective_chars": obj_chars, "warning": "content did not fit on one page"}
