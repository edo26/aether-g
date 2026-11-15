# AETHER-G backend API (for the dashboard)

Everything the dashboard needs comes from this one service. Do not talk to InfluxDB or SQLite from the browser.

- Base URL: `https://aether-be.fakhrads.dev/api/v1` (local: `http://localhost:8090/api/v1`)
- Interactive docs with the same schemas: `/docs` (OpenAPI at `/openapi.json`)
- CORS is open (`*`). No auth on read endpoints; only `POST /ingest` (Jetson) needs the `X-Device-Token` header.
- All timestamps are ISO-8601 UTC. `timestamp_ms` inside samples is the *source's* elapsed session clock, not wall time.

## Rules the UI must respect

1. **Fire status comes only from `stage1_detector.status`** (`NOMINAL` or `SMOLDERING_ANOMALY`, nothing else). The LLM text never changes it. A dust event is still `NOMINAL`; show `false_alarm_dust_check` as an explanation.
2. `confidence_score` is an illustrative demo score, not a probability.
3. Show `source` (`jetson` / `simulator` / `replay`) so synthetic data is never presented as real. `mode: NASA_REPLAY` alone does not mean the data is from NASA.
4. Advisory steps are **demo guidance, not an official NASA checklist**. Every advisory response carries a `disclaimer` string; show it. Citations support the science context only.
5. `fallback: true` on an advisory means the text is deterministic (the LLM was unavailable, too slow, malformed, or the trigger is a dust event). It is still valid guidance; label it "rule-based".

## Endpoints

### `GET /status`: latest state per compartment

```json
{"latest": {"timestamp_ms": 40000, "mode": "LIVE_IOT", "compartment_id": "AVIONICS_BAY_B2", "telemetry": {"...": "as below"}, "stage1_detector": {"...": "as below"}},
 "compartments": [{
  "compartment_id": "AVIONICS_BAY_B2", "timestamp_ms": 40000, "mode": "LIVE_IOT", "source": "simulator",
  "telemetry": {"co_ppm": 85.0, "co2_ppm": 566.67, "voc_index": 140, "ch4_ppm": 0.47, "pm25_ug_m3": 27.33,
                "airflow_cm_s": 4.3, "thermocouples_c": [31.37, 46.58, 38.36], "max_ir_temp_c": 57.33,
                "smolder_velocity_mm_s": 1.0},
  "stage1_detector": {"status": "SMOLDERING_ANOMALY", "confidence_score": 0.81, "false_alarm_dust_check": "NOMINAL_NO_EVENT"},
  "features": {"dco_dt": 2.0, "ratio": 1.1999, "voc_delta": 113.0, "pm_delta": 22.015, "temp_delta": 31.455},
  "received_at": "2026-11-15T03:51:34.091855+00:00", "last_sample_age_s": 14.18,
  "latest_advisory": { "...": "an advisory object, see /advisories, or null" }
}]}
```
`latest` (next to `compartments`) is the most recently received sample as a bare contract frame: exactly the five envelope keys with the backend's own `stage1_detector`, or `null` before any data. The Vite dashboard in `dashboard/` polls this; it validates frames strictly, so `latest` never carries extra keys.

`last_sample_age_s` is the stale-data indicator (the Jetson posts every 0.5 s; treat more than ~3 s as stale). `features` are supporting evidence (`ratio` is `null` when the CO2 rise is below the 20 ppm noise floor).

### `GET /telemetry?compartment_id=AVIONICS_BAY_B2&range=5m&every=2s`: chart series

`range`: `90s`, `5m`, `1h`, `2d` (max `7d`). `every`: `500ms`, `2s`, `1m` (min 500 ms; `range/every` at most 5000 points). Values are window means; `anomaly`, `dust_rejected`, `confidence` are window maxima.

```json
{"compartment_id": "AVIONICS_BAY_B2", "range": "5m", "every": "2s", "points": [
  {"time": "2026-11-15T03:51:28+00:00", "co_ppm": 26.5, "co2_ppm": 517.4, "voc_index": 53.1, "ch4_ppm": 0.3,
   "pm25_ug_m3": 10.2, "airflow_cm_s": 3.1, "tc1_c": 28.4, "tc2_c": 27.1, "tc3_c": 25.0,
   "max_ir_temp_c": 32.8, "smolder_velocity_mm_s": 1.0, "anomaly": 1.0, "dust_rejected": 0.0, "confidence": 0.604}]}
```
(The shape is the real response; the numbers in this example are illustrative. The `/status`, `/advisories`, `/chat`, `/events` and SSE examples were captured from a run against the simulator; the `omlx` block in `/health` is a shape example because no oMLX was reachable during development.)

Errors: `422` bad range/every; `503` the time-series store is down (live data on `/status` and `/stream` keeps working).

### `GET /advisories?compartment_id=&limit=20`: advisory history, newest first

```json
{"disclaimer": "Operational steps are demo guidance, not an official NASA checklist. ...",
 "advisories": [{
  "id": 4, "created_at": "2026-11-15T03:51:26.919+00:00", "compartment_id": "AVIONICS_BAY_B2",
  "trigger": "status_change", "status": "SMOLDERING_ANOMALY", "confidence": 0.603,
  "warning": "Smoldering anomaly in AVIONICS_BAY_B2: CO is 28 ppm and rising (+2.00 ppm/s), with increasing VOC and localized heat.",
  "steps": ["Alert the crew and confirm the affected compartment and live readings.",
            "Open the vehicle's approved electrical/fire response checklist and follow its isolation instructions.",
            "Track gas and temperature trends and report whether the anomaly persists after checklist actions."],
  "citations": [{"id": "NTRS-20240002981", "title": "Preliminary Results from the Saffire VI Experiment (NASA NTRS)",
                 "source": "NASA NTRS 20240002981, abstract (published 2024-03-08)",
                 "url": "https://ntrs.nasa.gov/citations/20240002981", "doi": null, "license": "NTRS distribution: PUBLIC"}],
  "facts_used": ["NTRS-20240002981"], "raw_text": "WARNING: ...\n1. ...\nSOURCES: NTRS-20240002981",
  "model": "deterministic", "latency_ms": 328, "first_token_ms": null, "fallback": true,
  "window_summary": {"window_s": 60, "samples": 24, "source": "influxdb", "channels": {"co_ppm": {"min": 5.0, "max": 28.0, "last": 28.0, "slope_per_s": 2.0}}}
}]}
```
`trigger`: `status_change` (alarm onset), `periodic` (every 30 s while the alarm persists), `dust_rejected`, `ask`. For `ask`, `warning` holds the answer and `steps` is `[]`. `model` is `deterministic` or the LLM model id; `first_token_ms` / `latency_ms` are measured from the trigger.

### `POST /ask`: ask about a compartment

```json
// request                                                         // 202 response
{"compartment_id": "AVIONICS_BAY_B2", "question": "Why did the alarm trigger?"}   {"run_id": "3429dc35932d", "compartment_id": "AVIONICS_BAY_B2", "question": "Why did the alarm trigger?"}
```
The answer is not in the response: it streams on `/stream` as `advisory_start` / `advisory_token` / `advisory_done` with the same `run_id`, and is saved to `/chat` and `/advisories`. Errors: `404` no data yet for that compartment, `409` the advisor is busy with an alarm advisory (retry in a few seconds), `422` empty question or over 500 characters. While the LLM is unavailable the answer is the rule-based status and says so.

### `GET /chat?compartment_id=&limit=50`: saved Q&A, newest first

```json
{"messages": [
  {"id": 2, "created_at": "2026-11-15T03:51:34.263+00:00", "compartment_id": "AVIONICS_BAY_B2", "role": "assistant",
   "content": "The language model is unavailable, so this is the rule-based status instead of an answer to your question. Smoldering anomaly in AVIONICS_BAY_B2: CO is 85 ppm and rising (+2.00 ppm/s), with increasing VOC and localized heat.", "advisory_id": 5},
  {"id": 1, "created_at": "2026-11-15T03:51:34.245+00:00", "compartment_id": "AVIONICS_BAY_B2", "role": "user",
   "content": "Why did the alarm trigger?", "advisory_id": null}]}
```

### `GET /events?limit=50&compartment_id=`: timeline, newest first

```json
{"events": [
  {"id": 5, "created_at": "2026-11-15T03:51:26.590+00:00", "compartment_id": "AVIONICS_BAY_B2", "kind": "status_change",
   "detail": {"timestamp_ms": 10500, "confidence_score": 0.603, "source": "simulator", "from": "NOMINAL", "to": "SMOLDERING_ANOMALY"}},
  {"id": 4, "created_at": "2026-11-15T03:51:24.046+00:00", "compartment_id": "AVIONICS_BAY_B2", "kind": "session_reset",
   "detail": {"timestamp_ms": 0, "reason": "timestamp_backwards", "previous_status": "SMOLDERING_ANOMALY", "confidence_score": 0.95, "source": "simulator"}}]}
```
`kind`: `status_change`, `session_reset` (`mode_change`, `timestamp_backwards`, `idle_timeout`; a reset clears a latched alarm), `dust_rejected`, `advisory_fallback` (why the LLM text was not used), `advisor_error`, `influx_down`, `influx_recovered`, `llm_down`, `llm_recovered` (the last four have a `null` compartment).

### `GET /health`

```json
{"status": "ok", "influx": {"status": "up", "buffered_points": 0, "oldest_buffered_age_s": 0.0, "dropped_points": 0,
                            "down_since": null, "last_error": null, "last_write_ok_age_s": 0.2},
 "sqlite": {"status": "up", "path": "/data/aether.db", "advisories": 3, "events": 3, "chat": 0},
 "omlx": {"status": "up", "base_url": "http://...", "model": "...", "model_served": true, "served_models": ["..."],
          "last_ok_age_s": 4.2, "last_error": null},
 "advisor": {"rag_facts": 17, "dropped_triggers": 0},
 "compartments": {"AVIONICS_BAY_B2": {"last_ingest_age_s": 0.24, "source": "simulator", "mode": "LIVE_IOT"}},
 "sse_subscribers": 1}
```
`status` is `degraded` when InfluxDB is down (the alarm path still works; data is buffered in memory for up to 5 minutes). `omlx.status` is `not_configured`, `unknown`, `up` or `down`. An LLM outage does not degrade the alarm path.

### `POST /ingest`: Jetson only

Header `X-Device-Token: <token>`, optional `X-Aether-Source: jetson | simulator | replay` (default `jetson`). Body is one contract object, or `{"items": [...]}` with up to 100. `stage1_detector` may be omitted and is ignored; the response is the backend's authoritative result.

```json
{"compartment_id": "AVIONICS_BAY_B2", "timestamp_ms": 10500,
 "stage1_detector": {"status": "SMOLDERING_ANOMALY", "confidence_score": 0.603, "false_alarm_dust_check": "NOMINAL_NO_EVENT"},
 "features": {"dco_dt": 2.0, "ratio": null, "voc_delta": 25.0, "pm_delta": 4.805, "temp_delta": 6.875}}
```
`401` bad token, `422` contract violation (the body lists `loc` and `msg`; it never echoes the input).

### `POST /thermal`: thermal camera frame (Jetson / ESP32)

Same headers as `/ingest`. One MLX90640 frame (32x24 = 768 pixels, °C, row-major: `pixels_c[y * 32 + x]`). `sensor` defaults to `MLX90640`; `MLX90641` (16x12) is also accepted. Values must be finite and within -40..400 °C.

```json
{"timestamp_ms": 10500, "compartment_id": "AVIONICS_BAY_B2", "sensor": "MLX90640",
 "width": 32, "height": 24, "pixels_c": [24.1, 24.0, "... 768 values ..."]}
```
Response: `{compartment_id, timestamp_ms, min_c, max_c, mean_c, hotspot: {x, y, temp_c}}`. Frames are kept in memory (latest per compartment) and are not written to InfluxDB. They do not feed the detector: keep sending the frame maximum as `max_ir_temp_c` in the telemetry envelope to `/ingest`.

Over USB serial, send the frame as its own JSON line next to the telemetry lines; `tools/feed.py --stdin` routes lines with `pixels_c` here and everything else to `/ingest`.

### `GET /thermal?compartment_id=`

Latest frame (the body above) plus `source`, `received_at`, `age_s` and the stats. Without `compartment_id`, the most recent frame of any compartment. `204` (no body) until a frame arrives. The dashboard polls this every 500 ms in live mode and shows the measured image while `age_s` is under 3 s, otherwise the schematic estimate.

## `GET /stream`: Server-Sent Events

```js
const es = new EventSource("https://aether-be.fakhrads.dev/api/v1/stream");
es.addEventListener("telemetry", e => update(JSON.parse(e.data)));
es.addEventListener("status_change", e => banner(JSON.parse(e.data)));
// advisory_start / advisory_token / advisory_done / health likewise
```
`EventSource` reconnects by itself (`retry: 2000`). On connect you immediately get one `health` event; call `GET /status` for the current state. Comment lines (`: keepalive`) arrive every 15 s.

| Event | When | Payload |
| --- | --- | --- |
| `telemetry` | each sample, at most 2 Hz per compartment | `{compartment_id, timestamp_ms, mode, source, telemetry{}, stage1_detector{}, features{}}` (same shape as `/status`) |
| `status_change` | the detector status changes, or a session resets | `{compartment_id, timestamp_ms, previous_status, status, confidence_score, false_alarm_dust_check, features{}, reset_reason}`; `reset_reason` is `null` for a normal transition |
| `advisory_start` | an advisory run begins | `{run_id, compartment_id, trigger, status, started_at, llm, preliminary{warning, steps[], model}}` |
| `advisory_token` | each LLM token (only when the LLM is used) | `{run_id, compartment_id, token}` |
| `advisory_done` | the run ends | `{run_id, cancelled: false, advisory{...as in /advisories..., disclaimer}}` or `{run_id, compartment_id, cancelled: true}` |
| `health` | on connect and every 10 s | same as `GET /health` |

Showing an alarm: the `status_change` event arrives first, with no model involved. `advisory_start.preliminary` is rule-based text you can show at once (`llm: true` means a better LLM text may replace it). Append `advisory_token`s to a live area; when `advisory_done` arrives, replace everything for that `run_id` with the final `advisory` (it can be the deterministic fallback if the LLM failed mid-stream, so do not keep the partial tokens). `cancelled: true` means a higher-priority run replaced this one; ignore its tokens.

```
event: status_change
data: {"compartment_id":"AVIONICS_BAY_B2","timestamp_ms":10500,"previous_status":"NOMINAL","status":"SMOLDERING_ANOMALY","confidence_score":0.603,"false_alarm_dust_check":"NOMINAL_NO_EVENT","features":{"dco_dt":2.0,"ratio":null,"voc_delta":25.0,"pm_delta":4.805,"temp_delta":6.875},"reset_reason":null}
```

## Compartment ids and data you can rely on

The initial demo has one compartment, `AVIONICS_BAY_B2`; the dashboard should still iterate `compartments`. Citations come from `data/rag/` only (NASA PSI brief plus two hand-checked facts). The backend never calls the internet at runtime.
