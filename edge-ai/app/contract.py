"""Pydantic models mirroring docs/architecture.md (JSON contract), exactly.

Rules enforced here: no extra keys, finite numbers only, exactly three thermocouples,
only the two detector statuses and the two dust-check values.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr

Mode = Literal["LIVE_IOT", "NASA_REPLAY"]
Status = Literal["NOMINAL", "SMOLDERING_ANOMALY"]
DustCheck = Literal["NOMINAL_NO_EVENT", "DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE"]

# Strict float: accepts JSON ints and floats, rejects bool and strings; NaN/Infinity rejected.
Num = Annotated[StrictFloat, Field(allow_inf_nan=False)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Telemetry(_Strict):
    co_ppm: Num
    co2_ppm: Num
    voc_index: StrictInt
    ch4_ppm: Num
    pm25_ug_m3: Num
    airflow_cm_s: Num
    thermocouples_c: Annotated[list[Num], Field(min_length=3, max_length=3)]
    max_ir_temp_c: Num
    smolder_velocity_mm_s: Num


class Stage1Detector(_Strict):
    status: Status
    confidence_score: Annotated[StrictFloat, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
    false_alarm_dust_check: DustCheck


class Sample(_Strict):
    """One contract object as sent by the Jetson or simulator.

    `stage1_detector` is optional on ingest and, when present, is provisional and ignored.
    """

    timestamp_ms: Annotated[StrictInt, Field(ge=0)]
    mode: Mode
    compartment_id: Annotated[StrictStr, Field(min_length=1)]
    telemetry: Telemetry
    stage1_detector: Stage1Detector | None = None


class IngestBatch(_Strict):
    items: Annotated[list[Sample], Field(min_length=1, max_length=100)]
