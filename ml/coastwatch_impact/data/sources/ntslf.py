"""Manual issued-forecast importer for the NTSLF tide-surge model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from ..schemas import validate_forecasts_frame
from ._parsing import optional_float, pick_column, read_tabular_payload, required_utc
from .base import NoSourceDataError, SourceAdapter, json_dumps_canonical, payloads_from_file
from .forecast_contracts import select_latest_as_of, validate_issued_forecasts
from .registry import source_metadata


class NTSLFForecastManualAdapter(SourceAdapter):
    """Import genuine archived forecast runs; final observations are not substitutes."""

    metadata = source_metadata("ntslf")
    parser_version = "ntslf-issued-forecast-1"
    supported_suffixes = frozenset({".csv", ".json", ".zip"})

    def parse(
        self,
        raw_path: str | Path,
        *,
        default_site_id: str | None = None,
        default_model_run_id: str | None = None,
        source_model: str = "ntslf_tide_surge",
    ) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for payload in payloads_from_file(raw_path, allowed_suffixes=(".csv", ".json")):
            frame = read_tabular_payload(payload)
            issue_column = pick_column(
                frame,
                ("issue_time_utc", "issue_time", "issued_at", "forecast_reference_time"),
                required=True,
                meaning="forecast issue time",
            )
            valid_column = pick_column(
                frame,
                ("valid_time_utc", "valid_time", "forecast_time", "time"),
                required=True,
                meaning="forecast valid time",
            )
            site_column = pick_column(frame, ("site_id", "station_id", "station", "site"))
            run_column = pick_column(frame, ("model_run_id", "run_id", "forecast_run", "model_run"))
            total_column = pick_column(
                frame,
                ("forecast_total_water_level_m_aod", "total_water_level_m_aod", "total_level"),
            )
            tide_column = pick_column(
                frame,
                ("forecast_tide_m_aod", "tide_m_aod", "astronomical_tide"),
            )
            surge_column = pick_column(
                frame,
                ("forecast_surge_m", "surge_m", "surge_residual", "residual"),
            )
            version_column = pick_column(frame, ("model_version", "version"))
            if total_column is None and tide_column is None and surge_column is None:
                raise ValueError(
                    f"{payload.name}: at least one total-water, tide or surge forecast is required"
                )
            for row_number, raw in enumerate(frame.to_dict(orient="records")):
                site_id = raw.get(site_column) if site_column else default_site_id
                run_id = raw.get(run_column) if run_column else default_model_run_id
                if site_id is None or not str(site_id).strip():
                    raise ValueError(
                        f"{payload.name} row {row_number}: site_id is required; "
                        "provide a column or default_site_id"
                    )
                if run_id is None or not str(run_id).strip():
                    raise ValueError(
                        f"{payload.name} row {row_number}: model_run_id is required; "
                        "provide a column or default_model_run_id"
                    )
                issue = required_utc(raw[issue_column], name="issue_time_utc")  # type: ignore[index]
                valid = required_utc(raw[valid_column], name="valid_time_utc")  # type: ignore[index]
                rows.append(
                    {
                        "site_id": str(site_id).strip(),
                        "issue_time_utc": issue,
                        "valid_time_utc": valid,
                        "lead_hours": (valid - issue).total_seconds() / 3600.0,
                        "source_model": source_model,
                        "model_run_id": str(run_id).strip(),
                        "forecast_total_water_level_m_aod": (
                            optional_float(raw.get(total_column)) if total_column else None
                        ),
                        "forecast_tide_m_aod": (
                            optional_float(raw.get(tide_column)) if tide_column else None
                        ),
                        "forecast_surge_m": (
                            optional_float(raw.get(surge_column)) if surge_column else None
                        ),
                        "quality_flag": None,
                        "source_model_version": (
                            None if version_column is None else raw.get(version_column)
                        ),
                        "source_member": payload.name,
                        "raw_fields_json": json_dumps_canonical(raw),
                    }
                )
        if not rows:
            raise NoSourceDataError("NTSLF archive contains no issued forecasts")
        frame = validate_issued_forecasts(pd.DataFrame.from_records(rows))
        # The canonical validator rejects duplicated forecast identities and
        # enforces lead consistency a second time at the shared schema boundary.
        return validate_forecasts_frame(frame)

    @staticmethod
    def select_as_of(
        frame: pd.DataFrame,
        *,
        prediction_time: str,
        valid_times: list[str] | None = None,
    ) -> pd.DataFrame:
        return select_latest_as_of(
            frame,
            prediction_time=prediction_time,
            valid_times=valid_times,
        )


# Short alias used by CLI integrations.
NTSLFManualArchiveAdapter = NTSLFForecastManualAdapter


__all__ = ["NTSLFForecastManualAdapter", "NTSLFManualArchiveAdapter"]
