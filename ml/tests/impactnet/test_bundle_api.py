from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from coastwatch_impact.export.model_bundle import (
    BundleIntegrityError,
    create_model_bundle,
    load_model_bundle,
    verify_model_bundle,
)
from coastwatch_impact.models.impactnet import ImpactNet, ImpactNetConfig
from coastwatch_impact.serve.app import create_app
from coastwatch_impact.serve.model_loader import BundlePredictor


def _bundle(tmp_path):
    model = ImpactNet(
        ImpactNetConfig(
            past_feature_dim=2,
            forecast_feature_dim=0,
            static_feature_dim=1,
            time_feature_dim=0,
            variant="obs_only_tcn",
            history_hours=4,
            forecast_hours=3,
            hidden_channels=4,
            num_blocks=1,
            dilations=(1,),
            kernel_size=2,
            decoder_hidden_dim=6,
            decoder_layers=1,
            lead_embedding_dim=2,
            dropout=0.0,
            water_target_mode="absolute",
        )
    )
    return create_model_bundle(
        tmp_path / "bundle",
        model,
        model_version="synthetic-test-v1",
        model_name="CoastWatch Synthetic-Test TCN",
        label_mode="confirmed_impact",
        coverage_scope="Synthetic engineering fixture only",
        horizons_hours=(1, 3),
        calibration={
            "method": "global_temperature",
            "temperature": 1.0,
            "fitted_split": "validation",
            "calibrated": True,
        },
        thresholds={
            "fitted_split": "validation",
            "research_bands": {"advisory": 0.2, "warning": 0.5, "critical": 0.8},
        },
        feature_schema={"max_missing_fraction_past": 0.25},
        sites=[{"site_id": "synthetic_01"}],
        synthetic_data=True,
    )


def _variant_bundle(
    tmp_path,
    name: str,
    variant: str,
    *,
    feature_manifest_hash: str | None = None,
):
    is_hybrid = variant == "hybrid_tcn"
    model = ImpactNet(
        ImpactNetConfig(
            past_feature_dim=2,
            forecast_feature_dim=1 if is_hybrid else 0,
            static_feature_dim=1,
            time_feature_dim=0,
            variant=variant,
            history_hours=4,
            forecast_hours=3,
            hidden_channels=4,
            num_blocks=1,
            dilations=(1,),
            kernel_size=2,
            decoder_hidden_dim=6,
            decoder_layers=1,
            lead_embedding_dim=2,
            dropout=0.0,
            water_target_mode="absolute",
        )
    )
    return create_model_bundle(
        tmp_path / name,
        model,
        model_version=name,
        model_name="CoastWatch Synthetic-Test TCN",
        label_mode="confirmed_impact",
        coverage_scope="Synthetic engineering fixture only",
        horizons_hours=(1, 3),
        thresholds={"fitted_split": "validation"},
        feature_schema={
            "future_feature_names": (["future"] if is_hybrid else []),
            "future_feature_sources": ({"future": "weather"} if is_hybrid else {}),
            "max_missing_fraction_past": 0.25,
            "max_missing_fraction_future": 0.25,
            "max_source_age_hours": {"weather": 12},
            **(
                {"feature_manifest_hash": feature_manifest_hash}
                if feature_manifest_hash is not None
                else {}
            ),
        },
        sites=[{"site_id": "synthetic_01"}],
        synthetic_data=True,
    )


def _request():
    return {
        "site_id": "synthetic_01",
        "prediction_time_utc": "2026-08-13T12:00:00Z",
        "past_values": [[1.0, 2.0], [1.1, 2.1], [1.2, 2.2], [1.3, 2.3]],
        "past_mask": [[True, True]] * 4,
        "static_values": [1.0],
        "source_issue_times": {"weather": "2026-08-13T06:00:00Z"},
    }


def _hybrid_request():
    payload = _request()
    payload["future_values"] = [[0.1], [0.2], [0.3]]
    payload["future_mask"] = [[True], [True], [True]]
    payload["issued_forecast_provenance"] = [
        {
            "source_model": "weather",
            "model_run_id": "weather-20260813-06z",
            "issue_time_utc": "2026-08-13T06:00:00Z",
            "valid_time_utc": f"2026-08-13T{hour:02d}:00:00Z",
        }
        for hour in (13, 14, 15)
    ]
    return payload


def test_bundle_roundtrip_and_synthetic_marker(tmp_path):
    path = _bundle(tmp_path)
    loaded = load_model_bundle(path)
    assert loaded.manifest["shadow_mode"] is True
    assert loaded.manifest["synthetic_data"] is True
    assert loaded.manifest["deployable_as_real"] is False
    assert loaded.architecture.history_hours == 4


def test_even_non_synthetic_bundle_does_not_self_approve_real_deployment(tmp_path):
    bundle = create_model_bundle(
        tmp_path / "research-real-bundle",
        ImpactNet(
            ImpactNetConfig(
                past_feature_dim=2,
                forecast_feature_dim=0,
                static_feature_dim=1,
                time_feature_dim=0,
                variant="obs_only_tcn",
                history_hours=4,
                forecast_hours=3,
                hidden_channels=4,
                num_blocks=1,
                kernel_size=2,
                dilations=(1,),
                static_hidden_dim=4,
                static_context_dim=2,
                decoder_hidden_dim=4,
                decoder_layers=1,
                lead_embedding_dim=2,
                water_target_mode="absolute",
            )
        ),
        model_version="research-real-v1",
        model_name="CoastWatch ImpactNet",
        label_mode="confirmed_impact",
        coverage_scope="Reviewed research scope only",
        horizons_hours=[1, 3],
        synthetic_data=False,
    )
    manifest = verify_model_bundle(bundle)
    assert manifest["synthetic_data"] is False
    assert manifest["deployable_as_real"] is False


def test_corrupted_bundle_is_rejected_before_load(tmp_path):
    path = _bundle(tmp_path)
    architecture = json.loads((path / "architecture.json").read_text("utf-8"))
    architecture["history_hours"] = 999
    (path / "architecture.json").write_text(json.dumps(architecture), encoding="utf-8")
    with pytest.raises(BundleIntegrityError, match="hash mismatch"):
        load_model_bundle(path)


def test_api_success_is_shadow_only(tmp_path):
    predictor = BundlePredictor(load_model_bundle(_bundle(tmp_path)))
    client = TestClient(create_app(predictor))
    response = client.post("/v1/predict/features", json=_request())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["shadow_mode"] is True
    assert payload["synthetic_data"] is True
    assert list(payload["event_probability"]) == ["1h", "3h"]
    assert (
        payload["water_level_quantiles_m_aod"][0]["p10"]
        <= payload["water_level_quantiles_m_aod"][0]["p50"]
    )


def test_successful_inference_writes_complete_audit_log(tmp_path, caplog):
    predictor = BundlePredictor(load_model_bundle(_bundle(tmp_path)))
    client = TestClient(create_app(predictor))

    with caplog.at_level(logging.INFO, logger="coastwatch_impact.serve"):
        response = client.post(
            "/v1/predict/features",
            json=_request(),
            headers={"x-request-id": "audit-request-01"},
        )

    assert response.status_code == 200, response.text
    records = [record for record in caplog.records if record.getMessage() == "shadow_prediction"]
    assert len(records) == 1
    record = records[0]
    assert record.request_id == "audit-request-01"
    assert record.model_version == "synthetic-test-v1"
    assert record.model_variant == "obs_only_tcn"
    assert record.site_id == "synthetic_01"
    assert record.feature_manifest_hash is None
    assert record.source_issue_times == {"weather": "2026-08-13T06:00:00+00:00"}
    assert record.issued_forecast_provenance == []
    assert record.data_quality["status"] == "normal"
    assert len(record.raw_logits) == 3
    assert set(record.calibrated_probabilities) == {"1h", "3h"}
    assert record.calibrated is True
    assert record.latency_ms >= 0.0
    assert record.shadow_mode is True


def test_out_of_domain_site_has_explicit_quality_status(tmp_path):
    predictor = BundlePredictor(load_model_bundle(_bundle(tmp_path)))
    payload = _request()
    payload["site_id"] = "unknown-site"
    response = TestClient(create_app(predictor)).post("/v1/predict/features", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["data_quality"] == {
        "status": "out_of_domain",
        "missing_fraction": 0.0,
        "stale_sources": [],
        "out_of_domain": True,
    }


def test_api_rejects_future_issue_time(tmp_path):
    predictor = BundlePredictor(load_model_bundle(_bundle(tmp_path)))
    client = TestClient(create_app(predictor))
    payload = _request()
    payload["source_issue_times"] = {"weather": "2026-08-13T13:00:00Z"}
    response = client.post("/v1/predict/features", json=payload)
    assert response.status_code == 422
    assert response.json()["shadow_mode"] is True


def test_insufficient_response_has_no_probability(tmp_path):
    predictor = BundlePredictor(load_model_bundle(_bundle(tmp_path)))
    client = TestClient(create_app(predictor))
    payload = _request()
    payload["past_mask"] = [[False, False]] * 4
    response = client.post("/v1/predict/features", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "insufficient_data"
    assert body["event_probability"] is None
    assert body["shadow_mode"] is True


def test_health_without_bundle_remains_shadow_only():
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "not_ready",
        "shadow_mode": True,
        "loaded_models": [],
    }
    not_ready = client.get("/v1/model-info")
    assert not_ready.status_code == 503
    assert not_ready.json()["shadow_mode"] is True


def test_degraded_routing_covers_hybrid_obs_only_and_physics(tmp_path):
    hybrid = load_model_bundle(_variant_bundle(tmp_path, "hybrid", "hybrid_tcn"))
    obs_only = load_model_bundle(_variant_bundle(tmp_path, "obs", "obs_only_tcn"))
    client = TestClient(create_app(BundlePredictor(hybrid, obs_only=obs_only)))

    normal = client.post("/v1/predict/features", json=_hybrid_request())
    assert normal.status_code == 200, normal.text
    assert normal.json()["model_variant"] == "hybrid_tcn"
    assert normal.json()["data_quality"]["status"] == "normal"
    assert (
        normal.json()["issued_forecast_provenance"]
        == _hybrid_request()["issued_forecast_provenance"]
    )

    degraded = client.post("/v1/predict/features", json=_request())
    assert degraded.status_code == 200, degraded.text
    assert degraded.json()["model_variant"] == "obs_only_tcn"
    assert degraded.json()["data_quality"]["status"] == "degraded_obs_only"
    assert degraded.json()["shadow_mode"] is True

    def physics_fallback(_request_payload):
        return {
            "cumulative_event_probability": [index / 48 for index in range(1, 25)],
            "water_quantiles": [[1.0, 1.2, 1.5] for _ in range(24)],
            "calibrated": False,
        }

    physics_client = TestClient(
        create_app(BundlePredictor(hybrid, physics_fallback=physics_fallback))
    )
    physics = physics_client.post("/v1/predict/features", json=_request())
    assert physics.status_code == 200, physics.text
    assert physics.json()["model_variant"] == "physics_baseline"
    assert physics.json()["data_quality"]["status"] == "degraded_physics_only"
    assert physics.json()["calibrated"] is False
    assert physics.json()["shadow_mode"] is True

    insufficient = TestClient(create_app(BundlePredictor(hybrid))).post(
        "/v1/predict/features", json=_request()
    )
    assert insufficient.status_code == 422
    assert insufficient.json()["event_probability"] is None
    assert insufficient.json()["shadow_mode"] is True


def test_stale_hybrid_forecast_routes_to_obs_only(tmp_path):
    hybrid = load_model_bundle(_variant_bundle(tmp_path, "hybrid", "hybrid_tcn"))
    obs_only = load_model_bundle(_variant_bundle(tmp_path, "obs", "obs_only_tcn"))
    payload = _hybrid_request()
    for row in payload["issued_forecast_provenance"]:
        row["issue_time_utc"] = "2026-08-12T12:00:00Z"
    response = TestClient(create_app(BundlePredictor(hybrid, obs_only=obs_only))).post(
        "/v1/predict/features", json=payload
    )
    assert response.status_code == 200, response.text
    assert response.json()["data_quality"]["status"] == "degraded_obs_only"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["issued_forecast_provenance"].__setitem__(
            0,
            {
                **payload["issued_forecast_provenance"][0],
                "issue_time_utc": "2026-08-13T12:00:01Z",
            },
        ),
        lambda payload: payload["issued_forecast_provenance"].pop(),
        lambda payload: payload["issued_forecast_provenance"][0].__setitem__(
            "valid_time_utc", "2026-08-13T16:00:00Z"
        ),
        lambda payload: payload["issued_forecast_provenance"].append(
            dict(payload["issued_forecast_provenance"][0])
        ),
    ],
    ids=("future-issued", "missing-lead", "unexpected-valid-time", "duplicate-source-lead"),
)
def test_hybrid_provenance_rejects_invalid_asof_or_lead_coverage(tmp_path, mutate):
    hybrid = load_model_bundle(_variant_bundle(tmp_path, "hybrid", "hybrid_tcn"))
    payload = _hybrid_request()
    mutate(payload)

    response = TestClient(create_app(BundlePredictor(hybrid))).post(
        "/v1/predict/features", json=payload
    )

    assert response.status_code == 422
    assert response.json()["status"] == "invalid_request"
    assert response.json()["shadow_mode"] is True


def test_hybrid_without_structured_provenance_degrades_to_obs_only(tmp_path):
    hybrid = load_model_bundle(_variant_bundle(tmp_path, "hybrid", "hybrid_tcn"))
    obs_only = load_model_bundle(_variant_bundle(tmp_path, "obs", "obs_only_tcn"))
    payload = _hybrid_request()
    payload.pop("issued_forecast_provenance")

    response = TestClient(create_app(BundlePredictor(hybrid, obs_only=obs_only))).post(
        "/v1/predict/features", json=payload
    )

    assert response.status_code == 200, response.text
    assert response.json()["model_variant"] == "obs_only_tcn"
    assert response.json()["data_quality"]["status"] == "degraded_obs_only"


def test_hybrid_provenance_must_match_feature_column_source_binding(tmp_path):
    hybrid = load_model_bundle(_variant_bundle(tmp_path, "hybrid", "hybrid_tcn"))
    payload = _hybrid_request()
    for row in payload["issued_forecast_provenance"]:
        row["source_model"] = "marine"
    response = TestClient(create_app(BundlePredictor(hybrid))).post(
        "/v1/predict/features", json=payload
    )
    assert response.status_code == 422
    assert "source 'weather'" in response.json()["reason"]
    assert response.json()["event_probability"] is None


def test_feature_manifest_hash_is_bound_to_bundle_not_caller_claim(tmp_path):
    expected_hash = "a" * 64
    hybrid = load_model_bundle(
        _variant_bundle(
            tmp_path,
            "hybrid-with-feature-hash",
            "hybrid_tcn",
            feature_manifest_hash=expected_hash,
        )
    )
    client = TestClient(create_app(BundlePredictor(hybrid)))

    matching = _hybrid_request()
    matching["feature_manifest_hash"] = expected_hash.upper()
    assert client.post("/v1/predict/features", json=matching).status_code == 200

    missing = _hybrid_request()
    missing_response = client.post("/v1/predict/features", json=missing)
    assert missing_response.status_code == 422
    assert "required by this model bundle" in missing_response.json()["reason"]

    mismatch = _hybrid_request()
    mismatch["feature_manifest_hash"] = "b" * 64
    mismatch_response = client.post("/v1/predict/features", json=mismatch)
    assert mismatch_response.status_code == 422
    assert "does not match" in mismatch_response.json()["reason"]

    unbound_client = TestClient(
        create_app(
            BundlePredictor(
                load_model_bundle(_variant_bundle(tmp_path, "hybrid-unbound", "hybrid_tcn"))
            )
        )
    )
    unbound = _hybrid_request()
    unbound["feature_manifest_hash"] = expected_hash
    unbound_response = unbound_client.post("/v1/predict/features", json=unbound)
    assert unbound_response.status_code == 422
    assert "bundle declares no expected hash" in unbound_response.json()["reason"]


def test_malformed_bundle_feature_hash_is_rejected_at_predictor_startup(tmp_path):
    malformed = load_model_bundle(
        _variant_bundle(
            tmp_path,
            "hybrid-malformed-feature-hash",
            "hybrid_tcn",
            feature_manifest_hash="caller-chooses-this",
        )
    )

    with pytest.raises(ValueError, match="must be a SHA-256 hex digest"):
        BundlePredictor(malformed)
