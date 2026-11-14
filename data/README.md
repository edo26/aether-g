# NASA data and provenance

NASA research provides the scientific context for AETHER-G's local RAG facts, risk matrix, and future telemetry replays. **This scaffold includes no downloaded datasets; simulator outputs are synthetic.** Dataset ingestion and validation remain implementation work.

Start with the [NASA Physical Sciences Informatics (PSI) portal](https://psi.nasa.gov/), the [PSI investigation catalog](https://www.nasa.gov/physical-sciences-informatics-psi/psi-investigations-by-research-area/), and the [PSI researcher's guide](https://www.nasa.gov/science-research/for-researchers/researchers-guide-to-physical-sciences-informatics-system/).

## Sources to use

| NASA source | What it contributes | Discovery / source pointer |
| --- | --- | --- |
| **Saffire I–VI** — Spacecraft Fire Experiments | Practical-scale microgravity fire behavior, flame spread, gas hazards, and detection context | Search PSI for `SAFFIRE`; begin with [Saffire I, PSI-98](https://psi.nasa.gov/physci/repo/data/investigations/PSI-98). Use the [NASA series overview](https://www.nasa.gov/humans-in-space/flame-burns-out-on-nasas-long-running-spacecraft-fire-experiment/) and [Saffire VI preliminary results](https://ntrs.nasa.gov/citations/20240002981) for published findings. Verify dataset availability separately for each flight. |
| **BASS / BASS-II** — Burning and Suppression of Solids | Solid-fuel burning, airflow effects, and suppression context for retrieved facts and the risk matrix | Search the [PSI investigation catalog](https://www.nasa.gov/physical-sciences-informatics-psi/psi-investigations-by-research-area/) for `BASS` and `BASS-II`; preserve the selected experiment and run identifiers. |
| **USML-1** — First United States Microgravity Laboratory | Historical microgravity transport context from relevant investigations | Search PSI/catalog for `USML-1`. This is a mission/payload umbrella, not one combustion dataset; the catalog includes STDCE-1, a fluid-physics investigation. Identify a relevant record before using it, and do not treat convection measurements as cable-fire telemetry. |

Saffire VI reports found that increases in CO/CO2 could be more significant than the overall cabin temperature rise ([NASA report](https://ntrs.nasa.gov/citations/20240002981)). That supports studying gas sensing alongside thermal sensing; it does not establish this simulator's 25 ppm trigger, 60-second ramp, or detection lead time.

## Keep the repository small and traceable

Do **not** commit large binaries, raw experiment videos, bulk instrument downloads, model weights, or generated retrieval databases. Keep raw material in ignored `data/raw/` or `data/downloads/`, or in external storage. Commit only small, useful text extracts, replay fixtures, and provenance after review.

For each future curated fact or replay, record:

- NASA investigation/run identifier, title, source URL, and access date.
- Publication/figure/page or original file reference, with applicable usage terms.
- Experimental conditions, fuel/material, sensor locations, units, and time base.
- Original file checksum and all extraction, unit-conversion, resampling, or normalization steps.
- Which contract channels are measured, derived, synthetic, or unavailable.

Store provenance beside the replay or RAG facts; do not add keys to the shared telemetry envelope. The `NASA_REPLAY` mode alone does not prove that a sample came from NASA. Clearly identify synthetic fixtures and do not fabricate missing measurements when mapping real runs into the contract.

## Next steps

Fachri curates the first small Saffire/BASS fact set and a compatible replay; Edo adds citations and source labels to the dashboard. Heri checks units and signal comparability against the bench sensors. Validate the selected USML-1 investigation's relevance before adding it to RAG or the risk matrix.
