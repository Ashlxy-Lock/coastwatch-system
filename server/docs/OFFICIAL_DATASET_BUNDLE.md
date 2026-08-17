# UK official dataset bundle

The administrator console deliberately does not accept an arbitrary local path
or relabel uploaded sensor data as official. It discovers only immutable bundles
placed under the configured `COAST_OFFICIAL_DATASET_ROOT`:

```text
official_datasets/
  <globally-version-qualified-dataset-id>/
    <version>/
      manifest.json
      harmonized.csv
      raw/
        <every sources[].original_filename>
```

On the installed Windows service the root is:

```text
C:\ProgramData\CoastalWarning\data\official_datasets
```

Use [manifest.template.json](../examples/official_dataset_bundle/manifest.template.json)
and [harmonized.template.csv](../examples/official_dataset_bundle/harmonized.template.csv)
as structural templates only. They are intentionally invalid placeholders and
are not an official dataset.

## Build procedure

1. Download the source archives under the source owner's licence. Keep them
   outside Git, but copy the exact bytes referenced by the manifest into this
   bundle's `raw/` directory before registration.
2. Harmonise all timestamps to UTC and reconcile the tide-gauge vertical datum
   before joining sea level, tide, wave, and weather fields.
3. Produce one complete-case row per site and timestamp with every required
   numeric column. The current v1 bundle contract rejects blanks, `NaN`, and
   infinite values rather than silently imputing them across time splits. The
   `target_extreme_water` label must refer to a documented future horizon, not
   the current row and not the ESP32 measurement.
4. Use chronological `train`, `validation`, and `frozen_test` periods. The
   leakage gap must be at least the future-label horizon. Every positive row
   needs a non-background `storm_group_id`, and a storm group must not cross
   split boundaries.
5. Calculate SHA-256 for every original source file and for `harmonized.csv`.
   Enter the exact row count and hashes in `manifest.json`.
6. Copy the manifest, harmonised CSV, and every referenced raw source into the
   fixed directory layout, then click
   **Rescan protected directory** in the administrator console.
7. Select the registered version and sites, inspect readiness, and manually
   start training. The frozen-test metrics are not used to fit preprocessing,
   coefficients, or the decision threshold.

Activation additionally requires at least three selected sites, at least 200
usable rows in each chronological split, and both classes in the frozen test of
every selected site. The logistic operating threshold and one water-level rule
threshold per site are selected only on validation. The frozen test is never
used to tune either comparator.

PowerShell examples:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath '.\harmonized.csv'
(Import-Csv -LiteralPath '.\harmonized.csv').Count
```

The manifest pins the original filename, source URL, retrieval timestamp,
licence, citation, and checksum. Registration re-hashes every referenced raw
file and fails closed if one is absent, is a reparse point, escapes `raw/`, or
changes. It does not independently verify the source-owner/licence statements
or replay the transformation and label derivation. Those remain
operator-attested and are shown that way in the console.

`dataset_id` must itself be globally version-qualified (for example,
`uk-coasts-2022-2024-v1`). Reusing one `dataset_id` for different versions or
different bytes is rejected rather than silently replacing an earlier dataset.

## Required feature meanings

| Column | Meaning |
| --- | --- |
| `relative_water_level_m` | Observed water level on the declared common datum |
| `predicted_tide_relative_m` | Predicted astronomical tide on that datum |
| `significant_wave_height_m` | Quality-controlled significant wave height |
| `wave_period_s` | Associated wave period |
| `wind_speed_m_s` | Near-surface wind speed |
| `wind_gust_m_s` | Near-surface gust speed |
| `surface_pressure_hpa` | Surface pressure |
| `rainfall_mm_h` | Hourly rainfall rate/accumulation as documented |
| `air_temperature_c` | Near-surface air temperature |
| `relative_humidity_percent` | Relative humidity |
| `water_temperature_c` | Water temperature |
| `ocean_current_velocity_m_s` | Ocean-current speed |
| `hour_sin`, `hour_cos` | UTC hour encoded cyclically |
| `day_of_year_sin`, `day_of_year_cos` | UTC day of year encoded cyclically |
| `latitude`, `longitude` | Fixed site coordinates |

The feature order and units are exact: `m`, `s`, `m/s`, `hPa`, `mm/h`,
`degC`, `%`, `unitless`, and `degree` as shown in the manifest template.
`surge_residual_m` is deliberately not a model input because it is exactly
derivable from observed level and predicted tide, which would make the sensor
replacement path collinear and scientifically ambiguous.

The ESP32 ultrasonic values are absent from this table. They are transformed
only later by the external-test console and never enter training, scaling,
threshold selection, or frozen official evaluation.
