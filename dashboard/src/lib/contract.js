// Strict validator for the shared telemetry envelope (docs/architecture.md#json-contract).
export const MODES = ["LIVE_IOT", "NASA_REPLAY"];
export const STATUSES = ["NOMINAL", "SMOLDERING_ANOMALY"];
export const DUST = ["NOMINAL_NO_EVENT", "DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE"];

const TOP = ["timestamp_ms", "mode", "compartment_id", "telemetry", "stage1_detector"];
const TEL = ["co_ppm", "co2_ppm", "voc_index", "ch4_ppm", "pm25_ug_m3", "airflow_cm_s",
  "thermocouples_c", "max_ir_temp_c", "smolder_velocity_mm_s"];
const DET = ["status", "confidence_score", "false_alarm_dust_check"];

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const fin = (v) => typeof v === "number" && Number.isFinite(v);
const sameKeys = (o, keys) => Object.keys(o).length === keys.length && keys.every((k) => k in o);

/** Returns a list of problems; empty means the frame matches the contract. */
export function validateFrame(f) {
  if (!isObj(f) || !sameKeys(f, TOP)) return ["envelope keys do not match contract"];
  const e = [];
  if (!Number.isInteger(f.timestamp_ms) || f.timestamp_ms < 0) e.push("timestamp_ms must be a non-negative integer");
  if (!MODES.includes(f.mode)) e.push(`invalid mode ${f.mode}`);
  if (typeof f.compartment_id !== "string" || !f.compartment_id) e.push("compartment_id missing");
  const t = f.telemetry;
  if (!isObj(t) || !sameKeys(t, TEL)) e.push("telemetry keys do not match contract");
  else {
    for (const k of TEL) {
      if (k === "thermocouples_c") continue;
      if (!fin(t[k])) e.push(`telemetry.${k} must be a finite number`);
    }
    if (!Number.isInteger(t.voc_index)) e.push("voc_index must be an integer");
    if (!Array.isArray(t.thermocouples_c) || t.thermocouples_c.length !== 3 || !t.thermocouples_c.every(fin))
      e.push("thermocouples_c must hold exactly three finite numbers");
  }
  const d = f.stage1_detector;
  if (!isObj(d) || !sameKeys(d, DET)) e.push("stage1_detector keys do not match contract");
  else {
    if (!STATUSES.includes(d.status)) e.push(`invalid status ${d.status}`);
    if (!fin(d.confidence_score) || d.confidence_score < 0 || d.confidence_score > 1) e.push("confidence_score must be within 0-1");
    if (!DUST.includes(d.false_alarm_dust_check)) e.push("invalid false_alarm_dust_check");
  }
  return e;
}

/** Parse JSONL text into validated frames; throws with the first offending line. */
export function parseJsonl(text) {
  const frames = [];
  text.split("\n").forEach((line, i) => {
    if (!line.trim()) return;
    const frame = JSON.parse(line);
    const problems = validateFrame(frame);
    if (problems.length) throw new Error(`line ${i + 1}: ${problems.join("; ")}`);
    frames.push(frame);
  });
  return frames;
}
