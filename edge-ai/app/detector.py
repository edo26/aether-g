"""Stage-1 rule-based detector (docs/backend-plan.md section 4, edge-ai/README.md).

State is kept per compartment_id. Derivatives use the sample's `timestamp_ms`, never arrival
time. Arrival time (monotonic) is used only to notice a silent source (session reset after 30 s).

Thresholds are demo thresholds, not NASA-derived exposure limits.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Callable

from app.contract import Sample

CO_TRIGGER_PPM = 25.0
CO_FULL_SCALE_PPM = 100.0  # severity saturates at CO = 125 ppm
NOMINAL_CONFIDENCE = 0.95
BASELINE_SAMPLES = 10  # first 5 s at 500 ms
SESSION_IDLE_RESET_S = 30.0
DUST_PM_RISE_UG_M3 = 35.0
DUST_TEMP_BAND_C = 5.0
# Reference PM2.5 for "clean" air. The session baseline is capped at this value so that a
# session that *starts* inside a dust cloud (the simulator's `dust` scenario spikes from
# t=0) still reads as dust instead of learning the dust as its normal.
PM25_NOMINAL_REF_UG_M3 = 10.0
CO2_NOISE_FLOOR_PPM = 20.0

NOMINAL = "NOMINAL"
ANOMALY = "SMOLDERING_ANOMALY"
NO_EVENT = "NOMINAL_NO_EVENT"
DUST_REJECTED = "DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE"


@dataclass(frozen=True)
class Features:
    """Supporting evidence. Logged and returned, not triggers (except dco_dt, pm/temp for dust)."""

    dco_dt: float
    ratio: float | None  # dCO / dCO2 vs baseline; None when dCO2 is below the noise floor
    voc_delta: float
    pm_delta: float
    temp_delta: float

    def as_dict(self) -> dict[str, float | None]:
        return asdict(self)


@dataclass(frozen=True)
class Result:
    status: str
    confidence_score: float
    false_alarm_dust_check: str
    features: Features
    previous_status: str
    reset_reason: str | None = None  # set when this sample started a new session
    dust_event: bool = False  # this sample is the first of a dust rejection (marker just switched on)

    @property
    def status_changed(self) -> bool:
        return self.status != self.previous_status

    def stage1(self) -> dict:
        return {
            "status": self.status,
            "confidence_score": self.confidence_score,
            "false_alarm_dust_check": self.false_alarm_dust_check,
        }


@dataclass
class _State:
    mode: str
    last_ts_ms: int
    last_seen: float
    prev_co: float | None = None
    prev_ts_ms: int | None = None
    baseline_rows: list[dict[str, float]] = field(default_factory=list)
    baseline: dict[str, float] | None = None
    latched: bool = False
    status: str = NOMINAL
    prev_dust: bool = False


def _channels(sample: Sample) -> dict[str, float]:
    t = sample.telemetry
    return {
        "co": t.co_ppm,
        "co2": t.co2_ppm,
        "voc": float(t.voc_index),
        "pm25": t.pm25_ug_m3,
        # hottest of the three probes and the IR maximum
        "temp": max(max(t.thermocouples_c), t.max_ir_temp_c),
    }


def _median_baseline(rows: list[dict[str, float]]) -> dict[str, float]:
    return {k: median(r[k] for r in rows) for k in rows[0]}


class Detector:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._states: dict[str, _State] = {}

    def reset(self, compartment_id: str | None = None) -> None:
        if compartment_id is None:
            self._states.clear()
        else:
            self._states.pop(compartment_id, None)

    def status_of(self, compartment_id: str) -> str:
        state = self._states.get(compartment_id)
        return state.status if state else NOMINAL

    def process(self, sample: Sample) -> Result:
        now = self._clock()
        cid = sample.compartment_id
        state = self._states.get(cid)
        previous_status = state.status if state else NOMINAL
        reset_reason = None

        if state is not None:
            if state.mode != sample.mode:
                reset_reason = "mode_change"
            elif sample.timestamp_ms < state.last_ts_ms:
                reset_reason = "timestamp_backwards"
            elif now - state.last_seen > SESSION_IDLE_RESET_S:
                reset_reason = "idle_timeout"
            if reset_reason:
                state = None
        if state is None:
            state = _State(mode=sample.mode, last_ts_ms=sample.timestamp_ms, last_seen=now)
            self._states[cid] = state

        ch = _channels(sample)

        # Rolling baseline: median of the first BASELINE_SAMPLES samples; until it is full the
        # baseline is the median of what we have so far (including this sample).
        if state.baseline is None:
            state.baseline_rows.append(ch)
            baseline = _median_baseline(state.baseline_rows)
            if len(state.baseline_rows) >= BASELINE_SAMPLES:
                state.baseline = baseline
        else:
            baseline = state.baseline

        dco_dt = 0.0
        if state.prev_ts_ms is not None and sample.timestamp_ms > state.prev_ts_ms:
            dco_dt = (ch["co"] - (state.prev_co or 0.0)) / ((sample.timestamp_ms - state.prev_ts_ms) / 1000.0)
        state.prev_co = ch["co"]
        state.prev_ts_ms = sample.timestamp_ms
        state.last_ts_ms = sample.timestamp_ms
        state.last_seen = now

        co_delta = ch["co"] - baseline["co"]
        co2_delta = ch["co2"] - baseline["co2"]
        pm_delta = ch["pm25"] - min(baseline["pm25"], PM25_NOMINAL_REF_UG_M3)
        temp_delta = ch["temp"] - baseline["temp"]
        features = Features(
            dco_dt=round(dco_dt, 4),
            ratio=round(co_delta / co2_delta, 4) if co2_delta >= CO2_NOISE_FLOOR_PPM else None,
            voc_delta=round(ch["voc"] - baseline["voc"], 3),
            pm_delta=round(pm_delta, 3),
            temp_delta=round(temp_delta, 3),
        )

        if not state.latched and ch["co"] > CO_TRIGGER_PPM and dco_dt > 0.0:
            state.latched = True  # latched for the rest of the session

        if state.latched:
            severity = min(max(ch["co"] - CO_TRIGGER_PPM, 0.0) / CO_FULL_SCALE_PPM, 1.0)
            status = ANOMALY
            confidence = round(0.6 + 0.35 * severity, 3)
            dust = NO_EVENT  # gas/thermal anomaly takes precedence over dust rejection
        else:
            status = NOMINAL
            confidence = NOMINAL_CONFIDENCE
            is_dust = (
                pm_delta > DUST_PM_RISE_UG_M3
                and ch["co"] <= CO_TRIGGER_PPM
                and abs(temp_delta) <= DUST_TEMP_BAND_C
            )
            dust = DUST_REJECTED if is_dust else NO_EVENT

        state.status = status
        is_dust_now = dust == DUST_REJECTED
        dust_event = is_dust_now and not state.prev_dust
        state.prev_dust = is_dust_now
        return Result(
            status=status,
            confidence_score=confidence,
            false_alarm_dust_check=dust,
            features=features,
            previous_status=previous_status,
            reset_reason=reset_reason,
            dust_event=dust_event,
        )
