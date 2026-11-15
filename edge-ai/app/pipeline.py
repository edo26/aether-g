"""One sample in: detector, live state, storage, SSE. The alarm path never waits on storage."""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.contract import Sample
from app.detector import Detector, Result
from app.live import LiveState
from app.stream import Broker

log = logging.getLogger("aether.pipeline")


class Store:
    """Structural type of what the pipeline needs from storage (see storage/influx.py)."""

    def record(self, sample: Sample, result: Result, source: str) -> None: ...


def sample_payload(sample: Sample, result: Result, source: str) -> dict[str, Any]:
    return {
        "compartment_id": sample.compartment_id,
        "timestamp_ms": sample.timestamp_ms,
        "mode": sample.mode,
        "source": source,
        "telemetry": sample.telemetry.model_dump(),
        "stage1_detector": result.stage1(),
        "features": result.features.as_dict(),
    }


class Pipeline:
    def __init__(self, detector: Detector, store: Store, broker: Broker, live: LiveState) -> None:
        self.detector = detector
        self.store = store
        self.broker = broker
        self.live = live
        # Hooks for later phases (events table, advisor triggers). Called with the sample and result.
        self.on_result: list[Callable[[Sample, Result, str], None]] = []

    def handle(self, sample: Sample, source: str) -> Result:
        result = self.detector.process(sample)
        self.live.update(sample, result, source)
        payload = sample_payload(sample, result, source)
        try:
            self.store.record(sample, result, source)
        except Exception:  # storage must never break detection
            log.exception("store.record failed")
        if result.status_changed or result.reset_reason:
            self.broker.publish("status_change", {
                "compartment_id": sample.compartment_id,
                "timestamp_ms": sample.timestamp_ms,
                "previous_status": result.previous_status,
                **result.stage1(),
                "features": result.features.as_dict(),
                "reset_reason": result.reset_reason,
            })
        self.broker.publish_telemetry(sample.compartment_id, payload)
        for hook in self.on_result:
            try:
                hook(sample, result, source)
            except Exception:
                log.exception("result hook failed")
        return result
