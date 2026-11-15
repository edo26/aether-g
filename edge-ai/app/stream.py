"""In-process pub/sub for the dashboard's Server-Sent Events stream."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Callable

EVENTS = ("telemetry", "status_change", "advisory_start", "advisory_token", "advisory_done", "health")
TELEMETRY_MIN_INTERVAL_S = 0.45  # ~2 Hz per compartment, tolerant of 500 ms +/- jitter
QUEUE_SIZE = 256


class Broker:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._subs: set[asyncio.Queue[tuple[str, dict[str, Any]]]] = set()
        self._clock = clock
        self._last_telemetry: dict[str, float] = {}

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: str, data: dict[str, Any]) -> None:
        """Non-blocking. A slow subscriber loses its oldest events rather than stalling ingest."""
        for q in list(self._subs):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover
                    pass
            q.put_nowait((event, data))

    def publish_telemetry(self, compartment_id: str, data: dict[str, Any]) -> bool:
        now = self._clock()
        last = self._last_telemetry.get(compartment_id)
        if last is not None and now - last < TELEMETRY_MIN_INTERVAL_S:
            return False
        self._last_telemetry[compartment_id] = now
        self.publish("telemetry", data)
        return True


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'), allow_nan=False)}\n\n"


async def event_stream(
    broker: Broker,
    is_disconnected: Callable[[], Any],
    first: dict[str, Any] | None = None,
    keepalive_s: float = 15.0,
) -> AsyncIterator[str]:
    q = broker.subscribe()
    try:
        yield "retry: 2000\n\n"
        if first is not None:
            yield format_sse("health", first)
        while True:
            if await is_disconnected():
                return
            try:
                event, data = await asyncio.wait_for(q.get(), timeout=keepalive_s)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield format_sse(event, data)
    finally:
        broker.unsubscribe(q)
