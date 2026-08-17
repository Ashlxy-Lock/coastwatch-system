"""Credential/config guard for Met Office Weather DataHub integration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import SourceAdapter, SourceCredentialsError, UnsupportedSourceFormatError
from .registry import source_metadata


class MetOfficeDataHubAdapter(SourceAdapter):
    """A safe configuration stub; it never synthesises forecast runs."""

    metadata = source_metadata("metoffice_datahub")
    parser_version = "metoffice-datahub-stub-1"
    supported_suffixes = frozenset({".json"})
    credential_environment_variable = "METOFFICE_API_KEY"

    def __init__(
        self,
        data_root: str | Path,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        super().__init__(data_root)
        self.api_key = api_key or os.getenv(self.credential_environment_variable)
        self.endpoint = endpoint

    def require_configuration(self) -> None:
        if not self.api_key:
            raise SourceCredentialsError(
                f"Met Office DataHub is not configured: set "
                f"{self.credential_environment_variable}; credentials are never stored in manifests"
            )
        if not self.endpoint:
            raise UnsupportedSourceFormatError(
                "Met Office DataHub endpoint/model is not configured. Supply an explicitly "
                "reviewed endpoint and archive every raw issued run before backtesting."
            )

    def request_plan(self, *, site_id: str, latitude: float, longitude: float) -> dict[str, Any]:
        self.require_configuration()
        if not site_id.strip():
            raise ValueError("site_id must not be empty")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("invalid site coordinates")
        return {
            "endpoint": self.endpoint,
            "site_id": site_id,
            "latitude": latitude,
            "longitude": longitude,
            "credential_source": self.credential_environment_variable,
            "requires_issue_time": True,
            "requires_valid_time": True,
        }

    def fetch(self, **_: object) -> None:
        self.require_configuration()
        raise UnsupportedSourceFormatError(
            "Met Office network fetch is intentionally not implemented until the exact licensed "
            "product schema and endpoint have been reviewed. No forecast data were created."
        )


__all__ = ["MetOfficeDataHubAdapter"]
