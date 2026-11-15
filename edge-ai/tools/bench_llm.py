#!/usr/bin/env python3
"""Check the real oMLX and measure advisory latency (Phase 4 acceptance).

  uv run python tools/bench_llm.py --probe            # GET /v1/models, prints the raw model ids
  uv run python tools/bench_llm.py --runs 10          # trigger -> first token and -> complete advisory

Reads AETHER_OMLX_* from the environment or .env. Run 1 is the cold run and is reported apart:
the target (< 2 s complete) is for a warm model. Prints what was measured; does not judge the target.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.advisor.omlx import LlmError, OmlxClient  # noqa: E402
from app.advisor.rag import Rag  # noqa: E402
from app.advisor.summary import summarize  # noqa: E402
from app.config import Settings  # noqa: E402
from app.stream import Broker  # noqa: E402


class NullBroker(Broker):
    pass


def smoldering_summary() -> dict:
    sim = {}
    exec(compile((Path(__file__).resolve().parents[2] / "simulator" / "simulator.py").read_text(), "sim", "exec"), sim)
    rows, prev = [], None
    for i in range(24):  # 12 s of the smoldering scenario, just after the trip
        prev = sim["make_sample"]("smoldering", i * 500, prev)
        t = prev["telemetry"]
        rows.append({"t_s": i * 0.5, "co_ppm": t["co_ppm"], "co2_ppm": t["co2_ppm"], "voc_index": t["voc_index"],
                     "pm25_ug_m3": t["pm25_ug_m3"], "max_ir_temp_c": t["max_ir_temp_c"],
                     "tc1_c": t["thermocouples_c"][0], "tc2_c": t["thermocouples_c"][1], "tc3_c": t["thermocouples_c"][2]})
    return summarize(rows, 60, "benchmark")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--runs", type=int, default=10)
    a = ap.parse_args()
    s = Settings()
    if not s.omlx_configured:
        print("AETHER_OMLX_BASE_URL / AETHER_OMLX_MODEL are not set", file=sys.stderr)
        return 2
    client = OmlxClient(s.omlx_base_url, s.omlx_model, s.omlx_api_key, first_token_timeout_s=s.llm_first_token_timeout_s,
                        connect_timeout_s=s.llm_connect_timeout_s, total_timeout_s=s.llm_total_timeout_s)
    await client.probe()
    print("probe:", client.health())
    if a.probe:
        await client.stop()
        return 0 if client.up else 1
    rag = Rag.load(s.rag_brief, s.rag_manual)
    summary = smoldering_summary()
    facts = rag.retrieve("smoldering cable carbon monoxide gas temperature rise spacecraft fire", 4)
    first, total, failures = [], [], 0
    for i in range(a.runs):
        t0 = time.monotonic()
        try:
            adv = await client.advise(cid="AVIONICS_BAY_B2", trigger="status_change", run_id=f"bench{i}",
                                      status="SMOLDERING_ANOMALY", confidence=0.65, dust_check="NOMINAL_NO_EVENT",
                                      summary=summary, facts=facts, question=None, broker=NullBroker(), t0=t0)
        except LlmError as exc:
            failures += 1
            print(f"run {i + 1}: FAILED {exc}")
            continue
        done = (time.monotonic() - t0) * 1000
        first.append(adv.first_token_ms)
        total.append(done)
        print(f"run {i + 1}{' (cold)' if i == 0 else ''}: first token {adv.first_token_ms} ms, complete {done:.0f} ms, "
              f"citations {[c['id'] for c in adv.citations]}")
    warm_first, warm_total = first[1:], total[1:]
    if warm_total:
        print(f"\nwarm runs ({len(warm_total)}): first token median {statistics.median(warm_first):.0f} ms, "
              f"complete median {statistics.median(warm_total):.0f} ms, max {max(warm_total):.0f} ms; "
              f"{sum(t < 2000 for t in warm_total)}/{len(warm_total)} under 2 s")
    print(f"failures: {failures}/{a.runs}; model {s.omlx_model}")
    await client.stop()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
