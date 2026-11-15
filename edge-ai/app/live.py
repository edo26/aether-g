"""Latest state per compartment, kept in memory for /status."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.contract import Sample
from app.detector import Result


@dataclass
class Latest:
    sample: Sample
    result: Result
    source: str
    received_mono: float
    received_at: datetime


class LiveState:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._latest: dict[str, Latest] = {}
        self._ring: dict[str, deque] = {}

    def update(self, sample: Sample, result: Result, source: str) -> None:
        self._latest[sample.compartment_id] = Latest(
            sample, result, source, self._clock(), datetime.now(timezone.utc)
        )

        t = sample.telemetry
        row = {"t_s": sample.timestamp_ms / 1000.0, "co_ppm": t.co_ppm, "co2_ppm": t.co2_ppm,
               "voc_index": t.voc_index, "ch4_ppm": t.ch4_ppm, "pm25_ug_m3": t.pm25_ug_m3,
               "airflow_cm_s": t.airflow_cm_s, "max_ir_temp_c": t.max_ir_temp_c,
               "smolder_velocity_mm_s": t.smolder_velocity_mm_s,
               "tc1_c": t.thermocouples_c[0], "tc2_c": t.thermocouples_c[1], "tc3_c": t.thermocouples_c[2]}
        if result.reset_reason:  # a new session: never mix its window with the previous one
            self._ring.pop(sample.compartment_id, None)
        self._ring.setdefault(sample.compartment_id, deque(maxlen=480)).append(row)  # 4 min at 2 Hz

    def window(self, compartment_id: str, seconds: float) -> list[dict]:
        rows = list(self._ring.get(compartment_id, ()))
        if not rows:
            return []
        cutoff = rows[-1]["t_s"] - seconds
        return [r for r in rows if r["t_s"] >= cutoff]

    def get(self, compartment_id: str) -> Latest | None:
        return self._latest.get(compartment_id)

    def all(self) -> dict[str, Latest]:
        return dict(self._latest)

    def age_s(self, latest: Latest) -> float:
        return round(self._clock() - latest.received_mono, 2)
