# CoastWatch ImpactNet v2 Implementation Status

Last updated: 2026-08-13

## Executive status

ImpactNet v2 is engineering-complete for the synthetic validation milestone. Its
data contracts, leakage guards, model families, training/evaluation utilities,
verified bundle, Shadow API, CLI, and deterministic CPU synthetic E2E path are
implemented.

The project is not scientifically complete. No external real dataset was downloaded
or imported in this implementation, no real confirmed-impact event catalogue was
reviewed, and no real model was trained, calibrated, or evaluated. There is no
operational deployment approval.

## Milestone status

| Milestone | Engineering status | Real-data/scientific status | Evidence |
|---|---|---|---|
| M0 legacy audit and v1 protection | Complete | Existing v1 evidence preserved; not promoted to v2 impact evidence | `reports/legacy_audit.md`, SHA-256 regression test |
| M1 package, config, schemas, manifests, synthetic data | Complete | Synthetic fixtures only | `coastwatch_impact/config.py`, `data/`, `configs/` |
| M2 source adapters | Manual import contracts, immutable storage, validation guards, and opt-in EA tide path complete | No real archive downloaded/imported; Met Office and Copernicus downloads intentionally disabled pending product/schema/licence review | `data/sources/`, source-adapter tests |
| M3 site registry, event catalogue, and labels | Construction, review, A/B/C/U/N masking, and validation code complete | England candidate mappings are not approved; no reviewed real impact catalogue exists | `data/spatial.py`, `data/labels.py`, `data/label_builder.py` |
| M4 leakage-safe dataset and baselines | Complete | Tested on fixtures, not on a real UK dataset | `data/dataset.py`, `data/split.py`, `models/baselines.py` |
| M5 causal TCN and training | Observation-only and hybrid architectures are independently CPU smoke-trained; losses, checkpoints, resume, event weighting, and deterministic training utilities complete | Synthetic engineering evidence only; no real training run and no GPU validation | `models/`, `training/`, `workflow.py`, `synthetic_e2e.py` |
| M6 calibration and evaluation | Complete | Validation/test discipline proven on synthetic fixtures only; no scientific metric is claimed | `evaluation/` |
| M7 verified bundle and Shadow API | Bundle, provenance-bound features, degraded routes, replay, audit logging, and report-only monitoring complete | Only synthetic non-real-deployable bundle flow tested; no production warning service | `export/model_bundle.py`, `serve/`, `monitoring/` |
| M8 CLI and final engineering documentation | Complete generic label→dataset→train→calibrate→threshold→frozen-test→bundle workflow and runbooks | Must be revised when real source evidence and reviewed labels arrive | `cli.py`, `workflow.py`, `README_IMPACTNET.md`, `docs/` |

## Implemented source boundaries

Implemented manual import paths cover historic EA warnings, warning areas, flood
outlines, EA tide archives, WaveNet, NTSLF issued forecasts, and reviewed static
geography. These paths validate timestamps, CRS/datum semantics, checksums, and
versioned raw/interim artifacts. They were exercised with controlled test fixtures,
not official downloaded production files.

The EA tide live path is network-disabled unless `--allow-network` is explicitly
set. Its HTTP behavior was tested with a mock transport; no live EA request was made
for this milestone.

Met Office DataHub and Copernicus Marine classes currently validate credentials and
request configuration, then deliberately refuse downloads. This prevents fake or
schema-ambiguous forecast data from entering a backtest.

## Test status

On 2026-08-13 the final local suite collected 142 tests:

```text
141 passed, 1 skipped (opt-in 180-day scheduled/full-CI rerun)
```

The suite covers configuration, schemas, manifests, source fixtures, spatial
mapping, label masks, forecast as-of selection, split/group leakage, preprocessing,
datasets, baselines, both model variants, masked losses, training smoke, calibration,
thresholds, event/LOSO/bootstrap/water evaluation, bundles, API behavior, CLI, and a
reproducible synthetic E2E proof.

The persistent required-size engineering proof used three synthetic sites, 180
days, two CPU epochs, validation-only calibration/thresholds, one frozen test,
bundle/API loading, and a 71-file verified run inventory. It generated all required
PR/reliability/event/water plots and three evidence-backed event timelines. This is
not a scientific performance run. The ordinary suite uses a shorter fixture; the
full rerun is opt-in via `COASTWATCH_FULL_E2E=1`.

The optional ONNX test verifies a clear missing-dependency failure. It is not an
ONNX export/parity result.

The M0 audit recorded the pre-v2 v1 baselines as 3/3 ML tests, 40/40 server tests,
2/2 website tests, website lint, and website production build. The current v1
artifact hashes remain protected by an ImpactNet regression test.

## Runtime and optional dependencies

- Tested PyTorch: `2.13.0+cpu`.
- CUDA availability: `false`.
- GPU correctness, speed, mixed precision, and reproducibility: not tested.
- Optional `geo` group: not installed.
- Optional `marine` group: not installed.
- Optional `onnx` group: not installed.

Consequently, GeoPackage/shapefile workflows, NetCDF/Zarr/Copernicus workflows, and
ONNX export/parity remain unverified in this local environment.

## Current scientific status

- Current v2 evidence is synthetic engineering evidence only.
- No external real source archive has been incorporated.
- No confirmed-impact scientific training run has been performed.
- No real UK event probability has been produced or calibrated.
- No official warning comparison or operational backtest has been completed.
- No real performance, calibration, event recall, false-alert, or lead-time claim is
  supported.
- Every v2 service response must retain `shadow_mode=true`.
- Synthetic bundles carry `synthetic_data=true` and `deployable_as_real=false`.
- Drift and delayed-label reports never trigger retraining or bundle promotion.

## External blockers

1. Access, licensing, download, and checksum capture for the selected real sources.
2. Approved site/zone/station mappings after human review.
3. A real reviewed A/B impact event catalogue plus reviewed N intervals.
4. Historic issued forecast archives with issue-time provenance.
5. Verified vertical-datum conversions and historically appropriate static
   snapshots.
6. Sufficient independent storms for stable time, event, and LOSO evaluation.
7. Installation and validation of the optional dependency group required by each
   chosen source.
8. Scientific and operational review outside this engineering implementation.
