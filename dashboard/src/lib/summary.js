// Plain-language summary of the latest frames for the annunciator band.
// Deterministic and built only from the frame data: it never waits for, or depends on, the LLM.
// It restates the Stage-1 result; it never decides a status of its own.

const ALARM = "SMOLDERING_ANOMALY";
const CO_TRIGGER = 25; // demo trigger, same as the backend detector

export function prettyCompartment(id) {
  if (!id) return "this compartment";
  return id.split("_").filter(Boolean)
    .map((w) => (/\d/.test(w) ? w : w[0] + w.slice(1).toLowerCase()))
    .join(" ");
}

const n = (v, d = 1) => Number(v).toFixed(d);

/** Rate of change per second over the last `ms`, or null with too little history. */
function ratePerS(frames, pick, ms = 2000) {
  const last = frames[frames.length - 1];
  for (let i = frames.length - 2; i >= 0; i--) {
    const dt = last.timestamp_ms - frames[i].timestamp_ms;
    if (dt >= ms) return (pick(last) - pick(frames[i])) / (dt / 1000);
  }
  return null;
}

/** Seconds since the current status run began. */
function secondsInStatus(frames) {
  const last = frames[frames.length - 1];
  let i = frames.length - 1;
  while (i > 0 && frames[i - 1].stage1_detector.status === last.stage1_detector.status) i--;
  return (last.timestamp_ms - frames[i].timestamp_ms) / 1000;
}

/**
 * @returns {{ tone: "idle"|"ok"|"alarm", headline: string, details: string[] }}
 */
export function summarize(frames, { stale = false, ageS = null } = {}) {
  const f = frames[frames.length - 1];
  if (!f) return { tone: "idle", headline: "Waiting for the first sensor sample.", details: [] };

  const t = f.telemetry, d = f.stage1_detector;
  const where = prettyCompartment(f.compartment_id);
  const probes = t.thermocouples_c;
  const hot = probes.indexOf(Math.max(...probes));
  const startProbe = frames[0].telemetry.thermocouples_c[hot];
  const heatRise = probes[hot] - startProbe;
  const coRate = ratePerS(frames, (x) => x.telemetry.co_ppm);
  const coRising = coRate != null && coRate > 0.05;
  const details = [];
  if (stale) details.push(`No new data for ${ageS != null ? Math.round(ageS) : "several"} s, so this describes the last sample received.`);

  if (d.status === ALARM) {
    const since = secondsInStatus(frames);
    details.push(
      `CO is ${n(t.co_ppm)} ppm${coRising ? ` and rising ${n(coRate, 1)} ppm/s` : ""}, ` +
      `above the ${CO_TRIGGER} ppm demo trigger${since >= 1 ? ` for ${Math.round(since)} s` : ""}.`,
    );
    details.push(
      `Heat is concentrated near probe P${hot + 1} at ${n(probes[hot])} °C` +
      `${heatRise >= 1 ? `, up ${n(heatRise)} °C since the session started` : ""}; the IR maximum is ${n(t.max_ir_temp_c)} °C.`,
    );
    if (t.smolder_velocity_mm_s > 0) details.push(`The hotspot is moving about ${n(t.smolder_velocity_mm_s)} mm/s.`);
    details.push("Follow the vehicle's approved checklist.");
    return { tone: "alarm", headline: `Possible smoldering fire in ${where}.`, details };
  }

  if (d.false_alarm_dust_check !== "NOMINAL_NO_EVENT") {
    details.push(
      `A PM2.5 spike of ${n(t.pm25_ug_m3)} µg/m³ was rejected as dust, because CO, VOC and temperatures are not rising together with it.`,
    );
    details.push(`CO is ${n(t.co_ppm)} ppm and the warmest probe is P${hot + 1} at ${n(probes[hot])} °C.`);
    return { tone: "ok", headline: `No fire detected in ${where}.`, details };
  }

  details.push(
    `CO ${n(t.co_ppm)} ppm${coRising ? ` (rising ${n(coRate, 1)} ppm/s)` : ""}, warmest probe P${hot + 1} at ${n(probes[hot])} °C, ` +
    `PM2.5 ${n(t.pm25_ug_m3)} µg/m³. No dust event.`,
  );
  if (t.co_ppm > CO_TRIGGER) details.push(`CO is above the ${CO_TRIGGER} ppm demo trigger but not rising, so the detector holds NOMINAL.`);
  return { tone: "ok", headline: `No fire detected in ${where}.`, details };
}
