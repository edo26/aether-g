import asyncio

import pytest

from app.advisor import fallback
from app.advisor.rag import Fact, Rag
from app.advisor.summary import slope_per_s, summarize
from app.advisor.worker import AdvisorWorker
from app.detector import Detector
from app.live import LiveState
from app.storage.sqlite import Db
from app.stream import Broker
from tests.fakes import FakeStore
from tests.helpers import samples


def test_slope_and_summary():
    assert slope_per_s([(0, 0), (1, 2), (2, 4)]) == pytest.approx(2.0)
    assert slope_per_s([(0, 5)]) == 0.0 and slope_per_s([(1, 5), (1, 6)]) == 0.0
    rows = [{"t_s": t, "co_ppm": 5 + 2 * t, "voc_index": 10} for t in range(5)]
    s = summarize(rows, 60, "memory")
    assert s["samples"] == 5 and s["channels"]["co_ppm"] == {"min": 5.0, "max": 13.0, "last": 13.0, "slope_per_s": 2.0}
    assert "co2_ppm" not in s["channels"]


FACT = Fact("PSI-98", "SAFFIRE-I (PSI-98)", "txt", "investigation", "NASA PSI", "https://psi.nasa.gov/x", "10.1/x", "CC0")


def test_fallback_anomaly_text_uses_only_summary_numbers():
    summary = {"channels": {"co_ppm": {"last": 42.5, "slope_per_s": 2.4}, "voc_index": {"last": 60, "slope_per_s": 3.0},
                            "max_ir_temp_c": {"last": 30, "slope_per_s": 0.5}}}
    a = fallback.build("status_change", "BAY", "SMOLDERING_ANOMALY", summary, [FACT])
    assert a.fallback and a.model == "deterministic" and len(a.steps) == 3
    assert "BAY" in a.warning and "42.5 ppm" in a.warning and "increasing VOC and localized heat" in a.warning
    assert a.citations[0]["id"] == "PSI-98" and a.citations[0]["doi"] == "10.1/x"
    assert a.raw_text.splitlines()[0].startswith("WARNING:") and a.raw_text.endswith("SOURCES: PSI-98")
    assert "approved" in a.steps[1] and "checklist" in a.steps[1]  # points at a vehicle checklist, invents none


def test_fallback_without_data_or_facts_says_so():
    a = fallback.build("status_change", "BAY", "SMOLDERING_ANOMALY", None, [])
    assert "25 ppm" in a.warning and a.citations == [] and a.raw_text.endswith("SOURCES: none available")


def test_fallback_dust_and_ask():
    d = fallback.build("dust_rejected", "BAY", "NOMINAL", {"channels": {"pm25_ug_m3": {"last": 120.0, "slope_per_s": 0}}}, [])
    assert "Dust event" in d.warning and "120 ug/m3" in d.warning and "NOMINAL" in d.warning
    q = fallback.build("ask", "BAY", "NOMINAL", None, [], question="why?")
    assert "unavailable" in q.warning


# ---- worker -------------------------------------------------------------------------------
class SlowLLM:
    """Stands in for the Phase 4 client: blocks until released, then returns a canned advisory."""

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def advise(self, **kw):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return fallback.Advisory("llm text", ["1", "2", "3"], [], "WARNING: llm text", model="fake", fallback=False,
                                 first_token_ms=50)


def make_worker(llm=None, rag=None):
    det, live, store, db, broker = Detector(), LiveState(), FakeStore(), Db(":memory:"), Broker()
    w = AdvisorWorker(detector=det, live=live, store=store, db=db, rag=rag or Rag(), broker=broker, period_s=30, llm=llm)
    return w, det, live, store, db, broker


def feed(w, det, live, scenario, n):
    last = None
    for s in samples(scenario, n):
        r = det.process(s)
        live.update(s, r, "simulator")
        w.on_result(s, r, "simulator")
        last = r
    return last


async def settle(w):
    await asyncio.gather(*[r.task for r in list(w._running.values())], return_exceptions=True)


async def test_status_change_creates_one_fallback_advisory_with_events():
    rag = Rag.load("../data/rag/psi_fire_safety_brief.json", "../data/rag/facts_manual.json")
    w, det, live, store, db, broker = make_worker(rag=rag)
    q = broker.subscribe()
    feed(w, det, live, "smoldering", 40)
    await settle(w)
    advs = db.advisories("AVIONICS_BAY_B2")
    assert len(advs) == 1
    a = advs[0]
    assert a["trigger"] == "status_change" and a["fallback"] is True and a["status"] == "SMOLDERING_ANOMALY"
    assert len(a["steps"]) == 3 and a["citations"] and a["citations"][0]["url"]
    assert a["window_summary"]["source"] == "memory"  # FakeStore.window returns no rows -> ring buffer
    assert a["window_summary"]["channels"]["co_ppm"]["slope_per_s"] > 0
    names = []
    while not q.empty():
        names.append(q.get_nowait()[0])
    assert names == ["advisory_start", "advisory_done"]


async def test_dust_event_is_deterministic_and_has_a_cooldown():
    w, det, live, *_ , db, broker = make_worker(llm=SlowLLM())
    feed(w, det, live, "dust", 40)
    await settle(w)
    advs = db.advisories()
    assert len(advs) == 1 and advs[0]["trigger"] == "dust_rejected" and advs[0]["model"] == "deterministic"
    assert w.llm.calls == 0  # never the LLM for dust


async def test_same_priority_is_dropped_and_escalation_replaces():
    llm = SlowLLM()
    w, det, live, store, db, broker = make_worker(llm=llm)
    feed(w, det, live, "smoldering", 25)  # trips at 10.5 s -> status_change generation starts, blocks in LLM
    await llm.started.wait()
    assert w.is_running("AVIONICS_BAY_B2")
    assert w.trigger("AVIONICS_BAY_B2", "periodic") is False       # lower priority: dropped
    assert w.trigger("AVIONICS_BAY_B2", "status_change") is False  # same priority: dropped
    assert w.dropped == 2
    assert w.trigger("AVIONICS_BAY_B2", "ask") is False  # ask < status_change
    llm.release.set()
    await settle(w)
    assert len(db.advisories()) == 1


async def test_escalation_cancels_lower_priority_run():
    llm = SlowLLM()
    w, det, live, store, db, broker = make_worker(llm=llm)
    feed(w, det, live, "smoldering", 25)
    await asyncio.sleep(0)  # let the status_change task start
    await llm.started.wait()
    # make the running job a low-priority one by swapping state: cancel and restart as periodic
    w._running["AVIONICS_BAY_B2"].task.cancel()
    await asyncio.sleep(0)
    llm.started.clear()
    assert w.trigger("AVIONICS_BAY_B2", "periodic") is True
    await llm.started.wait()
    q = broker.subscribe()
    assert w.trigger("AVIONICS_BAY_B2", "status_change") is True  # escalation replaces it
    llm.release.set()
    await settle(w)
    done = []
    while not q.empty():
        e, d = q.get_nowait()
        if e == "advisory_done":
            done.append(d.get("cancelled"))
    assert False in done  # the replacement finished
    assert [a["trigger"] for a in db.advisories()] == ["status_change"]


async def test_periodic_refreshes_only_while_anomaly_persists_and_data_is_live():
    t = [0.0]
    w, det, live, store, db, broker = make_worker()
    w._clock = lambda: t[0]
    live._clock = lambda: t[0]
    feed(w, det, live, "smoldering", 25)
    await settle(w)
    assert len(db.advisories()) == 1
    # simulate the loop body by hand: 29 s later nothing, 31 s later one more (data still fresh)
    async def tick():
        now = w._clock()
        for cid, latest in live.all().items():
            if (det.status_of(cid) == "SMOLDERING_ANOMALY" and live.age_s(latest) < 5.0
                    and now - w._last_start.get(cid, -1e9) >= w.period_s):
                w.trigger(cid, "periodic")
    t[0] = 29.0
    await tick(); await settle(w)
    assert len(db.advisories()) == 1
    t[0] = 31.0
    live._latest["AVIONICS_BAY_B2"].received_mono = 30.0  # fresh data
    await tick(); await settle(w)
    assert [a["trigger"] for a in db.advisories()] == ["periodic", "status_change"]


async def test_run_periodic_loop_starts_and_stops():
    w, *_ = make_worker()
    await w.start()
    await asyncio.sleep(0.05)
    await w.stop()


async def test_window_prefers_influx_and_falls_back_on_error():
    w, det, live, store, db, broker = make_worker()
    feed(w, det, live, "smoldering", 25)
    await settle(w)
    store.window_rows = [{"t_s": float(i), "co_ppm": 10.0 + i} for i in range(5)]
    assert (await w.window_summary("AVIONICS_BAY_B2"))["source"] == "influxdb"
    store.down = True
    assert (await w.window_summary("AVIONICS_BAY_B2"))["source"] == "memory"


# ---- worker + real client wiring ----------------------------------------------------------
async def test_llm_failure_stores_fallback_and_event_and_alarm_is_unaffected():
    from app.advisor.omlx import LlmError

    class Down:
        async def advise(self, **kw):
            raise LlmError("first_token_timeout", "no token within 6 s")

    w, det, live, store, db, broker = make_worker(llm=Down())
    q = broker.subscribe()
    last = feed(w, det, live, "smoldering", 25)
    assert last.status == "SMOLDERING_ANOMALY"  # the detector result never depended on the LLM
    await settle(w)
    a = db.advisories()[0]
    assert a["fallback"] is True and a["model"] == "deterministic" and len(a["steps"]) == 3
    ev = db.events()
    assert ev[0]["kind"] == "advisory_fallback" and ev[0]["detail"]["reason"] == "first_token_timeout"
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    start = next(d for e, d in msgs if e == "advisory_start")
    assert start["llm"] is True and start["preliminary"]["steps"] and "Smoldering anomaly" in start["preliminary"]["warning"]
    assert [e for e, _ in msgs][-1] == "advisory_done"


async def test_llm_success_persists_model_and_first_token():
    w, det, live, store, db, broker = make_worker(llm=SlowLLM())
    w.llm.release.set()
    feed(w, det, live, "smoldering", 25)
    await settle(w)
    a = db.advisories()[0]
    assert a["fallback"] is False and a["model"] == "fake" and a["first_token_ms"] == 50
