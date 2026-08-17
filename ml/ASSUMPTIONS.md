# CoastWatch ImpactNet v2 Assumptions and Evidence Boundaries

1. The first Environment Agency label scope is England. Brighton, Portsmouth, and
   Plymouth are mapping candidates only; they are not approved warning-area,
   gauge, wave-station, or coastal-zone mappings.
2. Existing v1 Open-Meteo data and weak labels remain v1 evidence. They are not
   reclassified as confirmed impacts and are not evidence for ImpactNet accuracy.
3. Synthetic data demonstrate engineering behavior only. Every synthetic dataset,
   run, metric, model card, bundle, and API response must retain an explicit
   synthetic marker. A synthetic bundle is never deployable as a real model.
4. The default synthetic fixture contains three invented sites and 180 days. Its
   storms and labels are generated, not observations of UK events.
5. All stored instants are timezone-aware UTC. `Europe/London` is display-only;
   naive timestamps are rejected instead of guessed.
6. Forecast data are eligible only when
   `issue_time <= prediction_time < valid_time`. Hindcasts without historical
   issue-time provenance are not operational backtest forecasts.
7. Missing records are not automatically negative events. Only reviewed `N`
   intervals provide clean negative supervision; `U` remains masked. Lower-
   confidence C evidence is not silently promoted to A/B confirmed impact.
8. Water, ground, and defence elevations are never subtracted unless their vertical
   datums match or a reviewed conversion exists. A field name ending in `_m_aod`
   is not sufficient evidence when the source datum is unknown.
9. Scalers, imputers, clipping values, positive weights, calibration, and operating
   thresholds are fitted without test data. Calibration and threshold selection use
   validation data only; the test split remains frozen until decisions are fixed.
10. Global temporal boundaries, full-horizon target purge, storm-group isolation,
    and LOSO folds are safety constraints, not optional reporting choices. LOSO
    validation does not consume the final test split.
11. Manual source adapters establish parsing and provenance contracts for controlled
    fixtures. They do not establish that an official archive has been downloaded,
    licensed, complete, or schema-compatible until that exact file is imported and
    reviewed.
12. The EA live tide path is network opt-in. Mock HTTP tests do not prove current
    endpoint availability or production data quality, and no live request was made
    for the completed milestone.
13. Met Office DataHub and Copernicus Marine integrations are configuration stubs.
    They intentionally refuse downloads until credentials, products, variables,
    spatial bounds, licence terms, client version, and raw issued-run archival are
    frozen. No data are synthesized to fill this gap.
14. Wave directions, CRS, station identity, onset precision, source quality, and
    datum conversions are never inferred when the source does not establish them.
    Such values remain unknown or block the operation.
15. Static defence, exposure, and risk layers are snapshots. They require a recorded
    snapshot date and may be temporally inconsistent with historic events; present-
    day attributes cannot be assumed to describe past storms.
16. The workspace root has no Git metadata. Provenance reports
    `git_available=false` and relies on file/configuration hashes instead of
    inventing a commit ID. The nested website repository is not treated as v2
    workspace provenance.
17. The tested PyTorch build is `2.13.0+cpu` and CUDA is unavailable. CPU is the only
    verified execution path; no GPU, mixed-precision, speed, or cross-device
    reproducibility claim is made.
18. Optional GeoPandas/Shapely/PyProj/Rasterio, Xarray/netCDF4/Zarr/Copernicus
    Marine, and ONNX/ONNX Runtime dependencies are absent in the tested environment.
    Code paths requiring them remain unverified until installed and exercised.
19. Observation-only and hybrid models are both exercised by actual CPU smoke
    training and the generic CLI lifecycle; the persistent 180-day synthetic E2E
    proof trains the observation-only TCN. A real hybrid operational backtest still
    requires genuine historically issued forecasts and is not implied by synthetic
    tests.
20. Model output is research-only Shadow Mode. Official warnings remain
    authoritative. Insufficient inputs produce an explicit insufficient-data result,
    not a fabricated machine-learning probability.
21. Passing unit, integration, and synthetic E2E tests establishes software behavior
    under tested conditions. It does not establish external validity, real-world
    calibration, rare-event performance, operational reliability, or public-warning
    fitness.
22. Monitoring and delayed evaluation are report-only. Missing log evidence remains
    an explicit warning, and no drift or metric result automatically retrains or
    promotes a model.
