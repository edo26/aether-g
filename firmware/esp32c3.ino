#include <Arduino.h>
#include <Wire.h>
#include <math.h>

// ============================================================================
// KONFIGURASI FIRMWARE ESP32-S3 (aetherg Telemetry Node)
// ============================================================================
#define SERIAL_BAUD          115200
#define DEFAULT_DEMO_NORMAL  true   // true = mulai dalam mode Demo Normal (tanpa api)
#define USE_REAL_SENSORS     0      // Ubah ke 1 jika library sensor fisik sudah terpasang

// Pin Konfigurasi (Sesuaikan dengan wiring board ESP32-S3 Anda)
#define I2C_SDA_PIN          8
#define I2C_SCL_PIN          9
#define MQ7_ANALOG_PIN       4
#define MQ4_CH4_ANALOG_PIN   5
#define ANEMO_ANALOG_PIN     6

#if USE_REAL_SENSORS
  #include <Adafruit_MLX90640.h>
  #include <Adafruit_BME680.h>
  #include <SensirionI2CScd4x.h>
  #include <max6675.h>

  Adafruit_MLX90640 mlx;
  Adafruit_BME680 bme;
  SensirionI2CScd4x scd4x;
  MAX6675 tc1(12, 10, 13);
  MAX6675 tc2(12, 11, 13);
  MAX6675 tc3(12, 14, 13);
#endif

// ============================================================================
// STRUKTUR DATA TELEMETRI
// ============================================================================
struct TelemetryFrame {
  float co_ppm;
  float co2_ppm;
  float voc_kohm;
  float ch4_ppm;
  float pm25_ug_m3;
  float wind_ms;
  float tc_c[3];
  float mlx_min_c;
  float mlx_avg_c;
  float mlx_max_c;
  float mlx_matrix[768]; // 32 kolom x 24 baris
};

bool demoNormalMode = DEFAULT_DEMO_NORMAL;
String serialCmdBuffer = "";
TelemetryFrame currentFrame;

float randFloat(float minVal, float maxVal) {
  long r = random(0, 10001);
  return minVal + (maxVal - minVal) * (r / 10000.0f);
}

// ============================================================================
// GENERATOR DEMO NORMAL (KONDISI AMAN / TIDAK ADA API)
// ============================================================================
void generateDemoNormalFrame(TelemetryFrame &f) {
  float t = millis() / 1000.0f;

  // Baseline gas & udara bersih kompartemen (NOMINAL)
  f.co_ppm     = 4.20f + 0.65f * sinf(t * 0.4f) + randFloat(-0.25f, 0.25f);
  f.co2_ppm    = 438.0f + 12.0f * sinf(t * 0.15f) + randFloat(-3.0f, 3.0f);
  f.voc_kohm   = 210.0f + 10.0f * cosf(t * 0.2f) + randFloat(-2.5f, 2.5f);
  f.ch4_ppm    = 0.22f + 0.04f * sinf(t * 0.3f) + randFloat(-0.02f, 0.02f);
  f.pm25_ug_m3 = 8.40f + 1.50f * sinf(t * 0.25f) + randFloat(-0.50f, 0.50f);
  f.wind_ms    = 0.052f + 0.012f * sinf(t * 0.5f) + randFloat(-0.004f, 0.004f);

  // Thermocouple K [1..3] pada suhu ruang normal (~26-27.5 °C)
  f.tc_c[0] = 26.50f + 0.20f * sinf(t * 0.12f) + randFloat(-0.08f, 0.08f);
  f.tc_c[1] = 27.15f + 0.25f * cosf(t * 0.10f) + randFloat(-0.08f, 0.08f);
  f.tc_c[2] = 25.90f + 0.18f * sinf(t * 0.14f) + randFloat(-0.08f, 0.08f);

  // Matriks Termal MLX90640 (32x24): Suhu ruang merata + kehangatan PCB ringan (~32°C)
  float ambientBase = 26.80f + 0.25f * sinf(t * 0.1f);
  float minT = 999.0f;
  float maxT = -999.0f;
  double sumT = 0.0;

  float warmCx = 15.5f + 0.3f * sinf(t * 0.2f);
  float warmCy = 11.5f + 0.2f * cosf(t * 0.2f);

  for (int row = 0; row < 24; row++) {
    for (int col = 0; col < 32; col++) {
      int idx = row * 32 + col;
      float vertGrad = (23 - row) * 0.04f;
      float dx = (float)col - warmCx;
      float dy = (float)row - warmCy;
      float distSq = (dx * dx) / 18.0f + (dy * dy) / 14.0f;
      float electronicsWarmth = 5.2f * expf(-distSq);

      float pixelTemp = ambientBase + vertGrad + electronicsWarmth + randFloat(-0.22f, 0.22f);
      f.mlx_matrix[idx] = pixelTemp;

      if (pixelTemp < minT) minT = pixelTemp;
      if (pixelTemp > maxT) maxT = pixelTemp;
      sumT += pixelTemp;
    }
  }

  f.mlx_min_c = minT;
  f.mlx_max_c = maxT;
  f.mlx_avg_c = (float)(sumT / 768.0);
}

// ============================================================================
// PEMBACAAN SENSOR FISIK AKTUAL (MODE LIVE_SENSOR)
// ============================================================================
void readRealSensors(TelemetryFrame &f) {
#if USE_REAL_SENSORS
  int rawMq7 = analogRead(MQ7_ANALOG_PIN);
  f.co_ppm = max(0.0f, (rawMq7 / 4095.0f) * 100.0f);

  int rawCh4 = analogRead(MQ4_CH4_ANALOG_PIN);
  f.ch4_ppm = max(0.0f, (rawCh4 / 4095.0f) * 50.0f);

  int rawWind = analogRead(ANEMO_ANALOG_PIN);
  f.wind_ms = max(0.0f, (rawWind / 4095.0f) * 5.0f);

  if (bme.performReading()) {
    f.voc_kohm = bme.gas_resistance / 1000.0f;
  }

  uint16_t co2 = 0;
  float tempScd = 0.0f, humScd = 0.0f;
  if (scd4x.readMeasurement(co2, tempScd, humScd) == 0 && co2 > 0) {
    f.co2_ppm = (float)co2;
  }

  f.tc_c[0] = tc1.readCelsius();
  f.tc_c[1] = tc2.readCelsius();
  f.tc_c[2] = tc3.readCelsius();

  if (mlx.getFrame(f.mlx_matrix) == 0) {
    float minT = 999.0f, maxT = -999.0f;
    double sumT = 0.0;
    for (int i = 0; i < 768; i++) {
      float v = f.mlx_matrix[i];
      if (v < minT) minT = v;
      if (v > maxT) maxT = v;
      sumT += v;
    }
    f.mlx_min_c = minT;
    f.mlx_max_c = maxT;
    f.mlx_avg_c = (float)(sumT / 768.0);
  }
#else
  generateDemoNormalFrame(f);
#endif
}

// ============================================================================
// PENGIRIM SERIAL CHUNKED 64-BYTE (KOMPATIBEL HWCDC USB-CDC & UART)
// ============================================================================
void writeSerialChunked(const String &payload) {
  const size_t totalLen = payload.length();
  const size_t chunkSize = 64;
  const uint8_t *buf = (const uint8_t *)payload.c_str();

  size_t offset = 0;
  while (offset < totalLen) {
    size_t toWrite = (totalLen - offset < chunkSize) ? (totalLen - offset) : chunkSize;

    // Tunggu ruang buffer TX tersedia (maksimal 30 ms agar tidak hang jika port tidak dibuka)
    uint32_t waitStart = millis();
    while ((size_t)Serial.availableForWrite() < toWrite && (millis() - waitStart) < 30) {
      delay(1);
    }

    size_t written = Serial.write(buf + offset, toWrite);
    if (written == 0) {
      delay(2);
      written = Serial.write(buf + offset, toWrite);
      if (written == 0) break;
    }
    offset += written;
    delay(1);
  }
  Serial.flush();
}

void sendFullTelemetryJson(const TelemetryFrame &f) {
  String out;
  out.reserve(4600);

  out += "{\"firmware_mode\":\"";
  out += (demoNormalMode ? "DEMO_NORMAL" : "LIVE_SENSOR");
  out += "\",\"co_mq7\":{\"est_ppm\":";
  out += String(f.co_ppm, 2);
  out += "},\"co2_ppm\":";
  out += String(f.co2_ppm, 1);
  out += ",\"bme688\":{\"voc_kohm\":";
  out += String(f.voc_kohm, 2);
  out += "},\"ch4_ppm\":";
  out += String(f.ch4_ppm, 2);
  out += ",\"pm25_ug_m3\":";
  out += String(f.pm25_ug_m3, 2);
  out += ",\"anemometer\":{\"wind_ms\":";
  out += String(f.wind_ms, 3);
  out += "},\"thermocouple_k\":[";
  out += String(f.tc_c[0], 2);
  out += ",";
  out += String(f.tc_c[1], 2);
  out += ",";
  out += String(f.tc_c[2], 2);
  out += "],\"mlx90640\":{\"min_c\":";
  out += String(f.mlx_min_c, 1);
  out += ",\"avg_c\":";
  out += String(f.mlx_avg_c, 1);
  out += ",\"max_c\":";
  out += String(f.mlx_max_c, 1);
  out += ",\"matrix\":[";

  char numBuf[12];
  for (int i = 0; i < 768; i++) {
    snprintf(numBuf, sizeof(numBuf), "%.1f", f.mlx_matrix[i]);
    out += numBuf;
    if (i < 767) {
      out += ',';
    }
  }
  out += "]}}\n";

  writeSerialChunked(out);
}

void sendEventStatus(const char *eventName, const char *detail) {
  String evt = "{\"event\":\"";
  evt += eventName;
  evt += "\",\"mode\":\"";
  evt += (demoNormalMode ? "DEMO_NORMAL" : "LIVE_SENSOR");
  evt += "\",\"detail\":\"";
  evt += detail;
  evt += "\"}\n";
  writeSerialChunked(evt);
}

// ============================================================================
// HANDLER PERINTAH SERIAL
// ============================================================================
void handleCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  if (cmd.equalsIgnoreCase("READ_FULL")) {
    if (demoNormalMode) {
      generateDemoNormalFrame(currentFrame);
    } else {
      readRealSensors(currentFrame);
    }
    sendFullTelemetryJson(currentFrame);
  }
  else if (cmd.equalsIgnoreCase("DEMO_NORMAL") || cmd.equalsIgnoreCase("MODE_DEMO")) {
    demoNormalMode = true;
    sendEventStatus("MODE_CHANGED", "Demo Normal (no fire) activated");
  }
  else if (cmd.equalsIgnoreCase("LIVE_SENSOR") || cmd.equalsIgnoreCase("MODE_LIVE")) {
    demoNormalMode = false;
    sendEventStatus("MODE_CHANGED", "Live hardware sensor mode activated");
  }
  else if (cmd.equalsIgnoreCase("STATUS")) {
    sendEventStatus("STATUS_REPORT", demoNormalMode ? "Running DEMO_NORMAL" : "Running LIVE_SENSOR");
  }
}

// ============================================================================
// SETUP & LOOP
// ============================================================================
void setup() {
  Serial.begin(SERIAL_BAUD);
  randomSeed(esp_random());

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  Wire.setClock(400000);

#if USE_REAL_SENSORS
  mlx.begin(MLX90640_I2CADDR_DEFAULT, &Wire);
  mlx.setMode(MLX90640_CHESS);
  mlx.setResolution(MLX90640_ADC_18BIT);
  mlx.setRefreshRate(MLX90640_4_HZ);

  bme.begin(0x76, &Wire);
  scd4x.begin(Wire);
  scd4x.startPeriodicMeasurement();
#endif

  generateDemoNormalFrame(currentFrame);
  delay(200);
  sendEventStatus("BOOT_READY", demoNormalMode ? "Booted in DEMO_NORMAL mode" : "Booted in LIVE_SENSOR mode");
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (serialCmdBuffer.length() > 0) {
        handleCommand(serialCmdBuffer);
        serialCmdBuffer = "";
      }
    } else {
      serialCmdBuffer += c;
      if (serialCmdBuffer.length() > 128) {
        serialCmdBuffer = "";
      }
    }
  }
}
