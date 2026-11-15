# Edge AI and backend

**Owner: Fachri** — backend applications, Stage-1 detector, local inference, and RAG.

The backend service described in [`docs/backend-plan.md`](../docs/backend-plan.md) is implemented here (status below). The sections after it keep the original design rules the code follows.

## Implementation status

FastAPI service in `app/`: contract validation, Stage-1 detector, InfluxDB (telemetry + detector results), SQLite (advisories, events, chat), local RAG over `data/rag/`, an oMLX client, REST + SSE for the dashboard. The dashboard-facing contract is in [`API.md`](API.md).

| Phase | State |
| --- | --- |
| 0 contract, 1 detector + `/ingest` + `tools/feed.py`, 2 InfluxDB + `/telemetry` `/status` SSE, 3 SQLite + fallback advisor + RAG, 5 `/ask` `/events`, `API.md`, Docker | done, covered by `uv run pytest` (offline) |
| 4 oMLX client, streaming, timeouts | **code done and tested against a mock server only. Not yet verified against the real oMLX: the endpoint shape and model id are assumptions until `tools/bench_llm.py --probe` has been run, and the latency target (< 2 s) has not been measured.** |
| 6 rehearsal on the Jetson over NetBird | not done |

Measured so far (this machine, simulator data): `POST /ingest` median 1.3 ms, p95 about 2 ms (target < 20 ms). A 10-minute real-time run at 2 Hz stored 1200 of 1200 telemetry points and 1200 of 1200 detector points, including a roughly 90 s InfluxDB outage (`/health` reported `degraded`, the detector kept answering, the buffer flushed on recovery with 0 dropped).

### Run it

```bash
cd edge-ai
cp .env.example .env            # set real tokens, AETHER_OMLX_BASE_URL and AETHER_OMLX_MODEL
docker compose up -d --build    # InfluxDB + backend; API on http://localhost:8090 (AETHER_PORT), docs at /docs

uv run pytest                   # offline tests (InfluxDB integration test is skipped when it is not reachable)
uv run uvicorn app.main:app --port 8090          # without Docker (needs InfluxDB, see AETHER_INFLUX_URL)
uv run python tools/feed.py --scenario smoldering --url http://localhost:8090 --speed 4   # fake Jetson
uv run python tools/bench_llm.py --probe         # check the real oMLX: GET /v1/models
uv run python tools/bench_llm.py --runs 10       # trigger -> first token and -> complete advisory
```

Settings are `AETHER_*` environment variables (see `.env.example`). With no oMLX configured every advisory is the deterministic fallback (`fallback: true`), and `/health` shows `omlx: not_configured`.

### Behaviour worth knowing

- Fire status is computed only by `app/detector.py`. The LLM explains it and cannot change it. If oMLX is slow (first token over 6 s), down, cut off mid-stream or answers in the wrong format, a deterministic advisory is stored and published instead (`advisory_fallback` event).
- Deviations from the plan: the PM2.5 baseline is capped at 10 ug/m3 (the simulator's dust scenario is already dusty at t=0, so a learned baseline would never reject it); anomaly confidence never drops below 0.6 once latched; unknown keys in an ingest payload are rejected (422) so contract drift is noticed.
- Advisory windows and slopes use the source's `timestamp_ms`, not receive time, so a Jetson posting batches does not distort slopes. Only the current session counts (a session reset starts a new window).
- Citations come from `data/rag/psi_fire_safety_brief.json` (NASA PSI) and `data/rag/facts_manual.json` (two hand-checked facts with source URLs). The LLM cites by short tags; tags it invents are dropped.
- The instructions go in a single `user` message because some Gemma chat templates reject a `system` role.
- The stream, `/status` and the detector have no dependency on InfluxDB or oMLX. Up to 5 minutes of points are buffered in memory while InfluxDB is down.

## Planned stack (original design notes)

- FastAPI on the edge computer ingests the [shared JSON contract](../docs/architecture.md#json-contract) from USB Serial or a local replay adapter.
- Originally Ollama; the implemented client targets **oMLX** (OpenAI-compatible) on the team's Mac, same model rules. The model is a quantized **Gemma 2B/4B**, selected for the available RAM and latency budget, at **temperature `0.0`**. Select an actual model/version tag matching the desired size during implementation.
- RAG retrieves locally stored Saffire/BASS facts with source identifiers, experiment conditions, and citations. Use plain JSON for the first demo, or ChromaDB if already available; embeddings and retrieval must also run locally.
- The pipeline emits Stage-1 results immediately, then adds a Gemma explanation and three crew advisory steps through a separate advisory response. Keep the telemetry envelope unchanged.

Ollama, model weights, reference facts, and any embeddings must be provisioned before the offline rehearsal. Temperature zero reduces output variation but does not make generated advice verified or fully deterministic.

## Stage-1 rule-based detector

Compute features per `compartment_id`, using elapsed `timestamp_ms` rather than message arrival time. Keep a short rolling baseline/window (start with five seconds, then tune against recorded traces). Reset history when an input session restarts or its mode changes.

| Feature | Definition | Initial use |
| --- | --- | --- |
| dCO/dt | `(CO_now - CO_previous) / delta_seconds`, in ppm/s | Stub trigger: CO > 25 ppm and positive slope |
| ΔCO/ΔCO2 | CO increase divided by CO2 increase relative to the same baseline | Supporting combustion evidence; ignore the ratio when the denominator is below the calibrated noise floor |
| VOC emergence | Sustained `voc_index` increase over the baseline | Corroborate rising CO; tune threshold to the sensor and source experiment |
| Smolder velocity | Movement of the hottest probe/IR region divided by elapsed time, in mm/s | Corroborate a progressing hotspot; the stub uses 1 mm/s within the demo range of 0.5–2 mm/s |
| Dust discrimination | PM2.5 spike without a sustained gas or temperature rise | Reject the dust-only event while keeping status `NOMINAL` |

The two detector statuses are exactly `NOMINAL` and `SMOLDERING_ANOMALY`. On a dust-only event, use `false_alarm_dust_check: "DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE"`. Otherwise the dust-check field is `NOMINAL_NO_EVENT`, including during a smoldering anomaly. A gas/thermal anomaly takes precedence over dust rejection.

Mirror the simulator's initial behavior: nominal confidence `0.95`; on the smoldering trigger, confidence rises from about `0.6` toward `0.95` as CO rises toward 125 ppm. Retain the anomaly once the CO ramp plateaus; the initial demo resets on a new session. These are illustrative scores and thresholds, not NASA-derived exposure limits or calibrated probabilities.

The production Stage-1 service must recompute status from input telemetry rather than trust the simulator/firmware's provisional detector object. Evaluate the additional ratio, VOC, and velocity features before making them trigger requirements; this scaffold only implements the simple CO trigger.

## Gemma and RAG behavior

Pass Gemma a bounded recent telemetry summary, the authoritative Stage-1 result, and retrieved source excerpts. Request a brief warning, three crew steps grounded in an approved checklist, and citations. Keep scenario-specific thresholds distinct from NASA findings. Gemma explains the detector; it does not change the detector's status.

If a citation or procedure is unavailable, identify that gap instead of inventing a NASA procedure. Display the rule-based warning immediately even if inference is slow. Target a complete warning and three-step response in **under two seconds after the Stage-1 trigger**, with a warm model; measure the actual latency on the demo computer.

## Next steps

1. Implement a local JSONL/USB adapter, contract validation, and per-compartment detector state.
2. Reproduce all four [simulator scenarios](../simulator/README.md); verify dust rejection and alarm persistence.
3. Curate a small, attributed Saffire/BASS fact set using [data provenance notes](../data/README.md).
4. Integrate Ollama at temperature `0.0`, record model/quantization details, and benchmark the two-second target.
5. Provide a local dashboard stream and a separate advisory interface; define their transport with Edo.
6. Rehearse with Wi-Fi disabled and all assets available locally. Add offline TTS if time permits.
