import { useEffect, useRef } from "react";
import { COLS, ROWS, estimateHeatmap } from "../lib/heatmap.js";

const ramp = (u) => {
  const c = Math.max(0, Math.min(1, u));
  const hue = 240 - 240 * c; // blue → red
  return `hsl(${hue} 85% ${25 + 30 * c}%)`;
};

export default function Heatmap({ frame }) {
  const ref = useRef(null);
  const t = frame?.telemetry;
  useEffect(() => {
    const cv = ref.current;
    if (!cv || !t) return;
    const { grid, min, max } = estimateHeatmap(t.thermocouples_c, t.max_ir_temp_c);
    const ctx = cv.getContext("2d");
    const cw = cv.width / COLS, ch = cv.height / ROWS;
    const span = Math.max(max - min, 20); // fixed floor so nominal noise stays cool
    for (let r = 0; r < ROWS; r++)
      for (let c = 0; c < COLS; c++) {
        ctx.fillStyle = ramp((grid[r * COLS + c] - min) / span);
        ctx.fillRect(c * cw, r * ch, cw + 0.5, ch + 0.5);
      }
  }, [t]);
  return (
    <section className="panel">
      <h2>Thermal heatmap <span className="tag warn">SCHEMATIC ESTIMATE · not a measured image</span></h2>
      <canvas ref={ref} width={320} height={240} className="heat" aria-label="32 by 24 schematic thermal estimate" />
      {t ? (
        <dl className="kv">
          <dt>Probes (0 / 30 / 60 mm)</dt><dd>{t.thermocouples_c.map((v) => `${v.toFixed(1)}°C`).join(" · ")}</dd>
          <dt>IR maximum</dt><dd>{t.max_ir_temp_c.toFixed(1)} °C</dd>
          <dt>Hottest probe</dt><dd>#{t.thermocouples_c.indexOf(Math.max(...t.thermocouples_c)) + 1}</dd>
          <dt>Hotspot motion</dt><dd>{t.smolder_velocity_mm_s.toFixed(1)} mm/s</dd>
        </dl>
      ) : <p className="muted">Waiting for frames…</p>}
      <p className="muted small">32×24 grid interpolated from three thermocouples and the IR maximum. A measured MLX90640 frame needs a separately agreed transport.</p>
    </section>
  );
}
