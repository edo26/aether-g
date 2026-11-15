from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.pipeline import sample_payload
from app.routers.health import build_health
from app.storage.influx import StoreUnavailable, parse_duration_s
from app.stream import event_stream

router = APIRouter(prefix="/api/v1", tags=["dashboard"])

MAX_POINTS = 5000
MAX_RANGE_S = 7 * 86400


@router.get("/status", summary="Latest state per compartment")
async def status(request: Request):
    live = request.app.state.live
    items = []
    for cid, latest in sorted(live.all().items()):
        body = sample_payload(latest.sample, latest.result, latest.source)
        body["received_at"] = latest.received_at.isoformat()
        body["last_sample_age_s"] = live.age_s(latest)
        body["latest_advisory"] = request.app.state.db.latest_advisory(cid)
        items.append(body)
    return {"compartments": items}


@router.get("/telemetry", summary="Downsampled time series for charts")
async def telemetry(
    request: Request,
    compartment_id: Annotated[str, Query(min_length=1, examples=["AVIONICS_BAY_B2"])],
    range: Annotated[str, Query(examples=["5m"], description="e.g. 90s, 5m, 1h, 2d (max 7d)")] = "5m",
    every: Annotated[str, Query(examples=["2s"], description="window, e.g. 500ms, 2s, 1m")] = "2s",
):
    try:
        range_s, every_s = parse_duration_s(range), parse_duration_s(every)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if range_s > MAX_RANGE_S:
        raise HTTPException(status_code=422, detail="range is limited to 7d (retention)")
    if every_s < 0.5 or range_s / every_s > MAX_POINTS:
        raise HTTPException(status_code=422, detail=f"too many points: range/every must be <= {MAX_POINTS}, every >= 500ms")
    try:
        points = await request.app.state.store.series(compartment_id, range_s, every_s)
    except StoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"telemetry store unavailable: {exc}")
    return {"compartment_id": compartment_id, "range": range, "every": every, "points": points}


@router.get("/stream", summary="Server-Sent Events", response_class=StreamingResponse, responses={
    200: {"content": {"text/event-stream": {}}, "description": (
        "Events: telemetry, status_change, advisory_start, advisory_token, advisory_done, health. "
        "Each frame is `event: <name>` + `data: <json>`.")}})
async def stream(request: Request):
    app = request.app
    gen = event_stream(app.state.broker, request.is_disconnected, first=build_health(app))
    return StreamingResponse(gen, media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})
