from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AETHER-G edge backend", version="0.1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"]
    )
    return app


app = create_app()
