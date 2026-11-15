"""Turn a tabular data file (xlsx/xls/csv) into compact, factual statistics for RAG.

No physical interpretation is invented: channels are reported with the units given in their headers
(if any), plus baseline, range, onset (first clear departure from baseline) and time of peak.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

TABULAR_EXT = {".xlsx", ".xls", ".csv"}
_SKIP_WORDS = ("timestamp", "delta", "ts (ms)", "time (ms)")


def _sig(x: float | None, n: int = 4) -> float | None:
    if x is None or not np.isfinite(x):
        return None
    return float(f"{x:.{n}g}")


def _time_col(df: pd.DataFrame) -> str | None:
    cols = [c for c in df.columns if isinstance(c, str)]
    for c in cols:
        if "(s)" in c.lower() and "time" in c.lower() and "rel-to" not in c.lower():
            return c
    for c in cols:
        if c.lower() in ("t_s", "time_s", "time (s)", "seconds"):
            return c
    return None


def _is_meta(col: str) -> bool:
    lc = col.lower()
    return any(w in lc for w in _SKIP_WORDS) or "time" in lc


def _strip_leading_sentinel(v: pd.Series) -> pd.Series:
    """Logged channels often start with a repeated placeholder (e.g. -4078) before the sensor is live.
    Strip that leading run only if the value lies far outside the range of the remaining data, so a
    genuine flat baseline is kept."""
    if len(v) < 3:
        return v
    first = v.iloc[0]
    differs = (v != first).to_numpy()
    if not differs.any():
        return v
    run = int(differs.argmax())
    rest = v.iloc[run:]
    lo, hi = float(rest.min()), float(rest.max())
    span = max(hi - lo, 1e-12)
    dist = max(lo - first, first - hi)
    scale = max(span, float(rest.abs().median()))
    if dist > 10 * scale or (run <= 3 and dist > span):
        return rest
    return v


def summarize_column(v: pd.Series, t: pd.Series | None) -> dict:
    v = pd.to_numeric(v, errors="coerce").dropna()
    zeros_dropped = False
    if len(v) and (v == 0).mean() > 0.5 and (v != 0).sum() >= 3:
        # sparse merged logs write 0 where the channel had no sample in that row
        v, zeros_dropped = v[v != 0], True
    v = _strip_leading_sentinel(v)
    if v.empty:
        return {"kind": "empty"}
    vmin, vmax = float(v.min()), float(v.max())
    if vmin == vmax:
        return {"kind": "constant", "value": _sig(vmin), "n": int(len(v))}
    head = v.iloc[: max(5, len(v) // 10)]
    baseline = float(head.median())
    mad = float((head - baseline).abs().median()) * 1.4826
    thresh = max(5 * mad, 0.1 * (vmax - vmin))
    dev = (v - baseline).abs() > thresh
    out = {
        "kind": "signal",
        "n": int(len(v)),
        "min": _sig(vmin),
        "max": _sig(vmax),
        "mean": _sig(float(v.mean())),
        "baseline": _sig(baseline),
    }
    if zeros_dropped:
        out["note"] = "exact zeros treated as no-sample"
    peak_idx = (v - baseline).abs().idxmax()
    if t is not None:
        out["t_peak_s"] = _sig(float(t.loc[peak_idx]), 6)
        out["peak_value"] = _sig(float(v.loc[peak_idx]))
        if dev.any():
            out["t_onset_s"] = _sig(float(t.loc[dev.idxmax()]), 6)
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _with_header(raw: pd.DataFrame) -> pd.DataFrame:
    """Some sheets have a group-label row above the real header; pick the first mostly-text row."""
    for i in range(min(5, len(raw))):
        row = raw.iloc[i]
        texts = sum(isinstance(x, str) for x in row)
        if texts >= max(1, 0.5 * row.notna().sum()) and texts >= 0.5 * raw.shape[1] * 0.5:
            df = raw.iloc[i + 1 :].copy()
            names, seen = [], {}
            for x in row:
                n = str(x).strip() if isinstance(x, str) else f"Unnamed_{len(names)}"
                seen[n] = seen.get(n, 0) + 1
                names.append(n if seen[n] == 1 else f"{n}.{seen[n] - 1}")
            df.columns = names
            return df.reset_index(drop=True)
    return raw


def summarize_file(path: Path) -> dict:
    if path.suffix.lower() == ".csv":
        sheets = {"csv": pd.read_csv(path, header=None)}
    else:
        sheets = pd.read_excel(path, sheet_name=None, header=None)
    sheets = {k: _with_header(v) for k, v in sheets.items()}

    parsed = []
    for name, df in sheets.items():
        df = df.dropna(how="all").dropna(axis=1, how="all")
        tcol = _time_col(df)
        t = pd.to_numeric(df[tcol], errors="coerce") if tcol else None
        cols = {}
        for c in df.columns:
            if not isinstance(c, str) or c.startswith("Unnamed") or c == tcol or _is_meta(c):
                continue
            if pd.to_numeric(df[c], errors="coerce").notna().sum() < 10:
                continue
            cols[c.strip()] = summarize_column(df[c], t)
        span = None
        if t is not None and t.notna().any():
            span = [_sig(float(t.min()), 6), _sig(float(t.max()), 6)]
        parsed.append({"sheet": str(name), "rows": int(len(df)), "time_column": tcol, "time_span_s": span, "columns": cols})

    # Prefer sheets with a time base, and dedicated (narrow) sheets over wide merged ones.
    parsed.sort(key=lambda p: (p["time_column"] is None, len(p["columns"])))
    kept = []
    for sh in parsed:
        others = set().union(*[set(o["columns"]) for o in parsed if o is not sh]) if len(parsed) > 1 else set()
        names = set(sh["columns"])
        if sh["time_column"] is None and names and len(names & others) / len(names) >= 0.5:
            continue  # merged/duplicate tab without its own time base
        kept.append(sh)
    seen: set[str] = set()
    for sh in kept:
        sh["columns"] = {k: v for k, v in sh["columns"].items() if not (k in seen or seen.add(k))}
    kept = [sh for sh in kept if sh["columns"]]

    return {
        "file": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
        "sheets": kept,
        "dropped_duplicate_sheets": [s["sheet"] for s in parsed if s not in kept],
    }


def summarize_cached(path: Path, cache_dir: Path) -> dict:
    """Parsing xlsx is the slow part; cache by file name + mtime."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{path.resolve()}:{path.stat().st_mtime_ns}".encode()).hexdigest()[:16]
    cp = cache_dir / f"{key}.json"
    if cp.exists():
        return json.loads(cp.read_text())
    res = summarize_file(path)
    cp.write_text(json.dumps(res))
    return res
