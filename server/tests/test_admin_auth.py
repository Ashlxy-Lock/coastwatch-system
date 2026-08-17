from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth import (
    ADMIN_SESSION_COOKIE,
    SESSION_MAX_AGE_SECONDS,
    _LoginFailureLimiter,
    decode_admin_session,
    encode_admin_password_hash,
    issue_admin_session,
    load_admin_auth_config,
    login_client_key,
    verify_admin_credentials,
)
from app.gateway import MAX_ADMIN_LOGIN_BODY_BYTES, app

DEVICE_TOKEN = "admin-boundary-device-token"
ADMIN_PASSWORD = "admin-boundary-test-password"
ADMIN_PASSWORD_HASH = encode_admin_password_hash(
    ADMIN_PASSWORD, salt=b"admin-auth-tests"
)
ADMIN_SESSION_SECRET = "admin-auth-session-secret-value-0001"
DEVICE_HEADERS = {"X-Device-Token": DEVICE_TOKEN}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COASTAL_DB_PATH", str(tmp_path / "admin-auth.db"))
    monkeypatch.setenv("COAST_CUSTOM_MODEL_PATH", str(tmp_path / "custom-model.json"))
    monkeypatch.setenv(
        "COAST_OFFICIAL_DATASET_ROOT", str(tmp_path / "official-datasets")
    )
    monkeypatch.setenv(
        "COAST_OFFICIAL_REGISTRY_DIR", str(tmp_path / "official-registry")
    )
    monkeypatch.setenv(
        "COAST_OFFICIAL_ARTIFACT_DIR", str(tmp_path / "official-artifacts")
    )
    monkeypatch.setenv("COAST_DEVICE_TOKEN", DEVICE_TOKEN)
    monkeypatch.setenv("COAST_ADMIN_PASSWORD_HASH", ADMIN_PASSWORD_HASH)
    monkeypatch.setenv("COAST_ADMIN_SESSION_SECRET", ADMIN_SESSION_SECRET)
    monkeypatch.delenv("COAST_ADMIN_PASSWORD_HASH_FILE", raising=False)
    monkeypatch.delenv("COAST_ADMIN_SESSION_SECRET_FILE", raising=False)
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


def login(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"], response.cookies[ADMIN_SESSION_COOKIE]


def assert_admin_security_headers(response) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    policy = response.headers["content-security-policy"]
    assert "default-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy


def test_admin_login_page_and_backend_require_a_session(client: TestClient) -> None:
    entry = client.get("/admin", follow_redirects=False)
    assert entry.status_code == 303
    assert entry.headers["location"] == "/admin/login"
    assert client.get("/admin/login").status_code == 200

    console = client.get("/admin/console", follow_redirects=False)
    assert console.status_code == 303
    assert console.headers["location"] == "/admin/login"
    assert client.get("/admin/api/auth/session").status_code == 401
    assert client.get("/admin/api/v1/health").status_code == 401


def test_every_admin_response_has_no_store_and_browser_security_headers(
    client: TestClient,
) -> None:
    public_page = client.get("/admin/login")
    unauthorized_api = client.get("/admin/api/v1/health")
    login_response = client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    csrf_token = login_response.json()["csrf_token"]
    authenticated_responses = (
        client.get("/admin/api/auth/session"),
        client.get("/admin/console"),
        client.get("/admin/api/v1/health"),
        client.post("/admin/api/auth/logout", headers={"X-CSRF-Token": csrf_token}),
    )
    for response in (public_page, unauthorized_api, login_response):
        assert_admin_security_headers(response)
    for response in authenticated_responses:
        assert response.status_code == 200
        assert_admin_security_headers(response)


def test_login_cookie_console_prefix_and_mounted_backend_state(
    client: TestClient,
) -> None:
    login_response = client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert login_response.status_code == 200
    csrf_token = login_response.json()["csrf_token"]
    set_cookie = login_response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/admin" in set_cookie

    session = client.get("/admin/api/auth/session")
    assert session.status_code == 200
    assert session.json()["username"] == "admin"
    assert session.json()["csrf_token"]

    console = client.get("/admin/console")
    assert console.status_code == 200
    assert "海岸安全预警监控" in console.text
    assert "const ADMIN_MODE=true" in console.text
    assert 'const ADMIN_BASE="/admin"' in console.text
    assert "/admin/api/v1/simulations/train" in console.text
    assert "X-CSRF-Token" in console.text

    telemetry = client.post(
        "/admin/api/v1/telemetry",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "device_id": "COAST_01",
            "seq": 1,
            "uptime_ms": 1000,
            "distance_mm": 500,
            "water_rise_mm": 10,
            "rise_rate_mm_s": 2,
            "person_detected": False,
            "alarm_level": 0,
            "health_flags": 9,
            "wifi_rssi": -60,
        },
    )
    assert telemetry.status_code == 201
    assert client.get("/admin/api/v1/risk?device_id=COAST_01").status_code == 200


def test_all_admin_writes_require_matching_csrf(client: TestClient) -> None:
    scenario_path = "/admin/api/v1/simulations/device-scenario"
    assert client.put(scenario_path, json={}).status_code == 401
    assert client.delete(
        "/admin/api/v1/simulations/sessions/sim_admin_missing",
        params={"device_id": "COAST_01"},
    ).status_code == 401

    csrf_token, _session_token = login(client)
    path = "/admin/api/v1/device-model"
    payload = {"device_id": "COAST_01", "model_id": "coastal-risk-logreg-v1"}

    assert client.put(path, json=payload).status_code == 403
    assert (
        client.put(path, headers={"X-CSRF-Token": "wrong"}, json=payload).status_code
        == 403
    )
    accepted = client.put(path, headers={"X-CSRF-Token": csrf_token}, json=payload)
    assert accepted.status_code == 200

    scenario_payload = {
        "device_id": "COAST_01",
        "scenario_name": "Admin-only fictitious coast",
        "simulated_at": "2026-08-14T09:00:00Z",
        "sim_air_temperature_c": 12.0,
        "sim_humidity_percent": 70.0,
        "sim_wind_speed_kmh": 20.0,
        "sim_wave_height_m": 1.2,
        "sim_wave_period_s": 6.0,
        "sim_water_temperature_c": 14.0,
        "sim_sea_level_height_m": 0.2,
        "sim_ocean_current_velocity_kmh": 1.0,
        "sim_latitude": 50.8,
        "sim_longitude": -1.1,
        "note": "not a real coast",
    }
    assert client.put(scenario_path, json=scenario_payload).status_code == 403
    assert (
        client.put(
            scenario_path,
            headers={"X-CSRF-Token": "wrong"},
            json=scenario_payload,
        ).status_code
        == 403
    )
    accepted_scenario = client.put(
        scenario_path,
        headers={"X-CSRF-Token": csrf_token},
        json=scenario_payload,
    )
    assert accepted_scenario.status_code == 200
    assert accepted_scenario.json()["data_kind"] == "operator_supplied_simulation"

    session_id = "sim_admin_delete_guard"
    started = client.post(
        "/admin/api/v1/simulations/sessions",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "device_id": "COAST_01",
            "name": "Admin delete CSRF test",
            "session_id": session_id,
        },
    )
    assert started.status_code == 201
    stopped = client.post(
        f"/admin/api/v1/simulations/sessions/{session_id}/stop",
        headers={"X-CSRF-Token": csrf_token},
        json={"device_id": "COAST_01"},
    )
    assert stopped.status_code == 200
    delete_path = f"/admin/api/v1/simulations/sessions/{session_id}"
    assert client.delete(
        delete_path,
        params={"device_id": "COAST_01"},
    ).status_code == 403
    assert client.delete(
        delete_path,
        params={"device_id": "COAST_01"},
        headers={"X-CSRF-Token": "wrong"},
    ).status_code == 403
    deleted = client.delete(
        delete_path,
        params={"device_id": "COAST_01"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted_counts"] == {
        "sessions": 1,
        "samples": 0,
        "labels": 0,
        "scenario_snapshots": 1,
    }


def test_official_training_console_is_admin_only_and_all_writes_require_csrf(
    client: TestClient,
) -> None:
    assert client.get("/admin/api/v1/official-datasets").status_code == 401
    assert client.post("/admin/api/v1/official-datasets/rescan").status_code == 401

    csrf_token, _session_token = login(client)
    assert client.get("/admin/api/v1/official-datasets").status_code == 200
    assert client.post("/admin/api/v1/official-datasets/rescan").status_code == 403
    rescanned = client.post(
        "/admin/api/v1/official-datasets/rescan",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert rescanned.status_code == 200
    assert rescanned.json()["registered_count"] == 0

    # The public device gateway deliberately has no dataset/training routes.
    assert (
        client.get("/api/v1/official-datasets", headers=DEVICE_HEADERS).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/official-datasets/rescan", headers=DEVICE_HEADERS
        ).status_code
        == 404
    )


def test_device_and_admin_credentials_never_cross_authorize(
    client: TestClient,
) -> None:
    csrf_token, admin_token = login(client)

    # The admin cookie does not authorize the root device API.
    assert client.get("/api/v1/health").status_code == 401
    assert (
        client.get(
            "/api/v1/health", headers={"X-Device-Token": admin_token}
        ).status_code
        == 401
    )
    assert client.get("/api/v1/health", headers=DEVICE_HEADERS).status_code == 200

    # Conversely, a device token alone cannot enter the admin backend.
    client.cookies.clear()
    assert client.get("/admin/api/v1/health", headers=DEVICE_HEADERS).status_code == 401
    assert csrf_token


def test_logout_requires_csrf_and_revokes_browser_cookie(client: TestClient) -> None:
    csrf_token, _session_token = login(client)
    assert client.post("/admin/api/auth/logout").status_code == 403
    response = client.post(
        "/admin/api/auth/logout", headers={"X-CSRF-Token": csrf_token}
    )
    assert response.status_code == 200
    assert response.json() == {"authenticated": False}
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/admin/api/auth/session").status_code == 401


def test_bad_logins_are_rate_limited_without_account_disclosure(
    client: TestClient,
) -> None:
    for index in range(5):
        response = client.post(
            "/admin/api/auth/login",
            json={"username": f"unknown-{index}", "password": "incorrect"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid administrator credentials"

    blocked = client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


def test_login_failure_limiter_is_ttl_bounded_and_lru_evicted() -> None:
    limiter = _LoginFailureLimiter(limit=1, window_seconds=10, max_keys=3)
    for key in ("a", "b", "c"):
        limiter.record_failure(key, now=1.0)
    assert limiter.entry_count == 3

    # Touch a so b becomes the least-recently-used key.
    assert limiter.retry_after("a", now=2.0) is not None
    limiter.record_failure("d", now=2.0)
    assert limiter.entry_count == 3
    assert limiter.retry_after("b", now=2.0) is None
    assert limiter.retry_after("a", now=2.0) is not None

    # A later access performs TTL cleanup across all retained keys.
    assert limiter.retry_after("missing", now=12.0) is None
    assert limiter.entry_count == 0


def test_login_body_limit_rejects_declared_streamed_and_actual_oversize(
    client: TestClient,
) -> None:
    path = "/admin/api/auth/login"
    declared_oversize = client.post(
        path,
        content=b"x" * (MAX_ADMIN_LOGIN_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert declared_oversize.status_code == 413
    assert_admin_security_headers(declared_oversize)

    # Chunked forwarding has no Content-Length, so the actual stream counter is
    # the authoritative limit.
    streamed_oversize = client.post(
        path,
        content=(
            chunk
            for chunk in (
                b"x" * (MAX_ADMIN_LOGIN_BODY_BYTES // 2),
                b"x" * (MAX_ADMIN_LOGIN_BODY_BYTES // 2 + 1),
            )
        ),
        headers={"Content-Type": "application/json"},
    )
    assert "content-length" not in streamed_oversize.request.headers
    assert streamed_oversize.status_code == 413

    # A lying small Content-Length cannot bypass the actual byte counter.
    actual_oversize = client.post(
        path,
        content=b"x" * (MAX_ADMIN_LOGIN_BODY_BYTES + 1),
        headers={"Content-Type": "application/json", "Content-Length": "2"},
    )
    assert actual_oversize.status_code == 413


def test_login_body_limit_allows_small_streamed_json_with_no_content_length(
    client: TestClient,
) -> None:
    path = "/admin/api/auth/login"
    wrong_password = client.post(
        path,
        content=(chunk for chunk in (b'{"username":"admin",', b'"password":"wrong"}')),
        headers={"Content-Type": "application/json"},
    )
    assert "content-length" not in wrong_password.request.headers
    assert wrong_password.status_code == 401

    valid_body = f'{{"username":"admin","password":"{ADMIN_PASSWORD}"}}'.encode()
    accepted = client.post(
        path,
        content=(chunk for chunk in (valid_body[:17], valid_body[17:])),
        headers={"Content-Type": "application/json"},
    )
    assert "content-length" not in accepted.request.headers
    assert accepted.status_code == 200


def test_login_body_limit_rejects_invalid_length_but_keeps_normal_json(
    client: TestClient,
) -> None:
    invalid = client.post(
        "/admin/api/auth/login",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "invalid"},
    )
    assert invalid.status_code == 400

    normal = client.post(
        "/admin/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert normal.status_code == 200
    assert normal.json()["authenticated"] is True


def test_rate_limit_uses_cloudflare_ip_only_from_loopback_peer() -> None:
    def request(peer: str, connecting_ip: str) -> Request:
        return Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/admin/api/auth/login",
                "raw_path": b"/admin/api/auth/login",
                "query_string": b"",
                "headers": [
                    (b"host", b"example.test"),
                    (b"cf-connecting-ip", connecting_ip.encode("ascii")),
                    (b"x-forwarded-for", b"192.0.2.200"),
                ],
                "client": (peer, 12345),
                "server": ("127.0.0.1", 8001),
            }
        )

    assert login_client_key(request("127.0.0.1", "203.0.113.10")) == "ip:203.0.113.10"
    assert login_client_key(request("198.51.100.4", "203.0.113.10")) == (
        "peer:198.51.100.4"
    )
    assert login_client_key(request("127.0.0.1", "not-an-ip")) == "peer:127.0.0.1"


def test_admin_credentials_can_be_loaded_only_from_acl_file_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password_file = tmp_path / "password-hash.txt"
    secret_file = tmp_path / "session-secret.txt"
    password_file.write_text(ADMIN_PASSWORD_HASH, encoding="utf-8")
    secret_file.write_text(ADMIN_SESSION_SECRET, encoding="utf-8")
    monkeypatch.delenv("COAST_ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("COAST_ADMIN_SESSION_SECRET", raising=False)
    monkeypatch.setenv("COAST_ADMIN_PASSWORD_HASH_FILE", str(password_file))
    monkeypatch.setenv("COAST_ADMIN_SESSION_SECRET_FILE", str(secret_file))

    config = load_admin_auth_config()
    assert verify_admin_credentials(config, "admin", ADMIN_PASSWORD)
    assert not verify_admin_credentials(config, "admin", "incorrect")
    assert not verify_admin_credentials(config, "someone-else", ADMIN_PASSWORD)


def test_session_cookie_is_signed_and_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COAST_ADMIN_PASSWORD_HASH", ADMIN_PASSWORD_HASH)
    monkeypatch.setenv("COAST_ADMIN_SESSION_SECRET", ADMIN_SESSION_SECRET)
    monkeypatch.delenv("COAST_ADMIN_PASSWORD_HASH_FILE", raising=False)
    monkeypatch.delenv("COAST_ADMIN_SESSION_SECRET_FILE", raising=False)
    config = load_admin_auth_config()
    token, session = issue_admin_session(config, now=1_000)

    assert decode_admin_session(config, token, now=1_001) == session
    payload, signature = token.split(".", 1)
    replacement = "A" if signature[0] != "A" else "B"
    tampered = f"{payload}.{replacement}{signature[1:]}"
    assert decode_admin_session(config, tampered, now=1_001) is None
    assert (
        decode_admin_session(config, token, now=1_000 + SESSION_MAX_AGE_SECONDS) is None
    )


def test_gateway_fails_closed_without_admin_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COASTAL_DB_PATH", str(tmp_path / "missing-admin.db"))
    monkeypatch.setenv("COAST_DEVICE_TOKEN", DEVICE_TOKEN)
    for variable in (
        "COAST_ADMIN_PASSWORD_HASH",
        "COAST_ADMIN_PASSWORD_HASH_FILE",
        "COAST_ADMIN_SESSION_SECRET",
        "COAST_ADMIN_SESSION_SECRET_FILE",
    ):
        monkeypatch.delenv(variable, raising=False)
    with (
        pytest.raises(RuntimeError, match="COAST_ADMIN_PASSWORD_HASH"),
        TestClient(app, base_url="https://testserver"),
    ):
        pass
