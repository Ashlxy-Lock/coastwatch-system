# ImpactNet v2 Monitoring and Delayed-Evaluation Runbook

All jobs in this runbook are read-only with respect to models. They create a
JSON report plus a sibling `.sha256` file atomically. They never train, promote,
or replace a bundle.

## Input contract

Use newline-delimited output from the JSON service logger. Known
`shadow_prediction`, `shadow_request`, and `shadow_internal_error` records are
validated with strict, UTC-only schemas. A malformed known record aborts the
job. Unrelated valid JSON service messages are counted and reported as ignored;
they never enter a metric.

Current prediction logs contain source issue times, data-quality status,
missing fraction, binary out-of-domain state, calibrated probabilities, model
and forecast-run provenance, and inference latency. They do **not** contain raw
input feature ranges or a continuous OOD score. Reports therefore emit explicit
evidence warnings instead of inventing those values. A feature provider may add
the documented strict `input_summary` record; PSI and Wasserstein are computed
only from its explicit `distribution_sample` evidence.

Legacy logs also omit `synthetic_data`. For delayed evaluation, pass
`--data-kind synthetic` or `--data-kind real`; `auto` fails when the field is
absent. This declaration is recorded as provenance and a conflict with a logged
flag aborts the job.

## Operational aggregate

```powershell
cwml monitor aggregate `
  --logs artifacts/logs/shadow-service.jsonl `
  --output artifacts/monitoring/operational.json `
  --data-kind real
```

The report includes source freshness, missingness and available ranges,
prediction distributions, per-site binary OOD rate/optional continuous score,
API failures, request and inference latency, degraded-route fallback rate, and
evidence warnings.

Use `--dry-run` to parse and calculate without creating the output directory or
files.

## Delayed event evaluation

The supplied event catalogue is revalidated and hashed on every run. If its
canonical rows have no `site_id`, also pass the reviewed canonical sites table;
the job refuses missing or many-to-one zone mappings.

```powershell
cwml monitor delayed-evaluate `
  --logs artifacts/logs/shadow-service.jsonl `
  --event-catalog data/processed/event_catalog.parquet `
  --sites data/processed/sites.parquet `
  --threshold 0.35 `
  --horizon 24h `
  --window-days 90 `
  --step-days 30 `
  --data-kind real `
  --output artifacts/monitoring/delayed-events.json
```

The job uses the same one-to-one pre-onset episode matcher as offline model
evaluation. It reports overall and rolling event metrics and checks whether the
prediction timeline is continuous hourly evidence. Missing hours remain visible
as a warning because false-alert denominators are weaker on an incomplete log.

## Reference-versus-live drift

Create the frozen reference JSONL by replaying representative train/reference
feature windows through the same audit-summary path. Preserve it immutably. Do
not substitute validation or test outcomes into the reference.

```powershell
cwml monitor drift `
  --reference-logs artifacts/reference/train-reference.jsonl `
  --live-logs artifacts/logs/shadow-service.jsonl `
  --psi-review-threshold 0.20 `
  --missingness-review-delta 0.10 `
  --output artifacts/monitoring/drift.json
```

The report contains feature PSI and one-dimensional Wasserstein distance when
both sides have distribution evidence, feature and overall missingness drift,
per-site prediction/missingness/OOD drift, added or removed sites, and changes
to forecast sources, forecast model-run IDs, ImpactNet versions, and feature
manifest hashes. Wasserstein has no universal scale-free alert threshold and is
therefore reported for human interpretation.

## Review and integrity

Verify the report hash before review:

```powershell
Get-FileHash artifacts/monitoring/drift.json -Algorithm SHA256
Get-Content artifacts/monitoring/drift.json.sha256
```

A drift flag or delayed-label result can request human review only. It must not
start online learning. If reviewers decide to investigate a candidate, follow
`RETRAINING_RUNBOOK.md`: immutable inputs, versioned labels, fixed global splits,
validation-only tuning, one-time frozen test, Shadow Mode comparison, and manual
promotion.
