"""Writes status transitions, session resets, dust rejections and outages to the SQLite events table."""

from __future__ import annotations

from app.contract import Sample
from app.detector import Result
from app.storage.sqlite import Db


class EventLog:
    def __init__(self, db: Db) -> None:
        self.db = db

    def on_result(self, sample: Sample, result: Result, source: str) -> None:
        cid = sample.compartment_id
        base = {"timestamp_ms": sample.timestamp_ms, "confidence_score": result.confidence_score, "source": source}
        if result.reset_reason:
            self.db.add_event("session_reset", cid, {**base, "reason": result.reset_reason,
                                                      "previous_status": result.previous_status})
        if result.status_changed and not result.reset_reason:
            self.db.add_event("status_change", cid, {**base, "from": result.previous_status, "to": result.status})
        if result.dust_event:
            self.db.add_event("dust_rejected", cid, {**base, "pm_delta": result.features.pm_delta})

    def on_outage(self, kind: str, detail: str) -> None:
        self.db.add_event(kind, None, {"detail": detail})
