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
    llm = app.state.worker.llm
    degraded = store_health["status"] == "down"
    return {
        "status": "degraded" if degraded else "ok",
        "influx": store_health,
        "sqlite": {"status": "up", "path": app.state.db.path, **app.state.db.counts()},
        "omlx": llm.health() if hasattr(llm, "health") else {"status": "not_configured"},
        "advisor": {"rag_facts": len(app.state.rag.facts), "dropped_triggers": app.state.worker.dropped},
        "compartments": ingest,
        "sse_subscribers": app.state.broker.subscribers,
    }


@router.get("/health", summary="Service health", description=(
    "Influx state and outage buffer, last-ingest age per compartment. "
    "oMLX reachability and model id. LLM down does not make the alarm path degraded: advisories fall back to deterministic text."))
async def health(request: Request):
    return build_health(request.app)
