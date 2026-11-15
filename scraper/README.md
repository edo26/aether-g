# NASA PSI scraper API

A small FastAPI service that searches the public [NASA Physical Sciences Informatics (PSI)](https://psi.nasa.gov/) repository, reads investigation metadata, lists files, and downloads selected files into `data/raw/`. That folder is ignored by Git.

It calls the JSON API used by the PSI web app (`/geode-py/ws/...`). Public data needs no login. The service needs internet only while scraping; the offline demo uses the files already downloaded.

## Run

```bash
cd scraper
uv sync
uv run uvicorn app.main:app --port 8000
```

Interactive docs are at http://localhost:8000/docs.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/psi/search?q=saffire&size=25&page=0` | Search investigations |
| GET | `/api/psi/fire-safety` | Preset search (Saffire, BASS, smoldering, flame spread, smoke aerosol). Combustion Science results come first |
| GET | `/api/psi/investigations/{PSI-98}` | Cleaned metadata: objective, approach, hypothesis, hardware, dates, DOI, license, publications, related investigations. `/investigations/{PSI-98}/raw` returns the full PSI response |
| GET | `/api/psi/investigations/{PSI-98}/files?ext=xlsx&category=raw&q=telemetry` | Flat file list with optional filters |
| GET | `/api/psi/investigations/{PSI-98}/download-url?file=<name>` | Short-lived presigned S3 URL |
| POST | `/api/psi/investigations/{PSI-98}/download` | Body `{"file": "<name>", "max_mb": 200}`. Saves to `data/raw/PSI-98/` |
| GET | `/api/downloads` | Files already downloaded |
| POST | `/api/rag/brief` | Rebuild the one-page RAG brief |
| GET | `/api/rag/brief.md` / `.json` / `.pdf` | The brief as Markdown (for RAG), JSON (structured), or a single-page PDF |
| GET | `/api/health` | Checks that PSI is reachable |

Downloads are capped at 200 MB per file by default (`PSI_MAX_DOWNLOAD_MB`), because some investigations hold more than 10 GB. A file that is already on disk is not downloaded again unless `"overwrite": true`.

## Example: Saffire-I telemetry

```bash
curl "localhost:8000/api/psi/investigations/PSI-98/files?ext=xlsx&q=telemetry"
curl -X POST localhost:8000/api/psi/investigations/PSI-98/download \
  -H 'Content-Type: application/json' \
  -d '{"file": "PSI-98_Raw Data_Saffire-I Telemetry Data Good Process Version.xlsx"}'
```

## One-page RAG brief

`POST /api/rag/brief` combines every Combustion Science investigation from the fire-safety search with statistics for each xlsx/csv file already in `data/raw/`. It writes the result to `data/rag/psi_fire_safety_brief.{md,json,pdf}`. The PDF is a single A4 page; the font shrinks until everything fits.

The text is extractive; no language model is involved:
- Investigation summaries are trimmed sentences from PSI metadata.
- Each data channel reports baseline, min, max, onset time (first clear departure from baseline), and peak time, in the file's own time base.
- Channels without units in their header are raw, uncalibrated values. The brief says so instead of guessing a physical meaning.
- Placeholder values logged before a sensor was live are removed.

To add more data, download the file and rebuild:

```bash
curl -X POST localhost:8000/api/psi/investigations/PSI-26/download -H 'Content-Type: application/json' -d '{"file": "<name>"}'
curl -X POST localhost:8000/api/rag/brief
```

Every downloaded file makes the page denser, so pick the files that matter.

## Provenance

Record the accession, title, DOI, license, and access date for every file you use (see [data/README.md](../data/README.md)). The metadata endpoint returns these fields.

## Test

```bash
uv run pytest
```

The tests mock PSI and do not use the network.
