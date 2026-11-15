// Thermal camera frames (MLX90640 32x24) from GET {apiBase}/thermal. Pure helpers, no React.

export const THERMAL_FRESH_S = 3; // older than this, the panel falls back to the schematic estimate

/** Validates the frame shape the backend promises; returns a list of problems. */
export function validateThermal(f) {
  if (!f || typeof f !== "object") return ["not an object"];
  const e = [];
  if (!Number.isInteger(f.width) || !Number.isInteger(f.height) || f.width < 1 || f.height < 1) e.push("bad width/height");
  else if (!Array.isArray(f.pixels_c) || f.pixels_c.length !== f.width * f.height) e.push("pixels_c length does not match width*height");
  else if (!f.pixels_c.every((v) => typeof v === "number" && Number.isFinite(v))) e.push("pixels_c must be finite numbers");
  return e;
}

/** min / max / hottest pixel of a frame. */
export function thermalStats(f) {
  let min = Infinity, max = -Infinity, hot = 0;
  f.pixels_c.forEach((v, i) => {
    if (v < min) min = v;
    if (v > max) { max = v; hot = i; }
  });
  return { min, max, hotspot: { x: hot % f.width, y: Math.floor(hot / f.width), temp_c: max } };
}

/** Age in seconds now: the server's age at fetch time plus the time since we fetched it. */
export function thermalAgeS(f, fetchedAt, now) {
  if (!f || fetchedAt == null) return null;
  return (f.age_s ?? 0) + Math.max(0, now - fetchedAt) / 1000;
}

export const isMeasuredSource = (source) => source === "jetson";
