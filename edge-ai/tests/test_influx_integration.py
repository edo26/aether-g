"""Runs against a real InfluxDB (docker compose up influxdb). Skipped when it is unreachable."""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.config import Settings
from app.detector import Detector
from app.storage.influx import InfluxBackend, InfluxStore, build_points
from tests.helpers import samples

settings = Settings()


def _reachable() -> bool:
    try:
        return httpx.get(f"{settings.influx_url}/health", timeout=1).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="InfluxDB not reachable")


async def test_roundtrip_downsample_and_types():
    cid = f"TEST_{uuid.uuid4().hex[:8]}"
    backend = InfluxBackend(settings)
    store = InfluxStore(backend, settings.influx_bucket)
    try:
        det = Detector()
        base = datetime.now(timezone.utc) - timedelta(seconds=40)
        points = []
        for i, s in enumerate(samples("smoldering", 80)):  # 40 s of data
            s.compartment_id = cid
            r = det.process(s)
            points += build_points(s, r, "simulator", base + timedelta(milliseconds=500 * i))
        await backend.write(points)
        rows = await store.series(cid, 120, 2)
        assert 15 <= len(rows) <= 25
        assert all("co_ppm" in r and "tc3_c" in r and "anomaly" in r for r in rows)
        assert rows[0]["co_ppm"] < rows[-1]["co_ppm"]
        assert rows[-1]["anomaly"] == 1.0 and rows[0]["anomaly"] == 0.0
        assert "session_ts_ms" not in rows[0] and "status" not in rows[0]
        # an integer JSON value must not have fixed a field to int: write a float over the same series
        await backend.write(points[:2])
    finally:
        client = backend.client
        await client.delete_api().delete(
            start=datetime(2000, 1, 1, tzinfo=timezone.utc), stop=datetime.now(timezone.utc) + timedelta(days=1),
            predicate=f'compartment_id="{cid}"', bucket=settings.influx_bucket,
        )
        await backend.close()
