# CoastWatch ImpactNet v2

ImpactNet v2 is a leakage-aware coastal-impact research pipeline implemented in the
parallel `coastwatch_impact` package. It does not replace the existing
`coastal_risk` v1 demonstrator, its server route, the website model, or the
deterministic device warning path.

## Current result

The engineering pipeline is implemented and tested end to end on deterministic
synthetic data. The synthetic proof covers data generation, train-only
preprocessing, causal TCN training, validation-only calibration and threshold
selection, frozen-test evaluation, hash-verified `safetensors` export, safe bundle
loading, and a Shadow Mode FastAPI prediction.

This is not a scientific result about UK coastal flooding. No real external
archive was downloaded or imported during this implementation, no reviewed real
confirmed-impact catalogue was built, and no real model was trained or calibrated.
Synthetic metrics must not be reported as real predictive performance.

## Implemented engineering scope

- strict Pydantic configuration with explicit label and data semantics;
- canonical UTC schemas, immutable raw/interim manifests, checksums, provenance,
  missingness, source age, quality, CRS, and vertical-datum guards;
- manual adapters for historic EA warnings, warning areas, flood outlines, EA tide
  archives, WaveNet, NTSLF issued forecasts, and reviewed static geography;
- an opt-in EA tide request path with network access disabled by default;
- explicit Met Office and Copernicus credential/configuration stubs which refuse to
  create data until their exact products, schemas, licences, and archive strategy
  are reviewed;
- human-reviewed site mapping reports with no automatic nearest-station approval;
- A/B/C/U/N label handling, masked hourly onset hazards, global time splits,
  24-hour target purge, event buffers, storm-group isolation, train-only negative
  sampling/event weighting, and leave-one-site-out training folds that keep the
  final test split frozen;
- 72-hour observation windows, 24-hour forecast windows, strict
  `issue_time <= prediction_time < valid_time` selection, train-only preprocessing,
  and lazy PyTorch datasets;
- persistence, physics, and logistic baselines;
- independently trainable observation-only and hybrid causal TCN architectures,
  masked multitask losses, deterministic CPU training utilities, and safe
  resumable checkpoints;
- horizon, calibration, alert-episode, event, bootstrap, water-quantile, and LOSO
  evaluation utilities;
- validation-only temperature calibration and operating-threshold selection;
- non-pickle, hash-verified model bundles and an optional ONNX export API;
- a separate FastAPI application with verified feature/source provenance, explicit
  degraded/OOD states, complete non-secret inference audit logs, and responses that
  retain `shadow_mode=true`;
- report-only operational monitoring, delayed-label event evaluation, and
  PSI/Wasserstein/missingness/site/source-version drift reports which never trigger
  automatic retraining;
- a reproducible synthetic-only CPU E2E command and structured JSON CLI output.

## Tested environment

The current local environment uses Python 3.12 and `torch 2.13.0+cpu`.
`torch.cuda.is_available()` returned `false`; no CUDA/GPU training or performance
claim has been tested.

The optional dependency groups are not installed in the tested environment:

| Extra | Missing packages in this environment | Consequence |
|---|---|---|
| `geo` | GeoPandas, Shapely, PyProj, Rasterio | GeoJSON fallback logic is tested, but GeoPackage/shapefile and full geospatial workflows are not locally exercised. |
| `marine` | Xarray, netCDF4, Zarr, Copernicus Marine client | NetCDF/Zarr and credentialed marine workflows are unavailable. |
| `onnx` | ONNX, ONNX Runtime | ONNX export/parity is unavailable; the code fails with a clear optional-dependency error. |

These extras can be installed from the package definition when external access and
the intended workflow are available, for example
`python -m pip install -e ".\ml[geo,marine,onnx]"`. Installation and end-to-end use
of those extras have not been verified here.

## CLI

After installing `ml` as an editable package, the command name is `cwml`. In the
current workspace it is available as
`.\server\.venv\Scripts\cwml.exe` from the repository root. Every applicable write
command supports `--dry-run`, errors return a non-zero exit code, and command
results are emitted as JSON.

### Audit and configuration

```powershell
.\server\.venv\Scripts\cwml.exe --version
.\server\.venv\Scripts\cwml.exe audit legacy --workspace . --dry-run
.\server\.venv\Scripts\cwml.exe config validate `
  --config .\ml\configs\synthetic_phase1.yaml --dry-run
```

### Synthetic engineering data and E2E proof

```powershell
.\server\.venv\Scripts\cwml.exe data synthetic `
  --output .\ml\artifacts\synthetic-data --duration-days 180
.\server\.venv\Scripts\cwml.exe dataset summary `
  --dataset .\ml\artifacts\synthetic-data
.\server\.venv\Scripts\cwml.exe dataset audit-leakage `
  --dataset .\ml\artifacts\synthetic-data
.\server\.venv\Scripts\cwml.exe dataset loso-summary `
  --dataset .\ml\artifacts\synthetic-data
.\server\.venv\Scripts\cwml.exe e2e synthetic `
  --output .\ml\artifacts\synthetic-e2e --duration-days 180 --epochs 2
```

All outputs from these commands are explicitly marked synthetic-only and
non-deployable as real warning models.

### Manual real-source import paths

The commands below require source files obtained and reviewed by the project. Their
presence documents an import contract; it does not mean that the real files were
downloaded or validated in this environment.

```powershell
cwml data import-historic-warnings --input <path> --data-root <data-root> --dry-run
cwml data import-warning-areas --input <path> --data-root <data-root> --dry-run
cwml data import-flood-outlines --input <path> --data-root <data-root> --dry-run
cwml data import-ea-tide-archive --input <path> --data-root <data-root> --dry-run
cwml data import-wavenet --input <path> --data-root <data-root> --dry-run
cwml data import-ntslf-forecast --input <path> --data-root <data-root> --dry-run
cwml data import-static-geo --input <path> --data-root <data-root> --dry-run
```

The EA tide network path is intentionally opt-in:

```powershell
cwml data sync-ea-tide --data-root <data-root> --station-id <id> --dry-run
cwml data sync-ea-tide --data-root <data-root> --station-id <id> --allow-network
```

No real network request was made as part of the completed engineering proof.

### Dataset, training, calibration, frozen test, and replay

The same commands work with reviewed real canonical tables. The example paths below
are placeholders; use `--dry-run` first, and do not run `final-test` until every
choice is frozen.

```powershell
cwml dataset build --input <canonical-data> --config <config.yaml> `
  --feature-schema <feature-schema.json> --output <dataset>
cwml train impactnet --dataset <dataset> --config <config.yaml> `
  --output <run> --variant obs_only_tcn
cwml calibrate temperature --run <run>
cwml thresholds select --run <run>
cwml evaluate run --run <run> --split validation
cwml evaluate final-test --run <run>
cwml export bundle --run <run> --output <bundle> `
  --model-version <version> --coverage-scope <reviewed-scope>
cwml replay shadow --bundle <bundle> --input <requests.jsonl> `
  --output <responses.jsonl>
```

`hybrid_tcn` additionally requires genuine historically issued forecasts with
per-feature source bindings and complete issue/run/valid-time provenance.

### Site review, bundle verification, and service

```powershell
cwml sites review-report `
  --legacy-locations .\ml\coastal_risk\locations.json `
  --sites-config .\ml\configs\sites.yaml --output <review.csv> --dry-run
cwml sites validate-review --review-csv <review.csv>
cwml export verify --bundle <bundle-directory>
cwml serve --bundle <bundle-directory> --dry-run
cwml serve --bundle <real-reviewed-bundle> --host 127.0.0.1 --port 8000
cwml serve --bundle <synthetic-bundle> --allow-synthetic --host 127.0.0.1 --port 8000
```

`serve` loads only a verified bundle. The site endpoint additionally requires an
explicit live feature provider; it does not invent probabilities when data are
insufficient.

Monitoring commands are documented in `docs/MONITORING_RUNBOOK.md`:

```powershell
cwml monitor aggregate --logs <shadow.jsonl> --output <operational.json> --data-kind real
cwml monitor delayed-evaluate --logs <shadow.jsonl> `
  --event-catalog <event_catalog.parquet> --output <delayed.json> --data-kind real
cwml monitor drift --reference-logs <reference.jsonl> --live-logs <shadow.jsonl> `
  --output <drift.json>
```

## Verification

The final local suite collected 142 tests: 141 passed and the opt-in 180-day CI
acceptance test skipped because its already-completed persistent run is kept as a
separate long-running artifact:

```powershell
cd .\ml
..\server\.venv\Scripts\python.exe -m pytest -q tests\impactnet `
  --basetemp=tmp\pytest-impactnet -p no:cacheprovider
```

The ordinary suite includes a fast 12-day/one-epoch synthetic E2E proof, actual CPU
smoke training for both TCN variants, the complete CLI lifecycle, leakage tests,
bundle/API/replay tests, and monitoring jobs. The persistent 180-day/two-epoch CPU
run is `artifacts/runs/synthetic-e2e-20260813-final`; its 71-file run inventory and
bundle hashes verify, and its 21 required plots plus three event timelines are
generated from saved prediction evidence. Set `COASTWATCH_FULL_E2E=1` to opt the
long acceptance test into a scheduled CI job. These results prove software
continuity and safety guards, not real-world accuracy.

## Work still blocked on external evidence

A real confirmed-impact run still requires:

1. downloaded, licensed, checksummed source archives;
2. approved England coastal-zone, warning-area, gauge, and wave-station mappings;
3. human-reviewed real A/B impact labels and defensible N review intervals;
4. historic issued forecast runs with both issue and valid times;
5. verified vertical-datum relationships for tide, ground, and defence heights;
6. train/validation/test coverage containing enough independent storm groups;
7. installed optional dependencies for the selected real-data workflow;
8. independent scientific review before any operational interpretation.

Until then, official warnings remain authoritative and all v2 service output is
research-only Shadow Mode.

See `IMPLEMENTATION_STATUS.md`, `ASSUMPTIONS.md`, `docs/DATA_CARD.md`,
`docs/LABEL_CARD.md`, and `reports/legacy_audit.md` for the detailed evidence
boundary.
