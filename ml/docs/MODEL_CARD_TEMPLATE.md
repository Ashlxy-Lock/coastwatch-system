# CoastWatch Model Card Template

## Identity

- Model name: derived from `label_mode`
- Model version:
- Variant: `obs_only_tcn` or `hybrid_tcn`
- Label mode: `weak_rule`, `official_warning`, or `confirmed_impact`
- Shadow Mode: **true**
- Synthetic data: true/false

## Scope and evidence

- Approved coastal zones:
- Training/validation/test time ranges:
- Confirmed A/B event and storm-group counts:
- Dataset manifest SHA-256:
- Source versions and licences:

## Inputs and outputs

- 72-hour causal observation history
- optional as-of-issued 24-hour forecast
- static features with snapshot date and datum
- hourly onset hazards and cumulative 1/3/6/12/24-hour outputs
- hourly water-level P10/P50/P90
- data-quality/degradation state

## Evaluation protocol

- global chronological boundaries and target purge
- storm-group isolation
- operational-context and strict-no-overlap sensitivity result
- validation-only calibration and threshold selection
- untouched final test and leave-one-site-out results

## Results

- horizon PR-AUC/Brier/NLL/ECE
- event recall, missed-event list, precision, and lead-time distribution
- false-alert episodes per site-month
- P50 MAE/RMSE, pinball loss, interval coverage and width
- per-site/worst-site results and storm-group bootstrap intervals
- `insufficient_evidence` status and event count

## Limitations and prohibited uses

This is research decision support. It does not replace Environment Agency or local
emergency warnings and cannot autonomously trigger public alerts. Document label
uncertainty, datum gaps, issued-forecast coverage, static-snapshot inconsistency,
out-of-domain sites, degraded modes, and any calibration deterioration on test.

## Degradation and rollback

Document the exact hybrid -> obs-only -> physics -> insufficient-data routing and
the previous verified bundle/version used for rollback.

