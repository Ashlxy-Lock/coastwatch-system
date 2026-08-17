# CoastWatch ML Changelog

## 2026-08-13 - ImpactNet v2 synthetic engineering milestone

### Legacy protection

- Audited and SHA-256-protected the v1 dataset, report, model, locations, trainer,
  server inference code, and website model/page.
- Kept `coastal_risk`, `/api/v1/risk`, the website model, and existing v1 artifacts
  separate from ImpactNet v2.
- Recorded that the workspace root has no Git metadata and prevented invented commit
  provenance.

### Data and provenance

- Added the parallel `coastwatch_impact` package, strict YAML configuration, canonical
  schemas, raw/interim manifests, immutable content-addressed storage, SHA-256
  verification, and environment provenance.
- Added deterministic three-site synthetic data with explicit synthetic-only and
  non-scientific markers, missing/stale inputs, issued forecasts, events, and a lazy
  sample index.
- Added strict UTC parsing, source-age and missingness handling, forecast as-of
  selection, quality checks, CRS checks, and vertical-datum mismatch guards.
- Added manual adapters for historic EA warnings, warning areas, flood outlines, EA
  tide archives, WaveNet, NTSLF issued forecasts, and reviewed static geography.
- Added an opt-in EA tide network adapter, with no network access by default and no
  raw artifact written for empty responses.
- Added Met Office and Copernicus credential/configuration guards that deliberately
  refuse unreviewed downloads instead of creating placeholder data.
- Added site-mapping review generation and validation without automatic station
  approval.

### Labels, splitting, and datasets

- Added A/B/C/U/N event evidence handling, masked hourly onset hazards, uncertainty
  intervals, audit reasons, and explicit reviewed-negative semantics.
- Added global chronological splits, 24-hour full-target purge, optional context
  sensitivity, configurable event buffers, event/storm-group exclusivity audits,
  train-only stratified negative sampling/event-weight normalisation, and actual
  leave-one-site-out training folds that preserve the frozen final test split.
- Added train-only preprocessing, circular/directional feature support, missing masks,
  lead-time features, physics residual support, lazy 72-hour/24-hour PyTorch windows,
  and deterministic collation/cache keys.

### Models, training, and evaluation

- Added persistence, physics, and logistic baselines.
- Added causal convolutions, observation-only and hybrid TCN variants, cumulative
  onset-hazard outputs, non-crossing water P10/P50/P90 heads, masked event/pinball
  losses, auxiliary heads, and capped class weighting.
- Added deterministic CPU training, gradient clipping, early stopping, and safe
  non-pickle checkpoints.
- Added horizon classification/calibration metrics, reliability bins, temperature
  calibration, validation-only threshold selection, alert episodes, event matching,
  lead time, storm-group bootstrap intervals, water-quantile metrics, and LOSO
  aggregation.

### Export, API, CLI, and synthetic proof

- Added hash-verified `safetensors` model bundles with architecture, preprocessing,
  calibration, thresholds, schema, site records, model card, and synthetic
  non-deployability guards.
- Added optional ONNX export and PyTorch/ONNX Runtime parity code; the required
  optional dependencies are not installed or validated in the current environment.
- Added a separate FastAPI Shadow service with health, model-info, feature prediction,
  and site prediction routes; provenance is bound to feature sources and leads,
  inference logs contain the auditable non-secret fields, and insufficient data
  never produces a fake probability.
- Added structured JSON `cwml` commands for legacy/config audit, synthetic generation,
  manual imports, EA tide opt-in sync, site review, dataset/leakage/LOSO summaries,
  synthetic E2E, bundle verification, and service startup.
- Added a deterministic CPU synthetic E2E proof covering generation, training,
  validation calibration/thresholds, frozen testing, plots, predictions, bundle
  verification, Shadow API inference, and run-level hashes.
- Added data, label, model, deployment, retraining, and website-integration records.
- Added generic CLI label/dataset/training/calibration/threshold/frozen-test/export,
  actual Hybrid smoke, one-time test locks, LOSO training, and hashed offline replay.
- Added report-only service aggregation, delayed event evaluation, PSI/Wasserstein
  and missingness/site/source-version drift reporting; these jobs cannot retrain.
- Completed the required plot family and evidence-backed event timelines without
  fabricating unavailable official-warning intervals.

### Verification and limitations

- Passed 141 of 142 collected tests in the local Python 3.12 CPU-only environment;
  the one skip is the opt-in long 180-day rerun, whose persistent two-epoch run and
  71-file hash inventory were verified separately.
- Verified `torch 2.13.0+cpu` with CUDA unavailable; no GPU result is claimed.
- Did not download or import external real coastal data, build a reviewed real event
  catalogue, train a real model, or publish a scientific performance result.
- Did not install or verify the optional `geo`, `marine`, or `onnx` dependency groups.
- Retained `shadow_mode=true` as a non-negotiable service boundary.
