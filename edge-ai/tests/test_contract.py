import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contract import Sample

SIMULATOR = Path(__file__).resolve().parents[2] / "simulator" / "simulator.py"
SCENARIOS = ("nominal", "dust", "smoldering", "airgapped")

GOOD = {
    "timestamp_ms": 500,
    "mode": "LIVE_IOT",
    "compartment_id": "AVIONICS_BAY_B2",
    "telemetry": {
        "co_ppm": 1.5, "co2_ppm": 450.0, "voc_index": 12, "ch4_ppm": 0.2,
        "pm25_ug_m3": 4.0, "airflow_cm_s": 3.0,
        "thermocouples_c": [22.1, 22.5, 23.0],
        "max_ir_temp_c": 24.2, "smolder_velocity_mm_s": 0.0,
    },
    "stage1_detector": {
        "status": "NOMINAL", "confidence_score": 0.95,
        "false_alarm_dust_check": "NOMINAL_NO_EVENT",
    },
}


def simulator_lines(scenario: str, n: int = 150) -> list[str]:
    """Run the real simulator briefly by importing its generator, not its sleep loop."""
    spec_globals: dict = {}
    exec(compile(SIMULATOR.read_text(), str(SIMULATOR), "exec"), spec_globals)
    make_sample = spec_globals["make_sample"]
    out, prev = [], None
    for i in range(n):
        prev = make_sample(scenario, i * 500, prev)
        out.append(json.dumps(prev, allow_nan=False))
    return out


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_accepts_every_simulator_line(scenario):
    for line in simulator_lines(scenario):
        Sample.model_validate_json(line)


def test_stage1_detector_is_optional():
    d = copy.deepcopy(GOOD)
    del d["stage1_detector"]
    assert Sample.model_validate(d).stage1_detector is None


def mutate(path, value):
    d = copy.deepcopy(GOOD)
    node = d
    for key in path[:-1]:
        node = node[key]
    if value is KeyError:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return d


@pytest.mark.parametrize(
    "path,value",
    [
        (("telemetry", "thermocouples_c"), [22.0, 23.0]),
        (("telemetry", "thermocouples_c"), [22.0, 23.0, 24.0, 25.0]),
        (("telemetry", "co_ppm"), float("nan")),
        (("telemetry", "co2_ppm"), float("inf")),
        (("telemetry", "thermocouples_c"), [22.0, float("nan"), 23.0]),
        (("stage1_detector", "status"), "WARNING"),
        (("stage1_detector", "false_alarm_dust_check"), "DUST"),
        (("telemetry", "co_ppm"), KeyError),
        (("telemetry",), KeyError),
        (("compartment_id",), KeyError),
        (("timestamp_ms",), KeyError),
        (("mode",), "SIMULATED"),
        (("telemetry", "voc_index"), 12.5),
        (("telemetry", "co_ppm"), "1.5"),
        (("telemetry", "co_ppm"), True),
        (("timestamp_ms",), -1),
        (("compartment_id",), ""),
    ],
)
def test_rejects_bad_payload(path, value):
    with pytest.raises(ValidationError):
        Sample.model_validate(mutate(path, value))


def test_rejects_nan_from_raw_json_text():
    raw = json.dumps(GOOD).replace('"co_ppm": 1.5', '"co_ppm": NaN')
    with pytest.raises(ValidationError):
        Sample.model_validate_json(raw)


def test_rejects_extra_envelope_key():
    d = copy.deepcopy(GOOD)
    d["advisory"] = "nope"
    with pytest.raises(ValidationError):
        Sample.model_validate(d)
