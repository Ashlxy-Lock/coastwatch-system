"""Shared immutable-ingest machinery for external data adapters."""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from abc import ABC
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO

import pandas as pd

from ..manifests import (
    ImmutableManifestError,
    build_raw_manifest,
    load_manifest,
    sha256_file,
    store_raw_file_immutable,
    verify_raw_manifest,
    write_manifest_immutable,
)
from ..schemas import utc_datetime
from .registry import SourceMetadata


class SourceAdapterError(RuntimeError):
    """Base class for explicit source-ingest failures."""


class NoSourceDataError(SourceAdapterError):
    """Raised when an input contains no usable records or files."""


class UnsupportedSourceFormatError(SourceAdapterError):
    """Raised when a source cannot be safely parsed by the installed extras."""


class NetworkAccessDisabledError(SourceAdapterError):
    """Raised when network access was not explicitly enabled."""


class SourceCredentialsError(SourceAdapterError):
    """Raised when a credentialed adapter is not configured."""


class ForecastAvailabilityError(SourceAdapterError):
    """Raised when issued-forecast availability would be ambiguous or leaky."""


@dataclass(frozen=True, slots=True)
class RawImportResult:
    """Paths and checksum for one immutable raw payload."""

    raw_path: Path
    manifest_path: Path
    sha256: str
    created: bool


@dataclass(frozen=True, slots=True)
class NamedPayload:
    """A parser input, either a file or a member of an immutable ZIP."""

    name: str
    payload: bytes


def _safe_parser_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "parser"


class SourceAdapter(ABC):
    """Base adapter implementing content-addressed, immutable raw storage.

    Re-importing identical bytes returns the first manifest unchanged.  Changed
    bytes receive a different content-addressed filename and manifest, so raw
    data are versioned without overwrites.
    """

    metadata: SourceMetadata
    parser_version = "1.0.0"
    supported_suffixes: frozenset[str] = frozenset()

    def __init__(self, data_root: str | os.PathLike[str]) -> None:
        self.data_root = Path(data_root)

    @property
    def raw_directory(self) -> Path:
        return self.data_root / "raw" / self.metadata.name

    @property
    def manifest_directory(self) -> Path:
        return self.data_root / "manifests" / self.metadata.name

    @property
    def interim_directory(self) -> Path:
        return self.data_root / "interim" / self.metadata.name

    def import_file(
        self,
        source: str | os.PathLike[str],
        *,
        retrieved_at_utc: datetime | None = None,
        coverage_start_utc: datetime | str | None = None,
        coverage_end_utc: datetime | str | None = None,
        notes: str = "",
        license_name: str | None = None,
    ) -> RawImportResult:
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.stat().st_size == 0:
            raise NoSourceDataError(f"{self.metadata.name}: input file is empty: {path}")
        self._require_supported(path.name)
        return self._store(
            path,
            original_filename=path.name,
            retrieved_at_utc=retrieved_at_utc,
            coverage_start_utc=coverage_start_utc,
            coverage_end_utc=coverage_end_utc,
            notes=notes,
            license_name=license_name,
        )

    def import_bytes(
        self,
        payload: bytes,
        *,
        original_filename: str,
        retrieved_at_utc: datetime | None = None,
        coverage_start_utc: datetime | str | None = None,
        coverage_end_utc: datetime | str | None = None,
        notes: str = "",
        license_name: str | None = None,
    ) -> RawImportResult:
        if not payload:
            raise NoSourceDataError(f"{self.metadata.name}: empty response/input payload")
        self._require_supported(original_filename)
        return self._store(
            io.BytesIO(payload),
            original_filename=original_filename,
            retrieved_at_utc=retrieved_at_utc,
            coverage_start_utc=coverage_start_utc,
            coverage_end_utc=coverage_end_utc,
            notes=notes,
            license_name=license_name,
        )

    def import_path(
        self,
        source: str | os.PathLike[str],
        **kwargs: object,
    ) -> list[RawImportResult]:
        """Import one file or every supported file in a folder, in sorted order."""

        path = Path(source)
        if path.is_file():
            return [self.import_file(path, **kwargs)]  # type: ignore[arg-type]
        if not path.is_dir():
            raise FileNotFoundError(path)
        candidates = [
            item
            for item in sorted(path.rglob("*"))
            if item.is_file() and self._is_supported(item.name)
        ]
        if not candidates:
            suffixes = ", ".join(sorted(self.supported_suffixes)) or "configured formats"
            raise NoSourceDataError(
                f"{self.metadata.name}: no supported files ({suffixes}) under {path}"
            )
        return [self.import_file(item, **kwargs) for item in candidates]  # type: ignore[arg-type]

    def write_versioned_frame(
        self,
        frame: pd.DataFrame,
        *,
        raw_sha256: str,
        table_name: str,
    ) -> Path:
        """Write a parser-versioned Parquet result without overwriting a version."""

        if frame.empty:
            raise NoSourceDataError(f"{self.metadata.name}: parser produced no records")
        if not re.fullmatch(r"[0-9a-f]{64}", raw_sha256):
            raise ValueError("raw_sha256 must be a lowercase 64-character SHA-256 digest")
        parser_token = _safe_parser_token(self.parser_version)
        destination = self.interim_directory / (
            f"{raw_sha256[:16]}_{parser_token}_{_safe_parser_token(table_name)}.parquet"
        )
        if destination.exists():
            checksum_path = destination.with_suffix(destination.suffix + ".sha256")
            if not checksum_path.is_file() or checksum_path.read_text(
                encoding="ascii"
            ).strip() != sha256_file(destination):
                raise ImmutableManifestError(
                    f"versioned interim file is missing or fails its checksum: {destination}"
                )
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + f".{os.getpid()}.tmp")
        frame.to_parquet(temporary, index=False)
        try:
            # Exclusive creation of the final path keeps concurrent retries safe.
            with temporary.open("rb") as incoming, destination.open("xb") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    outgoing.write(chunk)
        except FileExistsError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
        checksum_path = destination.with_suffix(destination.suffix + ".sha256")
        checksum = sha256_file(destination)
        try:
            with checksum_path.open("x", encoding="ascii", newline="\n") as handle:
                handle.write(checksum + "\n")
        except FileExistsError:
            if checksum_path.read_text(encoding="ascii").strip() != checksum:
                raise ImmutableManifestError(
                    f"interim checksum record conflicts with output: {checksum_path}"
                ) from None
        return destination

    def _store(
        self,
        source: str | os.PathLike[str] | BinaryIO,
        *,
        original_filename: str,
        retrieved_at_utc: datetime | None,
        coverage_start_utc: datetime | str | None,
        coverage_end_utc: datetime | str | None,
        notes: str,
        license_name: str | None,
    ) -> RawImportResult:
        retrieved = utc_datetime(
            retrieved_at_utc or datetime.now(UTC),
            name="retrieved_at_utc",
        )
        coverage_start = (
            None
            if coverage_start_utc is None
            else utc_datetime(coverage_start_utc, name="coverage_start_utc")
        )
        coverage_end = (
            None
            if coverage_end_utc is None
            else utc_datetime(coverage_end_utc, name="coverage_end_utc")
        )
        if (
            coverage_start is not None
            and coverage_end is not None
            and coverage_end < coverage_start
        ):
            raise ValueError("coverage_end_utc must not precede coverage_start_utc")
        resolved_license = license_name or self.metadata.license_name
        if not resolved_license.strip():
            raise ValueError("license_name must not be empty")
        stored = store_raw_file_immutable(
            source,
            self.raw_directory,
            original_filename=original_filename,
        )
        digest = sha256_file(stored)
        manifest_path = self.manifest_directory / f"{digest}.json"
        if manifest_path.exists():
            record = load_manifest(manifest_path)
            verify_raw_manifest(stored, record)
            if record.source_name != self.metadata.name or record.sha256 != digest:
                raise ImmutableManifestError(
                    f"manifest identity mismatch for existing raw payload: {manifest_path}"
                )
            return RawImportResult(stored, manifest_path, digest, False)

        record = build_raw_manifest(
            stored,
            source_name=self.metadata.name,
            source_url=self.metadata.source_url,
            retrieved_at_utc=retrieved,
            license_name=resolved_license,
            owner=self.metadata.owner,
            access_method=self.metadata.access_method,
            authentication=self.metadata.authentication,
            update_frequency=self.metadata.update_frequency,
            allowed_modes=self.metadata.allowed_modes,
            parser_version=self.parser_version,
            coverage_start_utc=coverage_start,
            coverage_end_utc=coverage_end,
            original_filename=original_filename,
            notes=notes or self.metadata.notes,
        )
        write_manifest_immutable(record, manifest_path)
        return RawImportResult(stored, manifest_path, digest, True)

    def _is_supported(self, filename: str) -> bool:
        lowered = filename.lower()
        return not self.supported_suffixes or any(
            lowered.endswith(suffix) for suffix in self.supported_suffixes
        )

    def _require_supported(self, filename: str) -> None:
        if not self._is_supported(filename):
            raise UnsupportedSourceFormatError(
                f"{self.metadata.name}: unsupported input {filename!r}; expected one of "
                f"{sorted(self.supported_suffixes)}"
            )


def payloads_from_file(
    path: str | os.PathLike[str],
    *,
    allowed_suffixes: Iterable[str],
) -> Iterator[NamedPayload]:
    """Yield safe in-memory members from a plain file or ZIP.

    ZIP members are read, never extracted, which avoids path traversal and keeps
    the immutable ZIP as the sole raw source of truth.
    """

    source = Path(path)
    suffixes = tuple(suffix.lower() for suffix in allowed_suffixes)
    if source.suffix.lower() != ".zip":
        if source.name.lower().endswith(suffixes):
            yield NamedPayload(source.name, source.read_bytes())
            return
        raise UnsupportedSourceFormatError(f"unsupported parser input: {source.name}")

    with zipfile.ZipFile(source) as archive:
        members = []
        for info in archive.infolist():
            member = PurePosixPath(info.filename)
            if info.is_dir() or member.is_absolute() or ".." in member.parts:
                continue
            if member.name.lower().endswith(suffixes):
                members.append(info)
        if not members:
            raise NoSourceDataError(
                f"archive {source.name} contains no supported members {sorted(suffixes)}"
            )
        for info in sorted(members, key=lambda value: value.filename):
            yield NamedPayload(PurePosixPath(info.filename).name, archive.read(info))


def json_dumps_canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "ForecastAvailabilityError",
    "NamedPayload",
    "NetworkAccessDisabledError",
    "NoSourceDataError",
    "RawImportResult",
    "SourceAdapter",
    "SourceAdapterError",
    "SourceCredentialsError",
    "UnsupportedSourceFormatError",
    "json_dumps_canonical",
    "payloads_from_file",
]
