from datetime import datetime, timezone
from typing import Any

from app.storage.influx import StoreUnavailable


class FakeStore:
    """Stands in for InfluxStore in API tests."""

    def __init__(self) -> None:
        self.recorded: list[tuple[Any, Any, str]] = []
        self.rows: list[dict] = []
        self.down = False
        self.series_calls: list[tuple] = []
        self.window_rows: list[dict] = []
        self.on_outage: list = []

    def record(self, sample, result, source):
        self.recorded.append((sample, result, source))

    async def series(self, compartment_id, range_s, every_s):
        self.series_calls.append((compartment_id, range_s, every_s))
        if self.down:
            raise StoreUnavailable("connection refused")
        return self.rows

    async def flush(self):
        return True

    async def window(self, compartment_id, seconds):
        if self.down:
            raise StoreUnavailable("connection refused")
        return self.window_rows

    def health(self):
        return {"status": "down" if self.down else "up", "buffered_points": 0, "oldest_buffered_age_s": 0.0,
                "dropped_points": 0, "down_since": None, "last_error": None, "last_write_ok_age_s": 0.0}

    async def start(self): ...
    async def stop(self): ...


class FakeBackend:
    """Influx backend double with an on/off switch."""

    def __init__(self) -> None:
        self.down = False
        self.written: list[Any] = []
        self.queries: list[str] = []
        self.query_rows: list[dict] = []

    async def write(self, points):
        if self.down:
            raise ConnectionError("connection refused")
        self.written.extend(points)

    async def query(self, flux):
        if self.down:
            raise ConnectionError("connection refused")
        self.queries.append(flux)
        return self.query_rows

    async def ping(self):
        if self.down:
            raise ConnectionError("connection refused")
        return True

    async def close(self): ...


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)
