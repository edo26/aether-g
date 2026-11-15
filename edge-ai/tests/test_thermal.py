import json

import pytest
from fastapi.testclient import TestClient

from app.advisor.rag import Rag
from app.main import create_app
from app.storage.sqlite import Db
from tests.fakes import FakeStore

H = {"X-Device-Token": "dev-device-token"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AETHER_DEVICE_TOKEN", "dev-device-token")
    from app.config import get_settings

    get_settings.cache_clear()
    with TestClient(create_app(store=FakeStore(), db=Db(":memory:"), rag=Rag())) as c:
        yield c
    get_settings.cache_clear()


def frame(**kw):
    px = [24.0] * 768
    px[5 * 32 + 20] = 61.5  # hotspot at x=20, y=5
    body = {"timestamp_ms": 1000, "compartment_id": "AVIONICS_BAY_B2", "sensor": "MLX90640",
            "width": 32, "height": 24, "pixels_c": px}
    body.update(kw)
    return body


def test_requires_token(client):
    assert client.post("/api/v1/thermal", json=frame()).status_code == 401


def test_accepts_frame_and_returns_stats(client):
    r = client.post("/api/v1/thermal", json=frame(), headers=H)
    assert r.status_code == 200
    j = r.json()
    assert j["max_c"] == 61.5 and j["min_c"] == 24.0
    assert j["hotspot"] == {"x": 20, "y": 5, "temp_c": 61.5}


def test_get_latest_frame(client):
    assert client.get("/api/v1/thermal").status_code == 204
    client.post("/api/v1/thermal", json=frame(), headers={**H, "X-Aether-Source": "simulator"})
    j = client.get("/api/v1/thermal", params={"compartment_id": "AVIONICS_BAY_B2"}).json()
    assert j["source"] == "simulator" and j["width"] == 32 and len(j["pixels_c"]) == 768
    assert j["age_s"] >= 0 and j["hotspot"]["x"] == 20
    assert client.get("/api/v1/thermal", params={"compartment_id": "OTHER"}).status_code == 204
    assert client.get("/api/v1/health").json()["thermal"]["AVIONICS_BAY_B2"]["sensor"] == "MLX90640"


def test_mlx90641_shape(client):
    r = client.post("/api/v1/thermal", json=frame(sensor="MLX90641", width=16, height=12, pixels_c=[25.0] * 192), headers=H)
    assert r.status_code == 200


@pytest.mark.parametrize("bad", [
    {"pixels_c": [24.0] * 767},                    # wrong length
    {"width": 24, "height": 32},                    # wrong shape for MLX90640
    {"pixels_c": [24.0] * 767 + [999.0]},           # out of range
    {"pixels_c": [24.0] * 767 + ["hot"]},           # not a number
    {"extra": 1},                                   # no extra keys
])
def test_rejects_bad_frames(client, bad):
    r = client.post("/api/v1/thermal", json=frame(**bad), headers=H)
    assert r.status_code == 422
    assert all("input" not in e for e in r.json()["detail"])  # never echoes the payload


def test_rejects_nan(client):
    payload = json.dumps(frame()).replace("61.5", "NaN")
    r = client.post("/api/v1/thermal", content=payload, headers={**H, "content-type": "application/json"})
    assert r.status_code == 422
