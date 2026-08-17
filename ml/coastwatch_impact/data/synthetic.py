"""Deterministic, auditable synthetic-only ImpactNet engineering dataset."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .labels import build_hazard_labels
from .manifests import sha256_file
from .schemas import (
    validate_event_catalog_frame,
    validate_forecasts_frame,
    validate_observations_frame,
    validate_sites_frame,
    validate_static_features_frame,
)
from .split import GlobalSplitConfig, assign_global_time_split

SYNTHETIC_GENERATOR_VERSION = "impactnet-synthetic-v1"
SYNTHETIC_WARNING = (
    "SYNTHETIC DATA ONLY: engineering/CI use; not observed UK flooding, "
    "not a scientific result, and not suitable for public warning."
)


@dataclass(frozen=True)
class SyntheticDatasetBundle:
    sites: pd.DataFrame
    observations_hourly: pd.DataFrame
    forecasts_hourly: pd.DataFrame
    static_features: pd.DataFrame
    event_catalog: pd.DataFrame
    metadata: dict[str, Any]

    @property
    def tables(self) -> dict[str, pd.DataFrame]:
        return {
            "sites": self.sites,
            "observations_hourly": self.observations_hourly,
            "forecasts_hourly": self.forecasts_hourly,
            "static_features": self.static_features,
            "event_catalog": self.event_catalog,
        }

    def validate(self) -> SyntheticDatasetBundle:
        if self.metadata.get("synthetic_data") is not True:
            raise ValueError("synthetic bundle must carry synthetic_data=true")
        if self.metadata.get("scientific_use_allowed") is not False:
            raise ValueError("synthetic bundle must forbid scientific use")
        validate_sites_frame(self.sites)
        validate_observations_frame(self.observations_hourly)
        validate_forecasts_frame(self.forecasts_hourly)
        validate_static_features_frame(self.static_features)
        validate_event_catalog_frame(self.event_catalog)
        return self

    def write(self, output_directory: str | Path) -> Path:
        """Write canonical Parquet tables and an unmissable synthetic marker."""

        destination = Path(output_directory)
        if destination.exists() and any(destination.iterdir()):
            raise FileExistsError(
                f"refusing to overwrite non-empty synthetic dataset directory: {destination}"
            )
        destination.mkdir(parents=True, exist_ok=True)
        self.validate()
        table_records: dict[str, dict[str, Any]] = {}
        for name, frame in self.tables.items():
            path = destination / f"{name}.parquet"
            frame.to_parquet(path, index=False, engine="pyarrow")
            table_records[name] = {
                "filename": path.name,
                "rows": int(len(frame)),
                "sha256": sha256_file(path),
            }
        marker = dict(self.metadata)
        marker["warning"] = SYNTHETIC_WARNING
        marker["tables"] = table_records
        marker_path = destination / "SYNTHETIC_ONLY.json"
        with marker_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(marker, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        return destination


def _site_definitions() -> list[dict[str, Any]]:
    # These are deliberately synthetic IDs and coordinates.  They must never be
    # confused with the project's six v1 sites or official monitoring stations.
    return [
        {
            "site_id": "synthetic_site_01",
            "coastal_zone_id": "synthetic_zone_01",
            "site_name": "Synthetic North Bay",
            "latitude": 54.20,
            "longitude": -1.30,
            "phase": 0.0,
        },
        {
            "site_id": "synthetic_site_02",
            "coastal_zone_id": "synthetic_zone_02",
            "site_name": "Synthetic Central Estuary",
            "latitude": 52.10,
            "longitude": 0.20,
            "phase": 0.8,
        },
        {
            "site_id": "synthetic_site_03",
            "coastal_zone_id": "synthetic_zone_03",
            "site_name": "Synthetic South Head",
            "latitude": 50.70,
            "longitude": -3.10,
            "phase": 1.6,
        },
    ]


def _storm_hours(total_hours: int) -> list[int]:
    candidates = [int(total_hours * fraction) for fraction in (0.20, 0.45, 0.70, 0.88)]
    return sorted({min(max(value, 48), total_hours - 48) for value in candidates})


def _truth_at(
    hour: float,
    *,
    site_index: int,
    phase: float,
    storms: list[int],
) -> dict[str, float]:
    tide = 1.35 * math.sin(2.0 * math.pi * hour / 12.42 + phase)
    tide += 0.22 * math.sin(2.0 * math.pi * hour / (24.0 * 14.0) + phase / 2.0)
    surge = 0.0
    storm_wind = 0.0
    for storm_index, peak in enumerate(storms):
        shifted_peak = peak + site_index * 3 - storm_index
        pulse = math.exp(-0.5 * ((hour - shifted_peak) / 8.0) ** 2)
        surge += (0.55 + 0.12 * ((storm_index + site_index) % 3)) * pulse
        storm_wind += (10.0 + 2.0 * storm_index) * pulse
    wave = max(
        0.05,
        0.75 + 0.20 * math.sin(2.0 * math.pi * hour / (24.0 * 3.0) + phase) + 0.95 * surge,
    )
    wind = max(0.0, 5.5 + 2.2 * math.sin(2.0 * math.pi * hour / (24.0 * 5.0)) + storm_wind)
    pressure = 1014.0 - 1.5 * math.sin(2.0 * math.pi * hour / (24.0 * 7.0)) - 0.7 * storm_wind
    rainfall = max(0.0, 0.2 * math.sin(2.0 * math.pi * hour / 18.0) + 0.08 * storm_wind)
    temperature = 11.0 + 4.0 * math.sin(2.0 * math.pi * hour / (24.0 * 180.0))
    temperature += 1.5 * math.sin(2.0 * math.pi * (hour % 24.0) / 24.0 - 1.0)
    humidity = float(np.clip(76.0 - 0.8 * temperature + 0.5 * rainfall, 35.0, 100.0))
    return {
        "predicted_tide_m_aod": tide,
        "surge_residual_m": surge,
        "water_level_m_aod": tide + surge,
        "significant_wave_height_m": wave,
        "maximum_wave_height_m": 1.65 * wave,
        "wave_period_s": 5.5 + 0.9 * wave,
        "wave_direction_deg_true": float(
            (220.0 + 12.0 * math.sin(hour / 36.0) + site_index * 18) % 360
        ),
        "wind_speed_m_s": wind,
        "wind_gust_m_s": 1.45 * wind,
        "wind_direction_deg_true": float(
            (205.0 + 25.0 * math.sin(hour / 30.0) + site_index * 11) % 360
        ),
        "surface_pressure_hpa": pressure,
        "rainfall_mm_h": rainfall,
        "air_temperature_c": temperature,
        "humidity_percent": humidity,
    }


def _generate_sites() -> pd.DataFrame:
    rows = []
    for site in _site_definitions():
        rows.append(
            {
                "site_id": site["site_id"],
                "coastal_zone_id": site["coastal_zone_id"],
                "site_name": site["site_name"],
                "latitude": site["latitude"],
                "longitude": site["longitude"],
                "ea_warning_area_code": None,
                "tide_station_ids": [f"synthetic_tide_{site['site_id'][-2:]}"],
                "wave_station_ids": [f"synthetic_wave_{site['site_id'][-2:]}"],
                "timezone_display": "Europe/London",
                "active": True,
                "exclusion_reason": None,
                "coordinate_reference_system": "EPSG:4326",
                "synthetic_only": True,
            }
        )
    return pd.DataFrame(rows)


def _generate_observations(
    times: pd.DatetimeIndex,
    *,
    storms: list[int],
    seed: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    variables = (
        "water_level_m_aod",
        "predicted_tide_m_aod",
        "surge_residual_m",
        "significant_wave_height_m",
        "maximum_wave_height_m",
        "wave_period_s",
        "wave_direction_deg_true",
        "wind_speed_m_s",
        "wind_gust_m_s",
        "wind_direction_deg_true",
        "surface_pressure_hpa",
        "rainfall_mm_h",
        "air_temperature_c",
        "humidity_percent",
    )
    rng = np.random.default_rng(seed)
    for site_index, site in enumerate(_site_definitions()):
        for hour, timestamp in enumerate(times):
            truth = _truth_at(
                float(hour),
                site_index=site_index,
                phase=float(site["phase"]),
                storms=storms,
            )
            row: dict[str, Any] = {
                "site_id": site["site_id"],
                "coastal_zone_id": site["coastal_zone_id"],
                "timestamp_utc": timestamp,
                **truth,
                "water_level_local_m": None,
                "water_level_datum": "mAOD",
                "source_age_minutes": 8.0 + float((hour + site_index) % 7),
                "quality_flag": "good",
                "synthetic_only": True,
            }
            for variable in variables:
                row[f"{variable}__missing"] = False
                row[f"{variable}__source"] = "deterministic_synthetic_generator"
                row[f"{variable}__quality"] = "synthetic_good"
            # Deterministic short gaps are genuinely missing, while stale blocks
            # retain their values but must be masked by downstream quality logic.
            gap = (hour + 17 * site_index) % 389 in {0, 1, 2}
            stale = (hour + 31 * site_index) % 521 in {10, 11, 12, 13}
            if gap:
                for variable in (
                    "significant_wave_height_m",
                    "maximum_wave_height_m",
                    "wave_period_s",
                ):
                    row[variable] = np.nan
                    row[f"{variable}__missing"] = True
                    row[f"{variable}__quality"] = "synthetic_missing"
                row["quality_flag"] = "partial_missing"
            if stale:
                row["source_age_minutes"] = 240.0
                row["quality_flag"] = "stale"
                for variable in variables:
                    row[f"{variable}__quality"] = "synthetic_stale"
            # A tiny reproducible measurement perturbation keeps this from being
            # a perfectly analytic toy while preserving deterministic output.
            if not pd.isna(row["water_level_m_aod"]):
                row["water_level_m_aod"] += float(rng.normal(0.0, 0.012))
            rows.append(row)
    return pd.DataFrame(rows)


def _generate_forecasts(
    times: pd.DatetimeIndex,
    *,
    storms: list[int],
    seed: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    rng = np.random.default_rng(seed + 1000)
    start = times[0]
    end = times[-1]
    issue_times = pd.date_range(start=start - pd.Timedelta(hours=6), end=end, freq="6h", tz="UTC")
    for site_index, site in enumerate(_site_definitions()):
        for issue_number, issue in enumerate(issue_times):
            model_run_id = f"synthetic_run_{issue:%Y%m%dT%H%MZ}"
            # Six-hourly model runs carry 30 leads so an arbitrary prediction
            # hour always has a complete 24-hour horizon from the latest run that
            # was actually available (up to five hours old).
            for lead in range(1, 31):
                valid = issue + pd.Timedelta(hours=lead)
                if valid < start or valid > end:
                    continue
                valid_hour = (valid - start).total_seconds() / 3600.0
                truth = _truth_at(
                    valid_hour,
                    site_index=site_index,
                    phase=float(site["phase"]),
                    storms=storms,
                )
                scale = 0.025 + 0.004 * lead
                tide_error = float(rng.normal(0.0, scale * 0.35))
                surge_error = float(rng.normal(0.0, scale))
                wave_error = float(rng.normal(0.0, scale * 1.5))
                wind_error = float(rng.normal(0.0, scale * 4.0))
                row = {
                    "site_id": site["site_id"],
                    "issue_time_utc": issue,
                    "valid_time_utc": valid,
                    "lead_hours": float(lead),
                    "source_model": "synthetic_issued_model",
                    "model_run_id": model_run_id,
                    "forecast_total_water_level_m_aod": truth["water_level_m_aod"]
                    + tide_error
                    + surge_error,
                    "forecast_tide_m_aod": truth["predicted_tide_m_aod"] + tide_error,
                    "forecast_surge_m": truth["surge_residual_m"] + surge_error,
                    "forecast_wave_height_m": max(
                        0.0, truth["significant_wave_height_m"] + wave_error
                    ),
                    "forecast_wave_period_s": max(
                        0.1, truth["wave_period_s"] + float(rng.normal(0.0, 0.12))
                    ),
                    "forecast_wave_direction_deg_true": float(
                        (truth["wave_direction_deg_true"] + rng.normal(0.0, 4.0)) % 360
                    ),
                    "forecast_wind_speed_m_s": max(0.0, truth["wind_speed_m_s"] + wind_error),
                    "forecast_wind_gust_m_s": max(0.0, truth["wind_gust_m_s"] + 1.2 * wind_error),
                    "forecast_wind_direction_deg_true": float(
                        (truth["wind_direction_deg_true"] + rng.normal(0.0, 5.0)) % 360
                    ),
                    "forecast_pressure_hpa": truth["surface_pressure_hpa"]
                    + float(rng.normal(0.0, 0.8)),
                    "forecast_rainfall_mm_h": max(
                        0.0, truth["rainfall_mm_h"] + float(rng.normal(0.0, 0.08))
                    ),
                    "ensemble_member": None,
                    "quantile": None,
                    "quality_flag": "synthetic_good",
                    "forecast_age_hours": float((valid - issue).total_seconds() / 3600.0),
                    "forecast_wave_height_m__missing": False,
                    "synthetic_only": True,
                }
                if (issue_number * 30 + lead + site_index * 19) % 997 == 0:
                    row["forecast_wave_height_m"] = np.nan
                    row["forecast_wave_height_m__missing"] = True
                    row["quality_flag"] = "missing"
                elif (issue_number * 30 + lead + site_index * 23) % 1231 == 0:
                    row["quality_flag"] = "stale"
                    row["forecast_age_hours"] = 48.0
                rows.append(row)
    return pd.DataFrame(rows)


def _generate_static_features(snapshot_date: date) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for site_index, site in enumerate(_site_definitions()):
        defence = None if site_index == 2 else 3.0 + 0.25 * site_index
        row = {
            "coastal_zone_id": site["coastal_zone_id"],
            "latitude": site["latitude"],
            "longitude": site["longitude"],
            "coastal_orientation_sin": float(math.sin(0.6 + site_index * 0.5)),
            "coastal_orientation_cos": float(math.cos(0.6 + site_index * 0.5)),
            "ground_elevation_m_aod": 1.1 + 0.35 * site_index,
            "defence_crest_height_m_aod": defence,
            "defence_crest_height_m_aod__missing": defence is None,
            "defence_condition_code": ["good", "fair", None][site_index],
            "defence_condition_code__missing": site_index == 2,
            "distance_to_coast_m": 45.0 + 30.0 * site_index,
            "rofrs_risk_category": ["high", "medium", "low"][site_index],
            "historic_flood_fraction": 0.22 - 0.06 * site_index,
            "low_lying_area_fraction": 0.42 - 0.08 * site_index,
            "road_exposure_count": float(18 - 3 * site_index),
            "building_exposure_count": float(150 - 22 * site_index),
            "static_snapshot_date": snapshot_date,
            "vertical_datum": "mAOD",
            "source_versions_json": json.dumps(
                {"synthetic_generator": SYNTHETIC_GENERATOR_VERSION}
            ),
            "coordinate_reference_system": "EPSG:4326",
            "synthetic_only": True,
        }
        rows.append(row)
    return pd.DataFrame(rows)


def _event_row(
    *,
    event_id: str,
    storm_group_id: str | None,
    zone_id: str,
    onset: pd.Timestamp | None,
    end: pd.Timestamp | None,
    confidence: str,
    precision: str,
    confirmed: bool | None,
    severity: int | None,
    reviewed: bool,
    created: pd.Timestamp,
    notes: str,
) -> dict[str, Any]:
    peak = onset + pd.Timedelta(hours=3) if onset is not None else None
    return {
        "event_id": event_id,
        "storm_group_id": storm_group_id,
        "coastal_zone_id": zone_id,
        "onset_time_utc": onset,
        "peak_time_utc": peak,
        "end_time_utc": end,
        "onset_precision": precision,
        "impact_confirmed": confirmed,
        "impact_severity": severity,
        "label_confidence": confidence,
        "warning_max_severity": severity,
        "spatial_evidence": confidence in {"A", "B"},
        "observational_evidence": confidence in {"A", "B"},
        "human_reviewed": reviewed,
        "primary_source": "deterministic_synthetic_generator",
        "source_references_json": json.dumps(["synthetic://impactnet/test-fixture"]),
        "review_notes": f"{SYNTHETIC_WARNING} {notes}",
        "created_at_utc": created,
        "updated_at_utc": created,
        "synthetic_only": True,
    }


def _generate_events(
    times: pd.DatetimeIndex,
    *,
    storms: list[int],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    created = times[0] - pd.Timedelta(days=30)
    sites = _site_definitions()
    for storm_number, peak_hour in enumerate(storms, start=1):
        group_id = f"synthetic_storm_{storm_number:02d}"
        for site_index, site in enumerate(sites):
            onset = times[0] + pd.Timedelta(hours=peak_hour + site_index * 3 - 3)
            end = onset + pd.Timedelta(hours=14)
            if site_index == 0:
                confidence, confirmed, reviewed = "A", True, True
            elif site_index == 1:
                confidence, confirmed, reviewed = "B", True, False
            else:
                confidence, confirmed, reviewed = (
                    ("A", True, True) if storm_number % 2 == 0 else ("C", False, True)
                )
            rows.append(
                _event_row(
                    event_id=f"{group_id}_{site['coastal_zone_id']}",
                    storm_group_id=group_id,
                    zone_id=str(site["coastal_zone_id"]),
                    onset=onset,
                    end=end,
                    confidence=confidence,
                    precision="exact_hour",
                    confirmed=confirmed,
                    severity=2 + (storm_number % 2) if confidence in {"A", "B"} else None,
                    reviewed=reviewed,
                    created=created,
                    notes="Synthetic storm event.",
                )
            )

    # Explicit U interval ensures unknown evidence is tested as masked, never N.
    unknown_onset = times[0] + pd.Timedelta(hours=max(36, int(len(times) * 0.33)))
    rows.append(
        _event_row(
            event_id="synthetic_unresolved_01",
            storm_group_id=None,
            zone_id=str(sites[2]["coastal_zone_id"]),
            onset=unknown_onset,
            end=unknown_onset + pd.Timedelta(hours=8),
            confidence="U",
            precision="interval",
            confirmed=None,
            severity=None,
            reviewed=True,
            created=created,
            notes="Deliberate unknown interval for mask testing.",
        )
    )
    # Explicit N review windows provide auditable clean-negative examples without
    # claiming that every unrecorded synthetic hour is negative.
    for site_index, site in enumerate(sites):
        onset = times[0] + pd.Timedelta(days=5 + site_index)
        rows.append(
            _event_row(
                event_id=f"synthetic_reviewed_negative_{site_index + 1:02d}",
                storm_group_id=None,
                zone_id=str(site["coastal_zone_id"]),
                onset=onset,
                end=onset + pd.Timedelta(days=4),
                confidence="N",
                precision="interval",
                confirmed=False,
                severity=0,
                reviewed=True,
                created=created,
                notes="Reviewed synthetic non-event coverage interval.",
            )
        )
    return pd.DataFrame(rows)


def generate_synthetic_dataset(
    *,
    start_utc: str | pd.Timestamp = "2025-01-01T00:00:00Z",
    duration_days: int = 180,
    seed: int = 20260813,
) -> SyntheticDatasetBundle:
    """Generate exactly three deterministic sites with issued forecasts.

    The default covers 180 days as required by the development specification.
    Shorter durations are supported for focused unit tests, but always retain
    explicit synthetic-only provenance.
    """

    if duration_days < 5:
        raise ValueError("duration_days must be at least 5")
    start = pd.Timestamp(start_utc)
    if start.tzinfo is None:
        raise ValueError("start_utc must be timezone-aware")
    start = start.tz_convert("UTC")
    total_hours = int(duration_days * 24)
    times = pd.date_range(start=start, periods=total_hours, freq="h", tz="UTC")
    storms = _storm_hours(total_hours)
    sites = _generate_sites()
    observations = _generate_observations(times, storms=storms, seed=seed)
    forecasts = _generate_forecasts(times, storms=storms, seed=seed)
    static = _generate_static_features(start.date())
    events = _generate_events(times, storms=storms)
    train_end = start + pd.Timedelta(hours=int(total_hours * 0.60) - 1)
    validation_end = start + pd.Timedelta(hours=int(total_hours * 0.80) - 1)
    metadata: dict[str, Any] = {
        "dataset_name": "CoastWatch Synthetic-Test Impact Dataset",
        "generator_version": SYNTHETIC_GENERATOR_VERSION,
        "seed": int(seed),
        "site_count": 3,
        "duration_days": int(duration_days),
        "coverage_start_utc": start.isoformat(),
        "coverage_end_utc": times[-1].isoformat(),
        "synthetic_data": True,
        "synthetic_only": True,
        "scientific_use_allowed": False,
        "public_warning_use_allowed": False,
        "default_split_boundaries": {
            "train_end_utc": train_end.isoformat(),
            "validation_end_utc": validation_end.isoformat(),
            "test_end_utc": times[-1].isoformat(),
        },
        "contains_missing_values": True,
        "contains_stale_values": True,
        "contains_issued_forecasts": True,
        "warning": SYNTHETIC_WARNING,
    }
    bundle = SyntheticDatasetBundle(sites, observations, forecasts, static, events, metadata)
    return bundle.validate()


def default_synthetic_split_config(bundle: SyntheticDatasetBundle) -> GlobalSplitConfig:
    boundaries = bundle.metadata["default_split_boundaries"]
    return GlobalSplitConfig(
        train_end_utc=boundaries["train_end_utc"],
        validation_end_utc=boundaries["validation_end_utc"],
        test_end_utc=boundaries["test_end_utc"],
        forecast_horizon_hours=24,
    )


def build_synthetic_sample_index(
    bundle: SyntheticDatasetBundle,
    *,
    split_config: GlobalSplitConfig | None = None,
    stride_hours: int = 6,
    past_hours: int = 72,
    horizon_hours: int = 24,
) -> pd.DataFrame:
    """Build a small lazy-window index for smoke training, not scientific use."""

    if stride_hours <= 0:
        raise ValueError("stride_hours must be positive")
    times = pd.to_datetime(bundle.observations_hourly["timestamp_utc"], utc=True)
    start = times.min() + pd.Timedelta(hours=past_hours - 1)
    end = times.max() - pd.Timedelta(hours=horizon_hours)
    prediction_times = pd.date_range(start=start, end=end, freq=f"{stride_hours}h", tz="UTC")
    rows = [
        {
            "site_id": site.site_id,
            "coastal_zone_id": site.coastal_zone_id,
            "prediction_time_utc": prediction,
            "synthetic_label_coverage_known": True,
            "sample_weight": 1.0,
            "synthetic_only": True,
        }
        for site in bundle.sites.itertuples(index=False)
        for prediction in prediction_times
    ]
    samples = pd.DataFrame(rows)
    config = split_config or default_synthetic_split_config(bundle)
    if config.forecast_horizon_hours != horizon_hours:
        config = config.model_copy(update={"forecast_horizon_hours": horizon_hours})
    samples = build_hazard_labels(
        samples,
        bundle.event_catalog,
        horizon_hours=horizon_hours,
        known_negative_col="synthetic_label_coverage_known",
    )
    samples = assign_global_time_split(samples, config, drop_purged=True)
    samples["synthetic_only"] = True
    return samples


def write_synthetic_dataset(
    output_directory: str | Path,
    *,
    start_utc: str | pd.Timestamp = "2025-01-01T00:00:00Z",
    duration_days: int = 180,
    seed: int = 20260813,
) -> Path:
    bundle = generate_synthetic_dataset(
        start_utc=start_utc,
        duration_days=duration_days,
        seed=seed,
    )
    return bundle.write(output_directory)


# Concise alias for CLI orchestration.
generate = generate_synthetic_dataset


__all__ = [
    "SYNTHETIC_GENERATOR_VERSION",
    "SYNTHETIC_WARNING",
    "SyntheticDatasetBundle",
    "build_synthetic_sample_index",
    "default_synthetic_split_config",
    "generate",
    "generate_synthetic_dataset",
    "write_synthetic_dataset",
]
