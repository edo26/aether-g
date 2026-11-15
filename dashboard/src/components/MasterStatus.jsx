// Annunciator band: the Stage-1 result, readable from across the cabin. Never driven by the LLM.
const DUST_SHORT = {
  NOMINAL_NO_EVENT: "No dust event",
  DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE: "Dust spike rejected",
};

function timeInState(frames) {
  const last = frames[frames.length - 1];
  if (!last) return null;
  let i = frames.length - 1;
  while (i > 0 && frames[i - 1].stage1_detector.status === last.stage1_detector.status) i--;
  return (last.timestamp_ms - frames[i].timestamp_ms) / 1000;
}

function coSlope(frames) {
  const last = frames[frames.length - 1];
  if (!last) return null;
  const ref = [...frames].reverse().find((f) => last.timestamp_ms - f.timestamp_ms >= 2000);
  if (!ref) return null;
  return (last.telemetry.co_ppm - ref.telemetry.co_ppm) / ((last.timestamp_ms - ref.timestamp_ms) / 1000);
}

export default function MasterStatus({ frames, stale }) {
  const f = frames[frames.length - 1];
  const d = f?.stage1_detector;
  const alarm = d?.status === "SMOLDERING_ANOMALY";
  const dust = d && d.false_alarm_dust_check !== "NOMINAL_NO_EVENT";
  const tis = timeInState(frames);
  const slope = coSlope(frames);
  const cls = !d ? "idle" : alarm ? "alarm" : "ok"; // a dust rejection stays NOMINAL; only its chip turns amber

  return (
    <section className={`master ${cls}${stale ? " is-stale" : ""}`} role="status" aria-live="assertive">
      <div className="master-state">
        <span className="lamp" aria-hidden="true" />
        <div>
          <div className="eyebrow">Stage-1 detector · rule-based</div>
          <div className="master-word">{d ? (alarm ? "Smoldering anomaly" : "Nominal") : "Awaiting data"}</div>
        </div>
      </div>

      <dl className="master-facts">
        <div>
          <dt>Demo score</dt>
          <dd>
            <span className="mono big">{d ? d.confidence_score.toFixed(2) : "—"}</span>
            <span className="meter" aria-hidden="true"><i style={{ width: `${(d?.confidence_score ?? 0) * 100}%` }} /></span>
            <span className="hint">illustrative, not a probability</span>
          </dd>
        </div>
        <div>
          <dt>CO now</dt>
          <dd>
            <span className="mono big">{f ? f.telemetry.co_ppm.toFixed(1) : "—"}</span><span className="unit">ppm</span>
            <span className={`hint mono${slope != null && slope > 0.2 ? " t-rise" : ""}`}>
              {slope == null ? "trend —" : `${slope >= 0 ? "▲" : "▼"} ${Math.abs(slope).toFixed(2)} ppm/s`}
            </span>
          </dd>
        </div>
        <div>
          <dt>Dust check</dt>
          <dd><span className={`chip ${dust ? "caution" : "muted"}`}>{d ? DUST_SHORT[d.false_alarm_dust_check] : "—"}</span></dd>
        </div>
        <div>
          <dt>In this state</dt>
          <dd><span className="mono big">{tis != null ? tis.toFixed(1) : "—"}</span><span className="unit">s</span></dd>
        </div>
      </dl>
    </section>
  );
}
