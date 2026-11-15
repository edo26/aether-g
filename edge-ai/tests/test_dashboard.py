import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.advisor.rag import Rag
from app.main import create_app
from app.storage.sqlite import Db
from app.stream import Broker, event_stream, format_sse
from tests.fakes import FakeStore
from tests.helpers import simulate

H = {"X-Device-Token": "dev-device-token"}


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AETHER_DEVICE_TOKEN", "dev-device-token")
    from app.config import get_settings

    get_settings.cache_clear()
    store = FakeStore()
    app = create_app(store=store, db=Db(':memory:'), rag=Rag())
    with TestClient(app) as c:
        yield c, store, app
    get_settings.cache_clear()


def post(c, body):
    r = c.post("/api/v1/ingest", json=body, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def test_status_empty_then_populated(ctx):
    c, _, _ = ctx
    assert c.get("/api/v1/status").json() == {"compartments": []}
    post(c, simulate("nominal", 1)[0])
    comp = c.get("/api/v1/status").json()["compartments"][0]
    assert comp["compartment_id"] == "AVIONICS_BAY_B2"
    assert comp["stage1_detector"]["status"] == "NOMINAL"
    assert comp["telemetry"]["thermocouples_c"] and comp["source"] == "jetson"
    assert comp["last_sample_age_s"] >= 0 and comp["latest_advisory"] is None


def test_ingest_records_into_store_with_source(ctx):
    c, store, _ = ctx
    c.post("/api/v1/ingest", json=simulate("nominal", 1)[0], headers={**H, "X-Aether-Source": "simulator"})
    assert [src for _, _, src in store.recorded] == ["simulator"]


def test_ingest_survives_store_failure(ctx):
    c, store, _ = ctx

    def boom(*a):
        raise RuntimeError("disk on fire")

    store.record = boom
    j = post(c, simulate("smoldering", 40)[-1])
    assert j["stage1_detector"]["status"] in ("NOMINAL", "SMOLDERING_ANOMALY")


def test_health_reports_outage_without_blocking_ingest(ctx):
    c, store, _ = ctx
    post(c, simulate("nominal", 1)[0])
    h = c.get("/api/v1/health").json()
    assert h["status"] == "ok" and h["influx"]["status"] == "up"
    assert h["compartments"]["AVIONICS_BAY_B2"]["last_ingest_age_s"] >= 0
    store.down = True
    store.health = lambda: {"status": "down", "buffered_points": 42, "last_error": "refused"}
    post(c, simulate("nominal", 2)[1])
    h = c.get("/api/v1/health").json()
    assert h["status"] == "degraded" and h["influx"]["buffered_points"] == 42


def test_telemetry_ok_and_validation(ctx):
    c, store, _ = ctx
    store.rows = [{"time": "2026-11-15T12:00:02+00:00", "co_ppm": 1.0}]
    r = c.get("/api/v1/telemetry", params={"compartment_id": "AVIONICS_BAY_B2", "range": "5m", "every": "2s"})
    assert r.status_code == 200 and r.json()["points"] == store.rows
    assert store.series_calls[-1] == ("AVIONICS_BAY_B2", 300, 2)
    bad = [{"range": "5x"}, {"every": "0s"}, {"every": "100ms"}, {"range": "8d"}, {"range": "7d", "every": "1s"}]
    for extra in bad:
        r = c.get("/api/v1/telemetry", params={"compartment_id": "A", **extra})
        assert r.status_code == 422, extra
    assert c.get("/api/v1/telemetry").status_code == 422  # compartment_id required


def test_telemetry_503_when_store_down(ctx):
    c, store, _ = ctx
    store.down = True
    r = c.get("/api/v1/telemetry", params={"compartment_id": "A"})
    assert r.status_code == 503 and "unavailable" in r.json()["detail"]


def test_status_change_published_exactly_once_and_telemetry_throttled(ctx):
    c, _, app = ctx
    q = app.state.broker.subscribe()
    for s in simulate("smoldering", 40):
        post(c, s)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    changes = [d for e, d in events if e == "status_change"]
    assert len(changes) == 1
    assert changes[0]["previous_status"] == "NOMINAL" and changes[0]["status"] == "SMOLDERING_ANOMALY"
    assert changes[0]["timestamp_ms"] == 10500
    # all 40 samples arrive in a few ms, so the 2 Hz throttle lets only the first through
    assert sum(e == "telemetry" for e, _ in events) == 1


def test_broker_throttle_is_per_compartment_and_time_based():
    t = [0.0]
    b = Broker(clock=lambda: t[0])
    q = b.subscribe()
    assert b.publish_telemetry("A", {"n": 1}) and b.publish_telemetry("B", {"n": 1})
    assert not b.publish_telemetry("A", {"n": 2})
    t[0] += 0.5
    assert b.publish_telemetry("A", {"n": 3})
    assert q.qsize() == 3


def test_slow_subscriber_drops_oldest_not_ingest():
    b = Broker()
    q = b.subscribe()
    for i in range(300):
        b.publish("telemetry", {"i": i})
    assert q.qsize() == 256
    assert q.get_nowait()[1]["i"] == 300 - 256


async def test_event_stream_frames_and_cleanup():
    b = Broker()

    async def never():
        return False

    gen = event_stream(b, never, first={"status": "ok"}, keepalive_s=0.05)
    assert await gen.__anext__() == "retry: 2000\n\n"
    assert await gen.__anext__() == 'event: health\ndata: {"status":"ok"}\n\n'
    assert b.subscribers == 1
    assert await gen.__anext__() == ": keepalive\n\n"
    b.publish("status_change", {"x": 1})
    frame = await gen.__anext__()
    assert frame == 'event: status_change\ndata: {"x":1}\n\n'
    await gen.aclose()
    assert b.subscribers == 0


def test_format_sse_is_single_line_json():
    f = format_sse("telemetry", {"a": [1, 2], "b": "x\ny"})
    assert f.count("\n") == 3 and json.loads(f.split("data: ")[1]) == {"a": [1, 2], "b": "x\ny"}


def test_end_to_end_smoldering_run_one_status_change_one_advisory_with_citations(monkeypatch):
    """Phase 3 acceptance: a smoldering run gives one status_change and one fallback advisory."""
    import time

    from app.advisor.rag import Rag
    from app.config import get_settings

    monkeypatch.setenv("AETHER_DEVICE_TOKEN", "dev-device-token")
    get_settings.cache_clear()
    rag = Rag.load("../data/rag/psi_fire_safety_brief.json", "../data/rag/facts_manual.json")
    app = create_app(store=FakeStore(), db=Db(":memory:"), rag=rag)
    with TestClient(app) as c:
        q = app.state.broker.subscribe()
        for s in simulate("smoldering", 150):  # 75 s, past the plateau; far less than the 30 s periodic
            post(c, s)
        for _ in range(100):
            advs = c.get("/api/v1/advisories", params={"compartment_id": "AVIONICS_BAY_B2"}).json()["advisories"]
            if advs:
                break
            time.sleep(0.02)
        assert len(advs) == 1
        a = advs[0]
        assert a["fallback"] is True and a["trigger"] == "status_change" and len(a["steps"]) == 3
        assert a["citations"] and all(cit["url"] for cit in a["citations"])
        assert any(cit["id"] == "NTRS-20240002981" for cit in a["citations"])
        events = []
        while not q.empty():
            events.append(q.get_nowait()[0])
        assert events.count("status_change") == 1
        assert events.index("status_change") < events.index("advisory_start") < events.index("advisory_done")
        st = c.get("/api/v1/status").json()["compartments"][0]
        assert st["latest_advisory"]["id"] == a["id"]
        h = c.get("/api/v1/health").json()
        assert h["sqlite"]["advisories"] == 1 and h["sqlite"]["events"] == 1 and h["advisor"]["rag_facts"] == 17
        ev = app.state.db.events()
        assert [e["kind"] for e in ev] == ["status_change"] and ev[0]["detail"]["to"] == "SMOLDERING_ANOMALY"
    get_settings.cache_clear()


def test_ask_events_chat_flow(monkeypatch):
    import time

    from app.advisor.rag import Rag
    from app.config import get_settings

    monkeypatch.setenv("AETHER_DEVICE_TOKEN", "dev-device-token")
    get_settings.cache_clear()
    app = create_app(store=FakeStore(), db=Db(":memory:"), rag=Rag())
    with TestClient(app) as c:
        r = c.post("/api/v1/ask", json={"compartment_id": "AVIONICS_BAY_B2", "question": "why?"})
        assert r.status_code == 404  # nothing ingested yet
        post(c, simulate("nominal", 1)[0])
        assert c.post("/api/v1/ask", json={"compartment_id": "AVIONICS_BAY_B2", "question": ""}).status_code == 422
        assert c.post("/api/v1/ask", json={"compartment_id": "AVIONICS_BAY_B2", "question": "x" * 501}).status_code == 422
        q = app.state.broker.subscribe()
        r = c.post("/api/v1/ask", json={"compartment_id": "AVIONICS_BAY_B2", "question": "Is it safe?"})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        for _ in range(100):
            msgs = c.get("/api/v1/chat", params={"compartment_id": "AVIONICS_BAY_B2"}).json()["messages"]
            if len(msgs) == 2:
                break
            time.sleep(0.02)
        assert [m["role"] for m in msgs] == ["assistant", "user"] and msgs[1]["content"] == "Is it safe?"
        assert msgs[0]["advisory_id"] is not None
        adv = c.get("/api/v1/advisories").json()["advisories"][0]
        assert adv["trigger"] == "ask" and adv["fallback"] is True and adv["id"] == msgs[0]["advisory_id"]
        names = []
        while not q.empty():
            e, d = q.get_nowait()
            names.append((e, d.get("run_id")))
        assert ("advisory_start", run_id) in names and ("advisory_done", run_id) in names

        for s in simulate("smoldering", 25):
            post(c, s)
        ev = c.get("/api/v1/events", params={"limit": 5}).json()["events"]
        assert ev and ev[0]["kind"] == "status_change" and ev[0]["compartment_id"] == "AVIONICS_BAY_B2"
        assert c.get("/api/v1/events", params={"compartment_id": "NOPE"}).json()["events"] == []
        assert c.get("/api/v1/events", params={"limit": 0}).status_code == 422
    get_settings.cache_clear()


def test_ask_is_refused_with_409_while_an_alarm_advisory_runs(monkeypatch):
    import time

    from app.advisor.rag import Rag
    from app.config import get_settings

    class Hanging:
        async def advise(self, **kw):
            await asyncio.sleep(30)

    monkeypatch.setenv("AETHER_DEVICE_TOKEN", "dev-device-token")
    get_settings.cache_clear()
    app = create_app(store=FakeStore(), db=Db(":memory:"), rag=Rag(), llm=Hanging())
    with TestClient(app) as c:
        for s in simulate("smoldering", 25):
            post(c, s)
        for _ in range(100):
            if app.state.worker.is_running("AVIONICS_BAY_B2"):
                break
            time.sleep(0.02)
        r = c.post("/api/v1/ask", json={"compartment_id": "AVIONICS_BAY_B2", "question": "again?"})
        assert r.status_code == 409 and "busy" in r.json()["detail"]
        assert c.get("/api/v1/chat").json()["messages"] == []  # a refused question is not saved
    get_settings.cache_clear()
