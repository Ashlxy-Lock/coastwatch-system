import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import environment as environment_service
from app import gateway as gateway_service
from app.auth import encode_admin_password_hash
from app.database import get_device_location
from app.gateway import app

TEST_TOKEN = "test-only-device-token"
AUTH_HEADERS = {"X-Device-Token": TEST_TOKEN}
BEARER_AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}
TEST_ADMIN_PASSWORD_HASH = encode_admin_password_hash(
    "gateway-test-password", salt=b"gateway-test-salt"
)
TEST_ADMIN_SESSION_SECRET = "gateway-test-session-secret-value-0001"


def sample_payload() -> dict:
    return {
        "device_id": "COAST_01",
        "seq": 7,
        "uptime_ms": 123456,
        "distance_mm": 815,
        "water_rise_mm": 126,
        "rise_rate_mm_s": 21,
        "person_detected": True,
        "alarm_level": 3,
        "health_flags": 7,
        "wifi_rssi": -55,
    }


@pytest.fixture(autouse=True)
def isolated_environment_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COAST_ADMIN_PASSWORD_HASH", TEST_ADMIN_PASSWORD_HASH)
    monkeypatch.setenv("COAST_ADMIN_SESSION_SECRET", TEST_ADMIN_SESSION_SECRET)
    monkeypatch.delenv("COAST_ADMIN_PASSWORD_HASH_FILE", raising=False)
    monkeypatch.delenv("COAST_ADMIN_SESSION_SECRET_FILE", raising=False)
    for variable in (
        "COAST_LATITUDE",
        "COAST_LONGITUDE",
        "COAST_LOCATION_NAME",
        "COAST_DISPLAY_LOCATION",
    ):
        monkeypatch.delenv(variable, raising=False)

    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("gateway tests must not use the network")

    monkeypatch.setattr(environment_service, "_request_json", unexpected_network)
    environment_service.clear_environment_cache()
    yield
    environment_service.clear_environment_cache()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COASTAL_DB_PATH", str(tmp_path / "gateway-test.db"))
    monkeypatch.setenv("COAST_DEVICE_TOKEN", TEST_TOKEN)
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/health",
        "/api/v1/environment",
        "/api/v1/locations/presets",
    ],
)
def test_get_routes_require_correct_token(client: TestClient, path: str):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-Device-Token": "wrong-token"}).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong-token"}).status_code == 401
    assert client.get(path, headers={"Authorization": f"Basic {TEST_TOKEN}"}).status_code == 401
    assert client.get(path, headers=AUTH_HEADERS).status_code == 200
    assert client.get(path, headers=BEARER_AUTH_HEADERS).status_code == 200


def test_location_search_requires_token_before_calling_provider(client: TestClient):
    path = "/api/v1/locations/search?q=Brighton&count=8"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-Device-Token": "wrong-token"}).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong-token"}).status_code == 401


def test_device_can_list_presets_and_select_one_with_bearer_token(
    client: TestClient,
):
    presets = client.get(
        "/api/v1/locations/presets", headers=BEARER_AUTH_HEADERS
    )
    assert presets.status_code == 200
    assert presets.json()[0] == {
        "id": "uk_brighton",
        "kind": "coast",
        "name": "Brighton, England, United Kingdom",
        "display_location": "BRIGHTON ENGLAND GB",
        "lat": 50.82838,
        "lon": -0.13947,
    }
    assert len(presets.json()) == 16
    assert len(json.dumps(presets.json()).encode("utf-8")) <= 4096
    assert presets.json()[5] == {
        "id": "uk_bangor_ni",
        "kind": "coast",
        "name": "Bangor, Northern Ireland, United Kingdom",
        "display_location": "BANGOR NORTHERN IRELAND GB",
        "lat": 54.66079,
        "lon": -5.66802,
    }
    assert presets.json()[-1]["id"] == "cn_qingdao"
    assert all(item["kind"] == "coast" for item in presets.json())
    assert not any("LONDON" in item["display_location"] for item in presets.json())
    assert not any("CHANGCHUN" in item["display_location"] for item in presets.json())

    selection = {"device_id": "COAST_01", "location_id": "uk_brighton"}
    assert client.put("/api/v1/device-location", json=selection).status_code == 401
    response = client.put(
        "/api/v1/device-location",
        json=selection,
        headers=BEARER_AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert response.json() == {
        "device_id": "COAST_01",
        "id": "uk_brighton",
        "kind": "coast",
        "name": "Brighton, England, United Kingdom",
        "display_location": "BRIGHTON ENGLAND GB",
        "lat": 50.82838,
        "lon": -0.13947,
    }
    stored = get_device_location("COAST_01")
    assert stored is not None
    assert stored["kind"] == "coast"
    assert stored["location"] == "Brighton, England, United Kingdom"
    assert stored["display_location"] == "BRIGHTON ENGLAND GB"
    assert stored["latitude"] == 50.82838
    assert stored["longitude"] == -0.13947


def test_device_location_selection_rejects_unknown_or_arbitrary_values(
    client: TestClient,
):
    unknown = client.put(
        "/api/v1/device-location",
        json={"device_id": "COAST_01", "location_id": "not_a_preset"},
        headers=AUTH_HEADERS,
    )
    assert unknown.status_code == 404

    arbitrary = client.put(
        "/api/v1/device-location",
        json={
            "device_id": "COAST_01",
            "location_id": "uk_brighton",
            "lat": 0,
            "lon": 0,
        },
        headers=AUTH_HEADERS,
    )
    assert arbitrary.status_code == 422


def test_device_can_search_global_locations_when_chinese_lookup_is_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[str, str]] = []

    def fake_geocoding(_client, url: str, params) -> dict:
        assert url == environment_service.GEOCODING_API_URL
        calls.append((str(params["name"]), str(params["language"])))
        if params["language"] == "zh":
            return {"results": []}
        return {
            "results": [
                {
                    "id": 2654710,
                    "name": "Brighton",
                    "admin1": "England",
                    "country": "United Kingdom",
                    "country_code": "GB",
                    "feature_code": "PPLA2",
                    "population": 290_885,
                    "latitude": 50.82838,
                    "longitude": -0.13947,
                }
            ]
        }

    monkeypatch.setattr(environment_service, "_request_json", fake_geocoding)
    response = client.get(
        "/api/v1/locations/search",
        params={"q": "Brighton", "count": 8},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    # ASCII board searches use one English provider call for lower latency.
    assert calls == [("Brighton", "en")]
    assert response.json() == [
        {
            "id": "geo_2654710",
            "kind": "place",
            "name": "Brighton · England · United Kingdom",
            "display_location": "BRIGHTON ENGLAND GB",
            "lat": 50.82838,
            "lon": -0.13947,
        }
    ]
    assert client.get(
        "/api/v1/locations/search",
        params={"q": "Brighton", "count": 9},
        headers=AUTH_HEADERS,
    ).status_code == 422


def test_device_search_retries_then_cache_makes_apply_provider_independent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    calls = 0

    def flaky_geocoding(_client, url: str, params) -> dict:
        nonlocal calls
        calls += 1
        assert url == environment_service.GEOCODING_API_URL
        assert params["language"] == "en"
        if calls == 1:
            raise TimeoutError("one transient geocoder timeout")
        return {
            "results": [
                {
                    "id": 2643743,
                    "name": "London",
                    "admin1": "England",
                    "country": "United Kingdom",
                    "country_code": "GB",
                    "feature_code": "PPLC",
                    "population": 8_961_989,
                    "latitude": 51.50853,
                    "longitude": -0.12574,
                }
            ]
        }

    monkeypatch.setattr(environment_service, "_request_json", flaky_geocoding)
    search = client.get(
        "/api/v1/locations/search",
        params={"q": "London"},
        headers=AUTH_HEADERS,
    )
    assert search.status_code == 200
    assert search.json()[0]["id"] == "geo_2643743"
    assert calls == 2

    # Search results are server-trusted canonical data. Immediate APPLY must
    # not depend on a second upstream request that can fail independently.
    selected = client.put(
        "/api/v1/device-location",
        json={"device_id": "COAST_01", "location_id": "geo_2643743"},
        headers=AUTH_HEADERS,
    )
    assert selected.status_code == 200
    assert selected.json()["display_location"] == "LONDON ENGLAND GB"
    assert calls == 2

    repeated = client.get(
        "/api/v1/locations/search",
        params={"q": "London"},
        headers=AUTH_HEADERS,
    )
    assert repeated.status_code == 200
    assert calls == 2


def test_device_search_ranks_london_using_english_identity(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def fake_geocoding(_client, url: str, params) -> dict:
        assert url == environment_service.GEOCODING_API_URL
        if params["language"] == "zh":
            return {
                "results": [
                    {
                        "id": 2643743,
                        "name": "\u4f26\u6566",
                        "admin1": "\u82f1\u683c\u5170",
                        "country": "\u82f1\u56fd",
                        "country_code": "GB",
                        "feature_code": "PPLC",
                        "population": 8_961_989,
                        "latitude": 51.50853,
                        "longitude": -0.12574,
                    },
                    {
                        "id": 4517009,
                        "name": "London",
                        "admin1": "Ohio",
                        "country": "United States",
                        "country_code": "US",
                        "feature_code": "PPLA2",
                        "population": 10_060,
                        "latitude": 39.88645,
                        "longitude": -83.44825,
                    },
                ]
            }
        return {
            "results": [
                {
                    "id": 2643743,
                    "name": "London",
                    "admin1": "England",
                    "country": "United Kingdom",
                    "country_code": "GB",
                    "feature_code": "PPLC",
                    "population": 8_961_989,
                    "latitude": 51.50853,
                    "longitude": -0.12574,
                },
                {
                    "id": 4517009,
                    "name": "London",
                    "admin1": "Ohio",
                    "country": "United States",
                    "country_code": "US",
                    "feature_code": "PPLA2",
                    "population": 10_060,
                    "latitude": 39.88645,
                    "longitude": -83.44825,
                },
            ]
        }

    monkeypatch.setattr(environment_service, "_request_json", fake_geocoding)
    response = client.get(
        "/api/v1/locations/search",
        params={"q": "London"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    rows = response.json()
    assert rows[0]["id"] == "geo_2643743"
    assert rows[0]["display_location"] == "LONDON ENGLAND GB"
    assert rows[1]["display_location"] == "LONDON OHIO US"


def test_device_search_survives_one_language_failure_and_transliterates_latin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def fake_geocoding(_client, url: str, params) -> dict:
        assert url == environment_service.GEOCODING_API_URL
        if params["language"] == "zh":
            raise TimeoutError("localized provider timeout")
        return {
            "results": [
                {
                    "id": 3448439,
                    "name": "S\u00e3o Paulo",
                    "admin1": "S\u00e3o Paulo",
                    "country": "Brazil",
                    "country_code": "BR",
                    "feature_code": "PPLA",
                    "population": 12_400_000,
                    "latitude": -23.5475,
                    "longitude": -46.63611,
                }
            ]
        }

    monkeypatch.setattr(environment_service, "_request_json", fake_geocoding)
    response = client.get(
        "/api/v1/locations/search",
        # A non-ASCII dashboard query exercises zh failure -> en fallback.
        params={"q": "S\u00e3o Paulo"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()[0]["display_location"] == "SAO PAULO BR"


def test_device_search_utf8_name_is_safely_limited_to_79_bytes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    localized_name = "海岸城市" * 30

    def fake_geocoding(_client, url: str, params) -> dict:
        assert url == environment_service.GEOCODING_API_URL
        if params["language"] == "zh":
            return {
                "results": [
                    {
                        "id": 123456,
                        "name": localized_name,
                        "country": "英国",
                        "country_code": "GB",
                        "latitude": 51.0,
                        "longitude": -1.0,
                    }
                ]
            }
        return {
            "results": [
                {
                    "id": 123456,
                    "name": "Long Coastal City",
                    "admin1": "England",
                    "country": "United Kingdom",
                    "country_code": "GB",
                    "latitude": 51.0,
                    "longitude": -1.0,
                }
            ]
        }

    monkeypatch.setattr(environment_service, "_request_json", fake_geocoding)
    response = client.get(
        "/api/v1/locations/search",
        params={"q": "Coast"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    result = response.json()[0]
    assert len(result["name"].encode("utf-8")) <= 79
    assert not result["name"].endswith("�")
    assert result["display_location"] == "LONG COASTAL CITY ENGLAND GB"


def test_device_can_select_global_location_by_server_resolved_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[str, dict]] = []

    def fake_get(_client, url: str, params) -> dict:
        calls.append((url, dict(params)))
        return {
            "id": 2654710,
            "name": "Brighton",
            "admin1": "England",
            "country": "United Kingdom",
            "country_code": "GB",
            "latitude": 50.82838,
            "longitude": -0.13947,
        }

    monkeypatch.setattr(environment_service, "_request_json", fake_get)
    response = client.put(
        "/api/v1/device-location",
        json={"device_id": "COAST_01", "location_id": "geo_2654710"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert calls == [
        (
            environment_service.GEOCODING_GET_URL,
            {"id": 2654710, "language": "en"},
        )
    ]
    assert response.json() == {
        "device_id": "COAST_01",
        "id": "geo_2654710",
        "kind": "place",
        "name": "Brighton · England · United Kingdom",
        "display_location": "BRIGHTON ENGLAND GB",
        "lat": 50.82838,
        "lon": -0.13947,
    }
    stored = get_device_location("COAST_01")
    assert stored is not None
    assert stored["location"] == "Brighton · England · United Kingdom"
    assert stored["display_location"] == "BRIGHTON ENGLAND GB"
    assert stored["latitude"] == 50.82838
    assert stored["longitude"] == -0.13947

    untrusted_coordinates = client.put(
        "/api/v1/device-location",
        json={
            "device_id": "COAST_01",
            "location_id": "geo_2654710",
            "lat": 0,
            "lon": 0,
        },
        headers=AUTH_HEADERS,
    )
    assert untrusted_coordinates.status_code == 422


def test_device_location_provider_failures_are_reported_as_bad_gateway(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def provider_offline(*_args, **_kwargs):
        raise RuntimeError("simulated geocoding outage")

    monkeypatch.setattr(environment_service, "_request_json", provider_offline)
    search = client.get(
        "/api/v1/locations/search",
        params={"q": "Brighton"},
        headers=AUTH_HEADERS,
    )
    selection = client.put(
        "/api/v1/device-location",
        json={"device_id": "COAST_01", "location_id": "geo_2654710"},
        headers=AUTH_HEADERS,
    )
    assert search.status_code == 502
    assert selection.status_code == 502


def test_environment_query_is_resolved_for_requested_device(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    requested_devices: list[str | None] = []

    def fake_environment(device_id: str | None = None):
        requested_devices.append(device_id)
        return environment_service._demo_environment()

    monkeypatch.setattr(gateway_service, "load_environment", fake_environment)
    response = client.get(
        "/api/v1/environment",
        params={"device_id": "COAST_77"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert requested_devices == ["COAST_77"]


def test_telemetry_requires_token_and_persists_with_correct_token(
    client: TestClient,
):
    payload = sample_payload()
    assert client.post("/api/v1/telemetry", json=payload).status_code == 401
    assert (
        client.post(
            "/api/v1/telemetry",
            json=payload,
            headers={"X-Device-Token": "wrong-token"},
        ).status_code
        == 401
    )

    response = client.post(
        "/api/v1/telemetry", json=payload, headers=AUTH_HEADERS
    )
    assert response.status_code == 201
    assert response.json()["device_id"] == "COAST_01"
    assert response.json()["seq"] == 7


def test_risk_endpoint_requires_telemetry_and_returns_rule_or_model_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    path = "/api/v1/risk?device_id=COAST_01"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=AUTH_HEADERS).status_code == 404
    monkeypatch.setattr(
        gateway_service,
        "load_environment",
        lambda _device_id=None: environment_service._demo_environment(),
    )
    assert client.post(
        "/api/v1/telemetry", json=sample_payload(), headers=AUTH_HEADERS
    ).status_code == 201
    response = client.get(path, headers=AUTH_HEADERS)
    assert response.status_code == 200
    payload = response.json()
    assert payload["device_id"] == "COAST_01"
    assert payload["risk_level"] == 3
    assert payload["local_alarm_level"] == 3
    assert payload["data_quality"] == "stale"
    assert payload["degraded"] is True


def test_public_gateway_does_not_expose_dashboard_docs_or_queries(
    client: TestClient,
):
    hidden_routes = (
        "/",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/v1/telemetry",
        "/api/v1/telemetry/latest",
        "/api/v1/simulations/labels",
        "/api/v1/simulations/train",
        "/api/v1/simulations/sessions/sim_missing/samples",
        "/api/v1/simulations/sessions/sim_missing/labels",
    )
    for path in hidden_routes:
        response = client.get(path, headers=AUTH_HEADERS)
        assert response.status_code == 404, path
    assert (
        client.delete(
            "/api/v1/simulations/sessions/sim_missing_delete",
            params={"device_id": "COAST_01"},
            headers=AUTH_HEADERS,
        ).status_code
        == 404
    )

    route_paths = {route.path for route in app.routes}
    assert route_paths == {
        "/api/v1/telemetry",
        "/api/v1/environment",
        "/api/v1/risk",
        "/api/v1/locations/presets",
        "/api/v1/locations/search",
        "/api/v1/device-location",
        "/api/v1/models",
        "/api/v1/device-model",
        "/api/v1/simulations/sessions",
        "/api/v1/simulations/sessions/active",
        "/api/v1/simulations/sessions/{session_id}/stop",
        "/api/v1/health",
        "/admin",
        "/admin/login",
        "/admin/console",
        "/admin/api/auth/login",
        "/admin/api/auth/session",
        "/admin/api/auth/logout",
    }


def test_gateway_refuses_to_start_without_configured_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("COASTAL_DB_PATH", str(tmp_path / "unused.db"))
    monkeypatch.delenv("COAST_DEVICE_TOKEN", raising=False)
    with (
        pytest.raises(RuntimeError, match="COAST_DEVICE_TOKEN must be set"),
        TestClient(app),
    ):
        pass


def test_gateway_refuses_blank_configured_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("COASTAL_DB_PATH", str(tmp_path / "unused.db"))
    monkeypatch.setenv("COAST_DEVICE_TOKEN", "   ")
    with (
        pytest.raises(RuntimeError, match="COAST_DEVICE_TOKEN must be set"),
        TestClient(app),
    ):
        pass
