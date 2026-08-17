"""Shared, versioned feature and weak-label definitions."""

from __future__ import annotations


CLASS_NAMES: tuple[str, ...] = ("safe", "advisory", "warning", "critical")

# These are deliberately limited to values already available from the server's
# EnvironmentResponse.  Keeping training and live inference fields identical is
# more important than adding a larger list of historical-only variables.
FEATURE_NAMES: tuple[str, ...] = (
    "air_temperature_c",
    "humidity_percent",
    "wind_speed_kmh",
    "wave_height_m",
    "wave_period_s",
    "water_temperature_c",
    "sea_level_height_m",
    "ocean_current_velocity_kmh",
    "hour_sin",
    "hour_cos",
    "day_of_year_sin",
    "day_of_year_cos",
    "latitude",
    "longitude",
)

WEATHER_API_FIELDS: tuple[str, ...] = (
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
)
MARINE_API_FIELDS: tuple[str, ...] = (
    "wave_height",
    "wave_period",
    "sea_surface_temperature",
    "sea_level_height_msl",
    "ocean_current_velocity",
)

LABEL_RULE_VERSION = "demo_environment_rule_v1"
FORECAST_HORIZON_HOURS = 6

# Project heuristics for generating weak labels.  They are intentionally kept
# in one versioned structure and are NOT public-safety or navigation limits.
WEAK_LABEL_THRESHOLDS: dict[str, tuple[float, float, float]] = {
    "wave_height_m": (1.5, 2.5, 4.0),
    "wind_speed_kmh": (30.0, 50.0, 70.0),
}

