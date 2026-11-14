# Telemetry simulator

Dependency-free Python 3 stand-in for Heri's ESP32 and Fachri's detector integration. All emitted values are synthetic. Only `argparse`, `json`, `time`, `math`, and `random` from the standard library are used.

## Run

From the repository root:

```bash
python3 simulator/simulator.py --scenario nominal
python3 simulator/simulator.py --scenario dust --interval-ms 500
python3 simulator/simulator.py --scenario smoldering --interval-ms 500
python3 simulator/simulator.py --scenario airgapped --interval-ms 500
```

Run one command at a time; **Ctrl+C** stops it. `--scenario` is required. `--interval-ms` is a positive integer, defaulting to `500`. Each line on stdout is one complete JSON object, flushed immediately. Diagnostic argument errors go to stderr.

The first sample has `timestamp_ms: 0`; later samples increment by exactly the interval. This is a logical elapsed clock, not a Unix timestamp. Changing the interval changes sampling frequency; the 60-second smoldering ramp still follows elapsed simulation time. Small random fluctuations are synthetic sensor noise.

## Scenarios

| Scenario | Signal | Detector result | Mode |
| --- | --- | --- | --- |
| `nominal` | CO 0–3 ppm; CO2 400–600 ppm; low VOC; PM2.5 below 10 µg/m³; airflow 2–5 cm/s; thermocouples 22–24 °C; IR near 24 °C | `NOMINAL`, confidence `0.95`, `NOMINAL_NO_EVENT` | `LIVE_IOT` |
| `dust` | Immediate PM2.5 spike, varying within 80–150 µg/m³; gases and temperatures remain nominal | `NOMINAL`, confidence `0.95`, `DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE` | `LIVE_IOT` |
| `smoldering` | CO 5 → 125 ppm in 60 s; rising VOC and heat; hotspot traverses three probe positions at a synthetic 1 mm/s | `SMOLDERING_ANOMALY` when CO > 25 ppm and dCO/dt > 0; confidence grows from about `0.6` to `0.95` | `LIVE_IOT` |
| `airgapped` | Same ranges as nominal; no network access | `NOMINAL`, confidence `0.95`, `NOMINAL_NO_EVENT` | `NASA_REPLAY` |

At the default interval, smoldering trips at 10.5 seconds. After 60 seconds, CO and the overall heating envelope plateau, and the anomaly stays latched for the session. The hotspot moves back and forth across probe locations at 0, 30, and 60 mm; these are demo geometry, not measured hardware positions. Restarting the command resets the run.

`NOMINAL_NO_EVENT` means there is no rejected dust-only event; it can coexist with `SMOLDERING_ANOMALY`. No additional detector status is introduced.

## Contract and integration

Every sample follows the [shared JSON contract](../docs/architecture.md#json-contract) exactly. `compartment_id` is `AVIONICS_BAY_B2`, `thermocouples_c` contains exactly three floats, and `voc_index` is an integer. Other telemetry quantities are floats, `timestamp_ms` is an integer, and confidence is between zero and one.

The simulator embeds illustrative Stage-1 results so Edo can build the dashboard before the backend exists. Fachri's FastAPI service will recompute authoritative detector results from the telemetry. `airgapped` exercises the replay mode flag using synthetic samples; actual NASA replay files and their provenance are a separate next step in [data](../data/README.md).
