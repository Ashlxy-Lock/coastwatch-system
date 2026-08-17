"""Repeatable manual importer for Cefas WaveNet archives."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pandas as pd

from ._parsing import optional_float, pick_column, read_tabular_payload, required_utc
from .base import (
    NoSourceDataError,
    SourceAdapter,
    UnsupportedSourceFormatError,
    json_dumps_canonical,
    payloads_from_file,
)
from .registry import source_metadata

DirectionReference = Literal["magnetic", "true", "unknown"]
DirectionConvention = Literal["coming_from", "going_to", "unknown"]


class WaveNetManualArchiveAdapter(SourceAdapter):
    """Import WaveNet CSV data without guessing direction reference/convention."""

    metadata = source_metadata("wavenet")
    parser_version = "wavenet-manual-1"
    supported_suffixes = frozenset({".csv", ".zip", ".nc", ".nc4", ".netcdf"})

    def parse_csv(
        self,
        raw_path: str | Path,
        *,
        direction_reference: DirectionReference = "unknown",
        direction_convention: DirectionConvention = "unknown",
    ) -> pd.DataFrame:
        if direction_reference not in {"magnetic", "true", "unknown"}:
            raise ValueError(f"invalid direction_reference: {direction_reference}")
        if direction_convention not in {"coming_from", "going_to", "unknown"}:
            raise ValueError(f"invalid direction_convention: {direction_convention}")
        rows: list[dict[str, Any]] = []
        for payload in payloads_from_file(raw_path, allowed_suffixes=(".csv",)):
            frame = read_tabular_payload(payload)
            station_column = pick_column(
                frame,
                ("station_id", "station", "site_id", "site", "buoy_id"),
                required=True,
                meaning="WaveNet station identifier",
            )
            time_column = pick_column(
                frame,
                ("timestamp_utc", "timestamp", "date_time", "datetime", "time"),
                required=True,
                meaning="WaveNet timestamp",
            )
            hs_column = pick_column(
                frame,
                ("significant_wave_height_m", "significant_wave_height", "hs", "hm0"),
                required=True,
                meaning="significant wave height",
            )
            max_column = pick_column(frame, ("maximum_wave_height_m", "hmax", "max_wave_height"))
            period_column = pick_column(
                frame,
                ("wave_period_s", "peak_period_s", "tp", "period"),
            )
            direction_column = pick_column(
                frame,
                ("wave_direction_deg", "wave_direction", "direction", "mean_direction"),
            )
            quality_column = pick_column(frame, ("quality_flag", "qc_flag", "quality"))
            reference_column = pick_column(frame, ("direction_reference",))
            convention_column = pick_column(frame, ("direction_convention",))
            for row_number, raw in enumerate(frame.to_dict(orient="records")):
                height = optional_float(raw.get(hs_column))  # type: ignore[arg-type]
                if height is None or height < 0:
                    raise ValueError(f"{payload.name} row {row_number}: invalid wave height")
                raw_direction = (
                    optional_float(raw.get(direction_column)) if direction_column else None
                )
                if raw_direction is not None and not 0 <= raw_direction < 360:
                    raise ValueError(f"{payload.name} row {row_number}: direction outside [0, 360)")
                reference = (
                    str(raw.get(reference_column) or direction_reference).strip().lower()
                    if reference_column
                    else direction_reference
                )
                convention = (
                    str(raw.get(convention_column) or direction_convention).strip().lower()
                    if convention_column
                    else direction_convention
                )
                if reference not in {"magnetic", "true", "unknown"}:
                    raise ValueError(f"unknown wave direction reference: {reference!r}")
                if convention not in {"coming_from", "going_to", "unknown"}:
                    raise ValueError(f"unknown wave direction convention: {convention!r}")
                direction_true = None
                if raw_direction is not None and reference == "true" and convention != "unknown":
                    direction_true = (
                        raw_direction
                        if convention == "coming_from"
                        else (raw_direction + 180.0) % 360
                    )
                rows.append(
                    {
                        "wave_station_id": str(raw[station_column]).strip(),  # type: ignore[index]
                        "timestamp_utc": required_utc(
                            raw[time_column],
                            name="timestamp_utc",  # type: ignore[index]
                        ),
                        "significant_wave_height_m": height,
                        "maximum_wave_height_m": (
                            optional_float(raw.get(max_column)) if max_column else None
                        ),
                        "wave_period_s": (
                            optional_float(raw.get(period_column)) if period_column else None
                        ),
                        "wave_direction_deg_raw": raw_direction,
                        "wave_direction_deg_true": direction_true,
                        "direction_reference": reference,
                        "direction_convention": convention,
                        "quality_flag": None if quality_column is None else raw.get(quality_column),
                        "source_member": payload.name,
                        "raw_fields_json": json_dumps_canonical(raw),
                    }
                )
        if not rows:
            raise NoSourceDataError("WaveNet archive contains no CSV observations")
        return (
            pd.DataFrame.from_records(rows)
            .sort_values(["wave_station_id", "timestamp_utc"])
            .reset_index(drop=True)
        )

    def parse_netcdf(self, raw_path: str | Path) -> object:
        """Open NetCDF only when xarray is installed; no field mappings are guessed."""

        try:
            import xarray as xr
        except ImportError as exc:
            raise UnsupportedSourceFormatError(
                "WaveNet NetCDF parsing requires coastwatch-impact[marine]"
            ) from exc
        dataset = xr.open_dataset(raw_path)
        if not dataset.data_vars:
            dataset.close()
            raise NoSourceDataError(f"{Path(raw_path).name}: NetCDF has no data variables")
        return dataset


class WaveNetRealtimeAdapter(SourceAdapter):
    """Disabled placeholder until a stable documented machine interface is configured."""

    metadata = source_metadata("wavenet")

    def fetch(self) -> None:
        raise UnsupportedSourceFormatError(
            "WaveNet realtime sync is disabled: no stable documented API is configured. "
            "Use WaveNetManualArchiveAdapter with an official CSV/NetCDF download."
        )


__all__ = ["WaveNetManualArchiveAdapter", "WaveNetRealtimeAdapter"]
