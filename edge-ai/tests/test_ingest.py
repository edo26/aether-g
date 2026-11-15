import copy

import pytest
from fastapi.testclient import TestClient

from app.advisor.rag import Rag
from app.main import create_app
from app.storage.sqlite import Db
from tests.fakes import FakeStore
from tests.helpers import simulate

H = {"X-Device-Token": "dev-device-token"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AETHER_DEVICE_TOKEN", "dev-device-token")
    from app.config import get_settings

    get_settings.cache_clear()
    with TestClient(create_app(store=FakeStore(), db=Db(':memory:'), rag=Rag())) as c:
        yield c
    get_settings.cache_clear()


def test_requires_token(client):
    body = simulate("nominal", 1)[0]
    assert client.post("/api/v1/ingest", json=body).status_code == 401
    assert client.post("/api/v1/ingest", json=body, headers={"X-Device-Token": "nope"}).status_code == 401


def test_single_returns_authoritative_result_and_features(client):
    body = simulate("nominal", 1)[0]
    body["stage1_detector"]["status"] = "SMOLDERING_ANOMALY"  # provisional, must be ignored
    r = client.post("/api/v1/ingest", json=body, headers=H)
    assert r.status_code == 200
    j = r.json()
    assert j["stage1_detector"] == {
        "status": "NOMINAL", "confidence_score": 0.95, "false_alarm_dust_check": "NOMINAL_NO_EVENT",
    }
    assert set(j["features"]) == {"dco_dt", "ratio", "voc_delta", "pm_delta", "temp_delta"}


def test_stage1_detector_optional(client):
    body = simulate("nominal", 1)[0]
    del body["stage1_detector"]
    assert client.post("/api/v1/ingest", json=body, headers=H).status_code == 200


def test_batch_trips_in_order(client):
    items = simulate("smoldering", 40)
    r = client.post("/api/v1/ingest", json={"items": items}, headers=H)
    assert r.status_code == 200
    res = r.json()["results"]
    assert len(res) == 40
    first = next(x for x in res if x["stage1_detector"]["status"] == "SMOLDERING_ANOMALY")
    assert first["timestamp_ms"] == 10500


def test_batch_limit(client):
    items = simulate("nominal", 101)
    assert client.post("/api/v1/ingest", json={"items": items}, headers=H).status_code == 422
    assert client.post("/api/v1/ingest", json={"items": []}, headers=H).status_code == 422


@pytest.mark.parametrize("mutation", ["two_tc", "nan", "status", "missing", "extra"])
def test_rejects_contract_violations(client, mutation):
    body = copy.deepcopy(simulate("nominal", 1)[0])
    raw = None
    if mutation == "two_tc":
        body["telemetry"]["thermocouples_c"] = [22.0, 23.0]
    elif mutation == "status":
        body["stage1_detector"]["status"] = "WARNING"
    elif mutation == "missing":
        del body["telemetry"]["co2_ppm"]
    elif mutation == "extra":
        body["advisory"] = "x"
    elif mutation == "nan":
        import json

        raw = json.dumps(body)
        raw = raw.replace(f'"co_ppm": {body["telemetry"]["co_ppm"]}', '"co_ppm": NaN')
    if raw is not None:
        r = client.post("/api/v1/ingest", content=raw, headers={**H, "Content-Type": "application/json"})
    else:
        r = client.post("/api/v1/ingest", json=body, headers=H)
    assert r.status_code == 422


def test_invalid_source_header_rejected(client):
    body = simulate("nominal", 1)[0]
    r = client.post("/api/v1/ingest", json=body, headers={**H, "X-Aether-Source": "nasa"})
    assert r.status_code == 422
