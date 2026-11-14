# Hardware and firmware

**Owner: Heri** — hardware, sensors, ESP32 firmware, and IoT integration.

Controller: **ESP32-S3**. The ESP32 acquires telemetry and sends USB Serial JSON; Gemma runs on the connected edge computer.

## Sensor plan

| Hardware | Contract field | Integration note |
| --- | --- | --- |
| Dedicated CO and CO2 sensors | `co_ppm`, `co2_ppm` | Calibrate in ppm; finalize sensor models and interfaces |
| BME688 | `voc_index` | Produce an integer VOC index with a documented conversion/baseline |
| SEN55 particulate sensor | `pm25_ug_m3` | PM2.5 mass concentration in µg/m³ |
| Three Type-K thermocouples with suitable interfaces | `thermocouples_c` | Fixed probe order and recorded spacing; values in °C |
| MLX90640 thermal camera | `max_ir_temp_c` | Maximum measured temperature in °C |
| Calibrated airflow source, to select | `airflow_cm_s` | Convert to cm/s; record sensor placement |
| Dedicated CH4 source, to select | `ch4_ppm` | Preserve the field; BME688 VOC is not a methane measurement |
| Derived from probe/IR hotspot motion | `smolder_velocity_mm_s` | Use known geometry and elapsed time; align calculation with Fachri |

## USB Serial contract

Emit UTF-8 newline-delimited JSON, one complete object per line, using the [full shared JSON contract](../docs/architecture.md#json-contract). Coordinate a baud rate with the host adapter; start with 115200 baud and 500 ms samples. Send diagnostics through a separate channel so the telemetry stream stays parseable.

- `timestamp_ms`: elapsed milliseconds since boot/session start; the host resets its baseline after a restart.
- `mode`: `LIVE_IOT` for hardware.
- `compartment_id`: `AVIONICS_BAY_B2` for the initial demo.
- `thermocouples_c`: exactly three numbers, ordered consistently with the bay schematic.
- `stage1_detector`: use the same two statuses and dust marker as the simulator. Initial firmware results are provisional; FastAPI recomputes authoritative detection.

The contract carries only the thermal maximum, not the camera's full pixel array. Coordinate any later image stream separately with Edo and Fachri. Finalize CH4/airflow measurement and invalid-sensor handling before claiming complete live coverage; until then use the clearly labeled simulator rather than presenting missing measurements as valid zeroes.

## Simulator stand-in

From the repository root:

```bash
python3 simulator/simulator.py --scenario nominal --interval-ms 500
python3 simulator/simulator.py --scenario dust --interval-ms 500
python3 simulator/simulator.py --scenario smoldering --interval-ms 500
```

Run each command separately. These exercise the same JSON interface while the board is being assembled. See [simulator behavior](../simulator/README.md).

## Next steps

1. Finalize wiring, power budget, sensor interfaces, and probe positions.
2. Calibrate units, baselines, sample timing, and warm-up behavior.
3. Implement the serial serializer and validate it against simulator output.
4. Derive hotspot velocity using measured geometry and coordinate ownership of that calculation with Fachri.
5. Swap the simulator adapter for USB Serial and rehearse the same dashboard scenarios.
