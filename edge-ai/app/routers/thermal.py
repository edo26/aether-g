from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response

from app.contract import ThermalFrame
from app.routers.ingest import Source, require_device_token

router = APIRouter(prefix="/api/v1", tags=["thermal"])

ESP32_EXAMPLE = r"""// ESP32 + MLX90640 -> AETHER-G  POST /api/v1/thermal   (~2 frames/s)
// Arduino libraries: "Adafruit MLX90640" (Library Manager). Board: any ESP32 / ESP32-S3.
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <Adafruit_MLX90640.h>

const char* WIFI_SSID    = "your-ssid";
const char* WIFI_PASS    = "your-password";
const char* URL          = "https://aether-be.fakhrads.dev/api/v1/thermal";
const char* DEVICE_TOKEN = "<AETHER_DEVICE_TOKEN>";   // same token as /ingest
const char* COMPARTMENT  = "AVIONICS_BAY_B2";

Adafruit_MLX90640 mlx;
float frame[32 * 24];          // row-major: frame[y * 32 + x], degrees C
WiFiClientSecure tls;
HTTPClient http;

void setup() {
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(800000);       // fast I2C so a full frame reads in time
  if (!mlx.begin(MLX90640_I2CADDR_DEFAULT, &Wire)) {
    Serial.println("MLX90640 not found");
    while (true) delay(1000);
  }
  mlx.setMode(MLX90640_CHESS);
  mlx.setResolution(MLX90640_ADC_18BIT);
  mlx.setRefreshRate(MLX90640_4_HZ);   // getFrame() reads two subpages: ~2 full frames/s

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(250);
  tls.setInsecure();           // demo only: pin the server's root CA for real use
  http.setReuse(true);         // keep the TLS connection open between frames
}

void loop() {
  if (mlx.getFrame(frame) != 0) return;          // read error: try the next frame

  float maxC = -1000;
  for (int i = 0; i < 768; i++) {
    if (isnan(frame[i]) || frame[i] < -40 || frame[i] > 400) return;  // backend rejects these: skip frame
    if (frame[i] > maxC) maxC = frame[i];
  }

  String body;
  body.reserve(6400);
  body += "{\"timestamp_ms\":";
  body += millis();
  body += ",\"compartment_id\":\"";
  body += COMPARTMENT;
  body += "\",\"sensor\":\"MLX90640\",\"width\":32,\"height\":24,\"pixels_c\":[";
  for (int i = 0; i < 768; i++) {
    if (i) body += ',';
    body += String(frame[i], 1);
  }
  body += "]}";

  http.begin(tls, URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  http.addHeader("X-Aether-Source", "jetson");
  int code = http.POST(body);
  if (code != 200) Serial.printf("thermal POST %d: %s\n", code, http.getString().c_str());
  http.end();

  // Also send maxC as telemetry.max_ir_temp_c in your /ingest sample: the detector reads that, not the image.
}
"""


_EXAMPLE = {
    "timestamp_ms": 10500, "compartment_id": "AVIONICS_BAY_B2", "sensor": "MLX90640",
    "width": 32, "height": 24, "pixels_c": [24.0] * 768,
}


@router.post(
    "/thermal",
    dependencies=[Depends(require_device_token)],
    summary="Thermal camera frame (MLX90640 32x24 or MLX90641 16x12)",
    description=(
        "Row-major `pixels_c` in °C (`pixels_c[y * width + x]`), 768 values for an MLX90640. Kept in memory "
        "as the latest frame per compartment for the dashboard. This does not feed the detector: keep sending "
        "`max_ir_temp_c` (the frame maximum) in the telemetry envelope to `/ingest`.\n\n"
        "Over USB serial instead of Wi-Fi: print the same JSON as one line (`Serial.println(body)`); "
        "`tools/feed.py --stdin` forwards lines with `pixels_c` here.\n\n"
        "**ESP32 example (Arduino, Wi-Fi):**\n\n```cpp\n" + ESP32_EXAMPLE + "```"),
)
async def post_thermal(
    request: Request,
    frame: Annotated[ThermalFrame, Body(openapi_examples={"mlx90640": {"summary": "MLX90640 frame", "value": _EXAMPLE}})],
    x_aether_source: Annotated[Source, Header()] = "jetson",
):
    entry = request.app.state.thermal.update(frame, x_aether_source)
    return {"compartment_id": frame.compartment_id, "timestamp_ms": frame.timestamp_ms, **entry.stats}


@router.get("/thermal", summary="Latest thermal camera frame", description=(
    "Latest frame for `compartment_id` (or the most recent of any compartment), with `age_s`, `source` "
    "and stats. 204 (no body) when no frame has been received, so polling without a camera logs no errors."))
async def get_thermal(
    request: Request,
    compartment_id: Annotated[str | None, Query(examples=["AVIONICS_BAY_B2"])] = None,
):
    state = request.app.state.thermal
    entry = state.get(compartment_id)
    if entry is None:
        return Response(status_code=204)
    return {
        **entry.frame.model_dump(),
        "source": entry.source,
        "received_at": entry.received_at.isoformat(),
        "age_s": state.age_s(entry),
        **entry.stats,
    }
