from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.advisor.fallback import DISCLAIMER

router = APIRouter(prefix="/api/v1", tags=["advisor"])


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "example": {"compartment_id": "AVIONICS_BAY_B2", "question": "Why did the alarm trigger?"}})
    compartment_id: Annotated[str, Field(min_length=1)]
    question: Annotated[str, Field(min_length=1, max_length=500)]


@router.get("/advisories", summary="Advisory history (newest first)")
async def advisories(
    request: Request,
    compartment_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
):
    rows = request.app.state.db.advisories(compartment_id, limit)
    return {"disclaimer": DISCLAIMER, "advisories": rows}


@router.get("/events", summary="Status transitions, session resets, dust rejections and outages (newest first)")
async def events(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    compartment_id: Annotated[str | None, Query()] = None,
):
    return {"events": request.app.state.db.events(limit, compartment_id)}


@router.get("/chat", summary="Saved questions and answers (newest first)")
async def chat(
    request: Request,
    compartment_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    return {"messages": request.app.state.db.chat(compartment_id, limit)}


@router.post("/ask", status_code=202, summary="Ask about a compartment; the answer streams on /stream",
             description=(
                 "Returns immediately with a `run_id`. The answer arrives as `advisory_start`, "
                 "`advisory_token` and `advisory_done` events carrying that `run_id`, and is saved to "
                 "`/chat` and `/advisories` (trigger `ask`). While the LLM is down the answer is the "
                 "rule-based status (`fallback: true`). 409 while the advisor is busy with an alarm advisory."))
async def ask(request: Request, body: AskBody):
    app = request.app
    if app.state.live.get(body.compartment_id) is None:
        raise HTTPException(status_code=404, detail=f"no data received yet for {body.compartment_id!r}")
    run_id = app.state.worker.start_run(body.compartment_id, "ask", question=body.question)
    if run_id is None:
        raise HTTPException(status_code=409, detail="advisor is busy generating an alarm advisory; retry in a few seconds")
    app.state.db.add_chat(body.compartment_id, "user", body.question)
    return {"run_id": run_id, "compartment_id": body.compartment_id, "question": body.question}
