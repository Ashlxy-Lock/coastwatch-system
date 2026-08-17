"""Environment Agency tide-gauge realtime and manual-archive adapters."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pandas as pd

from ._parsing import optional_float, pick_column, read_tabular_payload, required_utc
from .base import (
    NetworkAccessDisabledError,
    NoSourceDataError,
    RawImportResult,
    SourceAdapter,
    json_dumps_canonical,
    payloads_from_file,
)
from .registry import source_metadata


def _datum_from_text(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", " ")
    if any(token in text for token in ("maod", "m aod", "ordnance datum", "ordnance")):
        return "mAOD"
    if any(token in text for token in ("local", "station datum")):
        return "local_station_datum"
    if "chart datum" in text:
        return "chart_datum"
    return "unknown"


def _safe_filename_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:80] or "station"


class EATideGaugeArchiveAdapter(SourceAdapter):
    """Parse manual EA tide CSV/JSON archives while retaining datum semantics."""

    metadata = source_metadata("ea_tide_archive")
    parser_version = "ea-tide-archive-1"
    supported_suffixes = frozenset({".csv", ".json", ".zip"})

    def parse(
        self,
        raw_path: str | Path,
        *,
        station_id: str | None = None,
        site_id: str | None = None,
        coastal_zone_id: str | None = None,
    ) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for payload in payloads_from_file(raw_path, allowed_suffixes=(".csv", ".json")):
            frame = read_tabular_payload(payload)
            timestamp_column = pick_column(
                frame,
                ("timestamp_utc", "date_time", "datetime", "dateTime", "time"),
                required=True,
                meaning="tide observation timestamp",
            )
            value_column = pick_column(
                frame,
                ("water_level", "water_level_m", "value", "reading"),
                required=True,
                meaning="tide water level",
            )
            station_column = pick_column(
                frame,
                ("station_id", "station_reference", "stationReference", "station", "notation"),
            )
            datum_column = pick_column(
                frame,
                ("water_level_datum", "datum", "vertical_datum", "measure_datum"),
            )
            quality_column = pick_column(
                frame,
                ("quality_flag", "quality", "qc_flag", "status"),
            )
            measure_column = pick_column(frame, ("measure", "measure_id", "measure_notation"))
            for row_number, raw in enumerate(frame.to_dict(orient="records")):
                observed = optional_float(raw.get(value_column))  # type: ignore[arg-type]
                if observed is None:
                    raise ValueError(f"{payload.name} row {row_number}: water level is missing")
                source_station = raw.get(station_column) if station_column else station_id
                if source_station is None or not str(source_station).strip():
                    raise ValueError(
                        f"{payload.name} row {row_number}: station identifier is required"
                    )
                raw_datum = raw.get(datum_column) if datum_column else raw.get(measure_column)
                if isinstance(raw_datum, dict):
                    raw_datum = " ".join(str(value) for value in raw_datum.values())
                datum = _datum_from_text(raw_datum)
                rows.append(
                    {
                        "site_id": site_id or str(source_station).strip(),
                        "coastal_zone_id": coastal_zone_id or str(source_station).strip(),
                        "tide_station_id": str(source_station).strip(),
                        "timestamp_utc": required_utc(
                            raw[timestamp_column],
                            name="timestamp_utc",  # type: ignore[index]
                        ),
                        "water_level_m_aod": observed if datum == "mAOD" else None,
                        "water_level_local_m": observed if datum != "mAOD" else None,
                        "water_level_datum": datum,
                        "quality_flag": None if quality_column is None else raw.get(quality_column),
                        "source_member": payload.name,
                        "raw_fields_json": json_dumps_canonical(raw),
                    }
                )
        if not rows:
            raise NoSourceDataError("EA tide archive contains no observations")
        result = pd.DataFrame.from_records(rows)
        # When local and mAOD representations share a timestamp, retain the mAOD
        # row as the model-ready record and leave the alternate raw row auditable.
        result["_datum_priority"] = (result["water_level_datum"] == "mAOD").astype(int)
        result = result.sort_values(
            ["tide_station_id", "timestamp_utc", "_datum_priority"],
            ascending=[True, True, False],
        )
        result = result.drop_duplicates(["tide_station_id", "timestamp_utc"], keep="first")
        return result.drop(columns="_datum_priority").reset_index(drop=True)


class EATideGaugeRealtimeAdapter(EATideGaugeArchiveAdapter):
    """Optional EA Flood Monitoring HTTP client; network is off by default."""

    metadata = source_metadata("ea_tide_realtime")
    parser_version = "ea-tide-realtime-1"

    def __init__(
        self,
        data_root: str | Path,
        *,
        allow_network: bool = False,
        client: httpx.Client | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(data_root)
        self.allow_network = allow_network
        self._client = client
        self.timeout_seconds = timeout_seconds

    def fetch_readings(
        self,
        station_id: str,
        *,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
    ) -> RawImportResult:
        if not self.allow_network:
            raise NetworkAccessDisabledError(
                "EA tide network sync is disabled; construct the adapter with "
                "allow_network=True or use EATideGaugeArchiveAdapter"
            )
        station = station_id.strip()
        if not station:
            raise ValueError("station_id must not be empty")
        params: dict[str, str] = {"_sorted": "true"}
        start: datetime | None = None
        end: datetime | None = None
        if since is not None:
            start = required_utc(since, name="since")
            params["since"] = start.isoformat().replace("+00:00", "Z")
        if until is not None:
            end = required_utc(until, name="until")
            params["until"] = end.isoformat().replace("+00:00", "Z")
        if start is not None and end is not None and end < start:
            raise ValueError("until must not precede since")
        url = f"{self.metadata.source_url.rstrip('/')}/{quote(station, safe='')}/readings"
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self.timeout_seconds)
        try:
            response = client.get(url, params=params, headers={"Accept": "application/json"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise NoSourceDataError(f"EA tide request failed for station {station}: {exc}") from exc
        finally:
            if owns_client:
                client.close()
        try:
            decoded = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise NoSourceDataError("EA tide response was not valid JSON") from exc
        if not isinstance(decoded, dict) or not decoded.get("items"):
            raise NoSourceDataError(
                f"EA tide API returned no readings for station {station}; no raw file was created"
            )
        filename = f"{_safe_filename_token(station)}_readings.json"
        return self.import_bytes(
            response.content,
            original_filename=filename,
            coverage_start_utc=start,
            coverage_end_utc=end,
            notes=f"GET {response.request.url}",
        )


__all__ = ["EATideGaugeArchiveAdapter", "EATideGaugeRealtimeAdapter"]
