from __future__ import annotations

from pathlib import Path

import pytest

from coastwatch_impact.data.manifests import (
    ImmutableManifestError,
    build_raw_manifest,
    verify_raw_manifest,
    write_manifest_immutable,
)


def test_raw_manifest_hash_verifies_and_cannot_be_changed(tmp_path: Path) -> None:
    raw = tmp_path / "source.csv"
    raw.write_bytes(b"time,value\n2025-01-01T00:00:00Z,1.2\n")
    record = build_raw_manifest(
        raw,
        source_name="test_source",
        source_url="https://example.invalid/data",
        retrieved_at_utc="2026-08-13T00:00:00Z",
        coverage_start_utc="2025-01-01T00:00:00Z",
        coverage_end_utc="2025-01-01T00:00:00Z",
        license_name="Test fixture licence",
        parser_version="1",
    )
    path = write_manifest_immutable(record, tmp_path / "manifest.json")
    assert verify_raw_manifest(raw, path)
    assert write_manifest_immutable(record, path) == path  # identical retry is idempotent

    raw.write_bytes(b"tampered")
    with pytest.raises(ImmutableManifestError, match="no longer matches"):
        verify_raw_manifest(raw, path)


def test_existing_manifest_is_not_overwritten(tmp_path: Path) -> None:
    raw = tmp_path / "one.bin"
    raw.write_bytes(b"one")
    first = build_raw_manifest(
        raw,
        source_name="source",
        source_url="https://example.invalid/one",
        retrieved_at_utc="2026-08-13T00:00:00Z",
        license_name="fixture",
        parser_version="1",
    )
    manifest_path = write_manifest_immutable(first, tmp_path / "manifest.json")
    second = first.model_copy(update={"notes": "different"})
    with pytest.raises(ImmutableManifestError, match="refusing to overwrite"):
        write_manifest_immutable(second, manifest_path)
