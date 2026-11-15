"""Response/request models. Examples are real values from PSI (Saffire-I, PSI-98) so Swagger shows realistic data."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PSI98_URL = "https://psi.nasa.gov/physci/repo/data/investigations/PSI-98"
TELEMETRY_FILE = "PSI-98_Raw Data_Saffire-I Telemetry Data Good Process Version.xlsx"


class SearchHit(BaseModel):
    accession: Any = Field(None, examples=["PSI-98"])
    title: Any = Field(None, examples=["Spacecraft Fire Safety Experiment-I"])
    acronym: Any = Field(None, examples=["SAFFIRE-I"])
    research_area: Any = Field(None, examples=["Combustion Science"])
    sub_research_area: Any = Field(None, examples=["Spacecraft Fire, Smoke Distribution"])
    flight_platform: Any = Field(None, examples=["Cygnus"])
    start_date: Any = Field(None, examples=["03-02-2001"])
    end_date: Any = Field(None, examples=["02-28-2008"])
    release_date: Any = Field(None, examples=["2024-09-18"])
    keywords: Any = Field(None, examples=[["spacecraft fire", "flame spread", "microgravity"]])
    related: Any = Field(None, examples=["PSI-99,PSI-100,PSI-26"])
    url: str = Field(examples=[PSI98_URL])


class SearchResponse(BaseModel):
    total: int | None = Field(None, examples=[3])
    page: int = Field(examples=[0])
    size: int = Field(examples=[25])
    results: list[SearchHit]


class FireSafetyHit(SearchHit):
    matched_terms: list[str] = Field(examples=[["saffire", "flame spread"]])


class FireSafetyResponse(BaseModel):
    terms: list[str] = Field(examples=[["saffire", "BASS", "smoldering", "flame spread", "smoke aerosol"]])
    total: int = Field(examples=[12])
    results: list[FireSafetyHit]


class Investigation(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "accession": "PSI-98",
        "title": "Spacecraft Fire Safety Experiment-I",
        "acronym": "SAFFIRE-I",
        "doi": "10.60555/0t15-1z43",
        "version": 4,
        "release_date": "18-Sep-2024",
        "modified_date": "03-Jun-2025",
        "start_date": "03-02-2001",
        "end_date": "02-28-2008",
        "research_area": "Combustion Science",
        "sub_research_area": "Spacecraft Fire, Smoke Distribution",
        "project_type": "Microgravity Investigation",
        "proposal_title": "Characterization of Smoke from Microgravity Fires for Improved Spacecraft Fire Detection",
        "flight_program": "International Space Station (ISS)",
        "nasa_center": "Glenn Research Center (GRC)",
        "hardware": "Saffire 1 enclosure in Cygnus",
        "hardware_link": "https://www1.grc.nasa.gov/space/iss-research/msg/saffire/",
        "objective": "The objectives of this investigation were to: (1) determine how rapidly a large scale fire grows in microgravity ...",
        "approach": "The Saffire-1 experiments was performed on the Orbital Science's Cygnus resupply vehicle after it departed the ISS ...",
        "hypothesis": "This investigation hypothesizes that due to large differences between fire behavior in normal and reduced gravity ...",
        "research_impacts": "The Saffire-I experiment provides a new way to study a realistic fire on an exploration vehicle ...",
        "description": "",
        "license": "Creative Commons Zero v1.0 Universal",
        "license_link": "https://spdx.org/licenses/CC0-1.0.html",
        "total_file_size_bytes": 12250600201.0,
        "public": True,
        "restricted": False,
        "related": ["PSI-99", "PSI-100", "PSI-26"],
        "contacts": [],
        "publications": [],
        "url": PSI98_URL,
    }]})

    accession: str
    title: str | None = None
    acronym: str | None = None
    doi: str | None = None
    version: int | None = None
    release_date: str | None = None
    modified_date: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    research_area: str | None = None
    sub_research_area: str | None = None
    project_type: str | None = None
    proposal_title: str | None = None
    flight_program: str | None = None
    nasa_center: str | None = None
    hardware: str | None = None
    hardware_link: str | None = None
    objective: str | None = None
    approach: str | None = None
    hypothesis: str | None = None
    research_impacts: str | None = None
    description: str | None = None
    license: str | None = None
    license_link: str | None = None
    total_file_size_bytes: float | None = None
    public: bool | None = None
    restricted: bool | None = None
    related: list[str] = []
    contacts: list[Any] = []
    publications: list[Any] = []
    url: str


class PSIFile(BaseModel):
    name: str = Field(examples=[TELEMETRY_FILE])
    key: str = Field(examples=[f"psi-data/PSI-98/Raw Data/{TELEMETRY_FILE}"])
    category: str | None = Field(None, examples=["Raw Data"])
    ext: str = Field(examples=["xlsx"])
    size_bytes: int | float | None = Field(None, examples=[2778726])
    size: str | int | None = Field(None, examples=["2.65 MB"])
    updated: str | None = Field(None, examples=["Wed, 04 Jun 2025 03:17:37 GMT"])
    version: int | None = Field(None, examples=[4])
    restricted: bool = Field(False, examples=[False])


class FilesResponse(BaseModel):
    accession: str = Field(examples=["PSI-98"])
    count: int = Field(examples=[4])
    total_size_bytes: int | float = Field(examples=[65073152])
    files: list[PSIFile]


class DownloadUrlResponse(BaseModel):
    url: str = Field(examples=[
        "https://psi-curation-prod.s3.amazonaws.com/legacy/102189/226/Saffire-I%20Telemetry%20Data%20Good%20Process%20Version.xlsx?AWSAccessKeyId=...&Signature=...&Expires=..."
    ])


class DownloadIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"file": TELEMETRY_FILE, "max_mb": 50, "overwrite": False}]})

    file: str = Field(description="File name exactly as returned by the /files endpoint")
    version: int | None = Field(None, description="Investigation version; defaults to the latest")
    max_mb: float | None = Field(None, description="Per-file size limit in MB (default 200)")
    overwrite: bool = Field(False, description="Download again even if the file already exists locally")


class DownloadResult(BaseModel):
    path: str = Field(examples=[f"/path/to/aether-g/data/raw/PSI-98/{TELEMETRY_FILE}"])
    size_bytes: int = Field(examples=[2778726])
    cached: bool = Field(examples=[False], description="True if the file was already on disk")


class LocalFile(BaseModel):
    accession: str = Field(examples=["PSI-98"])
    name: str = Field(examples=[TELEMETRY_FILE])
    path: str = Field(examples=[f"/path/to/aether-g/data/raw/PSI-98/{TELEMETRY_FILE}"])
    size_bytes: int = Field(examples=[2778726])


class Health(BaseModel):
    psi: str = Field(examples=["ok"])


class ErrorOut(BaseModel):
    detail: str = Field(examples=["PSI 404: Not Found"])


class RagPdfInfo(BaseModel):
    pages: int = Field(examples=[1])
    font_size: float = Field(examples=[6.25])
    objective_chars: int = Field(examples=[300])
    warning: str | None = None


class RagBuildResult(BaseModel):
    generated_at: str = Field(examples=["2026-11-15T03:20:00+00:00"])
    investigations: int = Field(examples=[10])
    datasets: int = Field(examples=[1])
    pdf: RagPdfInfo
    files: dict[str, str] = Field(examples=[{"md": "/api/rag/brief.md", "json": "/api/rag/brief.json", "pdf": "/api/rag/brief.pdf"}])
