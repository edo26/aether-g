# Backend implementation plan (Edge AI service)

**Owner:** Fachri. **Executor:** Claude Code on the `zerodays` host.
**Goal:** build the AETHER-G backend that receives sensor telemetry from the Jetson Nano, decides fire status instantly with a rule-based detector, stores everything, asks a local Gemma model (served by oMLX) for a crew advisory, and exposes one API for Edo's dashboard.

Read these first; they are binding and this plan does not repeat all of their detail:
[`docs/architecture.md`](architecture.md) (JSON contract), [`edge-ai/README.md`](../edge-ai/README.md) (detector + Gemma rules), [`simulator/`](../simulator/) (reference behaviour), [`data/README.md`](../data/README.md) (provenance rules), [`scraper/`](../scraper/) (NASA PSI scraper and RAG brief, already built).

---

## 1. Architecture

```text
Jetson Nano (sensors)
   │  HTTP POST, one contract JSON object per 500 ms
   ▼
Backend: FastAPI on zerodays  ─────────────────────────────────────────────┐
   1. validate contract                                                    │
   2. Stage-1 detector (rule-based, per compartment, < 1 ms)               │
   3. write telemetry + detector result ──► InfluxDB 2.x (Docker)          │
   4. publish to live stream (SSE)                                         │
   5. on trigger: Advisor worker                                           │
        read last 60 s window from InfluxDB                                │
        + retrieve NASA facts (local RAG over data/rag/)                   │
        ──► oMLX on Heri's Mac (Gemma, via NetBird) ──► advisory           │
        ──► SQLite (advisories, events, chat)  ──► publish to SSE          │
   ▼                                                                       │
Dashboard (Edo) ◄── REST + SSE from this backend only ◄────────────────────┘
```

Network: Jetson, zerodays and Heri's Mac are peers on the team's **NetBird** network. All addresses are configuration, never hard-coded.

### Design decisions (do not change without asking Fachri)

| Decision | Reason |
| --- | --- |
| Fire status comes from the **Stage-1 detector**, never from the LLM | `edge-ai/README.md`: the rule-based warning must appear immediately; Gemma explains, it cannot override. If oMLX is slow or down, alarms still work. |
| **InfluxDB** for telemetry and detector results; **SQLite** for advisories, events and chat | Influx is built for 2 Hz time series; long LLM text and relational history fit SQLite. |
| Dashboard talks to the **backend API only** | Browsers cannot read SQLite, and an Influx token in frontend code would be public. |
| The LLM is called **on triggers**, not per sample | Avoids a growing queue on Heri's Mac. Single-flight: at most one generation per compartment at a time. |
| RAG embeds **NASA facts**, not telemetry | Telemetry stays a time series; retrieval is over curated, cited facts. |
| Backend **recomputes** `stage1_detector` | Incoming `stage1_detector` (from simulator/firmware) is provisional and ignored. |

---

## 2. Repository layout

Put the service in `edge-ai/` (that folder is Fachri's per the README). Keep `edge-ai/README.md`, update it at the end.

```text
edge-ai/
├── README.md
├── pyproject.toml            # uv project, Python >= 3.11
├── docker-compose.yml        # influxdb + backend
├── Dockerfile
├── .env.example              # every setting below, no real secrets
├── app/
│   ├── main.py               # FastAPI app, lifespan, routers
│   ├── config.py             # pydantic-settings, env prefix AETHER_
│   ├── contract.py           # pydantic models = docs/architecture.md, exactly
│   ├── detector.py           # Stage-1, per-compartment state
│   ├── storage/
│   │   ├── influx.py         # async write (batched) + window queries
│   │   └── sqlite.py         # schema, migrations, DAO
│   ├── advisor/
│   │   ├── rag.py            # load + retrieve facts from data/rag/
│   │   ├── omlx.py           # LLM client (OpenAI-compatible, see section 6)
│   │   ├── prompts.py
│   │   ├── fallback.py       # deterministic advisory text when the LLM fails
│   │   └── worker.py         # triggers, single-flight, persistence
│   ├── stream.py             # in-process pub/sub -> SSE
│   └── routers/              # ingest.py, dashboard.py, advisor.py, health.py
├── tools/
│   └── feed.py               # fake Jetson: pipes simulator output into POST /ingest
└── tests/
```

---

## 3. Contract (must match `docs/architecture.md` exactly)

- Envelope: `timestamp_ms` (int, elapsed ms from session start), `mode` (`LIVE_IOT` | `NASA_REPLAY`), `compartment_id` (str), `telemetry`, `stage1_detector`.
- `telemetry`: `co_ppm`, `co2_ppm`, `ch4_ppm`, `pm25_ug_m3`, `airflow_cm_s`, `max_ir_temp_c`, `smolder_velocity_mm_s` (floats), `voc_index` (int), `thermocouples_c` (exactly 3 floats). All values finite (reject NaN/Infinity).
- `stage1_detector.status`: only `NOMINAL` or `SMOLDERING_ANOMALY`. **No third status.**
- `stage1_detector.false_alarm_dust_check`: only `NOMINAL_NO_EVENT` or `DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE`. Dust keeps status `NOMINAL`. During an anomaly the dust check is `NOMINAL_NO_EVENT`.
- Do **not** add keys to the envelope. Advisories, provenance and thermal images travel separately.
- `stage1_detector` is optional on ingest (Jetson may omit it); the backend always returns and stores its own.

---

## 4. Stage-1 detector (`app/detector.py`)

State per `compartment_id`; derivatives use `timestamp_ms`, not arrival time.

- **Session reset:** clear state when `mode` changes, when `timestamp_ms` goes backwards, or after 30 s with no data.
- **Rolling baseline:** first 5 s of a session (10 samples at 500 ms), median per channel.
- **Smoldering trigger** (mirrors `simulator.py`): `co_ppm > 25` and `dCO/dt > 0` ppm/s. Once triggered, **latch** `SMOLDERING_ANOMALY` for the session.
- **Confidence:** nominal `0.95`; anomaly `0.6 + 0.35 * min((co_ppm - 25) / 100, 1)` rounded to 3 decimals.
- **Dust rejection:** `pm25_ug_m3` rises more than 35 µg/m³ above baseline **and** CO ≤ 25 ppm **and** max thermocouple / IR temperature within 5 °C of baseline. Result: status `NOMINAL` with `DUST_EVENT_REJECTED_NO_THERMAL_GAS_RISE`. Gas/thermal anomaly takes precedence. (The simulator decides dust by scenario name; production must decide from data.)
- **Supporting features**, computed and logged but **not** triggers yet: ΔCO/ΔCO2 vs baseline (skip when ΔCO2 is below noise floor, start with 20 ppm), VOC rise over baseline, smolder velocity.
- Expose `features` (dco_dt, ratio, voc_delta, pm_delta, temp_delta) in the ingest response and Influx, **not** in the contract envelope.

**Acceptance:** feeding each simulator scenario through the detector reproduces the simulator's documented results: `nominal` and `airgapped` → always `NOMINAL` 0.95; `dust` → `NOMINAL` + dust marker; `smoldering` → trips at 10.5 s (CO ≈ 26 ppm), confidence ~0.6 → 0.95, stays latched after the plateau.

---

## 5. Storage

### InfluxDB 2.7 (Docker)

`docker-compose.yml`: `influxdb:2.7` with a named volume, `DOCKER_INFLUXDB_INIT_*` env for org `aether`, bucket `telemetry`, admin token from `.env`. Retention 7 days. Memory limit 512 MB.

Write points (batched, flush every 1 s, async client `influxdb-client[async]`):

| Measurement | Tags | Fields |
| --- | --- | --- |
| `telemetry` | `compartment_id`, `mode`, `source` (`jetson` / `simulator` / `replay`) | every telemetry value; thermocouples as `tc1_c`, `tc2_c`, `tc3_c`; `session_ts_ms` |
| `detector` | `compartment_id`, `mode` | `status` (string), `anomaly` (0/1), `confidence`, `dust_rejected` (0/1), each feature |

Use **server receive time** as the Influx timestamp (Jetson clock may drift); keep `timestamp_ms` as a field.

If Influx is down: keep serving the detector and SSE, buffer up to 5 minutes in memory, log loudly, report it in `/health`. Ingest must never fail because storage is down.

### SQLite (`data/aether.db`, WAL mode)

```sql
advisories(id INTEGER PK, created_at TEXT, compartment_id TEXT, trigger TEXT,        -- status_change | periodic | ask
           status TEXT, confidence REAL, window_summary TEXT, facts_used TEXT,       -- JSON
           model TEXT, latency_ms INTEGER, first_token_ms INTEGER,
           warning TEXT, steps TEXT, citations TEXT, raw_text TEXT, fallback INTEGER)
events(id INTEGER PK, created_at TEXT, compartment_id TEXT, kind TEXT, detail TEXT)  -- status transitions, session resets, storage/LLM outages
chat(id INTEGER PK, created_at TEXT, compartment_id TEXT, role TEXT, content TEXT, advisory_id INTEGER)
```

---

## 6. Advisor (oMLX + RAG)

### oMLX client

oMLX runs on Heri's Mac (Apple Silicon) and is reached over NetBird at `AETHER_OMLX_BASE_URL`.
**First task: verify its API.** It is expected to be OpenAI-compatible: check `GET {base}/v1/models` and a `POST {base}/v1/chat/completions` with curl, then record the real model id and endpoint shape in `edge-ai/README.md`. If it differs, change only `advisor/omlx.py`.

Call settings: `temperature 0.0`, `max_tokens 256`, streaming on, connect timeout 3 s, **first-token timeout 6 s**, total timeout 20 s. Record model id and quantization in each advisory row.

### Triggers and flow (`advisor/worker.py`)

- Triggers: (a) status transition `NOMINAL → SMOLDERING_ANOMALY`, (b) a dust rejection event (deterministic text only, no LLM), (c) every 30 s while an anomaly persists, (d) `POST /api/v1/ask`.
- Single-flight per compartment: if a generation is running, a new trigger of the same priority is dropped; an escalation cancels and replaces it.
- Window summary: query InfluxDB for the last 60 s of that compartment → min/max/last/slope per channel + detector status. Send this summary, not raw samples.
- Retrieve top 4 facts (see RAG), build the prompt, stream from oMLX, publish tokens on SSE, parse the output into `warning`, `steps` (exactly 3), `citations`, store in SQLite, publish `advisory`.
- **Fallback:** if oMLX errors or misses the first-token timeout, store and publish a deterministic advisory from `fallback.py` with `fallback=1`. The dashboard must never wait on the LLM to show the alarm.

### Prompt rules (from `edge-ai/README.md`)

- Use only the window summary, the authoritative Stage-1 result and the retrieved facts. Never invent numbers or NASA procedures; if a citation or procedure is missing, say so.
- Output format: `WARNING:` one sentence; `1.` `2.` `3.` crew steps; `SOURCES:` fact ids.
- Keep demo thresholds (25 ppm etc.) clearly separate from NASA findings. Operational steps are demo guidance, **not** an official NASA checklist; label them that way.

### RAG (`advisor/rag.py`)

- Source: `data/rag/psi_fire_safety_brief.json`, produced by `scraper/` (`POST /api/rag/brief`). It is a single brief; split it into fact chunks at load time: one chunk per investigation (id = accession) and one per data sheet timeline (id = `accession:file:sheet`). Each chunk keeps DOI, license and URL for citations.
- Retrieval v1: BM25 or keyword scoring in pure Python (plain JSON is allowed by `edge-ai/README.md`). Optional v2: embeddings, only if oMLX exposes an embeddings endpoint; never call an internet service.
- Add a small hand-curated `data/rag/facts_manual.json` for cited findings that are not in PSI, e.g. the Saffire VI NTRS report already cited in `docs/demo-script.md`. Every entry needs a source URL. Do not add uncited claims.

---

## 7. API

All under `/api/v1`, OpenAPI docs at `/docs` with realistic examples. CORS open on the private network.

| Method | Path | Who | Purpose |
| --- | --- | --- | --- |
| POST | `/ingest` | Jetson | One contract object, or `{"items": [...]}` up to 100. Header `X-Device-Token` must equal `AETHER_DEVICE_TOKEN`. Returns the authoritative `stage1_detector` + `features`. Target < 20 ms. |
| GET | `/status` | Dashboard | Per compartment: latest telemetry, Stage-1 result, last-sample age, latest advisory. |
| GET | `/telemetry?compartment_id=&range=5m&every=2s` | Dashboard | Time series from Influx for charts (downsampled). |
| GET | `/advisories?compartment_id=&limit=20` | Dashboard | From SQLite. |
| GET | `/events?limit=50` | Dashboard | Status transitions and outages. |
| POST | `/ask` | Dashboard | `{compartment_id, question}`; the answer streams on SSE and is saved to `chat`. |
| GET | `/stream` | Dashboard | **SSE.** Events: `telemetry` (throttled to 2 Hz per compartment), `status_change`, `advisory_start`, `advisory_token`, `advisory_done`, `health`. |
| GET | `/health` | Everyone | Influx, SQLite, oMLX reachability and model id, last-ingest age per compartment, buffer size. |

Write `edge-ai/API.md` for Edo with example payloads for every endpoint and SSE event.

---

## 8. Configuration (`.env.example`)

```
AETHER_DEVICE_TOKEN=change-me
AETHER_INFLUX_URL=http://influxdb:8086
AETHER_INFLUX_ORG=aether
AETHER_INFLUX_BUCKET=telemetry
AETHER_INFLUX_TOKEN=change-me
AETHER_SQLITE_PATH=data/aether.db
AETHER_OMLX_BASE_URL=http://<heri-mac-netbird-ip>:<port>
AETHER_OMLX_MODEL=<model id from /v1/models>
AETHER_OMLX_API_KEY=
AETHER_RAG_BRIEF=../data/rag/psi_fire_safety_brief.json
AETHER_RAG_MANUAL=../data/rag/facts_manual.json
AETHER_ADVISORY_PERIOD_S=30
AETHER_LLM_FIRST_TOKEN_TIMEOUT_S=6
AETHER_CORS_ORIGINS=*
```

---

## 9. Phases and acceptance

Work on a branch (`feat/edge-backend`), small commits, one PR at the end. Run tests after each phase.

| Phase | Deliverable | Done when |
| --- | --- | --- |
| 0 | Scaffold, config, contract models, `docker compose up influxdb` | Contract model accepts every simulator line and rejects: 2 thermocouples, NaN, unknown status, missing field |
| 1 | Detector + `POST /ingest` (no storage yet) + `tools/feed.py` | All four simulator scenarios reproduce section 4 acceptance, as unit tests and via `feed.py` |
| 2 | InfluxDB writes + `/telemetry` + `/status` + SSE `telemetry`/`status_change` | 10 min of `feed.py` at 2 Hz stored; Influx stop/start loses no detector output and `/health` shows the outage |
| 3 | SQLite + advisor with **fallback only** + RAG retrieval | Smoldering run produces one `status_change` and one fallback advisory with citations from `data/rag/` |
| 4 | oMLX client + streaming + timeouts | Verified against Heri's oMLX. Measure trigger → first token and → complete advisory on a warm model; target < 2 s complete. Record the measured numbers, do not claim the target if missed. Killing oMLX mid-generation falls back cleanly. |
| 5 | `/ask`, `/events`, `API.md`, README update, Docker image | Edo can build the dashboard from `API.md` alone; `docker compose up` starts everything |
| 6 | Rehearsal | Jetson posts real data over NetBird; full run of `docs/demo-script.md` |

Tests must run offline: mock oMLX (httpx `MockTransport`) and use an Influx test container or a fake writer. CI-style command: `uv run pytest`.

---

## 10. Constraints

- Do not change the JSON contract, the two statuses or the dust-check values.
- Do not commit secrets, `.env`, `data/aether.db`, or anything under `data/raw/`. `data/rag/` text is allowed.
- Do not modify `scraper/` beyond what the RAG loader needs; ask first.
- Label synthetic data as synthetic everywhere (`source=simulator`). `NASA_REPLAY` mode alone does not mean the data is from NASA.
- Never call internet services at runtime. Everything is local or on NetBird.
- Keep the demo honest: confidence values are illustrative, thresholds are demo thresholds, advisories are not official NASA procedures.

---

## 11. Open questions and risks (raise with the team)

1. **oMLX API shape and model id** are unverified (Phase 4 starts by checking them).
2. **NetBird during the Wi-Fi-off demo:** NetBird needs its management server to establish peers. For the "air-gapped" moment, test whether existing tunnels survive without internet; otherwise run Jetson, zerodays and Heri's Mac on one local switch/hotspot and use LAN IPs.
3. **Jetson payload:** confirm with Heri that it sends exactly the contract (3 thermocouples, `voc_index` integer) and which `compartment_id` it uses; agree on the device token.
4. **Data gaps in the docs vs PSI** (see `scraper/README.md` and the RAG brief): PSI has Saffire I-III only (IV-VI are not in PSI), USML-1 in PSI is fluid physics (STDCE), PSI has no smoldering investigation, and the Saffire-I telemetry file has no CO/CO2 channel. Claims in the pitch (e.g. "CO 89-3,900 ppm") need a cited source before they enter RAG.
5. **NASA replay file** in contract format is still to be built from PSI data (Fachri's task in `data/README.md`); only channels that were really measured may be filled.
