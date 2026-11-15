// Minimal dependency-free SVG line chart, time-aligned on timestamp_ms.
export default function Chart({ title, unit, frames, pick, color = "#4cc9f0", threshold }) {
  const W = 300, H = 90, P = 4;
  const pts = frames.map((f) => [f.timestamp_ms / 1000, pick(f)]);
  const last = pts[pts.length - 1];
  const tMax = Math.max(30, last ? last[0] : 0);
  const vals = pts.map((p) => p[1]).concat(threshold ?? []);
  const lo = Math.min(0, ...vals), hi = Math.max(1, ...vals) * 1.1;
  const x = (t) => P + (t / tMax) * (W - 2 * P);
  const y = (v) => H - P - ((v - lo) / (hi - lo)) * (H - 2 * P);
  const d = pts.map(([t, v], i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <figure className="chart">
      <figcaption>
        <span>{title}</span>
        <b>{last ? `${last[1].toFixed(1)} ${unit}` : "—"}</b>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title} trend`}>
        {threshold != null && (
          <line x1={P} x2={W - P} y1={y(threshold)} y2={y(threshold)} className="thr" />
        )}
        <path d={d} fill="none" stroke={color} strokeWidth="1.6" />
      </svg>
    </figure>
  );
}
