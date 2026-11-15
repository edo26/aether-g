"""Window summary: min/max/last/slope per channel over the recent window. Sent to the LLM instead of raw samples."""

from __future__ import annotations

from typing import Any, Iterable

CHANNELS = (
    "co_ppm", "co2_ppm", "voc_index", "ch4_ppm", "pm25_ug_m3", "airflow_cm_s",
    "tc1_c", "tc2_c", "tc3_c", "max_ir_temp_c", "smolder_velocity_mm_s",
)


def slope_per_s(points: list[tuple[float, float]]) -> float:
    """Least-squares slope (units per second)."""
    n = len(points)
    if n < 2:
        return 0.0
    mt = sum(t for t, _ in points) / n
    mv = sum(v for _, v in points) / n
    den = sum((t - mt) ** 2 for t, _ in points)
    return sum((t - mt) * (v - mv) for t, v in points) / den if den else 0.0


def summarize(rows: Iterable[dict[str, Any]], window_s: float, source: str) -> dict[str, Any]:
    """`rows` are dicts with `t_s` (seconds, any epoch) plus channel keys."""
    rows = sorted(rows, key=lambda r: r["t_s"])
    channels: dict[str, dict[str, float]] = {}
    for ch in CHANNELS:
        pts = [(r["t_s"], float(r[ch])) for r in rows if r.get(ch) is not None]
        if not pts:
            continue
        values = [v for _, v in pts]
        channels[ch] = {
            "min": round(min(values), 2), "max": round(max(values), 2),
            "last": round(values[-1], 2), "slope_per_s": round(slope_per_s(pts), 3),
        }
    return {"window_s": window_s, "samples": len(rows), "source": source, "channels": channels}
