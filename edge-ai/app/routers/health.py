from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1", tags=["health"])


def build_health(app) -> dict[str, Any]:
    """Cheap and synchronous: reads cached state, never touches the network."""
    store_health = app.state.store.health()
    live = app.state.live
    ingest = {
        cid: {"last_ingest_age_s": live.age_s(latest), "source": latest.source, "mode": latest.sample.mode}
        for cid, latest in sorted(live.all().items())
    }
    degraded = store_health["status"] == "down"
    return {
        "status": "degraded" if degraded else "ok",
        "influx": store_health,
        "sqlite": {"status": "up", "path": app.state.db.path, **app.state.db.counts()},
        "advisor": {"rag_facts": len(app.state.rag.facts), "llm": "not_configured" if app.state.worker.llm is None else "configured"},
        "compartments": ingest,
        "sse_subscribers": app.state.broker.subscribers,
    }


@router.get("/health", summary="Service health", description=(
    "Influx state and outage buffer, last-ingest age per compartment. "
    "oMLX reachability is added in Phase 4."))
async def health(request: Request):
    return build_health(request.app)
