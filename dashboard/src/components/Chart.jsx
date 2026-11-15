// Telemetry tile: big readout plus a dependency-free SVG trend over the last 60 s of timestamp_ms.
const WINDOW_S = 60;
const W = 320, H = 96, PT = 10, PB = 6;

export default function Chart({ channel, frames }) {
  const { label, unit, digits, minSpan, threshold, thresholdLabel, pick, state, stateText } = channel;
  const last = frames[frames.length - 1];
  const tNow = last ? last.timestamp_ms / 1000 : 0;
  const t0 = tNow - WINDOW_S;
  const pts = frames.filter((f) => f.timestamp_ms / 1000 >= t0).map((f) => [f.timestamp_ms / 1000, pick(f)]);
  const value = last ? pick(last) : null;
  const st = last && state ? state(value, last) : "ok";
  const note = last && stateText ? stateText(last) : null;

  const vals = pts.map((p) => p[1]);
  let lo = vals.length ? Math.min(...vals) : 0;
  let hi = vals.length ? Math.max(...vals) : 1;
  if (threshold != null) { lo = Math.min(lo, threshold); hi = Math.max(hi, threshold); }
  if (hi - lo < minSpan) { const mid = (hi + lo) / 2; lo = mid - minSpan / 2; hi = mid + minSpan / 2; }
  const padv = (hi - lo) * 0.12; lo -= padv; hi += padv;

  const x = (t) => W - ((tNow - t) / WINDOW_S) * W;
  const y = (v) => PT + (1 - (v - lo) / (hi - lo)) * (H - PT - PB);
  const line = pts.map(([t, v], i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = pts.length > 1 ? `${line} L${x(pts[pts.length - 1][0]).toFixed(1)},${H - PB} L${x(pts[0][0]).toFixed(1)},${H - PB} Z` : "";
  const ref = [...frames].reverse().find((f) => last && last.timestamp_ms - f.timestamp_ms >= 5000);
  const delta = ref && value != null ? value - pick(ref) : null;
  const flat = delta == null || Math.abs(delta) < minSpan * 0.02;
  const gid = `g-${channel.key}`;

  return (
    <figure className={`tile s-${st}`}>
      <figcaption className="tile-head">
        <span className="tile-label">{label}</span>
        {st !== "ok" && <span className={`chip ${st}`}>{note ?? (st === "alarm" ? "alarm" : "above trigger")}</span>}
      </figcaption>
      <div className="tile-value">
        <span className="mono">{value != null ? value.toFixed(digits) : "—"}</span>
        <span className="unit">{unit}</span>
        <span className={`trend mono${flat ? "" : delta > 0 ? " up" : " down"}`} title="change over the last 5 s">
          {delta == null ? "" : flat ? "steady" : `${delta > 0 ? "▲" : "▼"} ${Math.abs(delta).toFixed(digits)}`}
        </span>
      </div>
      <div className="well">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`${label} over the last 60 seconds`}>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" className="area-top" />
              <stop offset="1" className="area-bottom" />
            </linearGradient>
          </defs>
          {[0.25, 0.5, 0.75].map((g) => <line key={g} className="grid" x1={W * g} x2={W * g} y1={PT} y2={H - PB} />)}
          <line className="grid" x1="0" x2={W} y1={H - PB} y2={H - PB} />
          {threshold != null && <line className="thr" x1="0" x2={W} y1={y(threshold)} y2={y(threshold)} />}
          {area && <path d={area} fill={`url(#${gid})`} />}
          {line && <path d={line} className="trace" />}
        </svg>
        {pts.length > 0 && <i className="tip" style={{ top: `${(y(pts[pts.length - 1][1]) / H) * 100}%` }} />}
        {threshold != null && <span className="thr-label mono" style={{ top: `${(y(threshold) / H) * 100}%` }}>{thresholdLabel}</span>}
      </div>
      <div className="axis mono" aria-hidden="true"><span>−60 s</span><span>−30 s</span><span>now</span></div>
    </figure>
  );
}
