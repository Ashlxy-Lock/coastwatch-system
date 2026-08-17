"""Immutable registration and validation for UK-official coastal datasets.

The training pipeline deliberately accepts a registered bundle rather than an
arbitrary list of rows.  A bundle consists of a provenance manifest and one
harmonised CSV table.  The manifest pins both the raw-source citations/hashes
and the table hash, so later training runs can be reproduced and audited.

``synthetic_test_fixture`` is supported only behind an explicit test switch.
Such a registration is always non-activatable and can never masquerade as UK
official observations.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlparse

DATASET_SCHEMA = "coastwatch.uk-official-dataset"
DATASET_SCHEMA_VERSION = 1
REGISTRATION_SCHEMA = "coastwatch.official-dataset-registration"
REGISTRATION_SCHEMA_VERSION = 1
OFFICIAL_DATA_ORIGIN = "uk_official_archive"
TEST_DATA_ORIGIN = "synthetic_test_fixture"

OFFICIAL_FEATURE_ORDER: tuple[str, ...] = (
    "relative_water_level_m",
    "predicted_tide_relative_m",
    "significant_wave_height_m",
    "wave_period_s",
    "wind_speed_m_s",
    "wind_gust_m_s",
    "surface_pressure_hpa",
    "rainfall_mm_h",
    "air_temperature_c",
    "relative_humidity_percent",
    "water_temperature_c",
    "ocean_current_velocity_m_s",
    "hour_sin",
    "hour_cos",
    "day_of_year_sin",
    "day_of_year_cos",
    "latitude",
    "longitude",
)
NON_WATER_FEATURE_ORDER: tuple[str, ...] = tuple(
    name for name in OFFICIAL_FEATURE_ORDER if name != "relative_water_level_m"
)
REQUIRED_TABLE_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "site_id",
    "storm_group_id",
    "target_extreme_water",
    *OFFICIAL_FEATURE_ORDER,
)

_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SITE_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_BACKGROUND_STORM_GROUPS = {
    "",
    "none",
    "non-event",
    "non_event",
    "background",
    "normal",
}
ESP_MAX_WIND_M_S = 500.0 / 3.6
ESP_MAX_CURRENT_M_S = 100.0 / 3.6
OFFICIAL_FEATURE_RANGES: Mapping[str, tuple[float, float]] = MappingProxyType(
    {
        # The ESP32 environment parser accepts sea level only in [-20, 20] m.
        "relative_water_level_m": (-20.0, 20.0),
        "predicted_tide_relative_m": (-20.0, 20.0),
        "significant_wave_height_m": (0.0, 50.0),
        "wave_period_s": (0.0, 120.0),
        # Firmware accepts wind_speed_kmh in [0, 500]; the table uses m/s.
        "wind_speed_m_s": (0.0, ESP_MAX_WIND_M_S),
        "wind_gust_m_s": (0.0, ESP_MAX_WIND_M_S),
        # Wider than observed UK records, while rejecting unit mistakes (Pa/kPa).
        "surface_pressure_hpa": (800.0, 1100.0),
        "rainfall_mm_h": (0.0, 500.0),
        "air_temperature_c": (-100.0, 100.0),
        "relative_humidity_percent": (0.0, 100.0),
        "water_temperature_c": (-10.0, 60.0),
        # Firmware accepts ocean_current_velocity_kmh in [0, 100].
        "ocean_current_velocity_m_s": (0.0, ESP_MAX_CURRENT_M_S),
        "hour_sin": (-1.0, 1.0),
        "hour_cos": (-1.0, 1.0),
        "day_of_year_sin": (-1.0, 1.0),
        "day_of_year_cos": (-1.0, 1.0),
        "latitude": (-90.0, 90.0),
        "longitude": (-180.0, 180.0),
    }
)
OFFICIAL_FEATURE_UNITS: Mapping[str, str] = MappingProxyType(
    {
        "relative_water_level_m": "m",
        "predicted_tide_relative_m": "m",
        "significant_wave_height_m": "m",
        "wave_period_s": "s",
        "wind_speed_m_s": "m/s",
        "wind_gust_m_s": "m/s",
        "surface_pressure_hpa": "hPa",
        "rainfall_mm_h": "mm/h",
        "air_temperature_c": "degC",
        "relative_humidity_percent": "%",
        "water_temperature_c": "degC",
        "ocean_current_velocity_m_s": "m/s",
        "hour_sin": "unitless",
        "hour_cos": "unitless",
        "day_of_year_sin": "unitless",
        "day_of_year_cos": "unitless",
        "latitude": "degree",
        "longitude": "degree",
    }
)
_COORDINATE_ABS_TOLERANCE = 1e-6
_TIME_CYCLE_ABS_TOLERANCE = 1e-6


class OfficialDatasetError(ValueError):
    """Raised when an official dataset violates its provenance contract."""


@dataclass(frozen=True)
class ValidatedOfficialDataset:
    """A hash-verified in-memory view of one bundle."""

    dataset_id: str
    version: str
    data_origin: str
    manifest: Mapping[str, Any]
    manifest_path: Path | None
    table_path: Path
    manifest_sha256: str
    manifest_file_sha256: str | None
    table_sha256: str
    activatable: bool
    rows: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class RegisteredOfficialDataset:
    """A validated bundle tied to an immutable local registration record."""

    dataset_id: str
    version: str
    data_origin: str
    manifest: Mapping[str, Any]
    manifest_path: Path
    table_path: Path
    registration_path: Path
    registration_sha256: str
    manifest_sha256: str
    manifest_file_sha256: str
    table_sha256: str
    activatable: bool
    rows: tuple[Mapping[str, Any], ...]


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Return the one canonical JSON representation used for audit hashes."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_official_dataset(
    manifest: Mapping[str, Any],
    table_path: Path | str,
    *,
    allow_synthetic_test_fixture: bool = False,
    manifest_path: Path | str | None = None,
) -> ValidatedOfficialDataset:
    """Validate provenance, content hash, table schema, and chronological splits.

    Runtime callers must leave ``allow_synthetic_test_fixture`` false.  The
    switch exists solely so unit tests can exercise the complete pipeline
    without creating a false UK-official dataset.
    """

    if not isinstance(manifest, Mapping):
        raise OfficialDatasetError("dataset manifest must be a JSON object")
    manifest_copy = _json_mapping_copy(manifest, "dataset manifest")
    _validate_manifest_structure(
        manifest_copy,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )

    resolved_table = Path(table_path).resolve()
    if not resolved_table.is_file():
        raise OfficialDatasetError(f"harmonised table not found: {resolved_table}")
    table_hash = file_sha256(resolved_table)
    expected_table_hash = manifest_copy["table"]["sha256"]
    if table_hash != expected_table_hash:
        raise OfficialDatasetError("harmonised table sha256 does not match manifest")
    if resolved_table.name != manifest_copy["table"]["file"]:
        raise OfficialDatasetError("table filename does not match manifest table.file")
    _verify_source_archives(manifest_copy, resolved_table.parent)

    rows = _read_and_validate_rows(resolved_table, manifest_copy)
    expected_row_count = manifest_copy["table"]["row_count"]
    if len(rows) != expected_row_count:
        raise OfficialDatasetError(
            "harmonised table row count does not match manifest: "
            f"expected {expected_row_count}, got {len(rows)}"
        )

    encoded_manifest = canonical_json_bytes(manifest_copy)
    return ValidatedOfficialDataset(
        dataset_id=manifest_copy["dataset_id"],
        version=manifest_copy["version"],
        data_origin=manifest_copy["data_origin"],
        manifest=MappingProxyType(manifest_copy),
        manifest_path=(Path(manifest_path).resolve() if manifest_path else None),
        table_path=resolved_table,
        manifest_sha256=hashlib.sha256(encoded_manifest).hexdigest(),
        manifest_file_sha256=(
            file_sha256(Path(manifest_path).resolve()) if manifest_path else None
        ),
        table_sha256=table_hash,
        activatable=manifest_copy["data_origin"] == OFFICIAL_DATA_ORIGIN,
        rows=tuple(MappingProxyType(row) for row in rows),
    )


def register_official_dataset(
    manifest_path: Path | str,
    table_path: Path | str,
    registry_dir: Path | str,
    *,
    dataset_root: Path | str,
    allow_synthetic_test_fixture: bool = False,
) -> RegisteredOfficialDataset:
    """Hash-validate a bundle and write a small immutable registration record."""

    resolved_manifest, resolved_table = _validate_bundle_paths(
        dataset_root, manifest_path, table_path
    )
    if not resolved_manifest.is_file():
        raise OfficialDatasetError(f"dataset manifest not found: {resolved_manifest}")
    try:
        manifest = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OfficialDatasetError("dataset manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, Mapping) or (
        manifest.get("dataset_id") != resolved_manifest.parent.parent.name
        or manifest.get("version") != resolved_manifest.parent.name
    ):
        raise OfficialDatasetError(
            "manifest dataset_id/version must match root/dataset_id/version layout"
        )
    validated = validate_official_dataset(
        manifest,
        resolved_table,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
        manifest_path=resolved_manifest,
    )

    resolved_registry = Path(registry_dir).resolve()
    resolved_registry.mkdir(parents=True, exist_ok=True)
    filename = f"{validated.dataset_id}--{validated.version}.registration.json"
    registration_path = resolved_registry / filename
    if registration_path.exists():
        existing = load_registered_official_dataset(
            registration_path,
            dataset_root=dataset_root,
            allow_synthetic_test_fixture=allow_synthetic_test_fixture,
        )
        if (
            existing.manifest_sha256 != validated.manifest_sha256
            or existing.manifest_file_sha256 != validated.manifest_file_sha256
            or existing.table_sha256 != validated.table_sha256
        ):
            raise OfficialDatasetError(
                "dataset id/version is already registered with different content"
            )
        return existing

    record: dict[str, Any] = {
        "schema": REGISTRATION_SCHEMA,
        "schema_version": REGISTRATION_SCHEMA_VERSION,
        "dataset_id": validated.dataset_id,
        "version": validated.version,
        "data_origin": validated.data_origin,
        "manifest_path": str(resolved_manifest),
        "table_path": str(validated.table_path),
        "manifest_sha256": validated.manifest_sha256,
        "manifest_file_sha256": validated.manifest_file_sha256,
        "table_sha256": validated.table_sha256,
        "activatable": validated.activatable,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    record["registration_sha256"] = canonical_sha256(record)
    temporary_path = registration_path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(registration_path)
    return load_registered_official_dataset(
        registration_path,
        dataset_root=dataset_root,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )


def load_registered_official_dataset(
    registration_path: Path | str,
    *,
    dataset_root: Path | str,
    allow_synthetic_test_fixture: bool = False,
) -> RegisteredOfficialDataset:
    """Reload and re-verify a registration plus both referenced bundle files."""

    resolved_registration = Path(registration_path).resolve()
    try:
        record = json.loads(resolved_registration.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OfficialDatasetError("dataset registration is not valid JSON") from exc
    if not isinstance(record, Mapping):
        raise OfficialDatasetError("dataset registration must be a JSON object")
    record_copy = _json_mapping_copy(record, "dataset registration")
    if record_copy.get("schema") != REGISTRATION_SCHEMA:
        raise OfficialDatasetError("unsupported dataset registration schema")
    if record_copy.get("schema_version") != REGISTRATION_SCHEMA_VERSION:
        raise OfficialDatasetError("unsupported dataset registration schema_version")
    supplied_hash = record_copy.pop("registration_sha256", None)
    if not isinstance(supplied_hash, str) or supplied_hash != canonical_sha256(
        record_copy
    ):
        raise OfficialDatasetError("dataset registration hash is invalid")
    record_copy["registration_sha256"] = supplied_hash

    manifest_path = _absolute_registered_path(record_copy, "manifest_path")
    table_path = _absolute_registered_path(record_copy, "table_path")
    manifest_path, table_path = _validate_bundle_paths(
        dataset_root, manifest_path, table_path
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OfficialDatasetError("registered manifest cannot be read") from exc
    validated = validate_official_dataset(
        manifest,
        table_path,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
        manifest_path=manifest_path,
    )
    for key, actual in (
        ("dataset_id", validated.dataset_id),
        ("version", validated.version),
        ("data_origin", validated.data_origin),
        ("manifest_sha256", validated.manifest_sha256),
        ("manifest_file_sha256", validated.manifest_file_sha256),
        ("table_sha256", validated.table_sha256),
        ("activatable", validated.activatable),
    ):
        if record_copy.get(key) != actual:
            raise OfficialDatasetError(f"registered {key} no longer matches bundle")
    return RegisteredOfficialDataset(
        dataset_id=validated.dataset_id,
        version=validated.version,
        data_origin=validated.data_origin,
        manifest=validated.manifest,
        manifest_path=manifest_path,
        table_path=table_path,
        registration_path=resolved_registration,
        registration_sha256=supplied_hash,
        manifest_sha256=validated.manifest_sha256,
        manifest_file_sha256=str(validated.manifest_file_sha256),
        table_sha256=validated.table_sha256,
        activatable=validated.activatable,
        rows=validated.rows,
    )


def freeze_official_sensor_context(
    dataset: RegisteredOfficialDataset,
    *,
    site_id: str,
    timestamp: datetime | str | None = None,
    split: str = "frozen_test",
) -> dict[str, Any]:
    """Freeze one exact official row as non-water context for a sensor test."""

    if split not in {"validation", "frozen_test"}:
        raise OfficialDatasetError(
            "sensor context split must be validation or frozen_test"
        )
    if site_id not in dataset.manifest["site_ids"]:
        raise OfficialDatasetError(f"unknown site_id: {site_id}")
    split_range = dataset.manifest["splits"][split]
    split_start = _parse_datetime(split_range["start"], f"splits.{split}.start")
    split_end = _parse_datetime(split_range["end"], f"splits.{split}.end")
    requested = (
        _parse_datetime(timestamp, "timestamp") if timestamp is not None else None
    )
    candidates = [
        row
        for row in dataset.rows
        if row["site_id"] == site_id
        and split_start <= row["timestamp"] <= split_end
        and (requested is None or row["timestamp"] == requested)
    ]
    if not candidates:
        raise OfficialDatasetError(
            "no exact official row matches sensor context request"
        )
    row = min(candidates, key=lambda item: item["timestamp"])
    context_features = {name: row[name] for name in NON_WATER_FEATURE_ORDER}
    row_for_hash = {
        "timestamp": row["timestamp"].isoformat(),
        "site_id": row["site_id"],
        "storm_group_id": row["storm_group_id"],
        "target_extreme_water": row["target_extreme_water"],
        **{name: row[name] for name in OFFICIAL_FEATURE_ORDER},
    }
    site_metadata = dataset.manifest["site_metadata"][site_id]
    source_row_sha256 = canonical_sha256(row_for_hash)
    context_id = (
        f"{dataset.dataset_id}:{dataset.version}:{site_id}:{source_row_sha256[:16]}"
    )
    return {
        "context_id": context_id,
        "timestamp": row["timestamp"].isoformat(),
        "site_id": site_id,
        "datum": site_metadata["datum"],
        "source_split": split,
        "features": context_features,
        "source_row_sha256": source_row_sha256,
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "dataset_registration_sha256": dataset.registration_sha256,
    }


def rows_for_split(
    dataset: RegisteredOfficialDataset,
    split: str,
    *,
    selected_site_ids: Sequence[str] | None = None,
) -> list[Mapping[str, Any]]:
    """Return rows inside one manifest-pinned chronological split."""

    if split not in {"train", "validation", "frozen_test"}:
        raise OfficialDatasetError(f"unknown split: {split}")
    site_ids = _selected_sites(dataset, selected_site_ids)
    period = dataset.manifest["splits"][split]
    start = _parse_datetime(period["start"], f"splits.{split}.start")
    end = _parse_datetime(period["end"], f"splits.{split}.end")
    return [
        row
        for row in dataset.rows
        if row["site_id"] in site_ids and start <= row["timestamp"] <= end
    ]


def _selected_sites(
    dataset: RegisteredOfficialDataset,
    selected_site_ids: Sequence[str] | None,
) -> tuple[str, ...]:
    available = tuple(dataset.manifest["site_ids"])
    if selected_site_ids is None:
        return available
    if isinstance(selected_site_ids, (str, bytes)) or not selected_site_ids:
        raise OfficialDatasetError("selected_site_ids must be a non-empty sequence")
    result = tuple(dict.fromkeys(str(item).strip() for item in selected_site_ids))
    if any(not item for item in result):
        raise OfficialDatasetError("selected_site_ids contains an empty id")
    unknown = sorted(set(result) - set(available))
    if unknown:
        raise OfficialDatasetError(f"unknown selected site ids: {', '.join(unknown)}")
    return result


def _validate_manifest_structure(
    manifest: dict[str, Any],
    *,
    allow_synthetic_test_fixture: bool,
) -> None:
    required = {
        "schema",
        "schema_version",
        "data_origin",
        "dataset_id",
        "version",
        "sources",
        "table",
        "site_ids",
        "site_metadata",
        "date_range",
        "feature_schema",
        "label_definition",
        "splits",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise OfficialDatasetError(
            f"dataset manifest missing fields: {', '.join(missing)}"
        )
    if manifest["schema"] != DATASET_SCHEMA:
        raise OfficialDatasetError("unsupported dataset manifest schema")
    if manifest["schema_version"] != DATASET_SCHEMA_VERSION:
        raise OfficialDatasetError("unsupported dataset manifest schema_version")
    origin = manifest["data_origin"]
    if origin == TEST_DATA_ORIGIN:
        if not allow_synthetic_test_fixture:
            raise OfficialDatasetError(
                "synthetic_test_fixture is disabled outside explicit tests"
            )
    elif origin != OFFICIAL_DATA_ORIGIN:
        raise OfficialDatasetError("data_origin must be uk_official_archive")

    for key in ("dataset_id", "version"):
        value = manifest[key]
        if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
            raise OfficialDatasetError(f"manifest {key} is not a safe identifier")

    sources = manifest["sources"]
    if not isinstance(sources, list) or not sources:
        raise OfficialDatasetError("manifest sources must be a non-empty list")
    source_filenames: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            raise OfficialDatasetError(f"sources[{index}] must be an object")
        for key in (
            "name",
            "owner",
            "citation",
            "license",
            "source_url",
            "retrieved_at",
            "original_filename",
            "sha256",
        ):
            value = source.get(key)
            if not isinstance(value, str) or not value.strip():
                raise OfficialDatasetError(f"sources[{index}].{key} is required")
        if not _SHA256_RE.fullmatch(source["sha256"]):
            raise OfficialDatasetError(f"sources[{index}].sha256 is invalid")
        parsed_url = urlparse(source["source_url"])
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise OfficialDatasetError(
                f"sources[{index}].source_url must be an absolute HTTPS URL"
            )
        _require_utc_timestamp(source["retrieved_at"], f"sources[{index}].retrieved_at")
        original_filename = _safe_filename(
            source["original_filename"], f"sources[{index}].original_filename"
        )
        if original_filename in source_filenames:
            raise OfficialDatasetError(
                "manifest source original_filename values must be unique"
            )
        source_filenames.add(original_filename)

    table = manifest["table"]
    if not isinstance(table, Mapping) or table.get("format") != "csv":
        raise OfficialDatasetError("manifest table.format must be csv")
    table_file = table.get("file")
    _safe_filename(table_file, "manifest table.file")
    if not str(table_file).lower().endswith(".csv"):
        raise OfficialDatasetError("manifest table.file must name a CSV file")
    if not isinstance(table.get("sha256"), str) or not _SHA256_RE.fullmatch(
        table["sha256"]
    ):
        raise OfficialDatasetError("manifest table.sha256 is invalid")
    row_count = table.get("row_count")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 1:
        raise OfficialDatasetError("manifest table.row_count must be positive")

    site_ids = manifest["site_ids"]
    if (
        not isinstance(site_ids, list)
        or not site_ids
        or any(
            not isinstance(item, str)
            or not _SITE_IDENTIFIER_RE.fullmatch(item)
            or len(item.encode("ascii")) > 63
            for item in site_ids
        )
        or len(site_ids) != len(set(site_ids))
    ):
        raise OfficialDatasetError("manifest site_ids must be unique safe identifiers")
    metadata = manifest["site_metadata"]
    if not isinstance(metadata, Mapping) or set(metadata) != set(site_ids):
        raise OfficialDatasetError("site_metadata must contain exactly every site_id")
    for site_id in site_ids:
        item = metadata[site_id]
        if not isinstance(item, Mapping):
            raise OfficialDatasetError(f"site_metadata.{site_id} must be an object")
        for key in ("name", "datum"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise OfficialDatasetError(f"site_metadata.{site_id}.{key} is required")
        for key in ("latitude", "longitude"):
            value = _finite_number(item.get(key), f"site_metadata.{site_id}.{key}")
            minimum, maximum = OFFICIAL_FEATURE_RANGES[key]
            if not minimum <= value <= maximum:
                raise OfficialDatasetError(
                    f"site_metadata.{site_id}.{key} must be in [{minimum}, {maximum}]"
                )

    date_range = manifest["date_range"]
    overall_start, overall_end = _validate_period(date_range, "date_range")
    if overall_start >= overall_end:
        raise OfficialDatasetError("date_range start must precede end")

    feature_schema = manifest["feature_schema"]
    if not isinstance(feature_schema, Mapping):
        raise OfficialDatasetError("feature_schema must be an object")
    if feature_schema.get("feature_order") != list(OFFICIAL_FEATURE_ORDER):
        raise OfficialDatasetError("feature_schema.feature_order is not supported")
    units = feature_schema.get("units")
    if not isinstance(units, Mapping) or set(units) != set(OFFICIAL_FEATURE_ORDER):
        raise OfficialDatasetError("feature_schema.units must cover every feature")
    for name, expected_unit in OFFICIAL_FEATURE_UNITS.items():
        if units.get(name) != expected_unit:
            raise OfficialDatasetError(
                f"feature_schema.units.{name} must be {expected_unit}"
            )

    label = manifest["label_definition"]
    if not isinstance(label, Mapping):
        raise OfficialDatasetError("label_definition must be an object")
    if label.get("column") != "target_extreme_water":
        raise OfficialDatasetError(
            "label_definition.column must be target_extreme_water"
        )
    if label.get("positive_class") != 1:
        raise OfficialDatasetError("label_definition.positive_class must be 1")
    if label.get("target_time_relation") != "future":
        raise OfficialDatasetError(
            "label must describe a future target; current-state threshold labels are rejected"
        )
    horizon = label.get("forecast_horizon_hours")
    if isinstance(horizon, bool) or not isinstance(horizon, int):
        raise OfficialDatasetError("forecast_horizon_hours must be an integer")
    if not 1 <= horizon <= 72:
        raise OfficialDatasetError("forecast_horizon_hours must be in [1, 72]")
    horizon_hours = float(horizon)
    for key in ("derivation", "official_reference"):
        if not isinstance(label.get(key), str) or not label[key].strip():
            raise OfficialDatasetError(f"label_definition.{key} is required")

    splits = manifest["splits"]
    if not isinstance(splits, Mapping):
        raise OfficialDatasetError("splits must be an object")
    periods: dict[str, tuple[datetime, datetime]] = {}
    for name in ("train", "validation", "frozen_test"):
        periods[name] = _validate_period(splits.get(name), f"splits.{name}")
        start, end = periods[name]
        if start < overall_start or end > overall_end:
            raise OfficialDatasetError(f"splits.{name} falls outside date_range")
        if start >= end:
            raise OfficialDatasetError(f"splits.{name} start must precede end")
    gap = splits.get("leakage_gap_hours")
    if isinstance(gap, bool) or not isinstance(gap, (int, float)):
        raise OfficialDatasetError("splits.leakage_gap_hours must be numeric")
    gap_hours = float(gap)
    if not math.isfinite(gap_hours) or gap_hours <= 0:
        raise OfficialDatasetError("splits.leakage_gap_hours must be greater than zero")
    if gap_hours < horizon_hours:
        raise OfficialDatasetError(
            "splits.leakage_gap_hours must be at least forecast_horizon_hours"
        )
    seconds = gap_hours * 3600.0
    if (periods["validation"][0] - periods["train"][1]).total_seconds() < seconds:
        raise OfficialDatasetError("train/validation leakage gap is too small")
    if (periods["frozen_test"][0] - periods["validation"][1]).total_seconds() < seconds:
        raise OfficialDatasetError("validation/frozen-test leakage gap is too small")


def _read_and_validate_rows(
    table_path: Path,
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, datetime]] = set()
    overall_start = _parse_datetime(manifest["date_range"]["start"], "date_range.start")
    overall_end = _parse_datetime(manifest["date_range"]["end"], "date_range.end")
    try:
        with table_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames
            if header is None:
                raise OfficialDatasetError("harmonised CSV has no header")
            missing = [name for name in REQUIRED_TABLE_COLUMNS if name not in header]
            if missing:
                raise OfficialDatasetError(
                    f"harmonised CSV missing columns: {', '.join(missing)}"
                )
            for line_number, raw in enumerate(reader, start=2):
                timestamp = _parse_datetime(
                    raw.get("timestamp"), f"CSV line {line_number}"
                )
                if not overall_start <= timestamp <= overall_end:
                    raise OfficialDatasetError(
                        f"CSV line {line_number} timestamp falls outside date_range"
                    )
                site_id = (raw.get("site_id") or "").strip()
                if site_id not in manifest["site_ids"]:
                    raise OfficialDatasetError(
                        f"CSV line {line_number} has unknown site_id"
                    )
                key = (site_id, timestamp)
                if key in seen:
                    raise OfficialDatasetError(
                        f"duplicate site/timestamp at CSV line {line_number}"
                    )
                seen.add(key)
                target = _binary_target(
                    raw.get("target_extreme_water"),
                    f"CSV line {line_number}.target_extreme_water",
                )
                storm_group_id = (raw.get("storm_group_id") or "").strip()
                if target == 1 and storm_group_id.lower() in _BACKGROUND_STORM_GROUPS:
                    raise OfficialDatasetError(
                        f"CSV line {line_number} positive target requires an event storm_group_id"
                    )
                if (
                    storm_group_id.lower() not in _BACKGROUND_STORM_GROUPS
                    and not _IDENTIFIER_RE.fullmatch(storm_group_id)
                ):
                    raise OfficialDatasetError(
                        f"CSV line {line_number}.storm_group_id is not a safe identifier"
                    )
                row: dict[str, Any] = {
                    "timestamp": timestamp,
                    "site_id": site_id,
                    "storm_group_id": storm_group_id,
                    "target_extreme_water": target,
                }
                for name in OFFICIAL_FEATURE_ORDER:
                    value = _finite_number(
                        raw.get(name), f"CSV line {line_number}.{name}"
                    )
                    minimum, maximum = OFFICIAL_FEATURE_RANGES[name]
                    if not minimum <= value <= maximum:
                        raise OfficialDatasetError(
                            f"CSV line {line_number}.{name} must be in "
                            f"[{minimum}, {maximum}]"
                        )
                    row[name] = value
                metadata = manifest["site_metadata"][site_id]
                for name in ("latitude", "longitude"):
                    if not math.isclose(
                        row[name],
                        float(metadata[name]),
                        rel_tol=0.0,
                        abs_tol=_COORDINATE_ABS_TOLERANCE,
                    ):
                        raise OfficialDatasetError(
                            f"CSV line {line_number}.{name} does not match site_metadata"
                        )
                hour = (
                    timestamp.hour
                    + timestamp.minute / 60.0
                    + timestamp.second / 3600.0
                    + timestamp.microsecond / 3_600_000_000.0
                )
                angle = 2.0 * math.pi * hour / 24.0
                if not math.isclose(
                    row["hour_sin"],
                    math.sin(angle),
                    rel_tol=0.0,
                    abs_tol=_TIME_CYCLE_ABS_TOLERANCE,
                ) or not math.isclose(
                    row["hour_cos"],
                    math.cos(angle),
                    rel_tol=0.0,
                    abs_tol=_TIME_CYCLE_ABS_TOLERANCE,
                ):
                    raise OfficialDatasetError(
                        f"CSV line {line_number} hour_sin/hour_cos do not match UTC timestamp"
                    )
                days_in_year = 366.0 if _is_leap_year(timestamp.year) else 365.0
                day_angle = (
                    2.0 * math.pi * (timestamp.timetuple().tm_yday - 1) / days_in_year
                )
                if not math.isclose(
                    row["day_of_year_sin"],
                    math.sin(day_angle),
                    rel_tol=0.0,
                    abs_tol=_TIME_CYCLE_ABS_TOLERANCE,
                ) or not math.isclose(
                    row["day_of_year_cos"],
                    math.cos(day_angle),
                    rel_tol=0.0,
                    abs_tol=_TIME_CYCLE_ABS_TOLERANCE,
                ):
                    raise OfficialDatasetError(
                        f"CSV line {line_number} day_of_year_sin/day_of_year_cos "
                        "do not match UTC timestamp"
                    )
                rows.append(row)
    except UnicodeError as exc:
        raise OfficialDatasetError("harmonised CSV must be UTF-8") from exc
    rows.sort(key=lambda row: (row["timestamp"], row["site_id"]))
    return rows


def _binary_target(value: Any, path: str) -> int:
    if isinstance(value, bool):
        raise OfficialDatasetError(f"{path} must be 0 or 1")
    text = str(value).strip()
    if text not in {"0", "1", "0.0", "1.0"}:
        raise OfficialDatasetError(f"{path} must be 0 or 1")
    return int(float(text))


def _is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool):
        raise OfficialDatasetError(f"{path} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise OfficialDatasetError(f"{path} must be a finite number") from exc
    if not math.isfinite(result):
        raise OfficialDatasetError(f"{path} must be a finite number")
    return result


def _parse_datetime(value: Any, path: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise OfficialDatasetError(f"{path} is not an ISO-8601 timestamp") from exc
    else:
        raise OfficialDatasetError(f"{path} is not an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        raise OfficialDatasetError(f"{path} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_period(value: Any, path: str) -> tuple[datetime, datetime]:
    if not isinstance(value, Mapping):
        raise OfficialDatasetError(f"{path} must be an object")
    return (
        _parse_datetime(value.get("start"), f"{path}.start"),
        _parse_datetime(value.get("end"), f"{path}.end"),
    )


def _json_mapping_copy(value: Mapping[str, Any], path: str) -> dict[str, Any]:
    try:
        encoded = canonical_json_bytes(value)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise OfficialDatasetError(f"{path} is not canonical JSON data") from exc
    if not isinstance(decoded, dict):
        raise OfficialDatasetError(f"{path} must be an object")
    return decoded


def _absolute_registered_path(record: Mapping[str, Any], key: str) -> Path:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise OfficialDatasetError(f"registration {key} is invalid")
    path = Path(value)
    if not path.is_absolute():
        raise OfficialDatasetError(f"registration {key} must be absolute")
    return path.resolve()


def discover_official_dataset_bundles(
    dataset_root: Path | str,
) -> list[tuple[Path, Path]]:
    """Discover fixed-layout bundles without accepting any request-supplied path."""

    root = Path(dataset_root).resolve()
    if not root.is_dir():
        return []
    _reject_reparse_point(root, "dataset root")
    discovered: list[tuple[Path, Path]] = []
    seen_ids: set[tuple[str, str]] = set()
    for dataset_dir in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not dataset_dir.is_dir():
            continue
        _reject_reparse_point(dataset_dir, "dataset id directory")
        if not _IDENTIFIER_RE.fullmatch(dataset_dir.name):
            raise OfficialDatasetError(
                "dataset root contains an unsafe dataset directory"
            )
        for bundle_dir in sorted(
            dataset_dir.iterdir(), key=lambda item: item.name.lower()
        ):
            if not bundle_dir.is_dir():
                continue
            _reject_reparse_point(bundle_dir, "dataset version directory")
            if not _IDENTIFIER_RE.fullmatch(bundle_dir.name):
                raise OfficialDatasetError(
                    "dataset root contains an unsafe version directory"
                )
            manifest_path = bundle_dir / "manifest.json"
            if not manifest_path.is_file():
                continue
            _reject_reparse_point(manifest_path, "dataset manifest")
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise OfficialDatasetError(
                    f"invalid manifest in bundle {dataset_dir.name}/{bundle_dir.name}"
                ) from exc
            if not isinstance(manifest, Mapping):
                raise OfficialDatasetError(
                    f"manifest in bundle {dataset_dir.name}/{bundle_dir.name} must be an object"
                )
            if (
                manifest.get("dataset_id") != dataset_dir.name
                or manifest.get("version") != bundle_dir.name
            ):
                raise OfficialDatasetError(
                    "manifest dataset_id/version must match its two bundle directories"
                )
            table = manifest.get("table")
            if not isinstance(table, Mapping):
                raise OfficialDatasetError(
                    f"manifest in bundle {dataset_dir.name}/{bundle_dir.name} has no table object"
                )
            filename = table.get("file")
            _safe_filename(
                filename,
                f"bundle {dataset_dir.name}/{bundle_dir.name} table.file",
            )
            table_path = bundle_dir / str(filename)
            _validate_bundle_paths(root, manifest_path, table_path)
            identity = (
                str(manifest.get("dataset_id")),
                str(manifest.get("version")),
            )
            if identity in seen_ids:
                raise OfficialDatasetError(
                    "duplicate dataset_id/version found during protected-root rescan"
                )
            seen_ids.add(identity)
            discovered.append((manifest_path.resolve(), table_path.resolve()))
    return discovered


def _verify_source_archives(
    manifest: Mapping[str, Any], bundle_directory: Path
) -> None:
    """Verify every declared raw archive inside the fixed ``raw`` subdirectory."""

    raw_directory = bundle_directory / "raw"
    if not raw_directory.is_dir():
        raise OfficialDatasetError("official bundle is missing required raw directory")
    _reject_reparse_point(raw_directory, "raw source directory")
    resolved_raw = raw_directory.resolve()
    for index, source in enumerate(manifest["sources"]):
        filename = _safe_filename(
            source["original_filename"], f"sources[{index}].original_filename"
        )
        source_path = raw_directory / filename
        if source_path.parent.resolve() != resolved_raw or not source_path.is_file():
            raise OfficialDatasetError(
                f"sources[{index}] raw archive is missing from bundle/raw"
            )
        _reject_reparse_point(source_path, f"sources[{index}] raw archive")
        actual_hash = file_sha256(source_path)
        if actual_hash != source["sha256"]:
            raise OfficialDatasetError(
                f"sources[{index}] raw archive sha256 does not match manifest"
            )


def rescan_official_dataset_root(
    dataset_root: Path | str,
    registry_dir: Path | str,
    *,
    allow_synthetic_test_fixture: bool = False,
) -> list[RegisteredOfficialDataset]:
    """Register every direct fixed-layout bundle under a configured root."""

    return [
        register_official_dataset(
            manifest_path,
            table_path,
            registry_dir,
            dataset_root=dataset_root,
            allow_synthetic_test_fixture=allow_synthetic_test_fixture,
        )
        for manifest_path, table_path in discover_official_dataset_bundles(dataset_root)
    ]


def _validate_bundle_paths(
    dataset_root: Path | str,
    manifest_path: Path | str,
    table_path: Path | str,
) -> tuple[Path, Path]:
    root = Path(dataset_root).resolve()
    if not root.is_dir():
        raise OfficialDatasetError("configured official dataset root does not exist")
    _reject_reparse_point(root, "dataset root")
    manifest_input = Path(manifest_path)
    table_input = Path(table_path)
    manifest = manifest_input.resolve()
    table = table_input.resolve()
    if manifest.name != "manifest.json":
        raise OfficialDatasetError(
            "official bundle manifest must be named manifest.json"
        )
    if manifest.parent != table.parent:
        raise OfficialDatasetError(
            "manifest and table must be in the same bundle directory"
        )
    bundle = manifest.parent
    if bundle.parent.parent != root:
        raise OfficialDatasetError(
            "official bundle must use root/dataset_id/version fixed layout"
        )
    if not _IDENTIFIER_RE.fullmatch(bundle.parent.name) or not _IDENTIFIER_RE.fullmatch(
        bundle.name
    ):
        raise OfficialDatasetError("official bundle directories are unsafe")
    for path, label in (
        (bundle.parent, "dataset id directory"),
        (bundle, "dataset bundle directory"),
        (manifest_input, "dataset manifest"),
        (table_input, "harmonised table"),
    ):
        _reject_reparse_point(path, label)
    if not manifest.is_file() or not table.is_file():
        raise OfficialDatasetError("official bundle files do not exist")
    return manifest, table


def _reject_reparse_point(path: Path, label: str) -> None:
    try:
        if path.is_symlink():
            raise OfficialDatasetError(f"{label} may not be a symbolic link")
        status = os.lstat(path)
    except OSError as exc:
        raise OfficialDatasetError(f"cannot inspect {label}") from exc
    file_attributes = getattr(status, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if file_attributes & reparse_flag:
        raise OfficialDatasetError(f"{label} may not be a reparse point")


def _safe_filename(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OfficialDatasetError(f"{path} is required")
    name = value.strip()
    if (
        name in {".", ".."}
        or ".." in name
        or "/" in name
        or "\\" in name
        or Path(name).name != name
    ):
        raise OfficialDatasetError(f"{path} must be a safe relative filename")
    return name


def _require_utc_timestamp(value: Any, path: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise OfficialDatasetError(f"{path} must be an ISO-8601 UTC timestamp")
    text = value.strip()
    if text.endswith("Z"):
        parsed_text = text[:-1] + "+00:00"
    else:
        parsed_text = text
    try:
        parsed = datetime.fromisoformat(parsed_text)
    except ValueError as exc:
        raise OfficialDatasetError(f"{path} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise OfficialDatasetError(f"{path} must use UTC (Z or +00:00)")
    return parsed.astimezone(timezone.utc)


__all__ = [
    "DATASET_SCHEMA",
    "NON_WATER_FEATURE_ORDER",
    "OFFICIAL_DATA_ORIGIN",
    "OFFICIAL_FEATURE_ORDER",
    "OFFICIAL_FEATURE_RANGES",
    "OFFICIAL_FEATURE_UNITS",
    "TEST_DATA_ORIGIN",
    "OfficialDatasetError",
    "RegisteredOfficialDataset",
    "ValidatedOfficialDataset",
    "canonical_sha256",
    "discover_official_dataset_bundles",
    "file_sha256",
    "freeze_official_sensor_context",
    "load_registered_official_dataset",
    "register_official_dataset",
    "rescan_official_dataset_root",
    "rows_for_split",
    "validate_official_dataset",
]
