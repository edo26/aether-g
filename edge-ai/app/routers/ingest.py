import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request

from app.config import Settings, get_settings
from app.contract import IngestBatch, Sample
from app.detector import Result

router = APIRouter(prefix="/api/v1", tags=["ingest"])

Source = Literal["jetson", "simulator", "replay"]

_EXAMPLE_SAMPLE = {
    "timestamp_ms": 10500,
    "mode": "LIVE_IOT",
    "compartment_id": "AVIONICS_BAY_B2",
    "telemetry": {
        "co_ppm": 26.0, "co2_ppm": 517.5, "voc_index": 55, "ch4_ppm": 0.3,
        "pm25_ug_m3": 10.1, "airflow_cm_s": 3.2,
        "thermocouples_c": [26.7, 27.9, 25.1],
        "max_ir_temp_c": 32.75, "smolder_velocity_mm_s": 1.0,
    },
}


def require_device_token(
    x_device_token: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    if x_device_token is None or not secrets.compare_digest(
        x_device_token.encode(), settings.device_token.encode()
    ):
        raise HTTPException(status_code=401, detail="invalid or missing X-Device-Token")


def _result_body(sample: Sample, result: Result) -> dict:
    return {
        "compartment_id": sample.compartment_id,
        "timestamp_ms": sample.timestamp_ms,
        "stage1_detector": result.stage1(),
        "features": result.features.as_dict(),
    }


@router.post(
    "/ingest",
    dependencies=[Depends(require_device_token)],
    summary="Ingest one contract object or a batch of up to 100",
    description=(
        "Send one contract object, or `{\"items\": [...]}`. `stage1_detector` is optional and "
        "ignored: the response carries the authoritative result computed by the backend."
    ),
)
async def ingest(
    request: Request,
    payload: Annotated[
        Sample | IngestBatch,
        Body(openapi_examples={
            "single": {"summary": "One sample", "value": _EXAMPLE_SAMPLE},
            "batch": {"summary": "Batch", "value": {"items": [_EXAMPLE_SAMPLE]}},
        }),
    ],
    x_aether_source: Annotated[Source, Header()] = "jetson",
):
    pipeline = request.app.state.pipeline
    if isinstance(payload, IngestBatch):
        return {"results": [_result_body(s, pipeline.handle(s, x_aether_source)) for s in payload.items]}
    return _result_body(payload, pipeline.handle(payload, x_aether_source))
