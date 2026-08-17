"""Feature extraction shared by dataset building and model export tests."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Mapping

from .constants import FEATURE_NAMES


def parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return math.nan
    result = float(value)
    return result if math.isfinite(result) else math.nan


def feature_mapping(record: Mapping[str, object]) -> dict[str, float]:
    timestamp = parse_timestamp(record["timestamp"])  # type: ignore[arg-type]
    hour_angle = 2.0 * math.pi * (
        timestamp.hour + timestamp.minute / 60.0
    ) / 24.0
    days_in_year = 366.0 if _is_leap_year(timestamp.year) else 365.0
    day_angle = 2.0 * math.pi * (timestamp.timetuple().tm_yday - 1) / days_in_year

    result = {
        "air_temperature_c": _finite_number(record.get("air_temperature_c")),
        "humidity_percent": _finite_number(record.get("humidity_percent")),
        "wind_speed_kmh": _finite_number(record.get("wind_speed_kmh")),
        "wave_height_m": _finite_number(record.get("wave_height_m")),
        "wave_period_s": _finite_number(record.get("wave_period_s")),
        "water_temperature_c": _finite_number(record.get("water_temperature_c")),
        "sea_level_height_m": _finite_number(record.get("sea_level_height_m")),
        "ocean_current_velocity_kmh": _finite_number(
            record.get("ocean_current_velocity_kmh")
        ),
        "hour_sin": math.sin(hour_angle),
        "hour_cos": math.cos(hour_angle),
        "day_of_year_sin": math.sin(day_angle),
        "day_of_year_cos": math.cos(day_angle),
        "latitude": _finite_number(record.get("latitude")),
        "longitude": _finite_number(record.get("longitude")),
    }
    return result


def feature_vector(record: Mapping[str, object]) -> list[float]:
    values = feature_mapping(record)
    return [values[name] for name in FEATURE_NAMES]


def _is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)

