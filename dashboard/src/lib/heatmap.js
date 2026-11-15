// Schematic 32x24 thermal estimate from three probes + IR max. NOT a measured image.
export const COLS = 32;
export const ROWS = 24;
const PROBE_X = [0.15, 0.5, 0.85]; // probes sit at 0/30/60 mm along the bay, drawn left to right

export function estimateHeatmap(thermocouples, maxIr) {
  const base = Math.min(...thermocouples);
  const peak = Math.max(maxIr, ...thermocouples);
  const grid = new Float32Array(COLS * ROWS);
  const hot = thermocouples.indexOf(Math.max(...thermocouples));
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const x = c / (COLS - 1);
      const y = r / (ROWS - 1);
      let v = base;
      thermocouples.forEach((t, i) => {
        const w = Math.exp(-(((x - PROBE_X[i]) / 0.16) ** 2 + ((y - 0.5) / 0.35) ** 2));
        v = Math.max(v, base + (t - base) * w);
      });
      // IR maximum is attributed to the hottest probe's neighbourhood
      const wi = Math.exp(-(((x - PROBE_X[hot]) / 0.12) ** 2 + ((y - 0.5) / 0.25) ** 2));
      v = Math.max(v, base + (peak - base) * wi);
      grid[r * COLS + c] = v;
    }
  }
  return { grid, min: base, max: peak, hotProbe: hot };
}
