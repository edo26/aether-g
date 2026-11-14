# Edge AI and backend

**Owner: Fachri** — backend applications, Stage-1 detector, local inference, and RAG.

This directory is a handoff specification. No backend service, model, or retrieval database is implemented in this scaffold.

## Planned stack

- FastAPI on the edge computer ingests the [shared JSON contract](../docs/architecture.md#json-contract) from USB Serial or a local replay adapter.
- Ollama serves a quantized **Gemma 2B/4B** model, selected for the available RAM and latency budget, at **temperature `0.0`**. Select an actual model/version tag matching the desired size during implementation.
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
