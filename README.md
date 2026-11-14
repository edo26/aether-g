# AETHER-G

An offline Edge AI copilot for microgravity fire safety, built for the **NASA Space Apps Challenge 2026 — Tackling Flame in Freefall**.

AETHER-G combines multi-gas and thermal sensing with a local Gemma model to help crews recognize smoldering and understand the next steps when Earth is too far away to answer immediately.

**Scaffold status:** the dependency-free telemetry simulator runs now. The FastAPI detector, Gemma/RAG integration, dashboard, and ESP32 firmware are the team's next implementation steps.

## The problem

Floating dust can fool smoke-only detection aboard the ISS, while early smoke from smoldering cable insulation can be difficult to detect. NASA/NIST research describes both background dust interference and detection gaps for small particles from heated spacecraft materials ([research summary](https://www.nist.gov/news-events/news/2018/05/nistnasa-study-shows-one-detector-doesnt-fit-all-smoke-spacecraft)). AETHER-G's dust and smoldering scenarios model these concerns; they are synthetic demonstrations, not ISS incident records.

Farther from Earth, help is delayed: a message from Mars can take **up to 24 minutes one way**, with another delay for the reply ([NASA communications overview](https://www.nasa.gov/missions/tech-demonstration/space-communications-7-things-you-need-to-know/)). Crew guidance must be available locally.

## The solution

- Fuse CO/CO2, VOC, particulate, airflow, thermocouple, and infrared signals to distinguish dust from an emerging combustion event.
- Target detection of smoldering **minutes before flame** by tracking gas trends and a moving thermal hotspot. This is a design goal to validate, not a measured result of this stub.
- Run **Gemma 100% offline on the edge** through Ollama, using temperature `0.0` and locally stored NASA reference facts for retrieval-augmented generation (RAG).
- Give crews a visual warning and three concise, source-grounded advisory steps; offline voice output through text-to-speech (TTS) is a stretch goal.

## Architecture: two input modes

| Mode | Source | Purpose |
| --- | --- | --- |
| Live IoT Sensor (`LIVE_IOT`) | ESP32-S3 over USB Serial; simulator stands in today | Exercise the sensor-to-advisory path |
| NASA Telemetry Replay (`NASA_REPLAY`) | Local, attributed NASA-derived replay files, once prepared | Demonstrate the same pipeline without hardware or internet |

```text
Simulator / ESP32 / local NASA replay
  -> FastAPI Stage-1 rule-based detector
  -> Gemma via Ollama (temperature 0.0) + local NASA RAG
  -> Dashboard: thermal heatmap | sensor graphs | risk matrix | Gemma chat
  -> Crew advisory + offline TTS (stretch)
```

The current `airgapped` scenario emits synthetic nominal data labeled `NASA_REPLAY` to exercise that interface. It does not yet replay downloaded NASA measurements. The edge computer hosts inference; the ESP32 acquires sensors. See [architecture and contract](docs/architecture.md).

## Quickstart: offline demo available now

From the repository root, with Python 3 already installed:

```bash
python3 simulator/simulator.py --scenario nominal
```

One JSON object is emitted per line every 500 ms. Stop with **Ctrl+C**, then try the other scenarios:

```bash
python3 simulator/simulator.py --scenario dust --interval-ms 500
python3 simulator/simulator.py --scenario smoldering --interval-ms 500
python3 simulator/simulator.py --scenario airgapped --interval-ms 500
```

Run each command separately. Dust keeps `stage1_detector.status` at `NOMINAL` and sets the dust-rejection marker. Smoldering crosses the demo threshold after about 10.5 seconds and reaches 125 ppm CO at 60 seconds. `airgapped` works with Wi-Fi disabled. The simulator needs no packages, models, server, credentials, or network access.

The full local AI demo will also need a pre-provisioned Ollama model, local RAG facts, and a built dashboard. Those components are not included yet. See the [simulator guide](simulator/README.md) and [three-minute demo script](docs/demo-script.md).

## JSON contract

Preserve these names, nesting, types, and units across every component:

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

`LIVE_IOT_OR_NASA_REPLAY` is the template placeholder: emitted `mode` values are `LIVE_IOT` or `NASA_REPLAY`. `timestamp_ms` is elapsed time from session start, beginning at zero and increasing by the configured interval. Status is only `NOMINAL` or `SMOLDERING_ANOMALY`. Dust rejection uses `false_alarm_dust_check: "DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE"` while status remains `NOMINAL`. The dust-check field is independent of the overall status. Full semantics are in [the architecture document](docs/architecture.md#json-contract).

## NASA data used and integration plan

NASA research informs the project; this scaffold contains source pointers and synthetic telemetry, with dataset ingestion still to come.

| Source | Intended use |
| --- | --- |
| Saffire I–VI | Spacecraft flame spread, gas hazards, detection, and fire-safety context for replay and RAG |
| BASS / BASS-II | Solid-fuel burning, airflow, and suppression context for the risk matrix and RAG |
| USML-1 | Microgravity transport context from relevant mission investigations; select and attribute the exact investigation before ingestion |

Start at the [NASA Physical Sciences Informatics (PSI) portal](https://psi.nasa.gov/) and [investigation catalog](https://www.nasa.gov/physical-sciences-informatics-psi/psi-investigations-by-research-area/). The [Saffire VI preliminary report](https://ntrs.nasa.gov/citations/20240002981) motivates sensing gas hazards alongside temperature. See [data provenance and acquisition notes](data/README.md). Large raw binaries and model weights stay outside Git.

## Team

| Member | Role |
| --- | --- |
| Anggy Edo Prasetya (Edo) | Initiator; frontend applications, dashboard, and pitch |
| Fachri | Backend applications; Edge AI, Stage-1 detector, and RAG |
| Heri | Hardware, ESP32 firmware, sensors, and IoT integration |

## Repository structure

```text
aether-g/
├── README.md
├── LICENSE
├── .gitignore
├── simulator/
│   ├── README.md
│   └── simulator.py
├── edge-ai/
│   └── README.md
├── dashboard/
│   └── README.md
├── firmware/
│   └── README.md
├── data/
│   └── README.md
└── docs/
    ├── architecture.md
    └── demo-script.md
```

## AI disclosure

Gemma is the planned local language model for explaining detector output using retrieved NASA facts. The current simulator uses rules and synthetic signals, with no model inference. Code and documentation are AI-assisted and subject to team review. Detector confidence values are demo scores, not calibrated probabilities; NASA sources do not validate this prototype's thresholds or detection lead time.

## License

[MIT](LICENSE), copyright 2026 Anggy Edo Prasetya and the AETHER-G team. External datasets, NASA media, and Gemma model weights retain their respective terms.
