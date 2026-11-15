"""Client for the public NASA PSI repository API (the JSON backend behind psi.nasa.gov/physci/repo).

Endpoints (reverse-engineered from the PSI web app, no login needed for public data):
  GET  /geode-py/ws/repo/search?term=&source=psi&type=&from=&size=
  GET  /geode-py/ws/repo/investigations/{accession}
  POST /geode-py/ws/files/v2   {folder, fileType:"study", studyId, version, obfuscationcode}
  GET  /geode-py/ws/studies/{accession}/download?file=&version=&redirect=false  -> presigned S3 URL
"""

import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

WS = "/geode-py/ws"

# Detail fields worth exposing (raw response also carries file version diffs etc.)
DETAIL_FIELDS = {
    "accession": "accession",
    "title": "title",
    "investigationAcronym": "acronym",
    "doi": "doi",
    "version": "version",
    "releaseDate": "release_date",
    "modifiedDate": "modified_date",
    "investigationStartDate": "start_date",
    "investigationEndDate": "end_date",
    "researchArea": "research_area",
    "subResearchArea": "sub_research_area",
    "projectType": "project_type",
    "proposalTitle": "proposal_title",
    "flightProgram": "flight_program",
    "managingNasaCenter": "nasa_center",
    "experimentHardware": "hardware",
    "experimentHardwareLink": "hardware_link",
    "objective": "objective",
    "approach": "approach",
    "hypothesis": "hypothesis",
    "researchImpacts": "research_impacts",
    "description": "description",
    "licenseName": "license",
    "licenseLink": "license_link",
    "totalFileSize": "total_file_size_bytes",
    "public": "public",
    "restricted": "restricted",
}


class PSIError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class _TTLCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self._d: dict[Any, tuple[float, Any]] = {}

    def get(self, key):
        hit = self._d.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        return None

    def set(self, key, value):
        self._d[key] = (time.monotonic(), value)
        return value


def _date(v: Any) -> Any:
    """PSI search returns some dates as epoch seconds; convert those to ISO dates."""
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, UTC).date().isoformat()
    return v


def _search_hit(h: dict) -> dict:
    s = h.get("_source", h)
    return {
        "accession": s.get("Accession"),
        "title": s.get("Title"),
        "acronym": s.get("Acronym"),
        "research_area": s.get("Research Area"),
        "sub_research_area": s.get("SubResearch Area"),
        "flight_platform": s.get("Flight Platform"),
        "start_date": _date(s.get("Investigation Start Date")),
        "end_date": _date(s.get("Investigation End Date")),
        "release_date": _date(s.get("Public Release Date")),
        "keywords": s.get("Keywords"),
        "related": s.get("Related Investigations"),
        "url": f"https://psi.nasa.gov/physci/repo/data/investigations/{s.get('Accession')}",
    }


def _flatten_files(nodes: list[dict], category: str | None = None) -> list[dict]:
    out = []
    for n in nodes:
        cat = n.get("sortingCategory") or n.get("displayName") or category
        if n.get("directory") is False or (n.get("key") and not n.get("key", "").endswith("/") and not n.get("files")):
            key = n["key"]
            name = PurePosixPath(key).name
            out.append({
                "name": name,
                "key": key,
                "category": cat,
                "ext": name.rsplit(".", 1)[-1].lower() if "." in name else "",
                "size_bytes": n.get("bytes_size"),
                "size": n.get("size"),
                "updated": n.get("updated"),
                "version": n.get("version"),
                "restricted": bool(n.get("restricted") or n.get("restrictedAccess")),
            })
        if n.get("files"):
            out.extend(_flatten_files(n["files"], cat))
    return out


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in " ._-()" else "_" for c in name).strip() or "file"


class PSIClient:
    def __init__(self, base_url: str, timeout_s: float = 30.0, cache_ttl_s: float = 600.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=timeout_s, transport=transport, follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "aether-g-psi-scraper/0.1"},
        )
        self._cache = _TTLCache(cache_ttl_s)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = await self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise PSIError(502, f"PSI unreachable: {e}") from e
        if r.status_code >= 400:
            msg = r.text[:200]
            try:
                msg = r.json().get("message", msg)
            except Exception:  # noqa: BLE001
                pass
            raise PSIError(404 if r.status_code == 404 else 502, f"PSI {r.status_code}: {msg}")
        return r

    # ------------------------------------------------------------- search
    async def search(self, term: str = "", size: int = 25, page: int = 0, data_type: str = "investigation") -> dict:
        key = ("search", term, size, page, data_type)
        if (hit := self._cache.get(key)) is not None:
            return hit
        params = {"term": term, "source": "psi", "type": data_type, "from": page * size, "size": size}
        d = (await self._req("GET", f"{WS}/repo/search", params=params)).json()
        hits = d.get("hits", {})
        total = hits.get("total")
        if isinstance(total, dict):
            total = total.get("value")
        res = {"total": total, "page": page, "size": size, "results": [_search_hit(h) for h in hits.get("hits", [])]}
        return self._cache.set(key, res)

    # ------------------------------------------------------------- detail
    async def investigation_raw(self, accession: str) -> dict:
        key = ("inv", accession)
        if (hit := self._cache.get(key)) is not None:
            return hit
        d = (await self._req("GET", f"{WS}/repo/investigations/{accession}")).json()
        return self._cache.set(key, d)

    async def investigation(self, accession: str) -> dict:
        d = await self.investigation_raw(accession)
        out = {new: d.get(old) for old, new in DETAIL_FIELDS.items()}
        out["related"] = [x.strip() for x in (d.get("related_glds") or "").split(",") if x.strip()]
        out["contacts"] = d.get("contacts", [])
        out["publications"] = d.get("publications", [])
        out["url"] = f"https://psi.nasa.gov/physci/repo/data/investigations/{accession}"
        return out

    # -------------------------------------------------------------- files
    async def files(self, accession: str, version: int | None = None) -> list[dict]:
        if version is None:
            version = (await self.investigation_raw(accession)).get("version")
        key = ("files", accession, version)
        if (hit := self._cache.get(key)) is not None:
            return hit
        body = {"folder": f"psi-data/{accession}/", "fileType": "study", "studyId": accession,
                "version": version, "obfuscationcode": ""}
        d = (await self._req("POST", f"{WS}/files/v2", json=body)).json()
        nodes = d.get("files", d.get("data", d)) if isinstance(d, dict) else d
        return self._cache.set(key, _flatten_files(nodes))

    async def download_url(self, accession: str, file_name: str, version: int | None = None) -> str:
        if version is None:
            version = (await self.investigation_raw(accession)).get("version")
        r = await self._req("GET", f"{WS}/studies/{accession}/download",
                            params={"file": file_name, "version": version, "redirect": "false"})
        url = r.text.strip().strip('"')
        if not url.startswith("http"):
            raise PSIError(502, f"unexpected download response: {url[:200]}")
        return url

    async def download(self, accession: str, file_name: str, dest_dir: Path, max_bytes: int,
                       version: int | None = None, overwrite: bool = False) -> dict:
        dest = dest_dir / _safe_name(accession) / _safe_name(file_name)
        if dest.exists() and not overwrite:
            return {"path": str(dest), "size_bytes": dest.stat().st_size, "cached": True}
        url = await self.download_url(accession, file_name, version)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        written = 0
        try:
            async with self._http.stream("GET", url, headers={"Accept": "*/*"}) as r:
                if r.status_code >= 400:
                    raise PSIError(502, f"S3 download failed: HTTP {r.status_code}")
                declared = int(r.headers.get("content-length") or 0)
                if declared > max_bytes:
                    raise PSIError(413, f"file is {declared / 1e6:.1f} MB, over limit {max_bytes / 1e6:.0f} MB")
                with tmp.open("wb") as f:
                    async for chunk in r.aiter_bytes(1 << 16):
                        written += len(chunk)
                        if written > max_bytes:
                            raise PSIError(413, f"file exceeds limit {max_bytes / 1e6:.0f} MB")
                        f.write(chunk)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
        return {"path": str(dest), "size_bytes": written, "cached": False}
