"""Manual importer for Environment Agency Historic Flood Warnings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from ._parsing import optional_utc, pick_column, read_tabular_payload, required_utc
from .base import (
    NoSourceDataError,
    RawImportResult,
    SourceAdapter,
    json_dumps_canonical,
    payloads_from_file,
)
from .registry import source_metadata


class EAHistoricWarningsAdapter(SourceAdapter):
    """Preserve a downloaded warning archive and normalise auditable fields.

    The adapter does not turn warnings into confirmed impacts.  It preserves the
    raw row JSON so label-review tools can trace every candidate back to source.
    """

    metadata = source_metadata("ea_historic_warnings")
    parser_version = "ea-historic-warnings-1"
    supported_suffixes = frozenset({".zip", ".csv", ".json"})

    AREA_ALIASES = (
        "warning_area_id",
        "warning_area_code",
        "flood_area_id",
        "floodareaid",
        "flood_area_code",
        "ta_code",
    )
    ISSUED_ALIASES = (
        "issued_time_utc",
        "issued_time",
        "time_raised",
        "timeraised",
        "date_time_raised",
        "date_raised",
    )
    REMOVED_ALIASES = (
        "removed_time_utc",
        "removed_time",
        "time_removed",
        "timeremoved",
        "date_time_removed",
        "date_removed",
    )

    def parse(self, raw_path: str | Path) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for payload in payloads_from_file(raw_path, allowed_suffixes=(".csv", ".json")):
            frame = read_tabular_payload(payload)
            area_column = pick_column(
                frame,
                self.AREA_ALIASES,
                required=True,
                meaning="warning-area identifier",
            )
            issued_column = pick_column(
                frame,
                self.ISSUED_ALIASES,
                required=True,
                meaning="issued time",
            )
            removed_column = pick_column(frame, self.REMOVED_ALIASES)
            severity_column = pick_column(
                frame,
                ("severity", "severity_level", "severitydescription", "warning_severity"),
            )
            message_column = pick_column(
                frame,
                ("message", "warning_message", "description", "floodarea"),
            )
            source_column = pick_column(frame, ("source", "source_system", "publisher"))
            for row_number, raw in enumerate(frame.to_dict(orient="records")):
                issued = required_utc(raw[issued_column], name="issued_time_utc")  # type: ignore[index]
                removed = (
                    optional_utc(raw.get(removed_column), name="removed_time_utc")
                    if removed_column
                    else None
                )
                if removed is not None and removed < issued:
                    raise ValueError(
                        f"{payload.name} row {row_number}: removed time precedes issued time"
                    )
                rows.append(
                    {
                        "warning_area_id": str(raw[area_column]).strip(),  # type: ignore[index]
                        "severity": None if severity_column is None else raw.get(severity_column),
                        "issued_time_utc": issued,
                        "removed_time_utc": removed,
                        "message": None if message_column is None else raw.get(message_column),
                        "source": (
                            self.metadata.owner
                            if source_column is None
                            else str(raw.get(source_column) or self.metadata.owner)
                        ),
                        "source_member": payload.name,
                        "raw_fields_json": json_dumps_canonical(raw),
                    }
                )
        if not rows:
            raise NoSourceDataError("EA historic warnings: no warning records found")
        result = pd.DataFrame.from_records(rows)
        if (result["warning_area_id"].str.len() == 0).any():
            raise ValueError("warning_area_id must not be empty")
        return result.sort_values(["issued_time_utc", "warning_area_id"]).reset_index(drop=True)

    def import_and_parse(self, source: str | Path) -> tuple[RawImportResult, Path]:
        imported = self.import_file(source)
        frame = self.parse(imported.raw_path)
        output = self.write_versioned_frame(
            frame,
            raw_sha256=imported.sha256,
            table_name="historic_warnings",
        )
        return imported, output


# Naming used in the implementation specification.
HistoricWarningsManualAdapter = EAHistoricWarningsAdapter


__all__ = ["EAHistoricWarningsAdapter", "HistoricWarningsManualAdapter"]
