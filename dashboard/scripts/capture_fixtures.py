#!/usr/bin/env python3
"""Capture seeded 500 ms JSONL fixtures from simulator/simulator.py (synthetic data)."""
import json, random, sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "simulator"))
from simulator import make_sample  # noqa: E402

out = Path(__file__).resolve().parents[1] / "public" / "fixtures"
out.mkdir(parents=True, exist_ok=True)
FRAMES, INTERVAL = 240, 500  # 120 s: covers the 60 s ramp and the plateau

for i, scenario in enumerate(("nominal", "dust", "smoldering", "airgapped")):
    random.seed(2026 + i)
    prev, lines = None, []
    for n in range(FRAMES):
        prev = make_sample(scenario, n * INTERVAL, prev)
        lines.append(json.dumps(prev, allow_nan=False))
    (out / f"{scenario}.jsonl").write_text("\n".join(lines) + "\n")
    print(scenario, len(lines), "frames")
