#!/usr/bin/env python3
"""Emit synthetic AETHER-G telemetry as newline-delimited JSON, offline."""

import argparse
import json
import math
import random
import time


def positive_interval(value):
    """Reject zero and negative sample intervals at the CLI boundary."""
    interval = int(value)
    if interval <= 0:
        raise argparse.ArgumentTypeError("--interval-ms must be a positive integer")
    return interval


def make_sample(scenario, timestamp_ms, previous=None):
    """Build a complete sample; previous supplies the CO slope and alarm latch."""
    elapsed_s = timestamp_ms / 1000.0
    telemetry = {
        "co_ppm": round(random.uniform(0.0, 3.0), 2),
        "co2_ppm": round(random.uniform(400.0, 600.0), 2),
        "voc_index": random.randint(5, 20),
        "ch4_ppm": round(random.uniform(0.1, 0.5), 2),
        "pm25_ug_m3": round(random.uniform(1.0, 9.0), 2),
        "airflow_cm_s": round(random.uniform(2.0, 5.0), 2),
        "thermocouples_c": [round(random.uniform(22.0, 24.0), 2) for _ in range(3)],
        "max_ir_temp_c": round(random.uniform(24.0, 24.5), 2),
        "smolder_velocity_mm_s": 0.0,
    }

    if scenario == "dust":
        telemetry["pm25_ug_m3"] = round(random.uniform(80.0, 150.0), 2)
    elif scenario == "smoldering":
        progress = min(elapsed_s / 60.0, 1.0)
        # A 1 mm/s hotspot traverses the demo probes, then reverses direction.
        hotspot_mm = 60.0 - abs(60.0 - elapsed_s % 120.0)
        telemetry.update({
            "co_ppm": round(5.0 + 120.0 * progress, 2),
            "co2_ppm": round(500.0 + 100.0 * progress, 2),
            "voc_index": round(20 + 180 * progress),
            "pm25_ug_m3": round(4.0 + 35.0 * progress, 2),
            "thermocouples_c": [
                round(
                    23.0 + 12.0 * progress
                    + 30.0 * progress * math.exp(-((position - hotspot_mm) / 20.0) ** 2),
                    2,
                )
                for position in (0.0, 30.0, 60.0)
            ],
            "max_ir_temp_c": round(24.0 + 50.0 * progress, 2),
            "smolder_velocity_mm_s": 1.0,
        })

    dco_dt = 0.0
    was_anomaly = False
    if previous is not None:
        delta_s = (timestamp_ms - previous["timestamp_ms"]) / 1000.0
        if delta_s > 0:
            dco_dt = (telemetry["co_ppm"] - previous["telemetry"]["co_ppm"]) / delta_s
        was_anomaly = previous["stage1_detector"]["status"] == "SMOLDERING_ANOMALY"

    detector = {
        "status": "NOMINAL",
        "confidence_score": 0.95,
        "false_alarm_dust_check": "NOMINAL_NO_EVENT",
    }
    if telemetry["co_ppm"] > 25.0 and (dco_dt > 0.0 or was_anomaly):
        detector["status"] = "SMOLDERING_ANOMALY"
        severity = min((telemetry["co_ppm"] - 25.0) / 100.0, 1.0)
        detector["confidence_score"] = round(0.6 + 0.35 * severity, 3)
    elif scenario == "dust":
        detector["false_alarm_dust_check"] = "DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE"

    return {
        "timestamp_ms": timestamp_ms,
        "mode": "NASA_REPLAY" if scenario == "airgapped" else "LIVE_IOT",
        "compartment_id": "AVIONICS_BAY_B2",
        "telemetry": telemetry,
        "stage1_detector": detector,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", required=True,
        choices=("nominal", "dust", "smoldering", "airgapped"),
    )
    parser.add_argument(
        "--interval-ms", type=positive_interval, default=500,
        help="positive sampling interval in milliseconds (default: 500)",
    )
    args = parser.parse_args()
    timestamp_ms = 0
    previous = None
    try:
        while True:
            sample = make_sample(args.scenario, timestamp_ms, previous)
            print(json.dumps(sample, allow_nan=False), flush=True)
            previous = sample
            timestamp_ms += args.interval_ms
            time.sleep(args.interval_ms / 1000.0)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
