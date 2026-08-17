# CoastWatch UK official training and ultrasonic proxy strategy

## Scientific claim

The third CoastWatch model is a course-demonstration logistic-regression model trained and evaluated only with versioned UK official coastal observations. ESP32 ultrasonic measurements are never used to fit the scaler, model coefficients, probability threshold, calibration, or baseline.

The model output is an **extreme sea-level condition probability for a documented future horizon**. It is not a tsunami, flood, or general natural-disaster probability, and it remains in shadow mode.

## Two physically separate workflows

### A. UK official model training

1. Obtain quality-controlled observations from the named official sources.
2. Harmonise timestamps to UTC and reconcile every vertical datum before joining water levels or thresholds.
3. Build an immutable bundle containing the harmonised CSV, its manifest, and every referenced original archive under `raw/`. The manifest records source URLs, licences, retrieval times, raw-file hashes, site IDs, date coverage, feature definitions, the future target definition, and fixed train/validation/frozen-test periods.
4. Copy the bundle into the protected server-side official-dataset directory and use the administrator console to rescan it.
5. Select the registered dataset and sites, inspect readiness, then manually start training.
6. Fit preprocessing and logistic-regression coefficients on the training period only. Select the operating threshold on validation only. Report final metrics once on the frozen test period.
7. Activate a successful, contract-validated and byte/hash-verified artifact as
   the third shadow model. The operator-attested provenance limitation remains
   visible after activation.

No browser or API request may nominate an arbitrary filesystem path. A failed checksum, a same-time target, overlapping splits, or a non-official data origin blocks training.

The registry re-hashes the harmonised table and every referenced file under
`raw/`, validates the manifest structure, and preserves the source citations.
It does **not** independently download from the source owner, verify licence
entitlement, or replay the operator's harmonisation and label-building code.
Consequently the current assurance level is
`operator_attested_raw_hash_verified`, with
`deterministic_importer_replay_verified=false`. A manifest claim or matching
checksum alone is not independent proof that a dataset is official.

### B. ESP32 ultrasonic external test

1. Select an activated official model, target station, and immutable official frozen-test context.
2. Freeze an affine sensor profile **before** starting the collection session.
3. The profile maps the measured relative rise to the station scale:

   `proxy_level_m = reference_level_m + gain * (water_rise_mm / 1000)`

4. Formal mode derives `gain` from an independent calibration session and the official training range. Exploratory mode may use a manually entered gain but is labelled exploratory and is excluded from formal evidence.
5. The sensor replaces only the relative-water-level input. Tide, wave, wind,
   pressure, rainfall, air temperature, humidity, water temperature, current,
   time cycles, and location come from one frozen official test-row context and
   are never filled from live Open-Meteo or invented by the operator.
6. Values outside the official training range are retained and marked out-of-distribution; they are not clipped to make the result look better.
7. After collection, run the external test. The system verifies that the model artifact hash did not change and reports sensor-test results separately from official frozen-test metrics.

## Recommended first official dataset

For an initial course result, use 2022–2024 hourly records for three site groups, then extend to 2004–2024 when the ingestion and datum checks are stable:

- Liverpool / Liverpool Bay / Crosby;
- Bournemouth / Poole Bay / Hurn;
- Newhaven / Hastings / Herstmonceux.

Suggested sources:

- BODC/NTSLF UK Tide Gauge Network processed, quality-controlled sea level: <https://www.bodc.ac.uk/data/hosted_data_systems/sea_level/uk_tide_gauge_network/processed/>
- Cefas WaveNet post-recovery wave observations: <https://www.cefas.co.uk/data-and-publications/wavenet/data-download-procedure/>
- Met Office MIDAS Open hourly observations: <https://catalogue.ceda.ac.uk/uuid/99173f6a802147aeba430d96d2bb3099/>
- Environment Agency real-time Tide Gauge API for a short pilot only: <https://environment.data.gov.uk/flood-monitoring/doc/tidegauge>

BODC, WaveNet, and MIDAS downloads require their own registrations or licence checks. Raw archives must not be committed to Git unless their licence explicitly permits redistribution.

## Target and split rules

- The target must describe a future condition and declare a positive forecast horizon. A same-time threshold label is rejected.
- A defensible first label is whether quality-controlled official water level exceeds a documented extreme/high-water condition during the future horizon. If the threshold is statistical rather than an official warning threshold, call it a **high-water proxy**, not a disaster label.
- Keep chronological train, validation, and frozen-test periods disjoint. The
  gap must be at least the forecast horizon, plus any lookback/aggregation
  history if a later schema introduces one.
- A storm or event group cannot cross splits.
- Do not randomly split adjacent 15-minute or hourly rows.
- Report per-site results and a site-macro view; add leave-one-coast-out evaluation when enough sites are available.

## Required comparison and metrics

The official frozen test must compare logistic regression with a fair simple
water-level rule. Because stations may use different vertical datums and level
distributions, the rule threshold is selected separately for each station on
validation—not globally on train or test—and then frozen before test. Both
approaches are scored on exactly the same frozen rows and site-macro view. If
the model does not improve on this baseline, the correct conclusion is that the
simple rule is sufficient for the current data. A separate persistence score is
reported only when it is genuinely distinct from that observable-level rule.

Activation is deliberately stricter than merely fitting a classifier: select at
least three sites, provide at least 200 usable rows in each of train,
validation, and frozen test, and ensure every selected site's frozen test has
both classes so the declared site-macro primary metric is defined. Smaller
bundles may be inspected but cannot be activated on the ESP32.

Primary and supporting metrics:

- PR-AUC as the primary imbalanced-class metric;
- danger recall, precision, F1, specificity, and confusion matrix;
- ROC-AUC, Brier score, log loss, and reliability bins;
- false-positive rows per day (this is not an event-level false-alarm count);
- per-site and site-macro metrics;
- validation-selected decision threshold;
- dataset, manifest, run, and artifact SHA-256 provenance.

The console must always display:

`SENSOR ROWS USED FOR FIT = 0 · SCALER = 0 · THRESHOLD = 0`

## Presentation wording

> The logistic-regression model is trained and evaluated exclusively on time-separated, quality-controlled UK coastal observations. ESP32 ultrasonic measurements are never used for fitting. They are converted by a pre-registered affine mapping and are used solely as a post-training hardware-in-the-loop test.
