"""Local retrieval over curated NASA facts (docs/backend-plan.md section 6). No network, no embeddings.

Sources: the PSI brief produced by scraper/ (split into one chunk per investigation and one per
data-sheet timeline) and the hand-written facts_manual.json. BM25 in pure Python.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("aether.rag")

_STOP = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was were with into than then which".split()
)


def tokenize(text: str) -> list[str]:
    out = []
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        if tok in _STOP:
            continue
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.append(tok)
    return out


@dataclass(frozen=True)
class Fact:
    id: str
    title: str
    text: str
    kind: str  # investigation | sheet | manual
    source: str
    url: str | None = None
    doi: str | None = None
    license: str | None = None

    def citation(self) -> dict:
        return {"id": self.id, "title": self.title, "source": self.source,
                "url": self.url, "doi": self.doi, "license": self.license}


@dataclass
class Rag:
    facts: list[Fact] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    k1: float = 1.5
    b: float = 0.75

    def __post_init__(self) -> None:
        self._index()

    def _index(self) -> None:
        # the title is repeated so that a match in it counts for more than one in the body
        self._tokens = [Counter(tokenize(f"{f.title} {f.title} {f.text}")) for f in self.facts]
        self._len = [sum(c.values()) for c in self._tokens]
        self._avg = (sum(self._len) / len(self._len)) if self._len else 0.0
        df: Counter = Counter()
        for c in self._tokens:
            df.update(c.keys())
        n = len(self.facts)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def retrieve(self, query: str, k: int = 4) -> list[Fact]:
        q = set(tokenize(query))
        scored = []
        for i, fact in enumerate(self.facts):
            tf = self._tokens[i]
            score = 0.0
            for term in q:
                f = tf.get(term)
                if f:
                    norm = 1 - self.b + self.b * self._len[i] / (self._avg or 1)
                    score += self._idf[term] * f * (self.k1 + 1) / (f + self.k1 * norm)
            if score > 0:
                scored.append((-score, fact.id, fact))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [f for _, _, f in scored[:k]]

    def get(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    # ---- loading ----------------------------------------------------------------------------
    @classmethod
    def load(cls, brief_path: str | Path | None, manual_path: str | Path | None) -> "Rag":
        facts: list[Fact] = []
        caveats: list[str] = []
        if brief_path:
            try:
                brief = json.loads(Path(brief_path).read_text())
                f, caveats = facts_from_brief(brief)
                facts += f
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log.error("RAG brief %s not loaded (%s): advisories will have no PSI citations", brief_path, exc)
        if manual_path:
            try:
                facts += facts_from_manual(json.loads(Path(manual_path).read_text()))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log.error("RAG manual facts %s not loaded (%s)", manual_path, exc)
        return cls(facts=facts, caveats=caveats)


def _num(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def facts_from_brief(brief: dict) -> tuple[list[Fact], list[str]]:
    facts: list[Fact] = []
    by_accession: dict[str, dict] = {}
    for inv in brief.get("investigations", []):
        acc = inv["accession"]
        by_accession[acc] = inv
        name = inv.get("acronym") or inv.get("title", acc)
        parts = [f"{inv.get('title', '')}."]
        for label, key in (("Objective", "objective"), ("Impact", "impact"), ("Hardware", "hardware"),
                           ("Flight program", "flight_program"), ("Research area", "sub_research_area"),
                           ("NASA center", "nasa_center"), ("Period", "period")):
            if inv.get(key):
                parts.append(f"{label}: {inv[key]}.")
        facts.append(Fact(
            id=acc, title=f"{name} ({acc})", text=" ".join(parts), kind="investigation",
            source="NASA Physical Sciences Informatics (PSI)", url=inv.get("url"),
            doi=inv.get("doi"), license=inv.get("license"),
        ))
    for ds in brief.get("datasets", []):
        acc = ds["accession"]
        inv = by_accession.get(acc, {})
        name = ds.get("acronym") or acc
        for sh in ds.get("sheets", []):
            lines = [f"{name} telemetry file sheet {sh['sheet']}: {sh.get('rows', '?')} rows, "
                     f"time {_num(sh['time_span_s'][0])} to {_num(sh['time_span_s'][1])} s (source time base)."
                     if sh.get("time_span_s") else f"{name} telemetry file sheet {sh['sheet']}."]
            for col, c in sh.get("columns", {}).items():
                if c.get("kind") == "constant":
                    lines.append(f"{col}: constant {_num(c.get('value'))}.")
                    continue
                bits = [f"baseline {_num(c['baseline'])}"] if "baseline" in c else []
                if "min" in c and "max" in c:
                    bits.append(f"range {_num(c['min'])} to {_num(c['max'])}")
                if "t_onset_s" in c:
                    bits.append(f"first clear departure from baseline at t={_num(c['t_onset_s'])} s")
                if "peak_value" in c:
                    bits.append(f"peak {_num(c['peak_value'])} at t={_num(c.get('t_peak_s'))} s")
                lines.append(f"{col}: " + ", ".join(bits) + ".")
            facts.append(Fact(
                id=f"{acc}:{ds['file']}:{sh['sheet']}", title=f"{name} {sh['sheet']} timeline",
                text=" ".join(lines), kind="sheet",
                source=f"NASA PSI data file {ds['file']} (sha256 {ds.get('sha256', '?')[:12]})",
                url=inv.get("url"), doi=inv.get("doi"), license=inv.get("license"),
            ))
    return facts, list(brief.get("caveats", []))


def facts_from_manual(manual: dict) -> list[Fact]:
    out = []
    for f in manual["facts"]:
        if not f.get("url"):
            raise ValueError(f"manual fact {f.get('id')!r} has no source url")
        out.append(Fact(id=f["id"], title=f["title"], text=f["text"], kind="manual",
                        source=f["source"], url=f["url"], license=f.get("license")))
    return out
