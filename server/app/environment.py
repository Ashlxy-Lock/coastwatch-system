import logging
import math
import os
import threading
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx2

from .database import get_device_location
from .schemas import (
    DeviceLocationPreset,
    EnvironmentResponse,
    LocationKind,
    LocationSearchResult,
)

WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_API_URL = "https://marine-api.open-meteo.com/v1/marine"
GEOCODING_API_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEOCODING_GET_URL = "https://geocoding-api.open-meteo.com/v1/get"
CACHE_TTL_SECONDS = 10 * 60
REQUEST_TIMEOUT_SECONDS = 5.0
GEOCODING_REQUEST_TIMEOUT_SECONDS = 4.0
GEOCODING_CACHE_TTL_SECONDS = 24 * 60 * 60
GEOCODING_RETRY_ATTEMPTS = 2

_WEATHER_CURRENT_VARIABLES = (
    "temperature_2m,relative_humidity_2m,weather_code,"
    "wind_speed_10m,wind_direction_10m"
)
_MARINE_CURRENT_VARIABLES = (
    "wave_height,wave_period,sea_surface_temperature,sea_level_height_msl,"
    "ocean_current_velocity,ocean_current_direction"
)

logger = logging.getLogger(__name__)


LOCATION_PRESETS: tuple[LocationSearchResult, ...] = (
    LocationSearchResult(
        kind="coast",
        location="Brighton, England, United Kingdom",
        display_location="BRIGHTON ENGLAND GB",
        latitude=50.82838,
        longitude=-0.13947,
    ),
    LocationSearchResult(
        kind="coast",
        location="Portsmouth, England, United Kingdom",
        display_location="PORTSMOUTH ENGLAND GB",
        latitude=50.79899,
        longitude=-1.09125,
    ),
    LocationSearchResult(
        kind="coast",
        location="Plymouth, England, United Kingdom",
        display_location="PLYMOUTH ENGLAND GB",
        latitude=50.37153,
        longitude=-4.14305,
    ),
    LocationSearchResult(
        kind="coast",
        location="Aberdeen, Scotland, United Kingdom",
        display_location="ABERDEEN SCOTLAND GB",
        latitude=57.14369,
        longitude=-2.09814,
    ),
    LocationSearchResult(
        kind="coast",
        location="Cardiff, Wales, United Kingdom",
        display_location="CARDIFF WALES GB",
        latitude=51.48,
        longitude=-3.18,
    ),
    LocationSearchResult(
        kind="coast",
        location="Bangor, Northern Ireland, United Kingdom",
        display_location="BANGOR NORTHERN IRELAND GB",
        latitude=54.66079,
        longitude=-5.66802,
    ),
    LocationSearchResult(
        kind="coast",
        location="Lisbon, Portugal",
        display_location="LISBON LISBON DISTRICT PT",
        latitude=38.72509,
        longitude=-9.1498,
    ),
    LocationSearchResult(
        kind="coast",
        location="New York, United States",
        display_location="NEW YORK US",
        latitude=40.71427,
        longitude=-74.00597,
    ),
    LocationSearchResult(
        kind="coast",
        location="Vancouver, British Columbia, Canada",
        display_location="VANCOUVER BRITISH COLUMBIA CA",
        latitude=49.24966,
        longitude=-123.11934,
    ),
    LocationSearchResult(
        kind="coast",
        location="Rio de Janeiro, Brazil",
        display_location="RIO DE JANEIRO BR",
        latitude=-22.90642,
        longitude=-43.18223,
    ),
    LocationSearchResult(
        kind="coast",
        location="Cape Town, Western Cape, South Africa",
        display_location="CAPE TOWN WESTERN CAPE ZA",
        latitude=-33.92584,
        longitude=18.42322,
    ),
    LocationSearchResult(
        kind="coast",
        location="Mumbai, Maharashtra, India",
        display_location="MUMBAI MAHARASHTRA IN",
        latitude=19.07283,
        longitude=72.88261,
    ),
    LocationSearchResult(
        kind="coast",
        location="Singapore",
        display_location="SINGAPORE SG",
        latitude=1.28967,
        longitude=103.85007,
    ),
    LocationSearchResult(
        kind="coast",
        location="Tokyo, Japan",
        display_location="TOKYO JP",
        latitude=35.6895,
        longitude=139.69171,
    ),
    LocationSearchResult(
        kind="coast",
        location="Sydney, New South Wales, Australia",
        display_location="SYDNEY NEW SOUTH WALES AU",
        latitude=-33.86785,
        longitude=151.20732,
    ),
    LocationSearchResult(
        kind="coast",
        location="Qingdao, Shandong, China",
        display_location="QINGDAO SHANDONG CN",
        latitude=36.06488,
        longitude=120.38042,
    ),
)
LOCATION_PRESET_IDS: tuple[str, ...] = (
    "uk_brighton",
    "uk_portsmouth",
    "uk_plymouth",
    "uk_aberdeen",
    "uk_cardiff",
    "uk_bangor_ni",
    "pt_lisbon",
    "us_new_york",
    "ca_vancouver",
    "br_rio",
    "za_cape_town",
    "in_mumbai",
    "sg_singapore",
    "jp_tokyo",
    "au_sydney",
    "cn_qingdao",
)


@dataclass(frozen=True)
class LocationConfig:
    latitude: float
    longitude: float
    name: str
    display_name: str
    kind: LocationKind

    @property
    def cache_key(self) -> tuple[float, float, str, str, str]:
        return (
            self.latitude,
            self.longitude,
            self.name,
            self.display_name,
            self.kind,
        )


_cache_lock = threading.Lock()
_cache: dict[
    tuple[float, float, str, str, str], tuple[EnvironmentResponse, float]
] = {}
_geocoding_cache_lock = threading.Lock()
_geocoding_cache: dict[
    tuple[str, int], tuple[tuple[LocationSearchResult, ...], float]
] = {}
_geo_preset_cache: dict[int, tuple[DeviceLocationPreset, float]] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_location_presets() -> list[LocationSearchResult]:
    return [preset.model_copy(deep=True) for preset in LOCATION_PRESETS]


def get_device_location_presets() -> list[DeviceLocationPreset]:
    return [
        DeviceLocationPreset(
            id=preset_id,
            kind=preset.kind,
            name=preset.location,
            display_location=preset.display_location,
            lat=preset.latitude,
            lon=preset.longitude,
        )
        for preset_id, preset in zip(LOCATION_PRESET_IDS, LOCATION_PRESETS, strict=True)
    ]


def get_device_location_preset(preset_id: str) -> DeviceLocationPreset | None:
    return next(
        (preset for preset in get_device_location_presets() if preset.id == preset_id),
        None,
    )


def _configured_location() -> LocationConfig | None:
    latitude_text = os.getenv("COAST_LATITUDE", "").strip()
    longitude_text = os.getenv("COAST_LONGITUDE", "").strip()
    name = os.getenv("COAST_LOCATION_NAME", "").strip()
    display_name = os.getenv("COAST_DISPLAY_LOCATION", "COAST STATION").strip()
    if not latitude_text or not longitude_text or not name:
        return None

    try:
        latitude = float(latitude_text)
        longitude = float(longitude_text)
    except ValueError:
        logger.warning("Invalid COAST_LATITUDE or COAST_LONGITUDE; using demo data")
        return None

    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        logger.warning("Configured coastal coordinates are out of range; using demo data")
        return None
    return LocationConfig(
        latitude=latitude,
        longitude=longitude,
        name=name,
        display_name=display_name or "COAST STATION",
        kind="coast",
    )


def _device_location(device_id: str | None) -> LocationConfig | None:
    if not device_id:
        return None
    record = get_device_location(device_id)
    if record is None:
        return None
    return LocationConfig(
        latitude=float(record["latitude"]),
        longitude=float(record["longitude"]),
        name=str(record["location"]),
        display_name=str(record["display_location"]),
        kind=str(record.get("kind", "place")),
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result) or not result.is_integer():
        return None
    return int(result)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _weather_name(code: int | None) -> str:
    if code == 0:
        return "晴"
    if code == 1:
        return "大致晴朗"
    if code == 2:
        return "局部多云"
    if code == 3:
        return "阴"
    if code in (45, 48):
        return "雾"
    if code in (51, 53, 55, 56, 57):
        return "毛毛雨"
    if code in (61, 63, 65, 66, 67):
        return "雨"
    if code in (71, 73, 75, 77):
        return "雪"
    if code in (80, 81, 82):
        return "阵雨"
    if code in (85, 86):
        return "阵雪"
    if code in (95, 96, 99):
        return "雷暴"
    return "未知天气"


def _tide_status(marine_payload: Mapping[str, Any]) -> str | None:
    hourly = _mapping(marine_payload.get("hourly"))
    times = hourly.get("time")
    levels = hourly.get("sea_level_height_msl")
    if not isinstance(times, list) or not isinstance(levels, list):
        return None

    pairs = [
        (str(timestamp), level)
        for timestamp, raw_level in zip(times, levels)
        if (level := _number(raw_level)) is not None
    ]
    if len(pairs) < 2:
        return None

    current = _mapping(marine_payload.get("current"))
    current_time = current.get("time")
    delta: float | None = None
    if isinstance(current_time, str):
        current_index = next(
            (index for index, (timestamp, _) in enumerate(pairs) if timestamp == current_time),
            None,
        )
        if current_index is not None:
            if current_index + 1 < len(pairs):
                delta = pairs[current_index + 1][1] - pairs[current_index][1]
            elif current_index > 0:
                delta = pairs[current_index][1] - pairs[current_index - 1][1]

    if delta is None:
        delta = pairs[-1][1] - pairs[0][1]
    if delta > 0.01:
        return "涨潮"
    if delta < -0.01:
        return "落潮"
    return "平潮"


def _request_json(
    client: httpx2.Client, url: str, params: Mapping[str, object]
) -> Mapping[str, Any]:
    response = client.get(url, params=params)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise TypeError(f"Open-Meteo returned a non-object response for {url}")
    return payload


def _request_geocoding_json(
    client: httpx2.Client, url: str, params: Mapping[str, object]
) -> Mapping[str, Any]:
    """Retry one transient geocoder failure within the ESP32's 12 s budget."""

    last_error: Exception | None = None
    for attempt in range(GEOCODING_RETRY_ATTEMPTS):
        try:
            return _request_json(client, url, params)
        except Exception as exc:  # noqa: BLE001 - retry all provider/protocol failures.
            last_error = exc
            if attempt + 1 < GEOCODING_RETRY_ATTEMPTS:
                time.sleep(0.1)
    assert last_error is not None
    raise last_error


def _geocoding_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = payload.get("results")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _location_label(row: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("name", "admin1", "admin2", "country"):
        value = row.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    return " · ".join(parts)[:80]


def _truncate_utf8(value: str, max_bytes: int) -> str:
    """Truncate without ever returning a partial UTF-8 code point."""

    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def _text(row: Mapping[str, Any], key: str) -> str | None:
    value = row.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


_PLACE_SUFFIXES = ("特别行政区", "自治州", "地区", "省", "市", "县", "区", "州", "盟", "旗")
_MAJOR_PLACE_CODES = frozenset(("PPLC", "PPLA", "PPLA2", "PPLA3"))
_FEATURE_RANK = {
    "PPLC": 0,
    "PPLA": 1,
    "PPLA2": 2,
    "PPLA3": 3,
    "PPLA4": 4,
    "PPL": 5,
}


def _normalized_place_name(value: str) -> str:
    normalized = "".join(value.casefold().split())
    for suffix in _PLACE_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _query_variants(query: str) -> tuple[str, ...]:
    variants = [query]
    contains_cjk = any("\u4e00" <= character <= "\u9fff" for character in query)
    has_place_suffix = any(query.endswith(suffix) for suffix in _PLACE_SUFFIXES)
    if contains_cjk and not has_place_suffix:
        variants.append(f"{query}市")
    return tuple(variants)


def _row_sort_key(row: Mapping[str, Any], query: str) -> tuple[object, ...]:
    name = _text(row, "name") or ""
    exact_name = _normalized_place_name(name) == _normalized_place_name(query)
    feature_code = _text(row, "feature_code") or ""
    population = _integer(row.get("population")) or 0
    return (
        0 if exact_name else 1,
        0 if population > 0 else 1,
        -population,
        _FEATURE_RANK.get(feature_code, 99),
        _location_label(row),
    )


def _ascii_component(value: object) -> str:
    if not isinstance(value, str):
        return ""
    # Preserve Latin base letters in names such as Sao Paulo while still
    # emitting only characters supported by the LCD's compact ASCII font.
    normalized = unicodedata.normalize("NFKD", value)
    cleaned = "".join(
        character
        if character.isascii()
        and (character.isalnum() or character in " ._-")
        else ("" if unicodedata.combining(character) else " ")
        for character in normalized.upper()
    )
    return " ".join(cleaned.split()).strip(" ._-")


def _ascii_display_name(row: Mapping[str, Any]) -> str:
    """Build a short LCD label that still distinguishes global namesakes."""

    name = _ascii_component(row.get("name")) or "COAST STATION"
    admin1 = _ascii_component(row.get("admin1"))
    country_code = _ascii_component(row.get("country_code"))
    country = country_code or _ascii_component(row.get("country"))

    components: list[str] = []
    for component in (name, admin1, country):
        if component and component.casefold() not in {
            existing.casefold() for existing in components
        }:
            components.append(component)
    full_name = " ".join(components)
    if len(full_name) <= 32:
        return full_name

    # Preserve an administrative hint and country in the 32-character LCD
    # field, shortening the primary place name first when required.
    compact_country = country[:3] if country_code else country[:8]
    compact_admin1 = admin1[:10]
    tail = [part for part in (compact_admin1, compact_country) if part]
    tail_length = sum(len(part) for part in tail) + len(tail)
    name_length = max(6, 32 - tail_length)
    compact = " ".join([name[:name_length].rstrip(), *tail]).strip()
    return compact[:32].rstrip()


_GEO_LOCATION_ID_PREFIX = "geo_"


def _geo_location_id(provider_id: int) -> str | None:
    if provider_id <= 0:
        return None
    location_id = f"{_GEO_LOCATION_ID_PREFIX}{provider_id}"
    return location_id if len(location_id) <= 24 else None


def parse_geo_location_id(location_id: str) -> int | None:
    if not location_id.startswith(_GEO_LOCATION_ID_PREFIX):
        return None
    provider_id_text = location_id[len(_GEO_LOCATION_ID_PREFIX) :]
    if not provider_id_text or not provider_id_text.isascii():
        return None
    if not provider_id_text.isdigit() or provider_id_text.startswith("0"):
        return None
    provider_id = int(provider_id_text)
    return provider_id if _geo_location_id(provider_id) == location_id else None


def search_locations(query: str, count: int = 8) -> list[LocationSearchResult]:
    """Resolve a user-entered place name into selectable WGS84 coordinates."""

    cache_key = (" ".join(query.casefold().split()), count)
    now = time.monotonic()
    stale_cached: tuple[LocationSearchResult, ...] | None = None
    with _geocoding_cache_lock:
        cached = _geocoding_cache.get(cache_key)
        if cached is not None:
            stale_cached = cached[0]
            if now - cached[1] < GEOCODING_CACHE_TTL_SECONDS:
                return [item.model_copy(deep=True) for item in cached[0]]

    localized_rows: dict[object, Mapping[str, Any]] = {}
    english_by_id: dict[object, Mapping[str, Any]] = {}
    successful_requests = 0
    last_provider_error: Exception | None = None
    with httpx2.Client(
        timeout=GEOCODING_REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": "CoastalWarningSystem/0.3"},
    ) as client:
        for variant in _query_variants(query):
            params: dict[str, object] = {
                "name": variant,
                "count": max(count, 10),
                "format": "json",
            }
            localized_payload: Mapping[str, Any] = {}
            # The board keyboard is ASCII. For London/Brighton/etc. an English
            # request is sufficient and halves latency/failure exposure. Keep
            # the localized lookup for non-ASCII dashboard searches.
            if not query.isascii():
                try:
                    localized_payload = _request_geocoding_json(
                        client,
                        GEOCODING_API_URL,
                        {**params, "language": "zh"},
                    )
                    successful_requests += 1
                except Exception as exc:  # noqa: BLE001 - preserve alternate-language fallback.
                    last_provider_error = exc
                    logger.warning("Localized location lookup failed: %s", exc)
            try:
                english_payload = _request_geocoding_json(
                    client, GEOCODING_API_URL, {**params, "language": "en"}
                )
                successful_requests += 1
            except Exception as exc:  # noqa: BLE001 - an empty result is the safe fallback.
                last_provider_error = exc
                english_payload = {}
                logger.warning("English location lookup failed: %s", exc)
            for row in _geocoding_rows(localized_payload):
                row_id = row.get("id")
                key = row_id if isinstance(row_id, int) else (
                    row.get("name"),
                    row.get("latitude"),
                    row.get("longitude"),
                )
                localized_rows.setdefault(key, row)
            for row in _geocoding_rows(english_payload):
                row_id = row.get("id")
                key = row_id if isinstance(row_id, int) else (
                    row.get("name"),
                    row.get("latitude"),
                    row.get("longitude"),
                )
                if isinstance(row_id, int):
                    english_by_id.setdefault(row_id, row)
                # Open-Meteo can legitimately have no localized row while the
                # English lookup succeeds. Keep the English row as a fallback,
                # without replacing a localized row for the same provider ID.
                localized_rows.setdefault(key, row)

    if successful_requests == 0 and last_provider_error is not None:
        if stale_cached is not None:
            logger.warning("Serving stale cached geocoding result for %r", query)
            return [item.model_copy(deep=True) for item in stale_cached]
        raise last_provider_error

    def comparison_row(row: Mapping[str, Any]) -> Mapping[str, Any]:
        provider_id = row.get("id")
        return english_by_id.get(provider_id, row)

    rows = sorted(
        localized_rows.values(),
        key=lambda row: _row_sort_key(comparison_row(row), query),
    )
    normalized_query = _normalized_place_name(query)
    exact_matches = [
        row
        for row in rows
        if _normalized_place_name(
            _text(comparison_row(row), "name") or ""
        )
        == normalized_query
    ]
    significant_matches = [
        row
        for row in exact_matches
        if _text(comparison_row(row), "feature_code") in _MAJOR_PLACE_CODES
        or _integer(comparison_row(row).get("population")) is not None
    ]
    # Prefer exact, populated/administrative places. This keeps a major city
    # such as Changchun free of village noise without hiding Brighton (which
    # Open-Meteo classifies as an ordinary PPL rather than an admin capital).
    selected_rows = (
        (significant_matches or exact_matches)[:count]
        if exact_matches
        else rows[:count]
    )

    missing_english_ids = [
        row.get("id")
        for row in selected_rows
        if isinstance(row.get("id"), int)
        and not _ascii_component(
            english_by_id.get(row.get("id"), row).get("name")
        )
    ]
    if missing_english_ids:
        with httpx2.Client(
            timeout=GEOCODING_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": "CoastalWarningSystem/0.3"},
        ) as client:
            for provider_id in missing_english_ids:
                try:
                    english_by_id[provider_id] = _request_geocoding_json(
                        client,
                        GEOCODING_GET_URL,
                        {"id": provider_id, "language": "en"},
                    )
                except Exception as exc:  # noqa: BLE001 - enrichment is best-effort only.
                    logger.warning(
                        "Open-Meteo English location lookup failed for id %s: %s",
                        provider_id,
                        exc,
                    )

    results: list[LocationSearchResult] = []
    for row in selected_rows:
        latitude = _number(row.get("latitude"))
        longitude = _number(row.get("longitude"))
        if latitude is None or longitude is None:
            continue
        if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
            continue
        provider_id = row.get("id") if isinstance(row.get("id"), int) else None
        english_row = dict(row)
        english_row.update(english_by_id.get(provider_id, {}))
        location = _location_label(row)
        if not location:
            continue
        results.append(
            LocationSearchResult(
                provider_id=provider_id,
                name=_text(row, "name"),
                admin1=_text(row, "admin1"),
                admin2=_text(row, "admin2"),
                country=_text(row, "country"),
                feature_code=_text(row, "feature_code"),
                population=_integer(row.get("population")),
                kind="place",
                location=location,
                display_location=_ascii_display_name(english_row),
                latitude=latitude,
                longitude=longitude,
            )
        )
    with _geocoding_cache_lock:
        _geocoding_cache[cache_key] = (
            tuple(item.model_copy(deep=True) for item in results),
            now,
        )
    return results


def search_device_locations(query: str, count: int = 8) -> list[DeviceLocationPreset]:
    """Return compact, server-issued location choices for the ESP32."""

    results: list[DeviceLocationPreset] = []
    for location in search_locations(query, max(1, min(count, 8))):
        if location.provider_id is None:
            continue
        location_id = _geo_location_id(location.provider_id)
        if location_id is None:
            continue
        name = _truncate_utf8(location.location, 79)
        if not name:
            continue
        preset = DeviceLocationPreset(
            id=location_id,
            kind="place",
            name=name,
            display_location=location.display_location,
            lat=location.latitude,
            lon=location.longitude,
        )
        results.append(preset)
        with _geocoding_cache_lock:
            _geo_preset_cache[location.provider_id] = (
                preset.model_copy(deep=True),
                time.monotonic(),
            )
    return results


def get_geo_device_location_preset(
    provider_id: int,
) -> DeviceLocationPreset | None:
    """Resolve a server-issued GeoNames ID; never accept device coordinates."""

    location_id = _geo_location_id(provider_id)
    if location_id is None:
        return None
    now = time.monotonic()
    stale_cached: DeviceLocationPreset | None = None
    with _geocoding_cache_lock:
        cached = _geo_preset_cache.get(provider_id)
        if cached is not None:
            stale_cached = cached[0]
            if now - cached[1] < GEOCODING_CACHE_TTL_SECONDS:
                return cached[0].model_copy(deep=True)
    with httpx2.Client(
        timeout=GEOCODING_REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": "CoastalWarningSystem/0.3"},
    ) as client:
        try:
            row = _request_geocoding_json(
                client,
                GEOCODING_GET_URL,
                {"id": provider_id, "language": "en"},
            )
        except Exception:
            if stale_cached is not None:
                logger.warning(
                    "Serving stale cached canonical location id=%s", provider_id
                )
                return stale_cached.model_copy(deep=True)
            raise

    returned_id = row.get("id")
    if returned_id is not None and returned_id != provider_id:
        return None
    latitude = _number(row.get("latitude"))
    longitude = _number(row.get("longitude"))
    if (
        latitude is None
        or longitude is None
        or not -90.0 <= latitude <= 90.0
        or not -180.0 <= longitude <= 180.0
    ):
        return None
    name = _truncate_utf8(_location_label(row), 79)
    if not name:
        return None
    preset = DeviceLocationPreset(
        id=location_id,
        kind="place",
        name=name,
        display_location=_ascii_display_name(row),
        lat=latitude,
        lon=longitude,
    )
    with _geocoding_cache_lock:
        _geo_preset_cache[provider_id] = (preset.model_copy(deep=True), now)
    return preset


def _fetch_open_meteo(location: LocationConfig) -> EnvironmentResponse:
    common_params: dict[str, object] = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "timezone": "auto",
    }
    with httpx2.Client(
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": "CoastalWarningSystem/0.2"},
    ) as client:
        weather_payload = _request_json(
            client,
            WEATHER_API_URL,
            {**common_params, "current": _WEATHER_CURRENT_VARIABLES},
        )
        marine_payload: Mapping[str, Any] = {}
        if location.kind == "coast":
            marine_payload = _request_json(
                client,
                MARINE_API_URL,
                {
                    **common_params,
                    "current": _MARINE_CURRENT_VARIABLES,
                    "hourly": "sea_level_height_msl",
                    "past_hours": 1,
                    "forecast_hours": 2,
                },
            )

    weather_current = _mapping(weather_payload.get("current"))
    marine_current = _mapping(marine_payload.get("current"))
    weather_code = _integer(weather_current.get("weather_code"))
    return EnvironmentResponse(
        location=location.name,
        display_location=location.display_name,
        kind=location.kind,
        weather=_weather_name(weather_code),
        weather_code=weather_code,
        air_temperature_c=_number(weather_current.get("temperature_2m")),
        humidity_percent=_number(weather_current.get("relative_humidity_2m")),
        wind_speed_kmh=_number(weather_current.get("wind_speed_10m")),
        wind_direction_deg=_number(weather_current.get("wind_direction_10m")),
        water_temperature_c=_number(marine_current.get("sea_surface_temperature")),
        wave_height_m=_number(marine_current.get("wave_height")),
        wave_period_s=_number(marine_current.get("wave_period")),
        sea_level_height_m=_number(marine_current.get("sea_level_height_msl")),
        tide_status=_tide_status(marine_payload),
        ocean_current_velocity_kmh=_number(
            marine_current.get("ocean_current_velocity")
        ),
        ocean_current_direction_deg=_number(
            marine_current.get("ocean_current_direction")
        ),
        source="open-meteo",
        provider="open-meteo",
        stale=False,
        updated_at=_utc_now(),
    )


def _demo_environment() -> EnvironmentResponse:
    return EnvironmentResponse(
        location="未配置地点（演示）",
        display_location="COAST STATION",
        kind="coast",
        weather="多云（演示）",
        weather_code=3,
        air_temperature_c=29.2,
        humidity_percent=76.0,
        wind_speed_kmh=12.0,
        wind_direction_deg=135.0,
        water_temperature_c=26.4,
        wave_height_m=0.6,
        wave_period_s=5.0,
        sea_level_height_m=0.2,
        tide_status="涨潮（演示）",
        ocean_current_velocity_kmh=0.8,
        ocean_current_direction_deg=80.0,
        source="demo",
        provider="built-in-demo",
        stale=False,
        updated_at=_utc_now(),
    )


def _unavailable_environment(location: LocationConfig) -> EnvironmentResponse:
    return EnvironmentResponse(
        location=location.name,
        display_location=location.display_name,
        kind=location.kind,
        weather="环境数据暂不可用",
        source="stale",
        provider="open-meteo",
        stale=True,
        updated_at=_utc_now(),
    )


def get_environment(device_id: str | None = None) -> EnvironmentResponse:
    """Return configured live data, a fresh cache entry, or an explicit fallback."""

    location = _device_location(device_id) or _configured_location()
    if location is None:
        return _demo_environment()

    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(location.cache_key)
        if cached is not None and now - cached[1] < CACHE_TTL_SECONDS:
            return cached[0].model_copy(deep=True)

        try:
            fresh = _fetch_open_meteo(location)
        except Exception as exc:  # noqa: BLE001 - all provider failures share one fallback.
            logger.warning("Open-Meteo environment refresh failed: %s", exc)
            if cached is not None:
                return cached[0].model_copy(
                    deep=True, update={"source": "stale", "stale": True}
                )
            return _unavailable_environment(location)

        _cache[location.cache_key] = (fresh, now)
        return fresh.model_copy(deep=True)


def clear_environment_cache() -> None:
    """Clear process-local provider state; primarily useful for deterministic tests."""

    with _cache_lock:
        _cache.clear()
    with _geocoding_cache_lock:
        _geocoding_cache.clear()
        _geo_preset_cache.clear()
