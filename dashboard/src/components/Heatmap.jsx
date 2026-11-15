import { useEffect, useRef, useState } from "react";
import { COLS, ROWS, estimateHeatmap } from "../lib/heatmap.js";
import { THERMAL_FRESH_S, isMeasuredSource, thermalAgeS, thermalStats } from "../lib/thermal.js";

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

/**
 * Shows the measured MLX90640 frame when a fresh one exists (live mode), otherwise the schematic
 * estimate interpolated from the three thermocouples and the IR maximum.
 */
export default function Heatmap({ frame, thermal, now }) {
  const ref = useRef(null);
  const [view, setView] = useState("grid"); // grid shows the sensor cells, smooth interpolates them
  const t = frame?.telemetry;

  const cam = thermal?.frame ?? null;
  const camAge = thermalAgeS(cam, thermal?.fetchedAt, now);
  const camFresh = cam != null && camAge != null && camAge < THERMAL_FRESH_S;
  const camStats = camFresh ? thermalStats(cam) : null;
  const measured = camFresh && isMeasuredSource(cam.source);

  const est = !camFresh && t ? estimateHeatmap(t.thermocouples_c, t.max_ir_temp_c) : null;
  const lo = camFresh ? camStats.min : est?.min ?? 0;
  const span = camFresh ? Math.max(camStats.max - camStats.min, 10) : est ? Math.max(est.max - est.min, 20) : 20;
  const W = camFresh ? cam.width : COLS, H = camFresh ? cam.height : ROWS;

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (camFresh) {
      cam.pixels_c.forEach((v, i) => { ctx.fillStyle = ramp((v - lo) / span); ctx.fillRect(i % W, Math.floor(i / W), 1, 1); });
    } else if (est) {
      for (let r = 0; r < ROWS; r++)
        for (let c = 0; c < COLS; c++) { ctx.fillStyle = ramp((est.grid[r * COLS + c] - lo) / span); ctx.fillRect(c, r, 1, 1); }
    }
  }, [cam, camFresh, t]); // eslint-disable-line react-hooks/exhaustive-deps

  const chip = camFresh
    ? measured ? <span className="chip ok" title="Measured frame from the thermal camera">MLX90640 · measured</span>
      : <span className="chip caution" title="Synthetic frame sent by the simulator">Simulated camera frame</span>
    : <span className="chip caution" title="Interpolated from three thermocouples and the IR maximum">Schematic estimate</span>;

  return (
    <section className="panel" aria-labelledby="heat-h">
      <div className="panel-head">
        <h2 id="heat-h">Thermal</h2>
        <div className="head-tools">
          <div className="seg" role="group" aria-label="Heatmap view">
            <button type="button" aria-pressed={view === "grid"} onClick={() => setView("grid")}>Grid {W}×{H}</button>
            <button type="button" aria-pressed={view === "smooth"} onClick={() => setView("smooth")}>Smooth</button>
          </div>
          {chip}
        </div>
      </div>

      <div className={`well heat-well view-${view}`} style={{ "--cols": W, "--rows": H }}>
        <canvas key={`${W}x${H}`} ref={ref} width={W} height={H} className="heat"
          aria-label={camFresh ? `${W} by ${H} thermal camera frame` : "32 by 24 schematic thermal estimate, not a measured image"} />
        {camFresh && (
          <span className="hotspot" style={{ left: `${((camStats.hotspot.x + 0.5) / W) * 100}%`, top: `${((camStats.hotspot.y + 0.5) / H) * 100}%` }}>
            <i aria-hidden="true" />
            <span className="mono">{camStats.max.toFixed(1)}°</span>
          </span>
        )}
        {!camFresh && t && PROBE_X.map((px, i) => (
          <span key={i} className={`probe${est.hotProbe === i ? " hot" : ""}`} style={{ left: `${px * 100}%` }}>
            <b>P{i + 1}</b>
            <span className="mono">{t.thermocouples_c[i].toFixed(1)}°</span>
          </span>
        ))}
        {!camFresh && !t && <span className="heat-empty">Waiting for frames…</span>}
      </div>
      <div className="legend-bar">
        <span className="mono">{camFresh || est ? lo.toFixed(0) : "—"}°C</span>
        <i style={{ background: LEGEND }} />
        <span className="mono">{camFresh || est ? (lo + span).toFixed(0) : "—"}°C</span>
      </div>

      {camFresh ? (
        <dl className="mini-grid">
          <div><dt>Camera max</dt><dd className="mono">{camStats.max.toFixed(1)} °C</dd></div>
          <div><dt>Camera min</dt><dd className="mono">{camStats.min.toFixed(1)} °C</dd></div>
          <div><dt>Hotspot pixel</dt><dd className="mono">x {camStats.hotspot.x} · y {camStats.hotspot.y}</dd></div>
          <div><dt>Frame age</dt><dd className="mono">{camAge.toFixed(1)} s</dd></div>
        </dl>
      ) : (
        <dl className="mini-grid">
          <div><dt>IR maximum</dt><dd className="mono">{t ? `${t.max_ir_temp_c.toFixed(1)} °C` : "—"}</dd></div>
          <div><dt>Hottest probe</dt><dd className="mono">{t ? `P${est.hotProbe + 1}` : "—"}</dd></div>
          <div><dt>Hotspot motion</dt><dd className="mono">{t ? `${t.smolder_velocity_mm_s.toFixed(1)} mm/s` : "—"}</dd></div>
          <div><dt>Probe spacing</dt><dd className="mono">0 / 30 / 60 mm</dd></div>
        </dl>
      )}
      <p className="footnote">
        {camFresh
          ? `${cam.sensor ?? "MLX90640"} ${W}×${H} frame${measured ? " from the camera" : " from the simulator, not a measurement"}. The detector still uses max_ir_temp_c from the telemetry.`
          : cam
            ? `Camera frame is ${camAge != null ? camAge.toFixed(0) : "?"} s old, so this shows the schematic estimate from three thermocouples and the IR maximum.`
            : "Schematic estimate interpolated from three thermocouples and the IR maximum. In live mode a measured MLX90640 frame replaces it as soon as the camera sends one."}
      </p>
    </section>
  );
}
