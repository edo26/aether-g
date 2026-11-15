import logging

import pytest

from app.detector import Detector
from app.storage.influx import MAX_BUFFER_AGE_S, InfluxStore, build_points, parse_duration_s
from tests.fakes import FakeBackend, utc
from tests.helpers import samples


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def results_for(scenario, n):
    det = Detector(clock=Clock())
    ss = samples(scenario, n)
    return [(s, det.process(s)) for s in ss]


def lp(point) -> str:
    return point.to_line_protocol()


def test_points_use_server_time_and_keep_session_ts():
    s, r = results_for("smoldering", 30)[-1]
    tel, det = build_points(s, r, "simulator", utc(2026, 11, 15, 12, 0, 0))
    line = lp(tel)
    assert line.startswith("telemetry,compartment_id=AVIONICS_BAY_B2,mode=LIVE_IOT,source=simulator ")
    assert f"session_ts_ms={s.timestamp_ms}i" in line
    assert "tc1_c=" in line and "tc2_c=" in line and "tc3_c=" in line
    assert line.endswith(str(int(utc(2026, 11, 15, 12, 0, 0).timestamp() * 10**9)))
    dl = lp(det)
    assert dl.startswith("detector,compartment_id=AVIONICS_BAY_B2,mode=LIVE_IOT ")
    assert 'status="SMOLDERING_ANOMALY"' in dl and "anomaly=1i" in dl and "dust_rejected=0i" in dl


def test_floats_stay_floats_even_for_integer_json_values():
    """A JSON `0` must not become an Influx integer field: it would poison later float writes."""
    from app.contract import Sample
    from tests.helpers import simulate

    d = simulate("nominal", 1)[0]
    for k in ("co_ppm", "co2_ppm", "ch4_ppm", "pm25_ug_m3", "airflow_cm_s", "max_ir_temp_c", "smolder_velocity_mm_s"):
        d["telemetry"][k] = 0
    d["telemetry"]["thermocouples_c"] = [22, 23, 24]
    s = Sample.model_validate_json(__import__("json").dumps(d))
    r = Detector(clock=Clock()).process(s)
    line = lp(build_points(s, r, "jetson", utc(2026, 1, 1))[0])
    for k in ("co_ppm", "co2_ppm", "ch4_ppm", "pm25_ug_m3", "airflow_cm_s", "max_ir_temp_c",
              "smolder_velocity_mm_s", "tc1_c", "tc2_c", "tc3_c"):
        assert f"{k}=0" in line or f"{k}=2" in line
        assert f"{k}=0i" not in line and f"{k}=22i" not in line and f"{k}=23i" not in line
    assert "voc_index=" in line and "voc_index=" + str(d["telemetry"]["voc_index"]) + "i" in line


def test_ratio_none_is_skipped_in_detector_point():
    s, r = results_for("nominal", 1)[0]
    assert r.features.ratio is None
    assert "ratio" not in lp(build_points(s, r, "simulator", utc(2026, 1, 1))[1])


@pytest.fixture
def store():
    clock = Clock()
    backend = FakeBackend()
    st = InfluxStore(backend, "telemetry", clock=clock, now=lambda: utc(2026, 11, 16))
    st.clock = clock
    st.backend = backend
    return st


async def test_flush_writes_in_order(store):
    for s, r in results_for("nominal", 5):
        store.record(s, r, "simulator")
    assert await store.flush()
    assert len(store.backend.written) == 10  # 2 points per sample
    assert store.health()["buffered_points"] == 0 and store.health()["status"] == "up"
    ts = [lp(p) for p in store.backend.written if lp(p).startswith("telemetry")]
    assert [f"session_ts_ms={i * 500}i" in t for i, t in enumerate(ts)] == [True] * 5


async def test_outage_keeps_detector_output_and_recovers_in_order(store, caplog):
    events = []
    store.on_outage.append(lambda kind, detail: events.append(kind))
    res = results_for("smoldering", 40)
    store.backend.down = True
    with caplog.at_level(logging.ERROR, logger="aether.influx"):
        for i, (s, r) in enumerate(res):
            store.record(s, r, "simulator")  # must not raise
            store.clock.t += 0.5
            if i % 2 == 0:
                await store.flush()
    h = store.health()
    assert h["status"] == "down" and h["buffered_points"] == 80 and h["dropped_points"] == 0
    assert h["down_since"] and "ConnectionError" in h["last_error"]
    assert any("UNREACHABLE" in m for m in caplog.messages)
    assert events == ["influx_down"]  # one transition, not one per failed flush

    store.backend.down = False
    assert await store.flush()
    assert store.health()["status"] == "up" and store.health()["buffered_points"] == 0
    detector_lines = [lp(p) for p in store.backend.written if lp(p).startswith("detector")]
    assert len(detector_lines) == 40
    assert sum('status="SMOLDERING_ANOMALY"' in l for l in detector_lines) == sum(
        r.status == "SMOLDERING_ANOMALY" for _, r in res)
    assert events == ["influx_down", "influx_recovered"]


async def test_buffer_is_limited_to_five_minutes(store):
    store.backend.down = True
    s, r = results_for("nominal", 1)[0]
    store.record(s, r, "simulator")
    store.clock.t += MAX_BUFFER_AGE_S - 1
    store.record(s, r, "simulator")
    assert await store.flush() is False
    assert store.health()["buffered_points"] == 4 and store.dropped == 0
    store.clock.t += 2  # first pair is now 301 s old
    await store.flush()
    assert store.health()["buffered_points"] == 2 and store.dropped == 2


async def test_idle_ping_detects_outage(store):
    await store._ping()
    assert store.up is True
    store.backend.down = True
    await store._ping()
    assert store.up is False and store.health()["status"] == "down"


async def test_query_failure_raises_store_unavailable(store):
    from app.storage.influx import StoreUnavailable

    store.backend.down = True
    with pytest.raises(StoreUnavailable):
        await store.series("AVIONICS_BAY_B2", 300, 2)


async def test_series_merges_telemetry_and_detector_rows(store):
    t = utc(2026, 11, 15, 12, 0, 2)
    store.backend.query_rows = [{"_time": t, "co_ppm": 1.23456789, "tc1_c": 22.0, "result": "_result", "table": 0, "_start": 1}]
    # same fake rows are returned for both queries; keys merge on time
    out = await store.series("AVIONICS_BAY_B2", 300, 2)
    assert out == [{"time": t.isoformat(), "co_ppm": 1.2346, "tc1_c": 22.0}]
    tel_q, det_q = store.backend.queries
    assert 'r._measurement == "telemetry"' in tel_q and 'r.compartment_id == "AVIONICS_BAY_B2"' in tel_q
    assert "aggregateWindow(every: 2s, fn: mean" in tel_q
    # both queries must use the same absolute range, or their window boundaries drift apart
    import re
    assert re.search(r"range\(start: 2026-11-15T23:55:00.000000Z, stop: 2026-11-16T00:00:00.000000Z\)", tel_q)
    assert tel_q.split("|> range")[1].split("\n")[0] == det_q.split("|> range")[1].split("\n")[0]
    assert 'r._measurement == "detector"' in det_q and "fn: max" in det_q


async def test_series_escapes_compartment_id(store):
    await store.series('x") |> drop() //', 60, 1)
    assert '\\"' in store.backend.queries[0] and 'r.compartment_id == "x\\") |> drop() //"' in store.backend.queries[0]


@pytest.mark.parametrize("text,seconds", [("500ms", 0.5), ("2s", 2), ("5m", 300), ("1h", 3600), ("7d", 604800)])
def test_parse_duration(text, seconds):
    assert parse_duration_s(text) == seconds


@pytest.mark.parametrize("text", ["", "5", "0s", "-5m", "5w", "m5", "5 m", "5m; drop"])
def test_parse_duration_rejects(text):
    with pytest.raises(ValueError):
        parse_duration_s(text)


async def test_window_uses_session_time_so_batches_do_not_distort_slopes(store):
    t = utc(2026, 11, 15, 12, 0, 0)  # a whole batch received in the same instant
    store.backend.query_rows = [{"_time": t, "session_ts_ms": 500 * i, "co_ppm": 10.0 + i, "result": "_result", "table": 0}
                                for i in range(5)]
    rows = await store.window("AVIONICS_BAY_B2", 60)
    assert [r["t_s"] for r in rows] == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert "session_ts_ms" not in rows[0] and "table" not in rows[0] and rows[0]["co_ppm"] == 10.0
    from app.advisor.summary import summarize
    assert summarize(rows, 60, "influxdb")["channels"]["co_ppm"]["slope_per_s"] == 2.0


async def test_window_keeps_only_the_current_session(store):
    base = utc(2026, 11, 15, 12, 0, 0)
    from datetime import timedelta
    store.backend.query_rows = [
        {"_time": base + timedelta(seconds=i), "session_ts_ms": v, "co_ppm": float(c)}
        for i, (v, c) in enumerate([(10000, 90), (10500, 95), (0, 5), (500, 6), (1000, 7)])]
    rows = await store.window("A", 60)
    assert [r["t_s"] for r in rows] == [0.0, 0.5, 1.0] and [r["co_ppm"] for r in rows] == [5.0, 6.0, 7.0]


async def test_window_without_session_field_uses_receive_time(store):
    base = utc(2026, 11, 15, 12, 0, 0)
    store.backend.query_rows = [{"_time": base, "co_ppm": 1.0}]
    assert (await store.window("A", 60))[0]["t_s"] == base.timestamp()
