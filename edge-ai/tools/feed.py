#!/usr/bin/env python3
"""Fake Jetson: feed simulator output into POST /api/v1/ingest and summarise what came back.

  uv run python tools/feed.py --scenario smoldering --speed 20
  uv run python tools/feed.py --scenario smoldering --thermal      # also send synthetic MLX90640 frames
  python3 ../simulator/simulator.py --scenario dust | uv run python tools/feed.py --stdin
  cat /dev/ttyACM0 | uv run python tools/feed.py --stdin --speed 0 --source jetson   # ESP32 serial bridge

In --stdin mode a line with "pixels_c" is a thermal camera frame and goes to POST /api/v1/thermal;
every other JSON line goes to /api/v1/ingest. Lines that are not JSON (boot logs) are skipped.

--speed N sends N times faster than real time (timestamps stay logical, so detector maths is
unchanged). Defaults come from AETHER_* env vars / .env.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

SIMULATOR = Path(__file__).resolve().parents[2] / "simulator" / "simulator.py"


def load_simulator():
    spec = importlib.util.spec_from_file_location("aether_simulator", SIMULATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def generate(scenario: str, interval_ms: int, duration_s: float):
    sim = load_simulator()
    ts, prev = 0, None
    while ts <= duration_s * 1000:
        prev = sim.make_sample(scenario, ts, prev)
        yield prev
        ts += interval_ms


def read_stdin():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            print(f"skipped non-JSON line: {line[:80]}", file=sys.stderr)
            continue
        if isinstance(obj, dict):
            yield obj


PROBE_X = (0.15, 0.5, 0.85)  # same schematic probe positions as the dashboard


def synthetic_frame(sample: dict) -> dict:
    """A clearly synthetic 32x24 MLX90640-shaped frame consistent with the sample's probes and IR max."""
    import math
    import random

    t = sample["telemetry"]
    tcs, peak = t["thermocouples_c"], t["max_ir_temp_c"]
    base = min(tcs)
    weights = [max(v - base, 0.0) ** 2 for v in tcs]
    cx = sum(w * x for w, x in zip(weights, PROBE_X)) / sum(weights) if sum(weights) > 0 else 0.5
    px = []
    for y in range(24):
        for x in range(32):
            u, v = x / 31, y / 23
            g = math.exp(-(((u - cx) / 0.13) ** 2 + ((v - 0.5) / 0.22) ** 2))
            px.append(round(base + (peak - base) * g + random.uniform(-0.15, 0.15), 2))
    return {"timestamp_ms": sample["timestamp_ms"], "compartment_id": sample["compartment_id"],
            "sensor": "MLX90640", "width": 32, "height": 24, "pixels_c": px}


def env_default(name: str, fallback: str) -> str:
    if name in os.environ:
        return os.environ[name]
    env = Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return fallback


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--scenario", choices=("nominal", "dust", "smoldering", "airgapped"))
    src.add_argument("--stdin", action="store_true", help="read JSONL contract lines from stdin")
    p.add_argument("--url", default="http://localhost:8090", help="backend base URL")
    p.add_argument("--token", default=env_default("AETHER_DEVICE_TOKEN", "change-me"))
    p.add_argument("--interval-ms", type=int, default=500)
    p.add_argument("--duration-s", type=float, default=75.0, help="scenario length in logical seconds")
    p.add_argument("--speed", type=float, default=1.0, help="1 = real time, 20 = 20x faster")
    p.add_argument("--no-detector", action="store_true", help="omit stage1_detector like a bare Jetson")
    p.add_argument("--source", default="simulator", choices=("jetson", "simulator", "replay"))
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--thermal", action="store_true", help="also send a synthetic MLX90640 frame per sample")
    a = p.parse_args()

    samples = read_stdin() if a.stdin else generate(a.scenario, a.interval_ms, a.duration_s)
    headers = {"X-Device-Token": a.token, "X-Aether-Source": a.source}
    latencies: list[float] = []
    last_status = None
    first_anomaly = None
    confs: list[float] = []
    dust_samples = n = errors = mismatches = 0
    prev_ts = None
    consecutive_errors = 0
    frames_sent = 0
    with httpx.Client(base_url=a.url, headers=headers, timeout=5.0) as client:
        for sample in samples:
            if "pixels_c" in sample:  # thermal camera frame from the serial stream
                try:
                    r = client.post("/api/v1/thermal", json=sample)
                    if r.status_code != 200:
                        print(f"thermal HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
                        errors += 1
                    else:
                        frames_sent += 1
                except httpx.HTTPError as exc:
                    print(f"thermal POST failed: {exc}", file=sys.stderr)
                    errors += 1
                continue
            if a.thermal:
                try:
                    if client.post("/api/v1/thermal", json=synthetic_frame(sample)).status_code == 200:
                        frames_sent += 1
                except httpx.HTTPError as exc:
                    print(f"thermal POST failed: {exc}", file=sys.stderr)
            provisional = sample.get("stage1_detector")
            if a.no_detector:
                sample = {k: v for k, v in sample.items() if k != "stage1_detector"}
            t0 = time.perf_counter()
            try:
                r = client.post("/api/v1/ingest", json=sample)
            except httpx.HTTPError as exc:
                print(f"POST failed: {exc}", file=sys.stderr)
                errors += 1
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    print("aborting after 5 consecutive failures", file=sys.stderr)
                    break
                continue
            latencies.append((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                print(f"HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
                errors += 1
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    print("aborting after 5 consecutive failures", file=sys.stderr)
                    break
                continue
            consecutive_errors = 0
            n += 1
            d = r.json()["stage1_detector"]
            if provisional is not None and provisional != d:
                mismatches += 1
            if d["false_alarm_dust_check"] != "NOMINAL_NO_EVENT":
                dust_samples += 1
            if d["status"] == "SMOLDERING_ANOMALY":
                confs.append(d["confidence_score"])
                if first_anomaly is None:
                    first_anomaly = (sample["timestamp_ms"], sample["telemetry"]["co_ppm"])
            if d["status"] != last_status and not a.quiet:
                print(f"t={sample['timestamp_ms'] / 1000:7.1f}s  {last_status or '-'} -> {d['status']}"
                      f"  conf={d['confidence_score']}")
            last_status = d["status"]
            if prev_ts is not None and a.speed > 0:
                time.sleep(max(sample["timestamp_ms"] - prev_ts, 0) / 1000.0 / a.speed)
            prev_ts = sample["timestamp_ms"]

    print(f"\nsent={n} thermal_frames={frames_sent} errors={errors} final_status={last_status}")
    if latencies:
        qs = statistics.quantiles(latencies, n=20) if len(latencies) >= 20 else [max(latencies)] * 19
        print(f"POST latency ms: median={statistics.median(latencies):.1f} p95={qs[18]:.1f} max={max(latencies):.1f}")
    if first_anomaly:
        print(f"first anomaly at t={first_anomaly[0] / 1000:.1f}s (CO {first_anomaly[1]} ppm); "
              f"confidence {min(confs)} -> {max(confs)}")
    print(f"samples with dust marker: {dust_samples}")
    if not a.no_detector:
        print(f"backend differed from the simulator's embedded detector on {mismatches} samples")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
