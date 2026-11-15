"""Advisor worker: triggers, single-flight per compartment, window summary, RAG, persistence, SSE.

The rule-based alarm never waits on this: it runs as a background task after the detector has
already published `status_change`.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.advisor import fallback
from app.advisor.omlx import LlmError
from app.advisor.rag import Rag
from app.advisor.summary import CHANNELS, summarize
from app.contract import Sample
from app.detector import ANOMALY, Detector, Result
from app.live import LiveState
from app.storage.sqlite import Db
from app.stream import Broker

log = logging.getLogger("aether.advisor")

# Higher wins. An escalation cancels and replaces a running generation; same or lower is dropped.
PRIORITY = {"status_change": 3, "ask": 2, "periodic": 1, "dust_rejected": 1}
WINDOW_S = 60
DUST_COOLDOWN_S = 60.0
BASE_QUERY = {
    "SMOLDERING_ANOMALY": "smoldering cable overheating carbon monoxide carbon dioxide gas temperature rise smoke detection spacecraft fire",
    "NOMINAL": "dust smoke aerosol particulate detection spacecraft false alarm",
}


@dataclass
class _Running:
    priority: int
    task: asyncio.Task
    run_id: str


class AdvisorWorker:
    def __init__(
        self, *, detector: Detector, live: LiveState, store: Any, db: Db, rag: Rag, broker: Broker,
        period_s: float = 30.0, llm: Any = None, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.detector, self.live, self.store, self.db = detector, live, store, db
        self.rag, self.broker, self.period_s, self.llm, self._clock = rag, broker, period_s, llm, clock
        self._running: dict[str, _Running] = {}
        self._last_start: dict[str, float] = {}
        self._last_dust: dict[str, float] = {}
        self._periodic: asyncio.Task | None = None
        self.dropped = 0

    # ---- triggers ---------------------------------------------------------------------------
    def on_result(self, sample: Sample, result: Result, source: str) -> None:
        cid = sample.compartment_id
        if result.status_changed and result.status == ANOMALY:
            self.trigger(cid, "status_change")
        elif result.dust_event:
            now = self._clock()
            if now - self._last_dust.get(cid, -1e9) >= DUST_COOLDOWN_S:
                self._last_dust[cid] = now
                self.trigger(cid, "dust_rejected")

    def trigger(self, compartment_id: str, trigger: str, *, question: str | None = None) -> bool:
        """Start a generation unless one of equal or higher priority is running. Returns accepted."""
        return self.start_run(compartment_id, trigger, question=question) is not None

    def start_run(self, compartment_id: str, trigger: str, *, question: str | None = None) -> str | None:
        """Like `trigger`, but returns the run_id (carried by the SSE events) or None when dropped."""
        prio = PRIORITY[trigger]
        current = self._running.get(compartment_id)
        if current is not None:
            if prio <= current.priority:
                self.dropped += 1
                log.info("advisor trigger %s for %s dropped (running priority %d)", trigger, compartment_id, current.priority)
                return None
            current.task.cancel()  # escalation replaces the running generation
        run_id = uuid.uuid4().hex[:12]
        task = asyncio.get_running_loop().create_task(
            self._execute(compartment_id, trigger, run_id, question), name=f"advisor-{compartment_id}")
        self._running[compartment_id] = _Running(prio, task, run_id)
        return run_id

    def is_running(self, compartment_id: str) -> bool:
        return compartment_id in self._running

    async def run_periodic(self) -> None:
        """While an anomaly persists, refresh the advisory every `period_s` (only with live data)."""
        while True:
            await asyncio.sleep(1.0)
            now = self._clock()
            for cid, latest in self.live.all().items():
                if (self.detector.status_of(cid) == ANOMALY and self.live.age_s(latest) < 5.0
                        and now - self._last_start.get(cid, -1e9) >= self.period_s):
                    self.trigger(cid, "periodic")

    async def start(self) -> None:
        self._periodic = asyncio.create_task(self.run_periodic(), name="advisor-periodic")

    async def stop(self) -> None:
        tasks = [self._periodic] if self._periodic else []
        tasks += [r.task for r in self._running.values()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---- one generation ---------------------------------------------------------------------
    async def window_summary(self, compartment_id: str) -> dict | None:
        """Last 60 s from InfluxDB; falls back to the in-memory ring if Influx cannot answer."""
        latest = self.live.get(compartment_id)
        if latest is None:
            return None
        try:
            await asyncio.wait_for(self.store.flush(), timeout=1.0)  # make the newest points queryable
            rows = await asyncio.wait_for(self.store.window(compartment_id, WINDOW_S), timeout=2.0)
            if rows:
                return summarize(rows, WINDOW_S, "influxdb")
        except Exception as exc:  # Influx down, timeout, anything: the memory window is the safety net
            log.warning("window query failed (%s); using in-memory window", type(exc).__name__)
        return summarize(self.live.window(compartment_id, WINDOW_S), WINDOW_S, "memory")

    async def _execute(self, cid: str, trigger: str, run_id: str, question: str | None) -> None:
        t0 = self._clock()
        self._last_start[cid] = t0
        try:
            latest = self.live.get(cid)
            if latest is None:
                return
            status = latest.result.status
            quick = fallback.build(trigger, cid, status, summarize(self.live.window(cid, WINDOW_S), WINDOW_S, "memory"), [])
            self.broker.publish("advisory_start", {
                "run_id": run_id, "compartment_id": cid, "trigger": trigger, "status": status,
                "started_at": datetime.now(timezone.utc).isoformat(), "llm": self.llm is not None and trigger != "dust_rejected",
                "preliminary": {"warning": quick.warning, "steps": quick.steps, "model": quick.model}})
            summary = await self.window_summary(cid)
            query = BASE_QUERY.get(status, BASE_QUERY["NOMINAL"]) + (f" {question}" if question else "")
            facts = self.rag.retrieve(query, 4)
            adv = None
            if trigger != "dust_rejected" and self.llm is not None:
                try:
                    adv = await self.llm.advise(
                        cid=cid, trigger=trigger, run_id=run_id, status=status,
                        confidence=latest.result.confidence_score, dust_check=latest.result.false_alarm_dust_check,
                        summary=summary, facts=facts, question=question, broker=self.broker, t0=t0)
                except LlmError as exc:
                    log.warning("LLM failed for %s (%s); using deterministic advisory", cid, exc)
                    self.db.add_event("advisory_fallback", cid, {"trigger": trigger, "reason": exc.kind, "detail": str(exc)[:300]})
            if adv is None:
                adv = fallback.build(trigger, cid, status, summary, facts, question=question)
            latency_ms = int((self._clock() - t0) * 1000)
            advisory_id = self.db.add_advisory(
                compartment_id=cid, trigger=trigger, status=status, confidence=latest.result.confidence_score,
                window_summary=summary, facts_used=adv.facts_used, model=adv.model, latency_ms=latency_ms,
                first_token_ms=adv.first_token_ms, warning=adv.warning, steps=adv.steps,
                citations=adv.citations, raw_text=adv.raw_text, fallback=int(adv.fallback))
            if trigger == "ask":
                self.db.add_chat(cid, "assistant", adv.warning, advisory_id)
            self.broker.publish("advisory_done", {
                "run_id": run_id, "cancelled": False, "advisory": {**self.db.advisory(advisory_id), "disclaimer": fallback.DISCLAIMER}})
        except asyncio.CancelledError:
            self.broker.publish("advisory_done", {"run_id": run_id, "compartment_id": cid, "cancelled": True})
            raise
        except Exception:
            log.exception("advisory generation failed for %s", cid)
            self.db.add_event("advisor_error", cid, {"trigger": trigger})
        finally:
            running = self._running.get(cid)
            if running is not None and running.run_id == run_id:
                del self._running[cid]
