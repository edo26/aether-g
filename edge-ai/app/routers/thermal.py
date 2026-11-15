from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request

from app.contract import ThermalFrame
from app.routers.ingest import Source, require_device_token

router = APIRouter(prefix="/api/v1", tags=["thermal"])

_EXAMPLE = {
    "timestamp_ms": 10500, "compartment_id": "AVIONICS_BAY_B2", "sensor": "MLX90640",
    "width": 32, "height": 24, "pixels_c": [24.0] * 768,
}


@router.post(
    "/thermal",
    dependencies=[Depends(require_device_token)],
    summary="Thermal camera frame (MLX90640 32x24 or MLX90641 16x12)",
    description=(
        "Row-major `pixels_c` in °C (`pixels_c[y * width + x]`). Kept in memory as the latest frame per "
        "compartment for the dashboard. This does not feed the detector: keep sending `max_ir_temp_c` "
        "(the frame maximum) in the telemetry envelope to `/ingest`."),
)
async def post_thermal(
    request: Request,
    frame: Annotated[ThermalFrame, Body(openapi_examples={"mlx90640": {"summary": "MLX90640 frame", "value": _EXAMPLE}})],
    x_aether_source: Annotated[Source, Header()] = "jetson",
):
    entry = request.app.state.thermal.update(frame, x_aether_source)
    return {"compartment_id": frame.compartment_id, "timestamp_ms": frame.timestamp_ms, **entry.stats}


@router.get("/thermal", summary="Latest thermal camera frame", description=(
    "Latest frame for `compartment_id` (or the most recent of any compartment), with `age_s`, `source` "
    "and stats. 404 when no frame has been received."))
async def get_thermal(
    request: Request,
    compartment_id: Annotated[str | None, Query(examples=["AVIONICS_BAY_B2"])] = None,
):
    state = request.app.state.thermal
    entry = state.get(compartment_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="no thermal frame received yet")
    return {
        **entry.frame.model_dump(),
        "source": entry.source,
        "received_at": entry.received_at.isoformat(),
        "age_s": state.age_s(entry),
        **entry.stats,
    }
