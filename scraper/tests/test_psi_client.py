"""Offline tests: PSI responses are mocked with httpx.MockTransport (no network)."""

import asyncio
import json

import httpx
import pytest

from app.psi_client import PSIClient, PSIError

SEARCH = {"hits": {"total": 1, "hits": [{"_source": {
    "Accession": "PSI-98", "Title": "Spacecraft Fire Safety Experiment-I", "Acronym": "SAFFIRE-I",
    "Research Area": "Combustion Science"}}]}}
DETAIL = {"accession": "PSI-98", "title": "Spacecraft Fire Safety Experiment-I", "investigationAcronym": "SAFFIRE-I",
          "version": 4, "related_glds": "PSI-99, PSI-100", "objective": "obj", "contacts": [], "publications": []}
FILES = [{"displayName": "Raw Data", "key": "psi-data/PSI-98/raw/", "files": [
    {"directory": False, "key": "psi-data/PSI-98/raw/PSI-98_Raw Data_Telemetry.xlsx", "bytes_size": 10,
     "size": "10 B", "sortingCategory": "Raw Data", "version": 4, "files": []},
    {"directory": False, "key": "psi-data/PSI-98/raw/PSI-98_Raw Data_Video.zip", "bytes_size": 999,
     "size": "999 B", "sortingCategory": "Raw Data", "version": 4, "files": []},
]}]
PAYLOAD = b"0123456789"


def handler(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    if p == "/geode-py/ws/repo/search":
        assert request.url.params["source"] == "psi"
        return httpx.Response(200, json=SEARCH)
    if p == "/geode-py/ws/repo/investigations/PSI-98":
        return httpx.Response(200, json=DETAIL)
    if p == "/geode-py/ws/repo/investigations/PSI-404":
        return httpx.Response(404, text="Not Found")
    if p == "/geode-py/ws/files/v2":
        body = json.loads(request.content)
        assert body == {"folder": "psi-data/PSI-98/", "fileType": "study", "studyId": "PSI-98", "version": 4,
                        "obfuscationcode": ""}
        return httpx.Response(200, json={"files": FILES})
    if p == "/geode-py/ws/studies/PSI-98/download":
        return httpx.Response(200, text=f"https://s3.example/{request.url.params['file']}")
    if request.url.host == "s3.example":
        return httpx.Response(200, content=PAYLOAD, headers={"content-length": str(len(PAYLOAD))})
    return httpx.Response(500)


@pytest.fixture
def client():
    c = PSIClient("https://psi.nasa.gov", transport=httpx.MockTransport(handler))
    yield c
    asyncio.run(c.aclose())


def run(coro):
    return asyncio.run(coro)


def test_search(client):
    res = run(client.search("saffire"))
    assert res["total"] == 1
    assert res["results"][0]["accession"] == "PSI-98"
    assert res["results"][0]["research_area"] == "Combustion Science"


def test_investigation_cleaned(client):
    d = run(client.investigation("PSI-98"))
    assert d["acronym"] == "SAFFIRE-I" and d["related"] == ["PSI-99", "PSI-100"]


def test_not_found(client):
    with pytest.raises(PSIError) as e:
        run(client.investigation("PSI-404"))
    assert e.value.status == 404


def test_files_flattened(client):
    files = run(client.files("PSI-98"))
    assert [f["name"] for f in files] == ["PSI-98_Raw Data_Telemetry.xlsx", "PSI-98_Raw Data_Video.zip"]
    assert files[0]["ext"] == "xlsx" and files[0]["category"] == "Raw Data"


def test_download_and_size_limit(client, tmp_path):
    ok = run(client.download("PSI-98", "PSI-98_Raw Data_Telemetry.xlsx", tmp_path, max_bytes=100))
    assert ok["size_bytes"] == 10 and not ok["cached"]
    again = run(client.download("PSI-98", "PSI-98_Raw Data_Telemetry.xlsx", tmp_path, max_bytes=100))
    assert again["cached"]
    with pytest.raises(PSIError) as e:
        run(client.download("PSI-98", "big.bin", tmp_path, max_bytes=5))
    assert e.value.status == 413
    assert not list(tmp_path.rglob("*.part"))
