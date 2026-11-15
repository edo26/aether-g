from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.detector import Detector
from app.routers import ingest


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Do not echo `input`: it may hold NaN/Infinity, which JSON cannot encode (that would turn a
    # 422 into a 500). Location and message are enough for the sender to fix the payload.
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AETHER-G edge backend", version="0.1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"]
    )
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.state.detector = Detector()
    app.include_router(ingest.router)
    return app


app = create_app()
