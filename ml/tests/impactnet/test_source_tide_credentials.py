from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from coastwatch_impact.data.sources import (
    CopernicusMarineForecastAdapter,
    CopernicusRequestConfig,
    EATideGaugeRealtimeAdapter,
    MetOfficeDataHubAdapter,
    NetworkAccessDisabledError,
    NoSourceDataError,
    SourceCredentialsError,
    UnsupportedSourceFormatError,
)


def test_ea_tide_network_is_opt_in_and_saved_response_is_parseable(tmp_path: Path) -> None:
    disabled = EATideGaugeRealtimeAdapter(tmp_path / "data")
    with pytest.raises(NetworkAccessDisabledError, match="disabled"):
        disabled.fetch_readings("E123")
    assert not disabled.raw_directory.exists()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "items": [
                {
                    "dateTime": "2026-01-01T00:15:00Z",
                    "value": 2.4,
                    "measure": {"parameterName": "Water Level", "qualifier": "mAOD"},
                }
            ]
        }
        return httpx.Response(200, json=payload, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = EATideGaugeRealtimeAdapter(
        tmp_path / "data",
        allow_network=True,
        client=client,
    )
    imported = adapter.fetch_readings("E123")
    frame = adapter.parse(imported.raw_path, station_id="E123")

    assert frame.loc[0, "water_level_m_aod"] == 2.4
    assert frame.loc[0, "water_level_datum"] == "mAOD"
    assert json.loads(frame.loc[0, "raw_fields_json"])["value"] == 2.4
    client.close()


def test_ea_tide_empty_response_creates_no_raw_file(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = EATideGaugeRealtimeAdapter(
        tmp_path / "data",
        allow_network=True,
        client=client,
    )
    with pytest.raises(NoSourceDataError, match="no readings"):
        adapter.fetch_readings("E123")
    assert not adapter.raw_directory.exists()
    client.close()


def test_credentialed_stubs_fail_clearly_and_never_create_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("METOFFICE_API_KEY", raising=False)
    monkeypatch.delenv("COPERNICUSMARINE_USERNAME", raising=False)
    monkeypatch.delenv("COPERNICUSMARINE_PASSWORD", raising=False)
    met = MetOfficeDataHubAdapter(tmp_path / "data", api_key=None)
    with pytest.raises(SourceCredentialsError, match="METOFFICE_API_KEY"):
        met.request_plan(site_id="s1", latitude=50.0, longitude=0.0)

    configured_met = MetOfficeDataHubAdapter(
        tmp_path / "data",
        api_key="test-only",
        endpoint="https://example.invalid/review-required",
    )
    with pytest.raises(UnsupportedSourceFormatError, match="intentionally not implemented"):
        configured_met.fetch()

    config = CopernicusRequestConfig(
        product_id="PRODUCT",
        dataset_id="DATASET",
        variables=("VHM0",),
        product_version="review-me",
    )
    copernicus = CopernicusMarineForecastAdapter(tmp_path / "data", config=config)
    with pytest.raises(SourceCredentialsError, match="COPERNICUSMARINE_USERNAME"):
        copernicus.request_plan()
    assert not (tmp_path / "data" / "raw").exists()
