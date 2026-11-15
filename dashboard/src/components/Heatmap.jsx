import { useEffect, useRef } from "react";
import { COLS, ROWS, estimateHeatmap } from "../lib/heatmap.js";

const PROBE_X = [0.15, 0.5, 0.85]; // same positions as lib/heatmap.js (0 / 30 / 60 mm)
// Perceptual dark-to-hot ramp (deep slate → teal → amber → red) so nominal stays calm and heat reads clearly.
const STOPS = [[0, [22, 32, 48]], [0.35, [24, 92, 112]], [0.6, [214, 160, 60]], [0.8, [232, 104, 52]], [1, [255, 72, 72]]];
function ramp(u) {
  const c = Math.max(0, Math.min(1, u));
  let i = 0;
  while (i < STOPS.length - 2 && c > STOPS[i + 1][0]) i++;
  const [a, ca] = STOPS[i], [b, cb] = STOPS[i + 1];
  const k = (c - a) / (b - a);
  const m = ca.map((v, j) => Math.round(v + (cb[j] - v) * k));
  return `rgb(${m[0]} ${m[1]} ${m[2]})`;
}
const LEGEND = `linear-gradient(90deg, ${[0, 0.2, 0.4, 0.6, 0.8, 1].map(ramp).join(", ")})`;

export default function Heatmap({ frame }) {
  const ref = useRef(null);
  const t = frame?.telemetry;
  const est = t ? estimateHeatmap(t.thermocouples_c, t.max_ir_temp_c) : null;
  const span = est ? Math.max(est.max - est.min, 20) : 20; // fixed floor so nominal noise stays cool

  useEffect(() => {
    const cv = ref.current;
    if (!cv || !est) return;
    const ctx = cv.getContext("2d");
    for (let r = 0; r < ROWS; r++)
      for (let c = 0; c < COLS; c++) {
        ctx.fillStyle = ramp((est.grid[r * COLS + c] - est.min) / span);
        ctx.fillRect(c, r, 1, 1);
      }
  }, [t]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section className="panel" aria-labelledby="heat-h">
      <div className="panel-head">
        <h2 id="heat-h">Thermal</h2>
        <span className="chip caution" title="Interpolated from three thermocouples and the IR maximum">Schematic estimate</span>
      </div>

      <div className="well heat-well">
        <canvas ref={ref} width={COLS} height={ROWS} className="heat" aria-label="32 by 24 schematic thermal estimate, not a measured image" />
        {t && PROBE_X.map((px, i) => (
          <span key={i} className={`probe${est.hotProbe === i ? " hot" : ""}`} style={{ left: `${px * 100}%` }}>
            <b>P{i + 1}</b>
            <span className="mono">{t.thermocouples_c[i].toFixed(1)}°</span>
          </span>
        ))}
        {!t && <span className="heat-empty">Waiting for frames…</span>}
      </div>
      <div className="legend-bar">
        <span className="mono">{est ? est.min.toFixed(0) : "—"}°C</span>
        <i style={{ background: LEGEND }} />
        <span className="mono">{est ? (est.min + span).toFixed(0) : "—"}°C</span>
      </div>

      <dl className="mini-grid">
        <div><dt>IR maximum</dt><dd className="mono">{t ? `${t.max_ir_temp_c.toFixed(1)} °C` : "—"}</dd></div>
        <div><dt>Hottest probe</dt><dd className="mono">{t ? `P${est.hotProbe + 1}` : "—"}</dd></div>
        <div><dt>Hotspot motion</dt><dd className="mono">{t ? `${t.smolder_velocity_mm_s.toFixed(1)} mm/s` : "—"}</dd></div>
        <div><dt>Probe spacing</dt><dd className="mono">0 / 30 / 60 mm</dd></div>
      </dl>
      <p className="footnote">32×24 grid interpolated from three thermocouples and the IR maximum. A measured MLX90640 frame needs a separately agreed transport.</p>
    </section>
  );
}
