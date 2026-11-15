"""Latest thermal camera frame per compartment, kept in memory (never written to InfluxDB)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.contract import ThermalFrame


def frame_stats(frame: ThermalFrame) -> dict[str, Any]:
    px = frame.pixels_c
    i = max(range(len(px)), key=px.__getitem__)
    return {
        "min_c": round(min(px), 2),
        "max_c": round(px[i], 2),
        "mean_c": round(sum(px) / len(px), 2),
        "hotspot": {"x": i % frame.width, "y": i // frame.width, "temp_c": round(px[i], 2)},
    }


@dataclass
class ThermalEntry:
    frame: ThermalFrame
    stats: dict[str, Any]
    source: str
    received_mono: float
    received_at: datetime


class ThermalState:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._latest: dict[str, ThermalEntry] = {}

    def update(self, frame: ThermalFrame, source: str) -> ThermalEntry:
        entry = ThermalEntry(frame, frame_stats(frame), source, self._clock(), datetime.now(timezone.utc))
        self._latest[frame.compartment_id] = entry
        return entry

    def get(self, compartment_id: str | None) -> ThermalEntry | None:
        if compartment_id:
            return self._latest.get(compartment_id)
        return max(self._latest.values(), key=lambda e: e.received_mono, default=None)

    def all(self) -> dict[str, ThermalEntry]:
        return dict(self._latest)

    def age_s(self, entry: ThermalEntry) -> float:
        return round(self._clock() - entry.received_mono, 2)
