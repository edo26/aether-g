# AETHER-G architecture

## Pipeline and ownership

```text
Input adapters
  Simulator JSONL / ESP32-S3 USB Serial       [LIVE_IOT]
  Local NASA-derived telemetry replay       [NASA_REPLAY]
       |
       v
FastAPI Stage-1 detector                     [Fachri]
  validate contract -> maintain per-bay history
  -> dCO/dt + ΔCO/ΔCO2 + VOC + thermal/hotspot evidence
  -> NOMINAL or SMOLDERING_ANOMALY; dust rejection is a separate field
       |                          |
       | immediate status         v
       |                 Gemma via local Ollama, temperature 0.0
       |                 + local Saffire/BASS RAG facts and citations
       |                          |
       +--------------------------+
                    |
                    v
Vite + React dashboard, four panels          [Edo]
  thermal heatmap | sensor graphs | Saffire/BASS risk matrix | Gemma chat
                    |
                    v
Visual crew advisory + three steps
  + offline TTS voice guidance (stretch)

Hardware: ESP32-S3 and calibrated sensors    [Heri]
Inference host: connected edge computer; all runtime assets stored locally
```

The simulator is the only executable component in this scaffold. It supplies illustrative Stage-1 results for early integration. FastAPI will recompute the authoritative result. The LLM explains that result; it cannot override it. The first implementation uses the CO trigger; ratio, VOC, and thermal-motion fusion are specified in [edge-ai](../edge-ai/README.md).

The public Vercel demo will use bundled examples. The full offline demo uses a local dashboard, backend, Ollama model, and reference facts provisioned in advance. An ESP32 acquires sensors; it does not host Gemma. TTS must also be local if included.

## JSON contract

The same envelope is used by the simulator, firmware adapter, replay adapter, detector, and dashboard. Preserve the following template exactly:

```json
{
  "timestamp_ms": 0,
  "mode": "LIVE_IOT_OR_NASA_REPLAY",
  "compartment_id": "AVIONICS_BAY_B2",
  "telemetry": {
    "co_ppm": 0.0, "co2_ppm": 0.0, "voc_index": 0, "ch4_ppm": 0.0,
    "pm25_ug_m3": 0.0, "airflow_cm_s": 0.0,
    "thermocouples_c": [0.0, 0.0, 0.0],
    "max_ir_temp_c": 0.0, "smolder_velocity_mm_s": 0.0
  },
  "stage1_detector": {
    "status": "NOMINAL",
    "confidence_score": 0.0,
    "false_alarm_dust_check": "NOMINAL_NO_EVENT"
  }
}
```

### Semantics

| Field | Type / units | Meaning |
| --- | --- | --- |
| `timestamp_ms` | Integer, ms | Elapsed time from input-session start; simulator starts at 0 and increments by `--interval-ms` |
| `mode` | String | `LIVE_IOT` or `NASA_REPLAY`; the template's `LIVE_IOT_OR_NASA_REPLAY` denotes these alternatives |
| `compartment_id` | String | `AVIONICS_BAY_B2` for the initial demo |
| `telemetry.co_ppm`, `co2_ppm`, `ch4_ppm` | Float, ppm | Gas concentrations |
| `telemetry.voc_index` | Integer | Relative VOC index, not a ppm measurement |
| `telemetry.pm25_ug_m3` | Float, µg/m³ | PM2.5 mass concentration |
| `telemetry.airflow_cm_s` | Float, cm/s | Local airflow speed |
| `telemetry.thermocouples_c` | Array of three floats, °C | Temperatures in fixed probe order |
| `telemetry.max_ir_temp_c` | Float, °C | Maximum IR temperature; the contract does not carry a full thermal image |
| `telemetry.smolder_velocity_mm_s` | Float, mm/s | Estimated hotspot progression speed; synthetic in the stub |
| `stage1_detector.status` | String | Only `NOMINAL` or `SMOLDERING_ANOMALY` |
| `stage1_detector.confidence_score` | Float, 0–1 | Illustrative detector confidence; not a calibrated probability |
| `stage1_detector.false_alarm_dust_check` | String | `NOMINAL_NO_EVENT` or `DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE` |

Dust rejection is never a third status. A dust-only event remains `NOMINAL`. `NOMINAL_NO_EVENT` describes the dust check, so it also appears when the overall status is `SMOLDERING_ANOMALY`.

Messages are UTF-8 JSONL, one object per line, with finite numeric values. The template's zero values illustrate types, not a valid sensor baseline or an unknown-value convention. Full thermal images, NASA provenance, and Gemma advisories belong in separately agreed transports/files; do not silently extend this envelope.

The simulator uses a logical clock with exact increments, sleeping for the chosen interval between emissions; scheduling may introduce wall-clock drift. Detector derivatives use sample timestamps. Reset detector history at session restart or source-mode change. Hardware validity and missing-channel behavior must be agreed before live ingestion.

## Four scenarios

| Scenario | Telemetry behavior | Expected Stage-1 behavior | Mode |
| --- | --- | --- | --- |
| `nominal` | CO 0–3 ppm; CO2 400–600 ppm; VOC 5–20; PM2.5 1–9 µg/m³; airflow 2–5 cm/s; probes 22–24 °C; IR 24–24.5 °C | `NOMINAL`, confidence `0.95`, `NOMINAL_NO_EVENT` | `LIVE_IOT` |
| `dust` | PM2.5 spikes to 80–150 µg/m³; gas and thermal channels stay nominal | `NOMINAL`, confidence `0.95`, `DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE` | `LIVE_IOT` |
| `smoldering` | CO ramps 5 → 125 ppm over 60 s; VOC and temperatures rise; a moving hotspot travels at 1 mm/s (within the 0.5–2 mm/s target range); IR maximum rises | Trigger `SMOLDERING_ANOMALY` when CO > 25 ppm and dCO/dt > 0; confidence about `0.6` → `0.95`; dust check `NOMINAL_NO_EVENT` | `LIVE_IOT` |
| `airgapped` | Nominal synthetic telemetry; no network calls | `NOMINAL`, confidence `0.95`, `NOMINAL_NO_EVENT` | `NASA_REPLAY` |

At 500 ms intervals, the smoldering trigger occurs at 10.5 seconds (CO 26 ppm). The anomaly remains latched after the 60-second plateau; restart the scenario to reset. A moving hotspot can cool an individual probe after passing it even as the overall thermal envelope rises.

`airgapped` currently proves that the synthetic replay interface runs offline, not that a NASA dataset or Gemma has been integrated. See [data provenance](../data/README.md) for the actual replay acquisition plan.

## Demo acceptance targets

- All four simulator commands emit the same contract with monotonic timestamps and no external dependencies.
- Dust produces the rejection marker without changing `NOMINAL`.
- Rising CO triggers smoldering; heat and VOC provide visible corroboration.
- Once the full stack exists, Wi-Fi-off operation retains all four panels and local guidance.
- Measure the complete Gemma warning plus three steps in under two seconds from the Stage-1 trigger on a warm model. This is a target, not a scaffold benchmark.
- Validate any claimed minutes-before-flame lead time against attributed experiments before reporting it as a result.
