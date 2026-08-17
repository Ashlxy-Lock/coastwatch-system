from __future__ import annotations

import json

import numpy as np

from coastwatch_impact.models import (
    LogisticEventBaseline,
    PersistenceWaterBaseline,
    PhysicsBaseline,
    build_logistic_summary_features,
)


def test_logistic_event_baseline_fit_predict_and_safe_round_trip(tmp_path) -> None:
    rng = np.random.default_rng(42)
    features = rng.normal(size=(120, 5))
    target = (features[:, 0] - 0.5 * features[:, 1] > 0).astype(float)
    baseline = LogisticEventBaseline(max_iter=200).fit(features, target)

    probabilities = baseline.predict_proba(features)
    assert probabilities.shape == (120,)
    assert np.isfinite(probabilities).all()
    assert ((probabilities >= 0.0) & (probabilities <= 1.0)).all()

    json_path = tmp_path / "logistic.json"
    npz_path = tmp_path / "logistic.npz"
    baseline.save_json(json_path)
    baseline.save_npz(npz_path)
    # State is plain JSON and NPZ is explicitly loadable without object arrays.
    raw_json = json_path.read_text(encoding="utf-8")
    assert "NaN" not in raw_json
    json.loads(raw_json)
    with np.load(npz_path, allow_pickle=False) as archive:
        assert "coef" in archive.files

    from_json = LogisticEventBaseline.load_json(json_path)
    from_npz = LogisticEventBaseline.load_npz(npz_path)
    np.testing.assert_allclose(from_json.predict_proba(features), probabilities)
    np.testing.assert_allclose(from_npz.predict_proba(features), probabilities)


def test_logistic_summary_features_include_history_and_forecast() -> None:
    past = np.arange(2 * 4 * 3, dtype=float).reshape(2, 4, 3)
    future = np.arange(2 * 2 * 2, dtype=float).reshape(2, 2, 2)
    past[0, 1, 0] = np.nan
    summary = build_logistic_summary_features(past, future)
    assert summary.shape == (2, 6 * 3 + 3 * 2)
    assert np.isfinite(summary).all()


def test_persistence_water_baseline_uses_last_available_value(tmp_path) -> None:
    history = np.array([[0.1, 0.3, np.nan], [1.0, 1.2, 1.4]])
    baseline = PersistenceWaterBaseline(forecast_hours=4)
    prediction = baseline.predict_water(history)
    np.testing.assert_allclose(prediction[0], 0.3)
    np.testing.assert_allclose(prediction[1], 1.4)
    assert baseline.predict_quantiles(history).shape == (2, 4, 3)

    path = tmp_path / "persistence.json"
    baseline.save_json(path)
    restored = PersistenceWaterBaseline.load_json(path)
    np.testing.assert_allclose(restored.predict_water(history), prediction)


def test_physics_baseline_smoke_and_json_round_trip(tmp_path) -> None:
    rng = np.random.default_rng(8)
    forecast = rng.normal(1.0, 0.4, size=(80, 6))
    hazard = (forecast > 1.2).astype(float)
    observed = forecast + rng.normal(0.1, 0.1, size=forecast.shape)
    baseline = PhysicsBaseline(forecast_hours=6)
    baseline.fit_event_mapping(forecast, hazard)
    baseline.fit_water_offsets(forecast, observed)

    output = baseline.predict(forecast[:5])
    assert output["hazard_logits"].shape == (5, 6)
    assert output["cumulative_event_probability"].shape == (5, 6)
    assert output["water_quantiles"].shape == (5, 6, 3)
    assert np.isfinite(output["cumulative_event_probability"]).all()
    assert np.all(np.diff(output["cumulative_event_probability"], axis=1) >= -1e-12)
    assert np.all(output["water_quantiles"][..., 0] <= output["water_quantiles"][..., 1])
    assert np.all(output["water_quantiles"][..., 1] <= output["water_quantiles"][..., 2])

    path = tmp_path / "physics.json"
    baseline.save_json(path)
    restored = PhysicsBaseline.load_json(path)
    restored_output = restored.predict(forecast[:5])
    for key in output:
        np.testing.assert_allclose(restored_output[key], output[key])
