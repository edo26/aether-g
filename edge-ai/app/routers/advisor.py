from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.advisor.fallback import DISCLAIMER

router = APIRouter(prefix="/api/v1", tags=["advisor"])


@router.get("/advisories", summary="Advisory history (newest first)")
async def advisories(
    request: Request,
    compartment_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
):
    rows = request.app.state.db.advisories(compartment_id, limit)
    return {"disclaimer": DISCLAIMER, "advisories": rows}
