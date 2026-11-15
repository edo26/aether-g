# Build context is the repository root (the image needs edge-ai/ and data/rag/):
#   docker build -f edge-ai/Dockerfile -t aether-g-edge .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.5.31 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY edge-ai/pyproject.toml edge-ai/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim
RUN useradd --system --uid 10001 --home-dir /app aether && mkdir -p /data && chown aether /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY edge-ai/app ./app
COPY data/rag/psi_fire_safety_brief.json data/rag/facts_manual.json ./rag/
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    AETHER_RAG_BRIEF=/app/rag/psi_fire_safety_brief.json \
    AETHER_RAG_MANUAL=/app/rag/facts_manual.json \
    AETHER_SQLITE_PATH=/data/aether.db
USER aether
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
