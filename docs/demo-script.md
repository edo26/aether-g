# Three-minute demo script

**Presenter: Edo.** Fachri operates the local detector/Gemma stack; Heri operates the sensor board or simulator stand-in.

The timeline below describes the intended integrated demo. Today, the simulator demonstrates the JSON stream, dust rejection, smoldering trigger, and offline mode. Dashboard visuals, Gemma responses, and latency measurements still require implementation.

## Before the rehearsal

Use a local dashboard build, backend, pre-provisioned Ollama/Gemma model, and local NASA reference facts. Warm the model before timing. Keep all fonts, assets, and citations available locally. Select the simulator if hardware is not ready and label its telemetry synthetic. Prepare a sourced Saffire visual and the public Vercel link once deployed.

Run the scenarios separately from the repository root, stopping each with Ctrl+C:

```bash
python3 simulator/simulator.py --scenario airgapped --interval-ms 500
python3 simulator/simulator.py --scenario dust --interval-ms 500
python3 simulator/simulator.py --scenario smoldering --interval-ms 500
```

## Live flow

| Time | Show / do | Narration |
| --- | --- | --- |
| **0:00–0:45** | Introduce the crew problem; show an attributed Saffire finding and the two-mode pipeline. | “In microgravity, floating dust complicates smoke detection, while early cable smoldering can be hard to identify. Mars messages can take up to 24 minutes one way. Saffire shows why we must watch gas hazards as well as temperature.” |
| **0:45–1:30** | Visibly switch Wi-Fi off. Show nominal `airgapped` output with `NASA_REPLAY`, then run `dust` through the same local stack. PM2.5 rises to 80–150 µg/m³; gas and thermal readings stay nominal. Point to `NOMINAL` and `DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE`. | “The copilot runs locally. Particles alone do not trigger our demo's fire warning: this dust event has no corroborating gas or thermal rise.” |
| **1:30–2:30** | Start `smoldering` at 1:30. At about 1:40.5, CO passes 25 ppm with a positive slope. Show `SMOLDERING_ANOMALY`, rising VOC, and the moving hotspot. Time Gemma's warning plus three steps from that trigger, targeting **<2 s**. Let the ramp reach 125 ppm by 2:30. | “The detector sees rising CO and the other channels show growing heat. Gemma explains the evidence and brings local reference guidance to the crew without waiting for Earth.” |
| **2:30–3:00** | Close with the four panels, source citations, and the hardware/replay switch. Mention offline voice guidance if implemented. | “The same interface can support future Lunar Gateway and Artemis habitats, with habitat-specific calibration and procedures. Our next validation is real NASA replay and hardware measurements of detection lead time.” |

The dust and smoldering simulator scenarios emit `LIVE_IOT` as hardware stand-ins even while Wi-Fi is off. The `airgapped` scenario emits synthetic `NASA_REPLAY`; do not describe it as recorded NASA telemetry.

## Advisory rehearsal example

Target warning: **“Smoldering anomaly in AVIONICS_BAY_B2: CO is rising, with increasing VOC and localized heat.”**

Three proposed rehearsal steps, to bind to a reviewed vehicle checklist during integration:

1. Alert the crew and confirm the affected compartment and live readings.
2. Open the vehicle's approved electrical/fire response checklist and follow its isolation instructions.
3. Track gas and temperature trends and report whether the anomaly persists after checklist actions.

Show citations separately for NASA scientific findings and the source of operational procedures. The example is demo copy; it is not a NASA checklist. Stage-1 warning should appear immediately, even while Gemma is generating text. If the measured response exceeds two seconds, report the measured latency and keep the rule-based warning visible.

## Evidence on the opening slide

- [Saffire VI preliminary results, NASA NTRS](https://ntrs.nasa.gov/citations/20240002981): the studied gas increases were more significant than the overall cabin temperature/pressure rise.
- [NASA/NIST spacecraft smoke detection study](https://www.nist.gov/news-events/news/2018/05/nistnasa-study-shows-one-detector-doesnt-fit-all-smoke-spacecraft): material-dependent detection gaps and background dust complications.
- [NASA space communications overview](https://www.nasa.gov/missions/tech-demonstration/space-communications-7-things-you-need-to-know/): Mars communications can take up to 24 minutes each way.

Keep “minutes before flame” framed as the validation goal until an experiment supports a measured lead time. Synthetic scenario timing and advisory latency are different metrics.

## Thirty-second submission video

| Time | Shot / overlay |
| --- | --- |
| **0:00–0:05** | AETHER-G title; microgravity dust/smoldering problem; “Up to 24 minutes one-way to Earth.” |
| **0:05–0:10** | Wi-Fi off; local pipeline running; “Offline Edge AI.” |
| **0:10–0:16** | Dust spike; `NOMINAL`; show the rejection marker. |
| **0:16–0:24** | Cut to the smoldering threshold crossing; show heat and CO trends, Gemma warning, and three crew steps. Label edited scenario time; display measured latency only after verification. |
| **0:24–0:30** | Four panels, NASA source credits, Edo / Fachri / Heri, and the actual public demo link once available. Close with Lunar Gateway / Artemis potential. |

Credit NASA media and AI assistance. Label simulator footage and any mock UI clearly; only show Gemma guidance as a working feature once inference has been recorded. Use TTS in the clip only if the offline voice path is implemented.
