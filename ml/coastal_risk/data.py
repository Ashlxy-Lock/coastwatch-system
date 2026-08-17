"""Download and align Open-Meteo historical weather and marine records."""

from __future__ import annotations

import csv
import gzip
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import httpx2

from .constants import MARINE_API_FIELDS, WEATHER_API_FIELDS
from .labels import add_future_targets


WEATHER_HISTORY_URLS: dict[str, str] = {
    "archive": "https://archive-api.open-meteo.com/v1/archive",
    # Historical Forecast uses the same variable schema as the live Forecast
    # API, but its free endpoint can be considerably slower for bulk exports.
    "historical-forecast": "https://historical-forecast-api.open-meteo.com/v1/forecast",
}
MARINE_ARCHIVE_URL = "https://marine-api.open-meteo.com/v1/marine"
DEFAULT_LOCATIONS_PATH = Path(__file__).with_name("locations.json")
DEFAULT_CHUNK_DAYS = 90

DATASET_FIELDS: tuple[str, ...] = (
    "location_id",
    "location_name",
    "latitude",
    "longitude",
    "timestamp",
    "weather_source",
    "marine_source",
    "air_temperature_c",
    "humidity_percent",
    "wind_speed_kmh",
    "wave_height_m",
    "wave_period_s",
    "water_temperature_c",
    "sea_level_height_m",
    "ocean_current_velocity_kmh",
    "instant_risk_level",
    "instant_reason_codes",
    "target_risk_level",
    "target_reason_codes",
    "forecast_horizon_hours",
    "label_rule_version",
)


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    latitude: float
    longitude: float


def load_locations(
    path: Path = DEFAULT_LOCATIONS_PATH,
    selected_ids: Sequence[str] | None = None,
) -> list[Location]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("locations file must contain a JSON array")
    locations = [
        Location(
            id=str(row["id"]),
            name=str(row["name"]),
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
        )
        for row in payload
    ]
    if selected_ids:
        requested = set(selected_ids)
        known = {location.id for location in locations}
        missing = sorted(requested - known)
        if missing:
            raise ValueError(f"unknown location IDs: {', '.join(missing)}")
        locations = [location for location in locations if location.id in requested]
    if not locations:
        raise ValueError("at least one location is required")
    return locations


def _date_chunks(start: date, end: date, chunk_days: int) -> Iterable[tuple[date, date]]:
    if end < start:
        raise ValueError("end date must not precede start date")
    if chunk_days < 1:
        raise ValueError("chunk_days must be positive")
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=chunk_days - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _cache_path(
    cache_root: Path,
    source: str,
    location: Location,
    start: date,
    end: date,
) -> Path:
    return cache_root / source / location.id / f"{start}_{end}.json"


def _read_or_fetch_json(
    client: httpx2.Client,
    url: str,
    params: Mapping[str, object],
    cache_path: Path,
    refresh: bool,
) -> Mapping[str, Any]:
    if cache_path.exists() and not refresh:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(payload, Mapping):
            return payload
        raise ValueError(f"cached API response is not an object: {cache_path}")

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client.get(url, params=dict(params))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise ValueError(f"API response is not an object: {url}")
            if payload.get("error"):
                raise ValueError(str(payload.get("reason", "Open-Meteo error")))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            return payload
        except (httpx2.HTTPError, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.0 * (2**attempt))
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def _hourly_rows(
    payload: Mapping[str, Any], field_map: Mapping[str, str]
) -> dict[str, dict[str, object]]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, Mapping):
        raise ValueError("Open-Meteo response is missing hourly data")
    times = hourly.get("time")
    if not isinstance(times, list):
        raise ValueError("Open-Meteo hourly data is missing time")

    result: dict[str, dict[str, object]] = {}
    for index, timestamp in enumerate(times):
        if not isinstance(timestamp, str):
            continue
        row: dict[str, object] = {}
        for source_name, target_name in field_map.items():
            values = hourly.get(source_name)
            row[target_name] = (
                values[index]
                if isinstance(values, list) and index < len(values)
                else None
            )
        result[timestamp] = row
    return result


def fetch_location_records(
    location: Location,
    start: date,
    end: date,
    cache_root: Path,
    *,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    refresh: bool = False,
    weather_source: str = "archive",
) -> list[dict[str, object]]:
    if weather_source not in WEATHER_HISTORY_URLS:
        raise ValueError(f"unknown weather source: {weather_source}")
    records: list[dict[str, object]] = []
    headers = {"User-Agent": "CoastalWarningSystem-ML/1.0"}
    with httpx2.Client(timeout=60.0, follow_redirects=True, headers=headers) as client:
        for chunk_start, chunk_end in _date_chunks(start, end, chunk_days):
            common = {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "start_date": chunk_start.isoformat(),
                "end_date": chunk_end.isoformat(),
                "timezone": "UTC",
            }
            weather_payload = _read_or_fetch_json(
                client,
                WEATHER_HISTORY_URLS[weather_source],
                {
                    **common,
                    "hourly": ",".join(WEATHER_API_FIELDS),
                    "cell_selection": "land",
                },
                _cache_path(
                    cache_root,
                    "weather" if weather_source == "archive" else "weather_historical_forecast",
                    location,
                    chunk_start,
                    chunk_end,
                ),
                refresh,
            )
            marine_payload = _read_or_fetch_json(
                client,
                MARINE_ARCHIVE_URL,
                {
                    **common,
                    "hourly": ",".join(MARINE_API_FIELDS),
                    "cell_selection": "sea",
                    "length_unit": "metric",
                },
                _cache_path(cache_root, "marine", location, chunk_start, chunk_end),
                refresh,
            )

            weather = _hourly_rows(
                weather_payload,
                {
                    "temperature_2m": "air_temperature_c",
                    "relative_humidity_2m": "humidity_percent",
                    "wind_speed_10m": "wind_speed_kmh",
                },
            )
            marine = _hourly_rows(
                marine_payload,
                {
                    "wave_height": "wave_height_m",
                    "wave_period": "wave_period_s",
                    "sea_surface_temperature": "water_temperature_c",
                    "sea_level_height_msl": "sea_level_height_m",
                    "ocean_current_velocity": "ocean_current_velocity_kmh",
                },
            )

            for timestamp in sorted(weather.keys() & marine.keys()):
                records.append(
                    {
                        "location_id": location.id,
                        "location_name": location.name,
                        "latitude": location.latitude,
                        "longitude": location.longitude,
                        "timestamp": timestamp + "Z",
                        "weather_source": f"open-meteo-{weather_source}",
                        "marine_source": "open-meteo-marine-history",
                        **weather[timestamp],
                        **marine[timestamp],
                    }
                )
    return records


def write_dataset(rows: Sequence[Mapping[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output_path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DATASET_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_dataset(path: Path) -> list[dict[str, object]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    numeric_fields = {
        "latitude",
        "longitude",
        "air_temperature_c",
        "humidity_percent",
        "wind_speed_kmh",
        "wave_height_m",
        "wave_period_s",
        "water_temperature_c",
        "sea_level_height_m",
        "ocean_current_velocity_kmh",
    }
    integer_fields = {
        "instant_risk_level",
        "target_risk_level",
        "forecast_horizon_hours",
    }
    for row in rows:
        for name in numeric_fields:
            raw = row.get(name, "")
            row[name] = float(raw) if raw not in (None, "") else None
        for name in integer_fields:
            row[name] = int(row[name])
    return rows


def download_dataset(
    locations: Sequence[Location],
    start: date,
    end: date,
    cache_root: Path,
    output_path: Path,
    *,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    refresh: bool = False,
    weather_source: str = "archive",
) -> list[dict[str, object]]:
    labelled: list[dict[str, object]] = []
    for index, location in enumerate(locations, start=1):
        print(f"[DATA] {index}/{len(locations)} {location.id} {start}..{end}")
        raw = fetch_location_records(
            location,
            start,
            end,
            cache_root,
            chunk_days=chunk_days,
            refresh=refresh,
            weather_source=weather_source,
        )
        labelled.extend(add_future_targets(raw))
    write_dataset(labelled, output_path)
    print(f"[DATA] wrote {len(labelled):,} labelled rows to {output_path}")
    return labelled
