import { useEffect, useState } from "react";
import Chart from "./components/Chart.jsx";
import Heatmap from "./components/Heatmap.jsx";
import RiskPanel from "./components/RiskPanel.jsx";
import GemmaPanel from "./components/GemmaPanel.jsx";
import MasterStatus from "./components/MasterStatus.jsx";
import { SCENARIOS, STALE_AFTER_MS, useSource } from "./lib/useSource.js";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const SCENARIO_NOTE = {
  nominal: "Synthetic nominal baseline (stub mode LIVE_IOT).",
  dust: "Synthetic PM2.5 spike 80–150 µg/m³ with nominal gas/thermal.",
  smoldering: "Synthetic CO 5→125 ppm ramp over 60 s; trips at ~10.5 s.",
  airgapped: "Synthetic nominal frames through the NASA_REPLAY interface, not NASA measurements.",
};

const isAlarm = (f) => f?.stage1_detector.status === "SMOLDERING_ANOMALY";
const isDust = (f) => f?.stage1_detector.false_alarm_dust_check !== "NOMINAL_NO_EVENT";

// Channel table: one place for labels, units, precision and the scale floor that keeps sensor noise flat.
const CHANNELS = [
  { key: "co", label: "CO", unit: "ppm", digits: 1, minSpan: 10, threshold: 25, thresholdLabel: "trigger 25 ppm",
    pick: (f) => f.telemetry.co_ppm, state: (v, f) => (isAlarm(f) ? "alarm" : v > 25 ? "caution" : "ok") },
  { key: "co2", label: "CO₂", unit: "ppm", digits: 0, minSpan: 100, pick: (f) => f.telemetry.co2_ppm },
  { key: "voc", label: "VOC index", unit: "", digits: 0, minSpan: 20, pick: (f) => f.telemetry.voc_index },
  { key: "ch4", label: "CH₄", unit: "ppm", digits: 2, minSpan: 1, pick: (f) => f.telemetry.ch4_ppm },
  { key: "pm", label: "PM2.5", unit: "µg/m³", digits: 1, minSpan: 20, pick: (f) => f.telemetry.pm25_ug_m3,
    state: (v, f) => (isDust(f) ? "caution" : "ok"), stateText: (f) => (isDust(f) ? "dust rejected" : null) },
  { key: "air", label: "Airflow", unit: "cm/s", digits: 1, minSpan: 5, pick: (f) => f.telemetry.airflow_cm_s },
  { key: "ir", label: "Max IR", unit: "°C", digits: 1, minSpan: 10, pick: (f) => f.telemetry.max_ir_temp_c },
  { key: "probe", label: "Probe peak", unit: "°C", digits: 1, minSpan: 10, pick: (f) => Math.max(...f.telemetry.thermocouples_c) },
];

const pad = (n) => String(n).padStart(2, "0");
function met(ms) {
  if (ms == null) return "--:--.-";
  const s = ms / 1000;
  return `${pad(Math.floor(s / 60))}:${pad(Math.floor(s % 60))}.${Math.floor((s * 10) % 10)}`;
}

export default function App() {
  const [source, setSource] = useState("replay");
  const [scenario, setScenario] = useState("airgapped"); // Mode 2 (NASA_REPLAY) is the primary demo
  const { frames, state, error, lastRxAt, restart } = useSource({ source, scenario, apiBase: API_BASE });
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const i = setInterval(() => setNow(Date.now()), 250); return () => clearInterval(i); }, []);

  const frame = frames[frames.length - 1];
  const ageMs = lastRxAt ? Math.max(0, now - lastRxAt) : null;
  const stale = source === "live" && (state === "disconnected" || state === "error" || (ageMs != null && ageMs > STALE_AFTER_MS));
  const alarm = isAlarm(frame);
  const clock = new Date(now);

  const link = stale ? { cls: "caution", text: "Stale" }
    : state === "live" ? { cls: "ok", text: "Live" }
    : state === "playing" ? { cls: "info", text: "Replay" }
    : state === "finished" ? { cls: "muted", text: "Replay ended" }
    : state === "error" ? { cls: "alarm", text: "Error" }
    : { cls: "muted", text: "Connecting" };

  return (
    <div className={`app${alarm ? " is-alarm" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <svg viewBox="0 0 32 32" className="mark" aria-hidden="true">
            <circle cx="16" cy="16" r="13" />
            <path d="M16 6 L23 24 L16 20 L9 24 Z" />
          </svg>
          <div>
            <div className="brand-name">AETHER-G</div>
            <div className="brand-sub">Fire &amp; hazard monitor</div>
          </div>
        </div>

        <div className="where">
          <span className="eyebrow">Compartment</span>
          <span className="where-name">{frame?.compartment_id?.replaceAll("_", " ") ?? "No compartment"}</span>
        </div>

        <dl className="readouts">
          <div><dt>Mode</dt><dd><span className={`chip ${frame?.mode === "NASA_REPLAY" ? "info" : "muted"}`}>{frame?.mode ?? "—"}</span></dd></div>
          <div><dt>Link</dt><dd><span className={`chip dot ${link.cls}`}>{link.text}</span></dd></div>
          <div><dt>Session T+</dt><dd className="mono">{met(frame?.timestamp_ms)}</dd></div>
          <div><dt>Last sample</dt><dd className={`mono${stale ? " t-caution" : ""}`}>{ageMs != null ? `${(ageMs / 1000).toFixed(1)} s` : "—"}</dd></div>
          <div><dt>Local</dt><dd className="mono">{pad(clock.getHours())}:{pad(clock.getMinutes())}:{pad(clock.getSeconds())}</dd></div>
        </dl>

        <div className="ops" role="group" aria-label="Data source">
          <label htmlFor="src">Source</label>
          <select id="src" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="replay">Captured replay</option>
            <option value="live">Live backend</option>
          </select>
          {source === "replay" && (
            <>
              <label htmlFor="scn">Scenario</label>
              <select id="scn" value={scenario} onChange={(e) => setScenario(e.target.value)}>
                {SCENARIOS.map((s) => <option key={s}>{s}</option>)}
              </select>
            </>
          )}
          <button type="button" onClick={restart}>Restart session</button>
        </div>
      </header>

      {(stale || error) && (
        <div className="banner caution" role="alert">
          {stale && <b>Data stale: no sample for {ageMs != null ? (ageMs / 1000).toFixed(0) : "?"} s. The status below is the last one received.</b>}
          {error && <span>Source error: {error}</span>}
        </div>
      )}

      <MasterStatus frames={frames} stale={stale} ageS={ageMs != null ? ageMs / 1000 : null} />

      <main className="layout">
        <div className="area-advisory"><GemmaPanel advisory={null} stage1Alarm={alarm} /></div>

        <section className="panel area-trends" aria-labelledby="trends-h">
          <div className="panel-head">
            <h2 id="trends-h">Sensor trends</h2>
            <span className="panel-meta">last 60 s · {frame ? `${frames.length} samples` : "waiting"}</span>
          </div>
          <div className="tiles">
            {CHANNELS.map((c) => <Chart key={c.key} channel={c} frames={frames} />)}
          </div>
          <p className="footnote">
            {source === "replay"
              ? `${SCENARIO_NOTE[scenario]} Frames are captured from simulator/simulator.py with fixed seeds; the mode badge is read from the frame, never relabeled.`
              : `Polling ${API_BASE}/status every 500 ms.`}
          </p>
        </section>

        <div className="area-thermal"><Heatmap frame={frame} /></div>
        <div className="area-evidence"><RiskPanel frame={frame} /></div>
      </main>
    </div>
  );
}
