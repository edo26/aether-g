import asyncio
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import brief as rag_brief
from .config import settings
from .models import (
    TELEMETRY_FILE,
    DownloadIn,
    DownloadResult,
    DownloadUrlResponse,
    ErrorOut,
    FilesResponse,
    FireSafetyResponse,
    Health,
    Investigation,
    LocalFile,
    RagBuildResult,
    SearchResponse,
)
from .psi_client import PSIClient, PSIError

psi = PSIClient(settings.base_url, settings.timeout_s, settings.cache_ttl_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await psi.aclose()


DESCRIPTION = """
Scraper for the public **NASA Physical Sciences Informatics (PSI)** repository, built for AETHER-G.

Typical flow:
1. `GET /api/psi/fire-safety`: find relevant investigations (Saffire, BASS, SAME, ...)
2. `GET /api/psi/investigations/{accession}`: read the metadata (DOI and license for provenance)
3. `GET /api/psi/investigations/{accession}/files?ext=xlsx`: pick a file
4. `POST /api/psi/investigations/{accession}/download`: save it to `data/raw/{accession}/`

Examples use real data from **PSI-98 (Saffire-I)**.
"""

app = FastAPI(
    title="AETHER-G PSI Scraper API",
    version="0.1.0",
    description=DESCRIPTION,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "search", "description": "Find PSI investigations"},
        {"name": "investigation", "description": "Metadata and file listings"},
        {"name": "download", "description": "Fetch files into data/raw"},
        {"name": "rag", "description": "One-page brief of all fire-safety investigations + downloaded data (Markdown, JSON, PDF)"},
        {"name": "system", "description": "Health"},
    ],
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ERRORS = {404: {"model": ErrorOut, "description": "Not found on PSI"}, 502: {"model": ErrorOut, "description": "PSI unreachable or returned an error"}}

Accession = Annotated[str, Path(description="PSI accession ID", examples=["PSI-98"])]


@app.exception_handler(PSIError)
async def psi_error(_: Request, e: PSIError):
    return JSONResponse(status_code=e.status, content={"detail": str(e)})


@app.get("/api/psi/search", response_model=SearchResponse, tags=["search"], responses=ERRORS)
async def search(
    q: Annotated[str, Query(description="Search term", examples=["saffire"])] = "",
    size: Annotated[int, Query(ge=1, le=100, description="Results per page", examples=[25])] = 25,
    page: Annotated[int, Query(ge=0, description="Page number, starting at 0", examples=[0])] = 0,
):
    """Search PSI investigations by keyword."""
    return await psi.search(q, size, page)


@app.get("/api/psi/fire-safety", response_model=FireSafetyResponse, tags=["search"], responses=ERRORS)
async def fire_safety():
    """Preset search for spacecraft fire and microgravity combustion investigations. Combustion Science results come first."""
    results = await rag_brief.fire_safety_investigations(psi, settings.fire_terms)
    return {"terms": settings.fire_terms, "total": len(results), "results": results}


@app.get("/api/psi/investigations/{accession}", response_model=Investigation, tags=["investigation"], responses=ERRORS)
async def investigation(accession: Accession):
    """Cleaned metadata: objective, approach, hypothesis, hardware, dates, DOI, license, publications, related investigations."""
    return await psi.investigation(accession.upper())


@app.get("/api/psi/investigations/{accession}/raw", response_model=dict[str, Any], tags=["investigation"], responses=ERRORS)
async def investigation_raw(accession: Accession):
    """The unmodified PSI response (large; includes file version history)."""
    return await psi.investigation_raw(accession.upper())


@app.get("/api/psi/investigations/{accession}/files", response_model=FilesResponse, tags=["investigation"], responses=ERRORS)
async def files(
    accession: Accession,
    ext: Annotated[str | None, Query(description="File extension", examples=["xlsx"])] = None,
    category: Annotated[str | None, Query(description="Category contains (e.g. raw, science)", examples=["raw"])] = None,
    q: Annotated[str | None, Query(description="File name contains", examples=["telemetry"])] = None,
    version: Annotated[int | None, Query(description="Investigation version; latest if empty")] = None,
):
    """Flat list of the investigation's files, with optional filters."""
    items = await psi.files(accession.upper(), version)
    if ext:
        items = [f for f in items if f["ext"] == ext.lower().lstrip(".")]
    if category:
        items = [f for f in items if category.lower() in (f["category"] or "").lower()]
    if q:
        items = [f for f in items if q.lower() in f["name"].lower()]
    return {"accession": accession.upper(), "count": len(items),
            "total_size_bytes": sum(f["size_bytes"] or 0 for f in items), "files": items}


@app.get("/api/psi/investigations/{accession}/download-url", response_model=DownloadUrlResponse, tags=["download"], responses=ERRORS)
async def download_url(
    accession: Accession,
    file: Annotated[str, Query(description="File name from the /files endpoint", examples=[TELEMETRY_FILE])],
    version: Annotated[int | None, Query(description="Investigation version; latest if empty")] = None,
):
    """Short-lived presigned S3 URL, so a client can download the file directly."""
    return {"url": await psi.download_url(accession.upper(), file, version)}


@app.post(
    "/api/psi/investigations/{accession}/download",
    response_model=DownloadResult,
    tags=["download"],
    responses={**ERRORS, 413: {"model": ErrorOut, "description": "File is over the size limit"}},
)
async def download(accession: Accession, body: DownloadIn):
    """Download one file into `data/raw/{accession}/`. A file already on disk is not downloaded again unless `overwrite` is true."""
    max_mb = body.max_mb or settings.max_download_mb
    return await psi.download(accession.upper(), body.file, settings.download_dir, int(max_mb * 1e6),
                              body.version, body.overwrite)


@app.get("/api/downloads", response_model=list[LocalFile], tags=["download"])
async def downloads():
    """Files already downloaded to local disk."""
    root = settings.download_dir
    if not root.exists():
        return []
    return [{"accession": p.parent.name, "name": p.name, "path": str(p), "size_bytes": p.stat().st_size}
            for p in sorted(root.glob("*/*")) if p.is_file() and not p.name.endswith(".part") and not p.parent.name.startswith(".")]


RAG_NAME = "psi_fire_safety_brief"
_rag_lock = asyncio.Lock()


async def _build_rag(combustion_only: bool = True) -> dict:
    async with _rag_lock:
        b = await rag_brief.build_brief(psi, settings.fire_terms, settings.download_dir, settings.cache_dir, combustion_only)
        pdf, pdf_info = await asyncio.to_thread(rag_brief.to_pdf, b)
        out = settings.rag_dir
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{RAG_NAME}.json").write_text(rag_brief.to_json(b))
        (out / f"{RAG_NAME}.md").write_text(rag_brief.to_markdown(b))
        (out / f"{RAG_NAME}.pdf").write_bytes(pdf)
        return {
            "generated_at": b["generated_at"],
            "investigations": len(b["investigations"]),
            "datasets": len(b["datasets"]),
            "pdf": pdf_info,
            "files": {ext: f"/api/rag/brief.{ext}" for ext in ("md", "json", "pdf")},
        }


@app.post("/api/rag/brief", response_model=RagBuildResult, tags=["rag"], responses=ERRORS)
async def rag_build(
    combustion_only: Annotated[bool, Query(description="Only Combustion Science investigations (drops unrelated keyword hits)")] = True,
):
    """(Re)generate the one-page brief from all fire-safety investigations and every xlsx/csv already in `data/raw/`.
    Download data files first, then call this to include their statistics."""
    return await _build_rag(combustion_only)


async def _rag_file(ext: str, media: str):
    path = settings.rag_dir / f"{RAG_NAME}.{ext}"
    if not path.exists():
        await _build_rag()
    return FileResponse(path, media_type=media, filename=path.name)


@app.get("/api/rag/brief.md", tags=["rag"], response_class=FileResponse,
         responses={200: {"content": {"text/markdown": {"example": "# AETHER-G RAG Brief: NASA PSI Microgravity Fire-Safety Data\n..."}}}})
async def rag_md():
    """Brief as Markdown (best for RAG ingestion). Built automatically if missing."""
    return await _rag_file("md", "text/markdown; charset=utf-8")


@app.get("/api/rag/brief.json", tags=["rag"], response_class=FileResponse,
         responses={200: {"content": {"application/json": {"example": {"title": "AETHER-G RAG Brief: NASA PSI Microgravity Fire-Safety Data", "investigations": [{"accession": "PSI-98", "acronym": "SAFFIRE-I"}], "datasets": [{"accession": "PSI-98", "file": "PSI-98_Raw Data_Saffire-I Telemetry Data Good Process Version.xlsx"}]}}}}})
async def rag_json():
    """Brief as structured JSON. Built automatically if missing."""
    return await _rag_file("json", "application/json")


@app.get("/api/rag/brief.pdf", tags=["rag"], response_class=FileResponse,
         responses={200: {"content": {"application/pdf": {}}, "description": "Single-page A4 PDF"}})
async def rag_pdf():
    """Brief as a single-page PDF. Built automatically if missing."""
    return await _rag_file("pdf", "application/pdf")


@app.get("/api/health", response_model=Health, tags=["system"], responses={503: {"model": ErrorOut}})
async def health():
    """Checks that PSI is reachable."""
    try:
        await psi.search("saffire", 1)
        return {"psi": "ok"}
    except PSIError as e:
        raise HTTPException(503, str(e)) from e
