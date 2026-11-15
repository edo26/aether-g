"""Deterministic advisory text: used when the LLM fails or is slow, and for dust events (no LLM).

Never invents a NASA procedure. Operational steps are demo guidance bound to a vehicle checklist
that this project does not have; citations are only for scientific findings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.advisor.rag import Fact

DISCLAIMER = (
    "Operational steps are demo guidance, not an official NASA checklist. Detector thresholds are "
    "demo thresholds. Citations support the science context only."
)


@dataclass
class Advisory:
    warning: str
    steps: list[str]
    citations: list[dict[str, Any]]
    raw_text: str
    model: str = "deterministic"
    fallback: bool = True
    facts_used: list[str] = field(default_factory=list)
    first_token_ms: int | None = None


def render(warning: str, steps: list[str], fact_ids: list[str]) -> str:
    lines = [f"WARNING: {warning}"]
    lines += [f"{i}. {s}" for i, s in enumerate(steps, start=1)]
    lines.append("SOURCES: " + (", ".join(fact_ids) if fact_ids else "none available"))
    return "\n".join(lines)


def _ch(summary: dict | None, name: str) -> dict | None:
    return (summary or {}).get("channels", {}).get(name)


def build(
    trigger: str,
    compartment_id: str,
    status: str,
    summary: dict | None,
    facts: list[Fact],
    *,
    question: str | None = None,
) -> Advisory:
    co, voc, ir, pm = (_ch(summary, c) for c in ("co_ppm", "voc_index", "max_ir_temp_c", "pm25_ug_m3"))

    if trigger == "dust_rejected":
        pm_txt = f" to {pm['last']:g} ug/m3" if pm else ""
        warning = (f"Dust event in {compartment_id}: PM2.5 rose{pm_txt} with no CO or temperature rise, "
                   "so Stage-1 stays NOMINAL.")
        steps = [
            "Confirm CO and temperature are flat while PM2.5 is high.",
            "Check for a dust source such as disturbed filters or cargo handling.",
            "Keep watching CO and temperature; escalate if either starts rising.",
        ]
    elif status == "SMOLDERING_ANOMALY":
        bits = []
        if co:
            trend = "rising" if co["slope_per_s"] > 0 else "elevated"
            bits.append(f"CO is {co['last']:g} ppm and {trend} ({co['slope_per_s']:+.2f} ppm/s)")
        else:
            bits.append("CO crossed the 25 ppm demo threshold while rising")
        extra = []
        if voc and voc["slope_per_s"] > 0:
            extra.append("increasing VOC")
        if ir and ir["slope_per_s"] > 0:
            extra.append("localized heat")
        warning = f"Smoldering anomaly in {compartment_id}: " + bits[0] + (
            ", with " + " and ".join(extra) + "." if extra else ".")
        steps = [
            "Alert the crew and confirm the affected compartment and live readings.",
            "Open the vehicle's approved electrical/fire response checklist and follow its isolation instructions.",
            "Track gas and temperature trends and report whether the anomaly persists after checklist actions.",
        ]
    else:
        warning = f"{compartment_id} is NOMINAL: no smoldering indicators in the recent window."
        steps = [
            "No action needed from the detector.",
            "Keep monitoring CO, VOC and temperature trends.",
            "Report any change in the readings.",
        ]
    if question:
        warning = ("The language model is unavailable, so this is the rule-based status instead of an "
                   "answer to your question. " + warning)

    fact_ids = [f.id for f in facts]
    return Advisory(
        warning=warning, steps=steps, citations=[f.citation() for f in facts],
        raw_text=render(warning, steps, fact_ids), facts_used=fact_ids,
    )
