"""Load the real simulator's generator without its sleep loop."""

from pathlib import Path

from app.contract import Sample

SIMULATOR = Path(__file__).resolve().parents[2] / "simulator" / "simulator.py"


def simulate(scenario: str, n: int, interval_ms: int = 500, seed: int = 1234) -> list[dict]:
    import random

    ns: dict = {}
    exec(compile(SIMULATOR.read_text(), str(SIMULATOR), "exec"), ns)
    random.seed(seed)
    out, prev = [], None
    for i in range(n):
        prev = ns["make_sample"](scenario, i * interval_ms, prev)
        out.append(prev)
    return out


def samples(scenario: str, n: int, **kw) -> list[Sample]:
    return [Sample.model_validate(d) for d in simulate(scenario, n, **kw)]
