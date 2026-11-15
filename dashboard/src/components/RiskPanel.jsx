const DUST_TEXT = {
  NOMINAL_NO_EVENT: "No dust-only event rejected.",
  DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE:
    "Particulate spike rejected as dust: PM2.5 is high but there is no sustained CO/CO2/VOC or thermal rise.",
};

export default function RiskPanel({ frame }) {
  const d = frame?.stage1_detector, t = frame?.telemetry;
  const alarm = d?.status === "SMOLDERING_ANOMALY";
  return (
    <section className="panel">
      <h2>Stage-1 status &amp; risk evidence</h2>
      {d ? (
        <>
          <div className={`status ${alarm ? "alarm" : "ok"}`} role="status">{d.status}</div>
          <p className="muted">Demo score {d.confidence_score.toFixed(2)} — illustrative, not a calibrated probability.</p>
          <p><b>Dust check:</b> {DUST_TEXT[d.false_alarm_dust_check]}</p>
          <ul className="evidence">
            <li className={t.co_ppm > 25 ? "hit" : ""}>CO {t.co_ppm.toFixed(1)} ppm (demo trigger &gt; 25 ppm with rising CO)</li>
            <li>CO2 {t.co2_ppm.toFixed(0)} ppm · VOC index {t.voc_index}</li>
            <li className={t.pm25_ug_m3 > 50 ? "hit" : ""}>PM2.5 {t.pm25_ug_m3.toFixed(1)} µg/m³</li>
            <li>Hotspot motion {t.smolder_velocity_mm_s.toFixed(1)} mm/s</li>
          </ul>
        </>
      ) : <p className="muted">Waiting for frames…</p>}
      <h3>References</h3>
      <ul className="refs">
        <li><a href="https://psi.nasa.gov/" target="_blank" rel="noreferrer">NASA Physical Sciences Informatics (PSI)</a> — Saffire / BASS data portal</li>
        <li><a href="https://ntrs.nasa.gov/citations/20240002981" target="_blank" rel="noreferrer">NTRS 20240002981</a> — Saffire VI preliminary report</li>
      </ul>
      <p className="muted small">The 25 ppm threshold is a synthetic demo threshold, not a NASA measurement or validated safety limit. Excerpt-level citations arrive with the RAG corpus (#6, #17).</p>
    </section>
  );
}
