"""InfluxDB 2.x storage: batched async writes with an in-memory outage buffer, plus queries.

Ingest must never fail because storage is down: `record()` only appends to a buffer. A background
loop flushes it every second. While InfluxDB is unreachable the buffer keeps up to 5 minutes of
points (older ones are dropped and counted), the outage is logged loudly and exposed via
`health()`, and everything is flushed in order once the database is back.

Points are stamped with the *server receive time*; the source's `timestamp_ms` is kept as a field.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from influxdb_client import Point, WritePrecision
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

from app.config import Settings
from app.contract import Sample
from app.detector import Result

log = logging.getLogger("aether.influx")

MAX_BUFFER_AGE_S = 300.0
MAX_BUFFER_POINTS = 50_000  # hard safety cap; 5 min at 2 Hz x 2 points is only ~1,200
WRITE_CHUNK = 5_000
PING_INTERVAL_S = 5.0
LOG_REPEAT_S = 30.0

TELEMETRY_FIELDS = (
    "co_ppm", "co2_ppm", "ch4_ppm", "pm25_ug_m3", "airflow_cm_s", "max_ir_temp_c", "smolder_velocity_mm_s",
)


class StoreUnavailable(RuntimeError):
    """InfluxDB cannot be reached or rejected the query."""


class Backend(Protocol):
    async def write(self, points: list[Point]) -> None: ...
    async def query(self, flux: str) -> list[dict[str, Any]]: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


def build_points(sample: Sample, result: Result, source: str, received_at: datetime) -> list[Point]:
    """Convert one sample + authoritative detector result into the two measurements.

    Every numeric field is written with an explicit Python type (floats via float()): Influx fixes
    a field's type on first write, so one JSON `0` landing as an integer would break later floats.
    """
    t = sample.telemetry
    tel = (
        Point("telemetry")
        .tag("compartment_id", sample.compartment_id)
        .tag("mode", sample.mode)
        .tag("source", source)
        .field("voc_index", int(t.voc_index))
        .field("session_ts_ms", int(sample.timestamp_ms))
        .time(received_at, WritePrecision.NS)
    )
    for name in TELEMETRY_FIELDS:
        tel = tel.field(name, float(getattr(t, name)))
    for i, value in enumerate(t.thermocouples_c, start=1):
        tel = tel.field(f"tc{i}_c", float(value))

    det = (
        Point("detector")
        .tag("compartment_id", sample.compartment_id)
        .tag("mode", sample.mode)
        .field("status", result.status)
        .field("anomaly", 1 if result.status == "SMOLDERING_ANOMALY" else 0)
        .field("confidence", float(result.confidence_score))
        .field("dust_rejected", 1 if result.false_alarm_dust_check != "NOMINAL_NO_EVENT" else 0)
        .time(received_at, WritePrecision.NS)
    )
    for name, value in result.features.as_dict().items():
        if value is not None:  # ratio is None when dCO2 is under the noise floor
            det = det.field(name, float(value))
    return [tel, det]


_DURATION = re.compile(r"^(\d+)(ms|s|m|h|d)$")
_UNIT_S = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration_s(text: str) -> float:
    m = _DURATION.match(text)
    if not m or int(m.group(1)) == 0:
        raise ValueError(f"bad duration {text!r}; use e.g. 500ms, 2s, 5m, 1h")
    return int(m.group(1)) * _UNIT_S[m.group(2)]


def _flux_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _flux_duration(seconds: float) -> str:
    return f"{int(round(seconds * 1000))}ms" if seconds < 1 or seconds % 1 else f"{int(seconds)}s"


class InfluxBackend:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bucket = settings.influx_bucket
        self._client: InfluxDBClientAsync | None = None

    @property
    def client(self) -> InfluxDBClientAsync:
        # Created lazily: the aiohttp session must be made inside the running event loop.
        if self._client is None:
            s = self._settings
            self._client = InfluxDBClientAsync(url=s.influx_url, token=s.influx_token, org=s.influx_org, timeout=3000)
        return self._client

    async def write(self, points: list[Point]) -> None:
        ok = await self.client.write_api().write(bucket=self._bucket, record=points)
        if ok is False:
            raise RuntimeError("InfluxDB write returned failure")

    async def query(self, flux: str) -> list[dict[str, Any]]:
        tables = await self.client.query_api().query(flux)
        return [dict(rec.values) for table in tables for rec in table.records]

    async def ping(self) -> bool:
        return bool(await self.client.ping())

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()


class InfluxStore:
    def __init__(
        self,
        backend: Backend,
        bucket: str,
        *,
        flush_interval_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._backend = backend
        self._bucket = bucket
        self._flush_interval_s = flush_interval_s
        self._clock = clock
        self._now = now
        self._buffer: deque[tuple[float, Point]] = deque()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.up: bool | None = None
        self.last_error: str | None = None
        self.down_since: datetime | None = None
        self.last_ok: float | None = None
        self.dropped = 0
        self._last_ping = 0.0
        self._last_logged = 0.0
        # (kind, detail) callbacks: "influx_down" / "influx_recovered"; wired to the events table.
        self.on_outage: list[Callable[[str, str], None]] = []

    # ---- write path -------------------------------------------------------------------------
    def record(self, sample: Sample, result: Result, source: str) -> None:
        """Never raises and never blocks: ingest must not depend on storage."""
        try:
            now = self._clock()
            for point in build_points(sample, result, source, self._now()):
                self._buffer.append((now, point))
            while len(self._buffer) > MAX_BUFFER_POINTS:
                self._buffer.popleft()
                self.dropped += 1
        except Exception:  # pragma: no cover - defensive
            log.exception("could not buffer telemetry point")

    def _drop_expired(self) -> None:
        cutoff = self._clock() - MAX_BUFFER_AGE_S
        while self._buffer and self._buffer[0][0] < cutoff:
            self._buffer.popleft()
            self.dropped += 1

    async def flush(self) -> bool:
        """Write everything buffered. Returns True when the buffer is empty afterwards."""
        async with self._lock:
            self._drop_expired()
            while self._buffer:
                n = min(len(self._buffer), WRITE_CHUNK)
                chunk = [self._buffer[i][1] for i in range(n)]
                try:
                    await self._backend.write(chunk)
                except Exception as exc:
                    self._mark_down(exc)
                    return False
                for _ in range(n):  # nothing else pops while the lock is held
                    self._buffer.popleft()
                self._mark_up()
            return True

    async def _ping(self) -> None:
        self._last_ping = self._clock()
        try:
            ok = await self._backend.ping()
            if not ok:
                raise RuntimeError("ping failed")
        except Exception as exc:
            self._mark_down(exc)
        else:
            self._mark_up()

    def _mark_down(self, exc: Exception) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"[:300]
        now = self._clock()
        if self.up is not False:
            self.down_since = self._now()
            self._last_logged = now
            log.error("InfluxDB UNREACHABLE, buffering up to %.0f s in memory: %s", MAX_BUFFER_AGE_S, self.last_error)
            self._notify("influx_down", self.last_error)
        elif now - self._last_logged >= LOG_REPEAT_S:
            self._last_logged = now
            log.error("InfluxDB still down since %s, %d points buffered, %d dropped: %s",
                      self.down_since.isoformat() if self.down_since else "?", len(self._buffer), self.dropped, self.last_error)
        self.up = False

    def _mark_up(self) -> None:
        self.last_ok = self._clock()
        if self.up is False:
            log.warning("InfluxDB recovered; %d points still buffered, %d dropped during the outage",
                        len(self._buffer), self.dropped)
            self._notify("influx_recovered", f"down since {self.down_since.isoformat() if self.down_since else '?'}")
            self.down_since = None
        self.up = True

    def _notify(self, kind: str, detail: str) -> None:
        for cb in self.on_outage:
            try:
                cb(kind, detail)
            except Exception:  # pragma: no cover
                log.exception("outage callback failed")

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval_s)
            if self._buffer:
                await self.flush()
            elif self._clock() - self._last_ping >= PING_INTERVAL_S:
                await self._ping()

    async def start(self) -> None:
        await self._ping()
        self._task = asyncio.create_task(self.run(), name="influx-flush")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        try:
            await asyncio.wait_for(self.flush(), timeout=5)
        except Exception:
            log.warning("final flush failed; %d points lost", len(self._buffer))
        await self._backend.close()

    def health(self) -> dict[str, Any]:
        now = self._clock()
        return {
            "status": {True: "up", False: "down", None: "unknown"}[self.up],
            "buffered_points": len(self._buffer),
            "oldest_buffered_age_s": round(now - self._buffer[0][0], 1) if self._buffer else 0.0,
            "dropped_points": self.dropped,
            "down_since": self.down_since.isoformat() if self.down_since else None,
            "last_error": self.last_error if self.up is False else None,
            "last_write_ok_age_s": round(now - self.last_ok, 1) if self.last_ok is not None else None,
        }

    # ---- read path --------------------------------------------------------------------------
    async def _query(self, flux: str) -> list[dict[str, Any]]:
        try:
            return await self._backend.query(flux)
        except Exception as exc:
            raise StoreUnavailable(f"{type(exc).__name__}: {exc}"[:300]) from exc

    def _base(self, measurement: str, compartment_id: str, range_s: float, stop: datetime | None = None) -> str:
        # Relative ranges are resolved per query; callers that merge queries pass one absolute `stop`
        # so that every query sees identical window boundaries.
        if stop is None:
            rng = f"range(start: -{_flux_duration(range_s)})"
        else:
            start = stop - timedelta(seconds=range_s)
            rng = f"range(start: {start.strftime('%Y-%m-%dT%H:%M:%S.%f')}Z, stop: {stop.strftime('%Y-%m-%dT%H:%M:%S.%f')}Z)"
        return (
            f"from(bucket: {_flux_str(self._bucket)})\n"
            f"  |> {rng}\n"
            f"  |> filter(fn: (r) => r._measurement == {_flux_str(measurement)} "
            f"and r.compartment_id == {_flux_str(compartment_id)})\n"
        )

    async def window(self, compartment_id: str, seconds: float) -> list[dict[str, Any]]:
        """Raw (not downsampled) telemetry rows of the last `seconds`, each with `t_s` (epoch seconds)."""
        flux = (
            self._base("telemetry", compartment_id, seconds)
            + "  |> filter(fn: (r) => r._field != \"session_ts_ms\")\n"
            + "  |> group(columns: [\"_field\"])\n  |> sort(columns: [\"_time\"])\n"
            + "  |> pivot(rowKey: [\"_time\"], columnKey: [\"_field\"], valueColumn: \"_value\")\n"
        )
        rows = await self._query(flux)
        return [{**{k: v for k, v in r.items() if not k.startswith("_") and k not in ("result", "table")},
                 "t_s": r["_time"].timestamp()} for r in rows]

    async def series(self, compartment_id: str, range_s: float, every_s: float) -> list[dict[str, Any]]:
        """Downsampled telemetry for charts, merged with detector anomaly/confidence per window."""
        window = _flux_duration(every_s)
        stop = self._now().astimezone(timezone.utc)
        tail = (
            "  |> group(columns: [\"_field\"])\n"
            "  |> aggregateWindow(every: {w}, fn: {fn}, createEmpty: false)\n"
            "  |> pivot(rowKey: [\"_time\"], columnKey: [\"_field\"], valueColumn: \"_value\")\n"
        )
        tel_q = (
            self._base("telemetry", compartment_id, range_s, stop)
            + "  |> filter(fn: (r) => r._field != \"session_ts_ms\")\n"
            + tail.format(w=window, fn="mean")
        )
        det_q = (
            self._base("detector", compartment_id, range_s, stop)
            + "  |> filter(fn: (r) => r._field == \"anomaly\" or r._field == \"dust_rejected\" or r._field == \"confidence\")\n"
            + tail.format(w=window, fn="max")
        )
        tel_rows, det_rows = await asyncio.gather(self._query(tel_q), self._query(det_q))
        merged: dict[Any, dict[str, Any]] = {}
        for rows in (tel_rows, det_rows):
            for row in rows:
                t = row["_time"]
                out = merged.setdefault(t, {"time": t.isoformat()})
                for key, value in row.items():
                    if not key.startswith("_") and key not in ("result", "table"):
                        out[key] = round(value, 4) if isinstance(value, float) else value
        return [merged[t] for t in sorted(merged)]
