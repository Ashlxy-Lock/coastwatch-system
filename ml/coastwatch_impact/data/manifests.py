"""Immutable provenance manifests and SHA-256 verification."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import utc_datetime


class ImmutableManifestError(RuntimeError):
    """Raised when code attempts to change an existing provenance record."""


def sha256_file(path: str | os.PathLike[str], *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def manifest_hash(value: BaseModel | dict[str, Any]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return sha256_bytes(canonical_json_bytes(payload))


class RawFileManifest(BaseModel):
    """One immutable raw-file provenance record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_name: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    retrieved_at_utc: datetime
    coverage_start_utc: datetime | None = None
    coverage_end_utc: datetime | None = None
    license_name: str = Field(min_length=1)
    owner: str = Field(default="unrecorded", min_length=1)
    access_method: str = Field(default="unrecorded", min_length=1)
    authentication: str = Field(default="unrecorded", min_length=1)
    update_frequency: str = Field(default="unrecorded", min_length=1)
    allowed_modes: tuple[str, ...] = ()
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_filename: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    notes: str = ""
    byte_size: int = Field(ge=0)
    synthetic_data: bool = False

    @field_validator(
        "retrieved_at_utc",
        "coverage_start_utc",
        "coverage_end_utc",
        mode="before",
    )
    @classmethod
    def _utc_times(cls, value: Any, info: Any) -> datetime | None:
        if value is None:
            return None
        return utc_datetime(value, name=info.field_name)

    @field_validator("sha256", mode="before")
    @classmethod
    def _lower_hash(cls, value: str) -> str:
        return str(value).lower()


def build_raw_manifest(
    raw_file: str | os.PathLike[str],
    *,
    source_name: str,
    source_url: str,
    retrieved_at_utc: datetime | str,
    license_name: str,
    owner: str = "unrecorded",
    access_method: str = "unrecorded",
    authentication: str = "unrecorded",
    update_frequency: str = "unrecorded",
    allowed_modes: tuple[str, ...] = (),
    parser_version: str,
    coverage_start_utc: datetime | str | None = None,
    coverage_end_utc: datetime | str | None = None,
    original_filename: str | None = None,
    notes: str = "",
    synthetic_data: bool = False,
) -> RawFileManifest:
    path = Path(raw_file)
    if not path.is_file():
        raise FileNotFoundError(path)
    return RawFileManifest.model_validate(
        {
            "source_name": source_name,
            "source_url": source_url,
            "retrieved_at_utc": retrieved_at_utc,
            "coverage_start_utc": coverage_start_utc,
            "coverage_end_utc": coverage_end_utc,
            "license_name": license_name,
            "owner": owner,
            "access_method": access_method,
            "authentication": authentication,
            "update_frequency": update_frequency,
            "allowed_modes": allowed_modes,
            "sha256": sha256_file(path),
            "original_filename": original_filename or path.name,
            "parser_version": parser_version,
            "notes": notes,
            "byte_size": path.stat().st_size,
            "synthetic_data": synthetic_data,
        }
    )


def write_manifest_immutable(
    manifest: RawFileManifest,
    destination: str | os.PathLike[str],
) -> Path:
    """Create a manifest once; identical retries are idempotent, changes fail."""

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        existing = path.read_text(encoding="utf-8")
        if existing != payload:
            raise ImmutableManifestError(f"refusing to overwrite manifest: {path}") from exc
    return path


def load_manifest(path: str | os.PathLike[str]) -> RawFileManifest:
    return RawFileManifest.model_validate_json(Path(path).read_text(encoding="utf-8"))


def verify_raw_manifest(
    raw_file: str | os.PathLike[str],
    manifest: RawFileManifest | str | os.PathLike[str],
) -> bool:
    record = load_manifest(manifest) if not isinstance(manifest, RawFileManifest) else manifest
    path = Path(raw_file)
    if not path.is_file():
        raise FileNotFoundError(path)
    actual_hash = sha256_file(path)
    actual_size = path.stat().st_size
    if actual_hash != record.sha256 or actual_size != record.byte_size:
        raise ImmutableManifestError(
            f"raw file no longer matches manifest: expected {record.sha256}/{record.byte_size}, "
            f"got {actual_hash}/{actual_size}"
        )
    return True


def store_raw_file_immutable(
    source: str | os.PathLike[str] | BinaryIO,
    destination_directory: str | os.PathLike[str],
    *,
    original_filename: str | None = None,
) -> Path:
    """Copy a raw payload without ever overwriting an existing byte sequence.

    The digest prefix is part of the stored name, which makes repeated downloads
    idempotent while retaining a distinct file whenever source bytes change.
    """

    directory = Path(destination_directory)
    directory.mkdir(parents=True, exist_ok=True)
    if hasattr(source, "read"):
        payload = source.read()  # type: ignore[union-attr]
        if not isinstance(payload, bytes):
            raise TypeError("binary source.read() must return bytes")
        digest = sha256_bytes(payload)
        name = original_filename or "download.bin"
        destination = directory / f"{digest[:16]}_{Path(name).name}"
        if destination.exists():
            if sha256_file(destination) != digest:
                raise ImmutableManifestError(f"hash-named raw file was modified: {destination}")
            return destination
        try:
            with destination.open("xb") as handle:
                handle.write(payload)
        except FileExistsError as exc:
            if sha256_file(destination) != digest:
                raise ImmutableManifestError(
                    f"concurrent raw write mismatch: {destination}"
                ) from exc
        return destination

    source_path = Path(source)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    digest = sha256_file(source_path)
    name = original_filename or source_path.name
    destination = directory / f"{digest[:16]}_{Path(name).name}"
    if destination.exists():
        if sha256_file(destination) != digest:
            raise ImmutableManifestError(f"hash-named raw file was modified: {destination}")
        return destination
    try:
        with source_path.open("rb") as incoming, destination.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
    except FileExistsError as exc:
        if sha256_file(destination) != digest:
            raise ImmutableManifestError(f"concurrent raw write mismatch: {destination}") from exc
    return destination


# Backwards-friendly names used by adapters and tests.
compute_sha256 = sha256_file
write_raw_manifest = write_manifest_immutable
verify_manifest = verify_raw_manifest


__all__ = [
    "ImmutableManifestError",
    "RawFileManifest",
    "build_raw_manifest",
    "canonical_json_bytes",
    "compute_sha256",
    "load_manifest",
    "manifest_hash",
    "sha256_bytes",
    "sha256_file",
    "store_raw_file_immutable",
    "verify_manifest",
    "verify_raw_manifest",
    "write_manifest_immutable",
    "write_raw_manifest",
]
