"""Credentialed Copernicus Marine request configuration without fake downloads."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .base import SourceAdapter, SourceCredentialsError, UnsupportedSourceFormatError
from .registry import source_metadata


@dataclass(frozen=True, slots=True)
class CopernicusRequestConfig:
    product_id: str
    dataset_id: str
    variables: tuple[str, ...]
    product_version: str

    def __post_init__(self) -> None:
        if not self.product_id.strip() or not self.dataset_id.strip():
            raise ValueError("Copernicus product_id and dataset_id are required")
        if not self.variables or any(not value.strip() for value in self.variables):
            raise ValueError("at least one explicit Copernicus variable is required")
        if not self.product_version.strip():
            raise ValueError("Copernicus product_version is required for provenance")


class _CopernicusBaseAdapter(SourceAdapter):
    metadata = source_metadata("copernicus_marine")
    parser_version = "copernicus-config-stub-1"
    supported_suffixes = frozenset({".nc", ".nc4", ".netcdf", ".zarr", ".zip"})

    def __init__(
        self,
        data_root: str | Path,
        *,
        config: CopernicusRequestConfig,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        super().__init__(data_root)
        self.config = config
        self.username = username or os.getenv("COPERNICUSMARINE_USERNAME")
        self.password = password or os.getenv("COPERNICUSMARINE_PASSWORD")

    def require_credentials(self) -> None:
        if not self.username or not self.password:
            raise SourceCredentialsError(
                "Copernicus Marine is not configured: set COPERNICUSMARINE_USERNAME and "
                "COPERNICUSMARINE_PASSWORD. Credentials are never written to raw manifests."
            )

    def request_plan(self) -> dict[str, object]:
        self.require_credentials()
        return {
            "product_id": self.config.product_id,
            "dataset_id": self.config.dataset_id,
            "variables": list(self.config.variables),
            "product_version": self.config.product_version,
            "credential_source": "COPERNICUSMARINE_USERNAME/PASSWORD",
        }

    def fetch(self, **_: object) -> None:
        self.require_credentials()
        raise UnsupportedSourceFormatError(
            "Copernicus download is intentionally disabled until spatial bounds, temporal mode, "
            "dataset variables and client version are frozen. No data were created."
        )


class CopernicusMarineForecastAdapter(_CopernicusBaseAdapter):
    """Forecast adapter configuration; downloaded records must include issue time."""

    data_semantics = "issued_forecast_requires_issue_time_and_valid_time"


class CopernicusMarineHindcastAdapter(_CopernicusBaseAdapter):
    """Hindcast adapter configuration, never business-equivalent forecast data."""

    data_semantics = "hindcast_research_only_not_operational_backtest"


__all__ = [
    "CopernicusMarineForecastAdapter",
    "CopernicusMarineHindcastAdapter",
    "CopernicusRequestConfig",
]
