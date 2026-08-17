import numpy as np
import pytest

from coastwatch_impact.evaluation import (
    compute_horizon_metrics,
    cumulative_event_probability,
    cumulative_targets_from_hazards,
    fit_global_temperature,
)


def binary_nll(logits: np.ndarray, targets: np.ndarray) -> float:
    return float(np.mean(np.logaddexp(0.0, logits) - targets * logits))


def test_cumulative_hazard_is_stable_bounded_and_monotone() -> None:
    logits = np.array([[-1000.0, -1000.0, 1000.0, -1000.0], [1000.0, -1000.0, 1000.0, 0.0]])
    cumulative = cumulative_event_probability(logits)
    assert np.isfinite(cumulative).all()
    assert np.all((cumulative >= 0.0) & (cumulative <= 1.0))
    assert np.all(np.diff(cumulative, axis=1) >= 0.0)
    assert cumulative[0, 0] < 1e-100
    assert cumulative[0, 2] == pytest.approx(1.0)


def test_temperature_is_validation_only_and_never_worsens_fit_nll() -> None:
    logits = np.array([[6.0, -6.0], [-5.0, 5.0], [4.0, -4.0], [-3.0, 3.0]])
    targets = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
    with pytest.raises(ValueError, match="validation-only"):
        fit_global_temperature(logits, targets, split="test")

    scaler = fit_global_temperature(logits, targets, split="validation")
    assert scaler.fitted_split == "validation"
    assert scaler.temperature > 0.0
    assert (
        binary_nll(scaler.transform_logits(logits), targets) <= binary_nll(logits, targets) + 1e-10
    )


def test_unknown_hazard_hours_do_not_turn_into_negative_horizon_labels() -> None:
    hazards = np.array(
        [
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 0.0],
        ]
    )
    mask = np.array(
        [
            [True, True, False, False],
            [True, True, True, True],
            [True, False, True, True],
        ]
    )
    cumulative_target, cumulative_mask = cumulative_targets_from_hazards(hazards, mask)
    assert cumulative_target[0].tolist() == [0.0, 1.0, 1.0, 1.0]
    assert cumulative_mask[0].all()  # known onset resolves every longer horizon
    assert cumulative_mask[2].tolist() == [True, False, False, False]

    probabilities = np.array(
        [
            [0.05, 0.80, 0.90, 0.95],
            [0.02, 0.05, 0.10, 0.15],
            [0.10, 0.20, 0.30, 0.40],
        ]
    )
    metrics = compute_horizon_metrics(
        probabilities,
        hazards,
        mask,
        horizons=(1, 2, 4),
        threshold=0.5,
        ece_bins=5,
    )
    assert metrics["1h"]["samples"] == 3
    assert metrics["4h"]["samples"] == 2
    assert metrics["4h"]["positives"] == 1
    assert metrics["4h"]["precision"] == pytest.approx(1.0)
    assert metrics["4h"]["recall"] == pytest.approx(1.0)
    assert metrics["4h"]["pr_auc"] == pytest.approx(1.0)
    assert 0.0 <= metrics["4h"]["brier"] <= 1.0


def test_horizon_metrics_reject_non_monotone_probabilities() -> None:
    with pytest.raises(ValueError, match="must not decrease"):
        compute_horizon_metrics(
            np.array([[0.4, 0.3]]),
            np.array([[0.0, 0.0]]),
            horizons=(1, 2),
        )
