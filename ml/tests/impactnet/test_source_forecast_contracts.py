from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from coastwatch_impact.data.sources import (
    ForecastAvailabilityError,
    NTSLFForecastManualAdapter,
    assert_available_at_prediction_time,
    select_latest_as_of,
)


def test_ntslf_import_requires_issued_run_and_selects_latest_available(tmp_path: Path) -> None:
    source = tmp_path / "ntslf.csv"
    source.write_text(
        "site_id,model_run_id,issue_time_utc,valid_time_utc,total_water_level_m_aod\n"
        "brighton,run00,2026-01-01T00:00:00Z,2026-01-01T06:00:00Z,2.1\n"
        "brighton,run03,2026-01-01T03:00:00Z,2026-01-01T06:00:00Z,2.3\n"
        "brighton,future,2026-01-01T05:00:00Z,2026-01-01T06:00:00Z,9.9\n",
        encoding="utf-8",
    )
    adapter = NTSLFForecastManualAdapter(tmp_path / "data")
    imported = adapter.import_file(source)
    frame = adapter.parse(imported.raw_path)

    selected = select_latest_as_of(frame, prediction_time="2026-01-01T04:00:00Z")

    assert selected["model_run_id"].tolist() == ["run03"]
    assert selected["forecast_total_water_level_m_aod"].tolist() == [2.3]
    assert selected.iloc[0]["issue_time_utc"] <= pd.Timestamp("2026-01-01T04:00:00Z")
    assert selected.iloc[0]["valid_time_utc"] > pd.Timestamp("2026-01-01T04:00:00Z")


def test_forecast_contract_rejects_future_issue_and_nonfuture_valid_time() -> None:
    frame = pd.DataFrame(
        {
            "site_id": ["s1"],
            "source_model": ["model"],
            "issue_time_utc": ["2026-01-01T05:00:00Z"],
            "valid_time_utc": ["2026-01-01T06:00:00Z"],
        }
    )
    with pytest.raises(ForecastAvailabilityError, match="issue_time <= prediction_time"):
        assert_available_at_prediction_time(frame, "2026-01-01T04:00:00Z")
    with pytest.raises(ForecastAvailabilityError, match="strictly later"):
        select_latest_as_of(
            frame,
            prediction_time="2026-01-01T04:00:00Z",
            valid_times=["2026-01-01T04:00:00Z"],
        )


def test_ntslf_rejects_naive_or_missing_issue_time(tmp_path: Path) -> None:
    adapter = NTSLFForecastManualAdapter(tmp_path / "data")
    missing_issue = tmp_path / "missing.csv"
    missing_issue.write_text(
        "site_id,model_run_id,valid_time_utc,total_water_level_m_aod\n"
        "s1,r1,2026-01-01T06:00:00Z,2.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="forecast issue time"):
        adapter.parse(adapter.import_file(missing_issue).raw_path)

    naive = tmp_path / "naive.csv"
    naive.write_text(
        "site_id,model_run_id,issue_time_utc,valid_time_utc,total_water_level_m_aod\n"
        "s1,r1,2026-01-01 00:00:00,2026-01-01T06:00:00Z,2.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        adapter.parse(adapter.import_file(naive).raw_path)
