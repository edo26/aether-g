import { useEffect, useState } from "react";
import Chart from "./components/Chart.jsx";
import Heatmap from "./components/Heatmap.jsx";
import RiskPanel from "./components/RiskPanel.jsx";
import GemmaPanel from "./components/GemmaPanel.jsx";
import { SCENARIOS, STALE_AFTER_MS, useSource } from "./lib/useSource.js";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const SCENARIO_NOTE = {
  nominal: "Synthetic nominal baseline (stub mode LIVE_IOT).",
  dust: "Synthetic PM2.5 spike 80–150 µg/m³ with nominal gas/thermal.",
  smoldering: "Synthetic CO 5→125 ppm ramp over 60 s; trips at ~10.5 s.",
  airgapped: "Synthetic nominal frames through the NASA_REPLAY interface — not NASA measurements.",
};

export default function App() {
  const [source, setSource] = useState("replay");
  const [scenario, setScenario] = useState("airgapped"); // Mode 2 (NASA_REPLAY) is the primary demo
  const { frames, state, error, lastRxAt, restart } = useSource({ source, scenario, apiBase: API_BASE });
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const i = setInterval(() => setNow(Date.now()), 250); return () => clearInterval(i); }, []);

  const frame = frames[frames.length - 1];
  const ageMs = lastRxAt ? Math.max(0, now - lastRxAt) : null;
  const stale = source === "live" && (state === "disconnected" || state === "error" || (ageMs != null && ageMs > STALE_AFTER_MS));
  const alarm = frame?.stage1_detector.status === "SMOLDERING_ANOMALY";

  return (
    <main className={alarm ? "app alarm-bg" : "app"}>
      <header>
        <h1>AETHER-G <small>edge thermal &amp; hazard responder</small></h1>
        <div className="controls">
          <label>Source
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="replay">Captured replay (public demo)</option>
              <option value="live">Live backend ({API_BASE})</option>
            </select>
          </label>
          {source === "replay" && (
            <label>Scenario
              <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
                {SCENARIOS.map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
          )}
          <button onClick={restart}>Restart session</button>
        </div>
      </header>

      <div className="strip" role="status">
        <span className={`tag ${frame?.mode === "NASA_REPLAY" ? "info" : "warn"}`}>{frame?.mode ?? "—"}</span>
        <span>Compartment <b>{frame?.compartment_id ?? "—"}</b></span>
        <span>Elapsed <b>{frame ? (frame.timestamp_ms / 1000).toFixed(1) : "—"} s</b></span>
        <span className={stale ? "stale" : ""}>Last sample <b>{ageMs != null ? `${(ageMs / 1000).toFixed(1)} s ago` : "—"}</b></span>
        <span className="muted">{state}{state === "finished" ? " (replay ended)" : ""}</span>
        {stale && <span className="stale">STALE / DISCONNECTED</span>}
      </div>
      {source === "replay" && <p className="muted small">{SCENARIO_NOTE[scenario]} Frames are captured from simulator/simulator.py with fixed seeds; the mode badge is read from the frame, never relabeled.</p>}
      {error && <p className="err">Source error: {error}</p>}

      <div className="grid">
        <Heatmap frame={frame} />
        <section className="panel wide">
          <h2>Real-time sensor graphs</h2>
          <div className="charts">
            <Chart title="CO" unit="ppm" frames={frames} pick={(f) => f.telemetry.co_ppm} color="#ff6b6b" threshold={25} />
            <Chart title="CO2" unit="ppm" frames={frames} pick={(f) => f.telemetry.co2_ppm} />
            <Chart title="VOC index" unit="" frames={frames} pick={(f) => f.telemetry.voc_index} color="#f4a261" />
            <Chart title="CH4" unit="ppm" frames={frames} pick={(f) => f.telemetry.ch4_ppm} color="#b5a8ff" />
            <Chart title="PM2.5" unit="µg/m³" frames={frames} pick={(f) => f.telemetry.pm25_ug_m3} color="#ffd166" />
            <Chart title="Airflow" unit="cm/s" frames={frames} pick={(f) => f.telemetry.airflow_cm_s} color="#80ed99" />
            <Chart title="Max IR" unit="°C" frames={frames} pick={(f) => f.telemetry.max_ir_temp_c} color="#ff9f1c" />
            <Chart title="Probe peak" unit="°C" frames={frames} pick={(f) => Math.max(...f.telemetry.thermocouples_c)} color="#ef476f" />
          </div>
        </section>
        <RiskPanel frame={frame} />
        <GemmaPanel advisory={null} stage1Alarm={alarm} />
      </div>
    </main>
  );
}
