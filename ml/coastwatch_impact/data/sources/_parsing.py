"""Small strict parsing helpers shared only by source adapters."""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import pandas as pd

from ..schemas import utc_datetime
from .base import NamedPayload, NoSourceDataError, UnsupportedSourceFormatError


def normalise_column(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def normalised_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result.columns = [normalise_column(column) for column in result.columns]
    return result


def pick_column(
    frame: pd.DataFrame,
    aliases: Iterable[str],
    *,
    required: bool = False,
    meaning: str | None = None,
) -> str | None:
    lookup = {normalise_column(column): str(column) for column in frame.columns}
    for alias in aliases:
        key = normalise_column(alias)
        if key in lookup:
            return lookup[key]
    if required:
        expected = ", ".join(aliases)
        raise ValueError(f"missing required {meaning or 'column'}; expected one of: {expected}")
    return None


def read_tabular_payload(payload: NamedPayload) -> pd.DataFrame:
    lower = payload.name.lower()
    if lower.endswith(".csv"):
        frame = pd.read_csv(io.BytesIO(payload.payload))
    elif lower.endswith(".json") or lower.endswith(".geojson"):
        try:
            parsed = json.loads(payload.payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON in {payload.name}: {exc}") from exc
        if isinstance(parsed, list):
            records = parsed
        elif isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
            records = parsed["items"]
        elif isinstance(parsed, dict) and isinstance(parsed.get("features"), list):
            records = [
                {
                    **(feature.get("properties") or {}),
                    "geometry": feature.get("geometry"),
                }
                for feature in parsed["features"]
                if isinstance(feature, dict)
            ]
        elif isinstance(parsed, dict):
            records = [parsed]
        else:
            raise ValueError(f"unsupported JSON root in {payload.name}")
        frame = pd.DataFrame.from_records(records)
    else:
        raise UnsupportedSourceFormatError(f"unsupported tabular payload: {payload.name}")
    if frame.empty:
        raise NoSourceDataError(f"{payload.name}: no records")
    return normalised_columns(frame)


def required_utc(value: Any, *, name: str) -> datetime:
    return utc_datetime(value, name=name)


def optional_utc(value: Any, *, name: str) -> datetime | None:
    if value is None or value is pd.NaT or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return utc_datetime(value, name=name)


def optional_float(value: Any) -> float | None:
    if value is None or value is pd.NaT or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return float(value)


__all__ = [
    "normalise_column",
    "normalised_columns",
    "optional_float",
    "optional_utc",
    "pick_column",
    "read_tabular_payload",
    "required_utc",
]
