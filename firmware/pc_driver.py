#!/usr/bin/env python3
"""
aetherg - ESP32-S3 Multi-Sensor Telemetry CLI Driver
- Core: Polls 'READ_FULL' from ESP32-S3 over Serial + Stage 1 Symbolic Detector
- Optional HTTP Ingest (--ingest): Sequential paired POST to /api/v1/ingest -> /api/v1/thermal
"""

import argparse
import json
import math
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import serial
import serial.tools.list_ports

# Physical geometry assumption for MLX90640 (55°x35° FOV at ~0.5m target distance)
MM_PER_PIXEL = 15.0


def clamp(val: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, val))


def mask_token(token: str) -> str:
    """Return masked preview of device token for safe CLI/JSON logging."""
    t = token.strip()
    if not t:
        return ""
    if len(t) <= 4:
        return "*" * len(t)
    return f"****{t[-4:]}"


def find_esp32_port() -> Optional[str]:
    """Auto-detect likely ESP32-S3 USB-UART (COM) or Native USB port."""
    ports = list(serial.tools.list_ports.comports())
    keywords = ("cp210", "ch340", "ch343", "ch9102", "usb serial", "uart", "espressif", "jtag")
    for p in ports:
        desc = f"{p.description} {p.manufacturer or ''}".lower()
        if any(k in desc for k in keywords):
            return p.device
    return ports[0].device if ports else None


def normalize_ingest_url(raw_url: str, default_path: str = "/api/v1/ingest") -> str:
    """Ensure endpoint URL points cleanly to target path (e.g. /api/v1/ingest or /api/v1/thermal)."""
    url = raw_url.strip()
    if not url:
        return f"https://aether-be.fakhrads.dev{default_path}"
    if url.startswith("/"):
        return f"https://aether-be.fakhrads.dev{url}"
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    if url.endswith("/"):
        url = url[:-1]
    if not url.endswith(default_path) and "/ingest" not in url and "/thermal" not in url:
        url = f"{url}{default_path}"
    return url


def derive_thermal_url(ingest_url: str) -> str:
    """Derive /api/v1/thermal endpoint from the telemetry ingest URL."""
    norm = normalize_ingest_url(ingest_url, "/api/v1/ingest")
    if norm.endswith("/api/v1/ingest"):
        return norm[: -len("/api/v1/ingest")] + "/api/v1/thermal"
    if norm.endswith("/ingest"):
        return norm[: -len("/ingest")] + "/thermal"
    return f"{norm}/thermal"


# =============================================================================
# OPTIONAL ASYNCHRONOUS HTTP INGEST CLIENT (/api/v1/ingest -> /api/v1/thermal)
# =============================================================================
class HttpIngestClient:
    """
    Non-blocking sequential HTTP POST publisher:
      Step 1: POST sensor telemetry JSON -> /api/v1/ingest
      Step 2: Immediately POST MLX90640 32x24 frame JSON -> /api/v1/thermal
    Both payloads are paired atomically and carry the exact same 'timestamp_ms'.
    """

    def __init__(
        self,
        ingest_url: str,
        thermal_url: Optional[str] = None,
        device_token: str = "",
        aether_source: str = "jetson",
        enabled: bool = False,
        timeout_s: float = 5.0,
    ):
        self.ingest_url = normalize_ingest_url(ingest_url, "/api/v1/ingest")
        self._custom_thermal_url = bool(thermal_url and thermal_url.strip())
        self.thermal_url = (
            normalize_ingest_url(thermal_url, "/api/v1/thermal")
            if self._custom_thermal_url
            else derive_thermal_url(self.ingest_url)
        )
        self.device_token = device_token.strip()
        self.aether_source = aether_source.strip() or "jetson"
        self.enabled = enabled
        self.timeout_s = timeout_s

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._pending_pairs: deque[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]] = deque(maxlen=2)
        self._stop_event = threading.Event()

        self.last_synced_timestamp_ms: Optional[int] = None

        # Telemetry (/api/v1/ingest) tracking
        self.sent_count = 0
        self.error_count = 0
        self.last_status = "IDLE" if enabled else "OFF (OPTIONAL)"
        self.last_status_code: Optional[int] = None
        self.last_latency_ms: int = 0
        self.last_response_body: str = ""

        # Thermal (/api/v1/thermal) tracking
        self.thermal_sent_count = 0
        self.thermal_error_count = 0
        self.thermal_last_status = "IDLE" if enabled else "OFF (OPTIONAL)"
        self.thermal_last_status_code: Optional[int] = None
        self.thermal_last_latency_ms: int = 0
        self.thermal_last_response_body: str = ""

        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def is_enabled(self) -> bool:
        with self._lock:
            return self.enabled

    def set_url(self, new_url: str) -> str:
        normalized = normalize_ingest_url(new_url, "/api/v1/ingest")
        with self._lock:
            self.ingest_url = normalized
            if not self._custom_thermal_url:
                self.thermal_url = derive_thermal_url(normalized)
        return normalized

    def set_thermal_url(self, new_url: str) -> str:
        cleaned = new_url.strip()
        with self._lock:
            if not cleaned or cleaned.lower() == "auto":
                self._custom_thermal_url = False
                self.thermal_url = derive_thermal_url(self.ingest_url)
            else:
                self._custom_thermal_url = True
                self.thermal_url = normalize_ingest_url(cleaned, "/api/v1/thermal")
            return self.thermal_url

    def set_token(self, token: str) -> str:
        cleaned = token.strip()
        with self._lock:
            self.device_token = cleaned
        return cleaned

    def set_source(self, source: str) -> str:
        cleaned = source.strip() or "jetson"
        with self._lock:
            self.aether_source = cleaned
        return cleaned

    def set_enabled(self, flag: bool) -> None:
        with self._cond:
            self.enabled = flag
            if flag:
                self.last_status = "IDLE"
                self.thermal_last_status = "IDLE"
                self._cond.notify_all()
            else:
                self._pending_pairs.clear()
                self.last_synced_timestamp_ms = None
                self.last_status = "OFF (OPTIONAL)"
                self.last_status_code = None
                self.last_latency_ms = 0
                self.last_response_body = ""
                self.thermal_last_status = "OFF (OPTIONAL)"
                self.thermal_last_status_code = None
                self.thermal_last_latency_ms = 0
                self.thermal_last_response_body = ""

    def get_state_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "synced_timestamp_ms": self.last_synced_timestamp_ms,
                "endpoint": self.ingest_url,
                "x_aether_source": self.aether_source,
                "x_device_token": mask_token(self.device_token) or None,
                "status": self.last_status,
                "status_code": self.last_status_code,
                "latency_ms": self.last_latency_ms,
                "sent_count": self.sent_count,
                "error_count": self.error_count,
                "response_body": self.last_response_body,
                "thermal": {
                    "endpoint": self.thermal_url,
                    "timestamp_ms": self.last_synced_timestamp_ms,
                    "status": self.thermal_last_status,
                    "status_code": self.thermal_last_status_code,
                    "latency_ms": self.thermal_last_latency_ms,
                    "sent_count": self.thermal_sent_count,
                    "error_count": self.thermal_error_count,
                    "response_body": self.thermal_last_response_body,
                },
            }

    def enqueue(self, full_packet: Dict[str, Any], matrix: Optional[List[float]] = None) -> None:
        """Pair /api/v1/ingest and /api/v1/thermal payloads with the EXACT same timestamp_ms."""
        with self._cond:
            if not self.enabled:
                return

            shared_ts_ms = int(full_packet["timestamp_ms"])
            comp_id = full_packet["compartment_id"]

            ingest_payload = {
                "timestamp_ms": shared_ts_ms,
                "mode": full_packet["mode"],
                "compartment_id": comp_id,
                "telemetry": full_packet["telemetry"],
            }

            thermal_payload: Optional[Dict[str, Any]] = None
            if matrix and len(matrix) == 768:
                thermal_payload = {
                    "timestamp_ms": shared_ts_ms,
                    "compartment_id": comp_id,
                    "sensor": "MLX90640",
                    "width": 32,
                    "height": 24,
                    "pixels_c": [round(float(v), 2) for v in matrix],
                }

            self._pending_pairs.append((ingest_payload, thermal_payload))
            self._cond.notify()

    def _post_json(
        self,
        target_url: str,
        payload: Dict[str, Any],
        source: str,
        token: str,
    ) -> Tuple[bool, Optional[int], int, str, str]:
        headers: Dict[str, str] = {
            "accept": "*/*",
            "Content-Type": "application/json",
            "User-Agent": "aetherg-telemetry/1.0",
        }
        if source:
            headers["x-aether-source"] = source
        if token:
            headers["x-device-token"] = token

        t0 = time.time()
        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                target_url,
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                code = resp.getcode()
                resp_text = resp.read().decode("utf-8", errors="replace").strip()
            ms = int((time.time() - t0) * 1000)
            return True, code, ms, resp_text[:240], f"{code} OK ({ms}ms)"
        except urllib.error.HTTPError as http_err:
            ms = int((time.time() - t0) * 1000)
            err_body = ""
            try:
                err_body = http_err.read().decode("utf-8", errors="replace").strip()
            except Exception:
                pass
            clean_body = (err_body or str(http_err.reason))[:240]
            return False, http_err.code, ms, clean_body, f"HTTP {http_err.code} ({ms}ms)"
        except Exception as exc:
            ms = int((time.time() - t0) * 1000)
            msg = str(exc)
            short_msg = (msg[:23] + "...") if len(msg) > 26 else msg
            return False, None, ms, msg[:240], f"ERR: {short_msg}"

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._cond:
                while not self._stop_event.is_set() and (not self.enabled or not self._pending_pairs):
                    self._cond.wait(timeout=0.4)

                if self._stop_event.is_set():
                    break
                if not self.enabled or not self._pending_pairs:
                    continue

                ingest_payload, thermal_payload = self._pending_pairs.popleft()
                ingest_url = self.ingest_url
                thermal_url = self.thermal_url
                token = self.device_token
                source = self.aether_source

            shared_ts_ms = ingest_payload["timestamp_ms"]

            # 1) POST sensor telemetry data first
            ok_ing, code_ing, ms_ing, body_ing, status_ing = self._post_json(
                ingest_url, ingest_payload, source, token
            )
            with self._lock:
                if not self.enabled:
                    continue
                if ok_ing:
                    self.sent_count += 1
                else:
                    self.error_count += 1
                self.last_status_code = code_ing
                self.last_latency_ms = ms_ing
                self.last_response_body = body_ing
                self.last_status = status_ing
                self.last_synced_timestamp_ms = shared_ts_ms

            # 2) Immediately POST thermal imaging right after sensor data with the EXACT same timestamp_ms
            if thermal_payload is not None and self.is_enabled():
                ok_th, code_th, ms_th, body_th, status_th = self._post_json(
                    thermal_url, thermal_payload, source, token
                )
                with self._lock:
                    if not self.enabled:
                        continue
                    if ok_th:
                        self.thermal_sent_count += 1
                    else:
                        self.thermal_error_count += 1
                    self.thermal_last_status_code = code_th
                    self.thermal_last_latency_ms = ms_th
                    self.thermal_last_response_body = body_th
                    self.thermal_last_status = status_th

    def stop(self) -> None:
        with self._cond:
            self._stop_event.set()
            self._cond.notify_all()


# =============================================================================
# STAGE 1: SYMBOLIC TELEMETRY PROCESSOR
# =============================================================================
class TelemetryProcessor:
    """Handles unit conversions, thermal centroid velocity tracking, and Stage 1 detection."""

    def __init__(
        self,
        compartment_id: str = "AVIONICS_BAY_B2",
        mode: str = "LIVE_IOT",
        ingest_client: Optional[HttpIngestClient] = None,
    ):
        self.compartment_id = compartment_id
        self.mode = mode
        self.ingest_client = ingest_client

        self.prev_centroid: Optional[Tuple[float, float]] = None
        self.prev_timestamp_ms: Optional[int] = None
        self.smoothed_velocity_mm_s: float = 0.0

    def compute_voc_index(self, bme_data: Optional[Dict[str, Any]]) -> int:
        if not bme_data or bme_data.get("voc_kohm") is None:
            return 0
        voc_kohm = max(float(bme_data["voc_kohm"]), 1.0)
        log_r = math.log10(voc_kohm)
        norm = clamp((2.40 - log_r) / (2.40 - 0.70), 0.0, 1.0)
        voc_idx = int(round(20 + norm * 460))
        return int(clamp(voc_idx, 0, 500))

    def analyze_thermal_frame(self, matrix: List[float], now_ms: int) -> Tuple[float, float]:
        if not matrix or len(matrix) != 768:
            return 0.0, 0.0

        max_temp = max(matrix)
        ambient = statistics.median(matrix)
        threshold = ambient + 8.0

        weight_sum = 0.0
        col_sum = 0.0
        row_sum = 0.0

        for idx, temp in enumerate(matrix):
            if temp > threshold:
                w = (temp - threshold) ** 2
                c = idx % 32
                r = idx // 32
                weight_sum += w
                col_sum += c * w
                row_sum += r * w

        if weight_sum > 0.0:
            cx = col_sum / weight_sum
            cy = row_sum / weight_sum
            centroid = (cx, cy)

            if self.prev_centroid is not None and self.prev_timestamp_ms is not None:
                dt_s = (now_ms - self.prev_timestamp_ms) / 1000.0
                if dt_s > 0.05:
                    dist_px = math.hypot(cx - self.prev_centroid[0], cy - self.prev_centroid[1])
                    inst_vel = (dist_px * MM_PER_PIXEL) / dt_s
                    self.smoothed_velocity_mm_s = 0.45 * inst_vel + 0.55 * self.smoothed_velocity_mm_s

            self.prev_centroid = centroid
            self.prev_timestamp_ms = now_ms
        else:
            self.prev_centroid = None
            self.prev_timestamp_ms = now_ms
            self.smoothed_velocity_mm_s *= 0.5
            if self.smoothed_velocity_mm_s < 0.1:
                self.smoothed_velocity_mm_s = 0.0

        return round(max_temp, 2), round(self.smoothed_velocity_mm_s, 2)

    def run_stage1_detector(
        self,
        co_ppm: float,
        voc_index: int,
        pm25: float,
        max_ir_c: float,
        tc_max_c: float,
        smolder_vel: float,
    ) -> Dict[str, Any]:
        thermal_score = clamp((max_ir_c - 38.0) / 45.0, 0.0, 1.0)
        tc_score = clamp((tc_max_c - 45.0) / 60.0, 0.0, 1.0)
        co_score = clamp((co_ppm - 10.0) / 50.0, 0.0, 1.0)
        voc_score = clamp((voc_index - 100.0) / 250.0, 0.0, 1.0)
        motion_score = clamp(smolder_vel / 40.0, 0.0, 1.0)

        confidence = (
            0.40 * thermal_score
            + 0.20 * co_score
            + 0.15 * voc_score
            + 0.15 * tc_score
            + 0.10 * motion_score
        )
        confidence = round(clamp(confidence, 0.0, 1.0), 3)

        if pm25 > 150.0 and max_ir_c < 40.0 and co_ppm < 15.0:
            dust_check = "SUSPECTED_DUST_FALSE_ALARM"
            status = "NOMINAL"
            confidence = round(confidence * 0.3, 3)
        elif confidence >= 0.65 or max_ir_c >= 75.0 or co_ppm >= 50.0:
            status = "ALARM"
            dust_check = "VERIFIED_THERMAL_GAS_EVENT"
        elif confidence >= 0.30 or max_ir_c >= 45.0 or co_ppm >= 25.0:
            status = "WARNING"
            dust_check = "ELEVATED_SIGNATURE_MONITORING"
        else:
            status = "NOMINAL"
            dust_check = "NOMINAL_NO_EVENT"

        return {
            "status": status,
            "confidence_score": confidence,
            "false_alarm_dust_check": dust_check,
        }

    def process_raw_packet(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        now_ms = int(time.time() * 1000)

        co_data = raw.get("co_mq7") or {}
        co_ppm = round(float(co_data.get("est_ppm", 0.0)), 2)

        co2_raw = raw.get("co2_ppm")
        co2_ppm = round(float(co2_raw), 1) if co2_raw is not None else 0.0

        bme_data = raw.get("bme688") or {}
        voc_index = self.compute_voc_index(bme_data)

        ch4_ppm = round(float(raw.get("ch4_ppm", 0.0)), 2)
        pm25_ug_m3 = round(float(raw.get("pm25_ug_m3", 0.0)), 2)

        anemo = raw.get("anemometer") or {}
        airflow_cm_s = round(float(anemo.get("wind_ms", 0.0)) * 100.0, 2)

        tc_raw = raw.get("thermocouple_k")
        if isinstance(tc_raw, list):
            tcs = [round(float(x or 0.0), 2) for x in (tc_raw + [0.0, 0.0, 0.0])[:3]]
        elif tc_raw is not None:
            tcs = [round(float(tc_raw), 2), 0.0, 0.0]
        else:
            tcs = [0.0, 0.0, 0.0]

        mlx = raw.get("mlx90640") or {}
        matrix = mlx.get("matrix") or []
        max_ir_c, smolder_vel = self.analyze_thermal_frame(matrix, now_ms)
        if not matrix and mlx.get("max_c") is not None:
            max_ir_c = round(float(mlx["max_c"]), 2)

        stage1 = self.run_stage1_detector(
            co_ppm=co_ppm,
            voc_index=voc_index,
            pm25=pm25_ug_m3,
            max_ir_c=max_ir_c,
            tc_max_c=max(tcs),
            smolder_vel=smolder_vel,
        )

        output_json: Dict[str, Any] = {
            "timestamp_ms": now_ms,
            "mode": self.mode,
            "compartment_id": self.compartment_id,
            "telemetry": {
                "co_ppm": co_ppm,
                "co2_ppm": co2_ppm,
                "voc_index": voc_index,
                "ch4_ppm": ch4_ppm,
                "pm25_ug_m3": pm25_ug_m3,
                "airflow_cm_s": airflow_cm_s,
                "thermocouples_c": tcs,
                "max_ir_temp_c": max_ir_c,
                "smolder_velocity_mm_s": smolder_vel,
            },
            "stage1_detector": stage1,
        }

        if self.ingest_client is not None and self.ingest_client.is_enabled():
            self.ingest_client.enqueue(output_json, matrix=matrix)
            output_json["http_ingest"] = self.ingest_client.get_state_dict()

        return output_json


# =============================================================================
# SERIAL CLIENT (WITH 64KB WINDOWS RX BUFFER, NULL-CLEANING & RETRY)
# =============================================================================
class SerialClient:
    """Thread-safe Serial client communicating with ESP32-S3."""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 2.5):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser: Optional[serial.Serial] = None
        self.lock = threading.Lock()

    def connect(self) -> None:
        with self.lock:
            self.disconnect()
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                write_timeout=self.timeout,
            )
            if hasattr(self.ser, "set_buffer_size"):
                try:
                    self.ser.set_buffer_size(rx_size=65536, tx_size=16384)
                except Exception:
                    pass
            time.sleep(0.5)
            self.ser.reset_input_buffer()

    def disconnect(self) -> None:
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    @property
    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def _read_json_line(self, deadline: float) -> Dict[str, Any]:
        assert self.ser is not None
        while time.time() < deadline:
            raw_bytes = self.ser.read_until(b"\n")
            if not raw_bytes:
                continue
            line = raw_bytes.decode("utf-8", errors="replace").replace("\x00", "").strip()
            if not line:
                continue
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    tail = line[-80:] if len(line) > 80 else line
                    raise json.JSONDecodeError(f"{exc.msg} | tail={tail!r}", exc.doc, exc.pos) from exc
                if "event" in data:
                    continue
                return data
        raise TimeoutError("Timed out waiting for READ_FULL response from ESP32-S3.")

    def request_full(self, retries: int = 1) -> Dict[str, Any]:
        with self.lock:
            if not self.is_connected:
                raise ConnectionError("Serial port is not connected.")
            assert self.ser is not None

            last_err: Optional[Exception] = None
            for attempt in range(retries + 1):
                if attempt > 0:
                    time.sleep(0.10)
                self.ser.reset_input_buffer()
                self.ser.write(b"READ_FULL\n")
                self.ser.flush()

                deadline = time.time() + self.timeout
                try:
                    return self._read_json_line(deadline)
                except json.JSONDecodeError as exc:
                    last_err = exc
                    continue

            assert last_err is not None
            raise last_err


# =============================================================================
# CLI HEADLESS MODE
# =============================================================================
def _cli_stdin_listener(ingest: HttpIngestClient, stop_event: threading.Event) -> None:
    """Allow interactive commands on stdin in CLI mode (token, source, post on/off, url, thermal-url)."""
    while not stop_event.is_set():
        try:
            line = sys.stdin.readline()
            if not line:
                break
            cmd = line.strip()
            if not cmd:
                continue
            low = cmd.lower()
            if low == "post on":
                ingest.set_enabled(True)
                sys.stderr.write(
                    f"[CLI] HTTP POST ENABLED -> {ingest.ingest_url} then {ingest.thermal_url}\n"
                )
            elif low == "post off":
                ingest.set_enabled(False)
                sys.stderr.write("[CLI] HTTP POST DISABLED\n")
            elif low == "token clear":
                ingest.set_token("")
                sys.stderr.write("[CLI] x-device-token CLEARED\n")
            elif low.startswith("token "):
                new_tok = cmd[6:].strip()
                ingest.set_token(new_tok)
                sys.stderr.write(f"[CLI] x-device-token UPDATED ({mask_token(new_tok)})\n")
            elif low.startswith("source "):
                new_src = ingest.set_source(cmd[7:].strip())
                sys.stderr.write(f"[CLI] x-aether-source UPDATED ({new_src})\n")
            elif low.startswith("url "):
                new_url = ingest.set_url(cmd[4:].strip())
                sys.stderr.write(
                    f"[CLI] Ingest URL UPDATED -> {new_url} (Thermal -> {ingest.thermal_url})\n"
                )
            elif low.startswith("thermal-url "):
                new_t_url = ingest.set_thermal_url(cmd[12:].strip())
                sys.stderr.write(f"[CLI] Thermal URL UPDATED -> {new_t_url}\n")
            elif low == "status":
                sys.stderr.write(f"[CLI] HTTP Ingest State: {json.dumps(ingest.get_state_dict())}\n")
        except Exception:
            break


def run_cli(args: argparse.Namespace) -> None:
    port = args.port or find_esp32_port()
    if not port:
        sys.stderr.write("[ERROR] No serial port found. Specify --port (e.g., COM7 or /dev/ttyUSB0).\n")
        sys.exit(1)

    ingest = HttpIngestClient(
        ingest_url=args.ingest_url,
        thermal_url=args.thermal_url,
        device_token=args.device_token,
        aether_source=args.aether_source,
        enabled=args.enable_ingest,
    )
    client = SerialClient(port=port, baudrate=args.baud)
    processor = TelemetryProcessor(
        compartment_id=args.compartment,
        mode=args.mode,
        ingest_client=ingest,
    )

    cli_stop = threading.Event()
    if sys.stdin and sys.stdin.isatty():
        threading.Thread(target=_cli_stdin_listener, args=(ingest, cli_stop), daemon=True).start()

    try:
        client.connect()
        tok_tag = f" [x-device-token: {mask_token(args.device_token)}]" if args.device_token else " [no token]"
        src_tag = f" [x-aether-source: {ingest.aether_source}]"
        ingest_info = (
            f"ON ({ingest.ingest_url} -> {ingest.thermal_url}{src_tag}{tok_tag})"
            if args.enable_ingest
            else f"OFF (optional{src_tag}{tok_tag})"
        )
        sys.stderr.write(
            f"[INFO] Connected to ESP32-S3 on {port} @ {args.baud} baud | POST: {ingest_info}\n"
        )
    except Exception as exc:
        sys.stderr.write(f"[ERROR] Failed to connect to {port}: {exc}\n")
        cli_stop.set()
        ingest.stop()
        sys.exit(1)

    try:
        while True:
            t0 = time.time()
            try:
                raw = client.request_full()
                telemetry_json = processor.process_raw_packet(raw)
                print(json.dumps(telemetry_json), flush=True)
            except Exception as exc:
                sys.stderr.write(f"[WARN] Read cycle error: {exc}\n")

            elapsed = time.time() - t0
            sleep_time = max(0.05, args.interval - elapsed)
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        sys.stderr.write("\n[INFO] Stopped by user.\n")
    finally:
        cli_stop.set()
        ingest.stop()
        client.disconnect()


def main() -> None:
    default_token = os.environ.get("AETHERG_DEVICE_TOKEN") or os.environ.get("X_DEVICE_TOKEN") or ""
    default_source = os.environ.get("AETHERG_SOURCE") or os.environ.get("X_AETHER_SOURCE") or "jetson"

    parser = argparse.ArgumentParser(description="aetherg Telemetry CLI Driver")
    parser.add_argument("--port", type=str, default=None, help="Serial COM port (auto-detected if omitted)")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baudrate (default: 115200)")
    parser.add_argument("--interval", type=float, default=0.5, help="Polling interval in seconds (default: 0.5)")
    parser.add_argument("--compartment", type=str, default="AVIONICS_BAY_B2", help="Compartment ID string")
    parser.add_argument("--mode", type=str, default="LIVE_IOT", help="JSON mode field (default: LIVE_IOT)")
    parser.add_argument(
        "--ingest",
        dest="enable_ingest",
        action="store_true",
        help="Enable sequential HTTP POST forwarding to /api/v1/ingest then /api/v1/thermal",
    )
    parser.add_argument(
        "--ingest-url",
        type=str,
        default="https://aether-be.fakhrads.dev/api/v1/ingest",
        help="Target telemetry HTTP POST endpoint (default: https://aether-be.fakhrads.dev/api/v1/ingest)",
    )
    parser.add_argument(
        "--thermal-url",
        type=str,
        default=None,
        help="Optional override for thermal POST endpoint (auto-derived as /api/v1/thermal if omitted)",
    )
    parser.add_argument(
        "--source",
        "--aether-source",
        "--x-aether-source",
        dest="aether_source",
        type=str,
        default=default_source,
        help="Value for 'x-aether-source' HTTP header (default: jetson)",
    )
    parser.add_argument(
        "--token",
        "--device-token",
        "--x-device-token",
        dest="device_token",
        type=str,
        default=default_token,
        help="Value for 'x-device-token' HTTP header on POST requests (or set AETHERG_DEVICE_TOKEN)",
    )
    args = parser.parse_args()
    run_cli(args)


if __name__ == "__main__":
    main()
