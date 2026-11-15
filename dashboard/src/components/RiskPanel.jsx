const DUST_TEXT = {
  NOMINAL_NO_EVENT: "No dust-only event rejected.",
  DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE:
    "Particulate spike rejected as dust: PM2.5 is high but there is no sustained CO/CO2/VOC or thermal rise.",
};

function Row({ state, label, value }) {
  return (
    <li className={`ev s-${state}`}>
      <span className="ev-mark" aria-hidden="true" />
      <span className="ev-label">{label}</span>
      <span className="ev-value mono">{value}</span>
    </li>
  );
}

export default function RiskPanel({ frame }) {
  const d = frame?.stage1_detector, t = frame?.telemetry;
  const dust = d && d.false_alarm_dust_check !== "NOMINAL_NO_EVENT";
  return (
    <section className="panel" aria-labelledby="ev-h">
      <div className="panel-head">
        <h2 id="ev-h">Detector evidence</h2>
        <span className="panel-meta">Saffire / BASS context</span>
      </div>
      {d ? (
        <>
          <ul className="evidence">
            <Row state={t.co_ppm > 25 ? (d.status === "SMOLDERING_ANOMALY" ? "alarm" : "caution") : "ok"}
              label="CO above 25 ppm demo trigger, rising" value={`${t.co_ppm.toFixed(1)} ppm`} />
            <Row state="ok" label="CO₂ · VOC index" value={`${t.co2_ppm.toFixed(0)} ppm · ${t.voc_index}`} />
            <Row state={dust ? "caution" : "ok"} label="PM2.5" value={`${t.pm25_ug_m3.toFixed(1)} µg/m³`} />
            <Row state={t.smolder_velocity_mm_s > 0 ? "caution" : "ok"} label="Hotspot motion" value={`${t.smolder_velocity_mm_s.toFixed(1)} mm/s`} />
          </ul>
          <p className={`dust-note${dust ? " is-dust" : ""}`}><b>Dust check.</b> {DUST_TEXT[d.false_alarm_dust_check]}</p>
        </>
      ) : <p className="empty">Waiting for frames…</p>}
      <div className="refs">
        <span className="eyebrow">References</span>
        <a href="https://psi.nasa.gov/" target="_blank" rel="noreferrer">NASA Physical Sciences Informatics (PSI)</a>
        <a href="https://ntrs.nasa.gov/citations/20240002981" target="_blank" rel="noreferrer">NTRS 20240002981 · Saffire VI preliminary report</a>
      </div>
      <p className="footnote">The 25 ppm threshold is a synthetic demo threshold, not a NASA measurement or validated safety limit.</p>
    </section>
  );
}
