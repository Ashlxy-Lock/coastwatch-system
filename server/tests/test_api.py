import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import database as database_service
from app import environment as environment_service
from app.main import app


@pytest.fixture(autouse=True)
def isolated_environment_provider(monkeypatch: pytest.MonkeyPatch):
    for variable in (
        "COAST_LATITUDE",
        "COAST_LONGITUDE",
        "COAST_LOCATION_NAME",
        "COAST_DISPLAY_LOCATION",
    ):
        monkeypatch.delenv(variable, raising=False)

    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("tests must mock Open-Meteo instead of using the network")

    monkeypatch.setattr(environment_service, "_request_json", unexpected_network)
    environment_service.clear_environment_cache()
    yield
    environment_service.clear_environment_cache()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COASTAL_DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as test_client:
        yield test_client


def sample_payload(seq: int = 42) -> dict:
    return {
        "device_id": "COAST_01",
        "seq": seq,
        "uptime_ms": 123456,
        "distance_mm": 815,
        "water_rise_mm": 126,
        "rise_rate_mm_s": 21,
        "person_detected": True,
        "alarm_level": 3,
        "health_flags": 7,
        "wifi_rssi": -55,
    }


def test_existing_location_database_migrates_safely_to_plain_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE device_locations (
                device_id TEXT PRIMARY KEY,
                location TEXT NOT NULL,
                display_location TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO device_locations VALUES
            ('COAST_01', 'London', 'LONDON ENGLAND GB', 51.50853,
             -0.12574, '2026-08-03T00:00:00Z')
            """
        )
    monkeypatch.setenv("COASTAL_DB_PATH", str(path))
    database_service.init_database()
    record = database_service.get_device_location("COAST_01")
    assert record is not None
    assert record["kind"] == "place"


def configure_live_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COAST_LATITUDE", "36.0671")
    monkeypatch.setenv("COAST_LONGITUDE", "120.3826")
    monkeypatch.setenv("COAST_LOCATION_NAME", "青岛海滨")


def open_meteo_payload(url: str) -> dict:
    if url == environment_service.WEATHER_API_URL:
        return {
            "current": {
                "time": "2026-08-02T12:00",
                "temperature_2m": 28.6,
                "relative_humidity_2m": 78,
                "weather_code": 2,
                "wind_speed_10m": 16.2,
                "wind_direction_10m": 135,
            }
        }
    if url == environment_service.MARINE_API_URL:
        return {
            "current": {
                "time": "2026-08-02T12:00",
                "wave_height": 0.7,
                "wave_period": 5.4,
                "sea_surface_temperature": 25.8,
                "sea_level_height_msl": 0.31,
                "ocean_current_velocity": 0.9,
                "ocean_current_direction": 82,
            },
            "hourly": {
                "time": [
                    "2026-08-02T11:00",
                    "2026-08-02T12:00",
                    "2026-08-02T13:00",
                ],
                "sea_level_height_msl": [0.20, 0.31, 0.46],
            },
        }
    raise AssertionError(f"unexpected provider URL: {url}")


def test_health_dashboard_and_demo_environment(client: TestClient):
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["database"] == "ok"

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "CoastWatch Great Yarmouth Monitoring Console" in dashboard.text

    environment = client.get("/api/v1/environment", params={"device_id": "COAST_01"})
    assert environment.status_code == 200
    assert environment.json()["source"] == "demo"
    assert environment.json()["provider"] == "built-in-demo"
    assert environment.json()["stale"] is False
    assert "演示" in environment.json()["location"]
    assert environment.json()["display_location"] == "COAST STATION"


def test_device_location_can_be_selected_and_drives_environment(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    presets = client.get("/api/v1/locations/presets")
    assert presets.status_code == 200
    assert presets.json()[0]["location"] == "Brighton, England, United Kingdom"
    assert presets.json()[0]["display_location"] == "BRIGHTON ENGLAND GB"
    assert presets.json()[0]["kind"] == "coast"

    selection = {
        "device_id": "COAST_01",
        "kind": "coast",
        "location": "Brighton, England, United Kingdom",
        "display_location": "brighton england gb",
        "latitude": 50.82838,
        "longitude": -0.13947,
    }
    saved = client.put("/api/v1/device-location", json=selection)
    assert saved.status_code == 200
    assert saved.json()["display_location"] == "BRIGHTON ENGLAND GB"
    assert saved.json()["kind"] == "coast"
    assert (
        client.get("/api/v1/device-location", params={"device_id": "COAST_01"}).json()
        == saved.json()
    )

    monkeypatch.setattr(
        environment_service,
        "_request_json",
        lambda _client, url, _params: open_meteo_payload(url),
    )
    environment = client.get("/api/v1/environment", params={"device_id": "COAST_01"})
    assert environment.status_code == 200
    assert environment.json()["location"] == "Brighton, England, United Kingdom"
    assert environment.json()["display_location"] == "BRIGHTON ENGLAND GB"
    assert environment.json()["kind"] == "coast"
    assert environment.json()["source"] == "open-meteo"


def test_plain_global_place_never_fabricates_marine_conditions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    saved = client.put(
        "/api/v1/device-location",
        json={
            "device_id": "COAST_01",
            "kind": "place",
            "location": "London, England, United Kingdom",
            "display_location": "LONDON ENGLAND GB",
            "latitude": 51.50853,
            "longitude": -0.12574,
        },
    )
    assert saved.status_code == 200

    calls: list[str] = []

    def weather_only(_client, url: str, _params) -> dict:
        calls.append(url)
        if url != environment_service.WEATHER_API_URL:
            raise AssertionError("ordinary places must not request marine data")
        return open_meteo_payload(url)

    monkeypatch.setattr(environment_service, "_request_json", weather_only)
    response = client.get("/api/v1/environment", params={"device_id": "COAST_01"})
    assert response.status_code == 200
    payload = response.json()
    assert calls == [environment_service.WEATHER_API_URL]
    assert payload["kind"] == "place"
    assert payload["display_location"] == "LONDON ENGLAND GB"
    assert payload["wave_height_m"] is None
    assert payload["water_temperature_c"] is None
    assert payload["tide_status"] is None


def test_location_search_returns_chinese_and_ascii_labels(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def fake_geocoding(_client, url: str, params) -> dict:
        assert url == environment_service.GEOCODING_API_URL
        if params["language"] == "zh":
            return {
                "results": [
                    {
                        "id": 1797929,
                        "name": "青岛市",
                        "admin1": "山东省",
                        "country": "中国",
                        "latitude": 36.066,
                        "longitude": 120.369,
                    }
                ]
            }
        return {
            "results": [
                {
                    "id": 1797929,
                    "name": "Qingdao",
                    "latitude": 36.066,
                    "longitude": 120.369,
                }
            ]
        }

    monkeypatch.setattr(environment_service, "_request_json", fake_geocoding)
    response = client.get("/api/v1/locations/search", params={"q": "青岛"})
    assert response.status_code == 200
    assert response.json()[0]["location"] == "青岛市 · 山东省 · 中国"
    assert response.json()[0]["display_location"] == "QINGDAO"


def test_location_search_prefers_real_changchun_city_over_same_named_villages(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[str, str]] = []

    def fake_geocoding(_client, url: str, params) -> dict:
        if url == environment_service.GEOCODING_GET_URL:
            calls.append((f"id={params['id']}", str(params["language"])))
            return {
                "id": 2038180,
                "name": "Changchun",
                "latitude": 43.88,
                "longitude": 125.32278,
            }
        assert url == environment_service.GEOCODING_API_URL
        language = str(params["language"])
        name = str(params["name"])
        calls.append((name, language))
        if name == "长春市":
            if language == "zh":
                return {
                    "results": [
                        {
                            "id": 2038180,
                            "name": "长春市",
                            "admin1": "吉林",
                            "admin2": "长春市",
                            "country": "中国",
                            "feature_code": "PPLA",
                            "population": 4_714_996,
                            "latitude": 43.88,
                            "longitude": 125.32278,
                        },
                        {
                            "id": 12538532,
                            "name": "长春市南湖公园",
                            "admin1": "吉林",
                            "country": "中国",
                            "feature_code": "PRK",
                            "latitude": 43.84921,
                            "longitude": 125.30284,
                        },
                    ]
                }
            return {"results": []}
        if language == "zh":
            return {
                "results": [
                    {
                        "id": 2038179,
                        "name": "长春",
                        "admin1": "黑龙江",
                        "admin2": "齐齐哈尔市",
                        "country": "中国",
                        "feature_code": "PPLA4",
                        "latitude": 47.73186,
                        "longitude": 125.65863,
                    },
                    {
                        "id": 1815770,
                        "name": "长春",
                        "admin1": "陕西",
                        "admin2": "渭南市",
                        "country": "中国",
                        "feature_code": "PPL",
                        "latitude": 34.8349,
                        "longitude": 109.03646,
                    },
                ]
            }
        return {"results": []}

    monkeypatch.setattr(environment_service, "_request_json", fake_geocoding)
    response = client.get("/api/v1/locations/search", params={"q": "长春"})
    assert response.status_code == 200
    assert calls == [
        ("长春", "zh"),
        ("长春", "en"),
        ("长春市", "zh"),
        ("长春市", "en"),
        ("id=2038180", "en"),
    ]
    assert response.json() == [
        {
            "provider_id": 2038180,
            "name": "长春市",
            "admin1": "吉林",
            "admin2": "长春市",
            "country": "中国",
            "feature_code": "PPLA",
            "population": 4_714_996,
            "kind": "place",
            "location": "长春市 · 吉林 · 中国",
            "display_location": "CHANGCHUN",
            "latitude": 43.88,
            "longitude": 125.32278,
        }
    ]


def test_live_environment_is_mapped_and_cached(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    configure_live_environment(monkeypatch)
    calls: list[tuple[str, dict]] = []

    def fake_request(_client, url: str, params) -> dict:
        calls.append((url, dict(params)))
        return open_meteo_payload(url)

    monkeypatch.setattr(environment_service, "_request_json", fake_request)

    first = client.get("/api/v1/environment", params={"device_id": "COAST_01"})
    second = client.get("/api/v1/environment", params={"device_id": "COAST_01"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(calls) == 2

    payload = first.json()
    assert payload["location"] == "青岛海滨"
    assert payload["weather"] == "局部多云"
    assert payload["weather_code"] == 2
    assert payload["air_temperature_c"] == 28.6
    assert payload["humidity_percent"] == 78.0
    assert payload["wind_speed_kmh"] == 16.2
    assert payload["wave_height_m"] == 0.7
    assert payload["wave_period_s"] == 5.4
    assert payload["water_temperature_c"] == 25.8
    assert payload["sea_level_height_m"] == 0.31
    assert payload["tide_status"] == "涨潮"
    assert payload["ocean_current_velocity_kmh"] == 0.9
    assert payload["ocean_current_direction_deg"] == 82.0
    assert payload["source"] == "open-meteo"
    assert payload["provider"] == "open-meteo"
    assert payload["stale"] is False
    assert second.json() == payload

    assert calls[0][1]["latitude"] == 36.0671
    assert calls[0][1]["longitude"] == 120.3826
    assert "weather_code" in calls[0][1]["current"]
    assert "wave_height" in calls[1][1]["current"]
    assert calls[1][1]["hourly"] == "sea_level_height_msl"


def test_expired_environment_cache_is_returned_stale_on_provider_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    configure_live_environment(monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(environment_service.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        environment_service,
        "_request_json",
        lambda _client, url, _params: open_meteo_payload(url),
    )

    fresh = client.get("/api/v1/environment").json()
    assert fresh["source"] == "open-meteo"
    clock[0] += environment_service.CACHE_TTL_SECONDS + 1

    def provider_offline(*_args, **_kwargs):
        raise RuntimeError("simulated provider outage")

    monkeypatch.setattr(environment_service, "_request_json", provider_offline)
    stale = client.get("/api/v1/environment")
    assert stale.status_code == 200
    payload = stale.json()
    assert payload["source"] == "stale"
    assert payload["provider"] == "open-meteo"
    assert payload["stale"] is True
    assert payload["wave_height_m"] == fresh["wave_height_m"]
    assert payload["updated_at"] == fresh["updated_at"]


def test_configured_environment_without_cache_reports_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    configure_live_environment(monkeypatch)

    def provider_offline(*_args, **_kwargs):
        raise RuntimeError("simulated cold-start outage")

    monkeypatch.setattr(environment_service, "_request_json", provider_offline)
    response = client.get("/api/v1/environment")
    assert response.status_code == 200
    payload = response.json()
    assert payload["location"] == "青岛海滨"
    assert payload["source"] == "stale"
    assert payload["provider"] == "open-meteo"
    assert payload["stale"] is True
    assert payload["air_temperature_c"] is None
    assert payload["wave_height_m"] is None


def test_post_latest_and_history_are_persisted(client: TestClient):
    first = client.post("/api/v1/telemetry", json=sample_payload(42))
    second = client.post("/api/v1/telemetry", json=sample_payload(43))
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["device_id"] == "COAST_01"
    assert first.json()["person_detected"] is True

    latest = client.get("/api/v1/telemetry/latest", params={"device_id": "COAST_01"})
    assert latest.status_code == 200
    assert latest.json()["seq"] == 43

    history = client.get(
        "/api/v1/telemetry", params={"device_id": "COAST_01", "limit": 10}
    )
    assert history.status_code == 200
    assert [row["seq"] for row in history.json()] == [43, 42]


def test_latest_returns_404_before_first_telemetry(client: TestClient):
    response = client.get("/api/v1/telemetry/latest", params={"device_id": "COAST_01"})
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("alarm_level", 5),
        ("wifi_rssi", 1),
        ("person_detected", 1),
        ("seq", -1),
        ("water_rise_mm", 2_147_483_648),
    ],
)
def test_invalid_telemetry_ranges_are_rejected(
    client: TestClient, field: str, value: object
):
    payload = sample_payload()
    payload[field] = value
    response = client.post("/api/v1/telemetry", json=payload)
    assert response.status_code == 422


def test_unknown_telemetry_field_is_rejected(client: TestClient):
    payload = sample_payload()
    payload["wifi_password"] = "must-not-be-accepted"
    response = client.post("/api/v1/telemetry", json=payload)
    assert response.status_code == 422
