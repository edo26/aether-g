import asyncio
import contextlib
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.advisor.omlx import OmlxClient
from app.advisor.rag import Rag
from app.advisor.worker import AdvisorWorker
from app.detector import Detector
from app.events import EventLog
from app.live import LiveState
from app.pipeline import Pipeline
from app.routers import advisor, dashboard, health, ingest
from app.storage.influx import InfluxBackend, InfluxStore
from app.storage.sqlite import Db
from app.stream import Broker

HEALTH_EVENT_PERIOD_S = 10.0


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Do not echo `input`: it may hold NaN/Infinity, which JSON cannot encode (that would turn a
    # 422 into a 500). Location and message are enough for the sender to fix the payload.
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


def create_app(*, store=None, db=None, rag=None, llm=None) -> FastAPI:
    """`store`, `db`, `rag`, `llm` are injectable so tests never touch real services."""
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if settings.device_token in ("", "change-me") or settings.influx_token == "change-me":
        logging.getLogger("aether").warning(
            "AETHER_DEVICE_TOKEN / AETHER_INFLUX_TOKEN still use the 'change-me' placeholder: do not expose this instance")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await app.state.store.start()
        await app.state.worker.start()
        if hasattr(app.state.worker.llm, "start"):
            await app.state.worker.llm.start()
        tasks = [asyncio.create_task(_health_loop(app), name="health-events")]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await t
            if hasattr(app.state.worker.llm, "stop"):
                await app.state.worker.llm.stop()
            await app.state.worker.stop()
            await app.state.store.stop()
            app.state.db.close()

    app = FastAPI(
        title="AETHER-G edge backend",
        version="0.1.0",
        description="Receives Jetson telemetry, decides fire status with the Stage-1 detector, "
                    "stores it, and serves the dashboard.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"]
    )
    app.add_exception_handler(RequestValidationError, _validation_error)

    app.state.detector = Detector()
    app.state.store = store or InfluxStore(InfluxBackend(settings), settings.influx_bucket)
    app.state.broker = Broker()
    app.state.live = LiveState()
    app.state.pipeline = Pipeline(app.state.detector, app.state.store, app.state.broker, app.state.live)

    if llm is None and settings.omlx_configured:
        llm = OmlxClient(
            settings.omlx_base_url, settings.omlx_model, settings.omlx_api_key,
            first_token_timeout_s=settings.llm_first_token_timeout_s,
            connect_timeout_s=settings.llm_connect_timeout_s, total_timeout_s=settings.llm_total_timeout_s)
    app.state.db = db or Db(settings.sqlite_path)
    app.state.rag = rag or Rag.load(settings.rag_brief, settings.rag_manual)
    app.state.worker = AdvisorWorker(
        detector=app.state.detector, live=app.state.live, store=app.state.store, db=app.state.db,
        rag=app.state.rag, broker=app.state.broker, period_s=settings.advisory_period_s, llm=llm)
    events = EventLog(app.state.db)
    app.state.pipeline.on_result += [events.on_result, app.state.worker.on_result]
    if llm is not None and hasattr(llm, "on_transition"):
        llm.on_transition.append(events.on_outage)
    if hasattr(app.state.store, "on_outage"):
        app.state.store.on_outage.append(events.on_outage)

    for r in (ingest.router, dashboard.router, advisor.router, health.router):
        app.include_router(r)
    return app


async def _health_loop(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(HEALTH_EVENT_PERIOD_S)
        if app.state.broker.subscribers:
            app.state.broker.publish("health", health.build_health(app))


app = create_app()
