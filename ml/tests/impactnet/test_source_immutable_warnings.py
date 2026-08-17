from __future__ import annotations

import json
from pathlib import Path

import pytest

from coastwatch_impact.data.manifests import (
    ImmutableManifestError,
    load_manifest,
    verify_raw_manifest,
)
from coastwatch_impact.data.sources import (
    SOURCE_REGISTRY,
    EAHistoricWarningsAdapter,
    NoSourceDataError,
)


def _warning_csv(path: Path, *, message: str = "High tide") -> bytes:
    payload = (
        "warning_area_id,severity,issued_time_utc,removed_time_utc,message\n"
        f"AREA-1,warning,2026-01-01T00:00:00Z,2026-01-01T03:00:00Z,{message}\n"
    ).encode()
    path.write_bytes(payload)
    return payload


def test_source_registry_declares_operational_provenance_policy() -> None:
    required = {
        "ea_historic_warnings",
        "ea_warning_areas",
        "ea_flood_outlines",
        "ea_tide_realtime",
        "ea_tide_archive",
        "wavenet",
        "ntslf",
        "metoffice_datahub",
        "copernicus_marine",
        "ea_aims_assets",
        "ea_rofrs",
    }
    assert required.issubset(SOURCE_REGISTRY)
    for name in required:
        source = SOURCE_REGISTRY[name]
        assert source.owner
        assert source.access_method
        assert source.authentication
        assert source.update_frequency
        assert source.allowed_modes


def test_raw_import_is_content_addressed_versioned_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "warnings.csv"
    original = _warning_csv(source)
    adapter = EAHistoricWarningsAdapter(tmp_path / "data")

    first = adapter.import_file(source)
    second = adapter.import_file(source)

    assert first.created is True
    assert second.created is False
    assert first.raw_path == second.raw_path
    assert first.raw_path.read_bytes() == original
    record = load_manifest(first.manifest_path)
    assert record.sha256 == first.sha256
    assert record.original_filename == "warnings.csv"
    assert record.owner == "Environment Agency"
    assert record.authentication == "none for supplied public archive"
    assert record.allowed_modes == ("hindcast_research", "operational_backtest")
    assert verify_raw_manifest(first.raw_path, record)

    _warning_csv(source, message="Changed official message")
    changed = adapter.import_file(source)
    assert changed.created is True
    assert changed.raw_path != first.raw_path
    assert first.raw_path.read_bytes() == original


def test_warning_parser_preserves_raw_fields_and_requires_aware_times(tmp_path: Path) -> None:
    source = tmp_path / "warnings.csv"
    _warning_csv(source)
    adapter = EAHistoricWarningsAdapter(tmp_path / "data")
    imported = adapter.import_file(source)

    frame = adapter.parse(imported.raw_path)

    assert frame.loc[0, "warning_area_id"] == "AREA-1"
    assert frame.loc[0, "issued_time_utc"].utcoffset().total_seconds() == 0
    assert json.loads(frame.loc[0, "raw_fields_json"])["severity"] == "warning"

    source.write_text(
        "warning_area_id,issued_time_utc\nAREA-1,2026-01-01 00:00:00\n",
        encoding="utf-8",
    )
    naive = adapter.import_file(source)
    with pytest.raises(ValueError, match="timezone-aware"):
        adapter.parse(naive.raw_path)


def test_folder_with_no_supported_data_fails_without_creating_raw(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "readme.txt").write_text("not data", encoding="utf-8")
    adapter = EAHistoricWarningsAdapter(tmp_path / "data")

    with pytest.raises(NoSourceDataError, match="no supported files"):
        adapter.import_path(incoming)
    assert not adapter.raw_directory.exists()


def test_versioned_interim_output_is_idempotent_and_checksum_guarded(tmp_path: Path) -> None:
    source = tmp_path / "warnings.csv"
    _warning_csv(source)
    adapter = EAHistoricWarningsAdapter(tmp_path / "data")

    _, first_output = adapter.import_and_parse(source)
    _, second_output = adapter.import_and_parse(source)

    assert first_output == second_output
    assert first_output.with_suffix(".parquet.sha256").is_file()
    first_output.write_bytes(b"tampered")
    with pytest.raises(ImmutableManifestError, match="fails its checksum"):
        adapter.import_and_parse(source)


def test_invalid_coverage_fails_before_raw_storage(tmp_path: Path) -> None:
    source = tmp_path / "warnings.csv"
    _warning_csv(source)
    adapter = EAHistoricWarningsAdapter(tmp_path / "data")

    with pytest.raises(ValueError, match="coverage_end_utc"):
        adapter.import_file(
            source,
            coverage_start_utc="2026-01-02T00:00:00Z",
            coverage_end_utc="2026-01-01T00:00:00Z",
        )
    assert not adapter.raw_directory.exists()
