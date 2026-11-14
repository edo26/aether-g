# Dashboard

**Owner: Edo (Anggy Edo Prasetya)** — frontend applications, demo experience, and pitch.

Planned stack: **Vite + React**. This scaffold contains the panel and integration brief; the application has not been created yet.

## Four panels

| Panel | Display | Contract inputs / integration |
| --- | --- | --- |
| Thermal heatmap | Bay schematic, three probe temperatures, hottest region, IR maximum, hotspot motion | `thermocouples_c`, `max_ir_temp_c`, `smolder_velocity_mm_s` |
| Real-time sensor graphs | Time-aligned CO, CO2, VOC, CH4, PM2.5, airflow, and temperature trends | `timestamp_ms` and `telemetry` |
| Saffire/BASS risk matrix | Current rule-based status and supporting gas/thermal evidence alongside cited NASA facts | `stage1_detector` plus locally bundled source references |
| Gemma chat panel | Warning, three crew advisory steps, source citations, and follow-up questions | Separate local backend advisory/chat interface |

The current contract has three thermocouples and an IR maximum, not a full MLX90640 pixel array. Label the first heatmap as a **schematic estimate**. A measured thermal image will need a separately agreed image transport without altering the shared telemetry envelope.

Always show input mode (`LIVE_IOT` / `NASA_REPLAY`), compartment, and latest sample age. Keep the Stage-1 status visible while Gemma responds. A dust rejection remains `NOMINAL`; show the dust-check explanation without introducing another detector status. Treat confidence as a demo score.

## Demo and deployment

Deploy the implemented Vite/React frontend to **Vercel** for the public submission link. The hosted demo should use bundled, clearly labeled synthetic samples or attributed replay data, so visitors can inspect the scenarios without access to the team's edge computer. Keep secrets and model weights out of the browser bundle.

For the Wi-Fi-off rehearsal, run a locally built dashboard with the local backend and pre-provisioned model/reference assets. Bundle fonts, scripts, and source facts locally. Vercel supplies the public presentation; offline execution uses the local build. No server or deployment is included in this scaffold.

## Next steps

1. Create the Vite + React application and wire all four panels to the [shared contract](../docs/architecture.md#json-contract).
2. Consume the simulator stream through Fachri's adapter; coordinate the live stream and separate chat/advisory transport.
3. Add scenario selection, source labels, citations, clear status text, and stale-data handling.
4. Rehearse dust rejection, smoldering, and offline operation using the [demo script](../docs/demo-script.md).
5. Measure trigger-to-guidance latency with Fachri, then publish the frontend on Vercel and record its real URL in the root README.
