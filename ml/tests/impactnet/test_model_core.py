from __future__ import annotations

import pytest
import torch

from coastwatch_impact.models import (
    ImpactNet,
    ImpactNetConfig,
    MissingFutureForecastError,
    TCNEncoder,
    WaterQuantileHead,
    cumulative_event_probability,
)


def test_residual_tcn_is_strictly_causal() -> None:
    torch.manual_seed(7)
    encoder = TCNEncoder(
        3,
        8,
        dilations=(1, 2, 4),
        kernel_size=3,
        dropout=0.0,
        normalization="group_norm",
    ).eval()
    original = torch.randn(2, 20, 3)
    perturbed = original.clone()
    perturbed[:, 11:, :] = torch.randn_like(perturbed[:, 11:, :]) * 100.0

    with torch.no_grad():
        original_output = encoder(original)
        perturbed_output = encoder(perturbed)

    torch.testing.assert_close(
        original_output[..., :11],
        perturbed_output[..., :11],
        rtol=0.0,
        atol=1e-6,
    )


def test_cumulative_hazard_is_finite_bounded_and_monotonic() -> None:
    logits = torch.tensor([[-1e6, -1000.0, 0.0, 1000.0, 1e6], [1e6, -1e6, 1e6, -1e6, 0.0]])
    cumulative = cumulative_event_probability(logits)

    assert torch.isfinite(cumulative).all()
    assert ((cumulative >= 0.0) & (cumulative <= 1.0)).all()
    assert torch.all(cumulative[:, 1:] >= cumulative[:, :-1])


def test_water_head_never_crosses_quantiles() -> None:
    torch.manual_seed(11)
    head = WaterQuantileHead(5)
    hidden = torch.randn(4, 9, 5) * 100.0
    baseline = torch.randn(4, 9)
    quantiles = head(hidden, baseline=baseline)

    assert quantiles.shape == (4, 9, 3)
    assert torch.isfinite(quantiles).all()
    assert torch.all(quantiles[..., 0] <= quantiles[..., 1])
    assert torch.all(quantiles[..., 1] <= quantiles[..., 2])


def _small_config(variant: str) -> ImpactNetConfig:
    return ImpactNetConfig(
        past_feature_dim=4,
        forecast_feature_dim=3 if variant == "hybrid_tcn" else 0,
        static_feature_dim=2,
        time_feature_dim=2,
        variant=variant,  # type: ignore[arg-type]
        history_hours=12,
        forecast_hours=6,
        hidden_channels=8,
        num_blocks=2,
        dilations=(1, 2),
        kernel_size=3,
        dropout=0.0,
        static_hidden_dim=8,
        static_context_dim=4,
        decoder_hidden_dim=12,
        decoder_layers=2,
        lead_embedding_dim=3,
        water_target_mode="residual" if variant == "hybrid_tcn" else "absolute",
    )


def test_obs_only_variant_forward_contract() -> None:
    torch.manual_seed(19)
    model = ImpactNet(_small_config("obs_only_tcn")).eval()
    output = model(
        torch.randn(3, 12, 4),
        static_features=torch.randn(3, 2),
        future_time_features=torch.randn(3, 6, 2),
    )

    assert set(output) == {
        "hazard_logits",
        "cumulative_event_probability",
        "water_quantiles",
    }
    assert output["hazard_logits"].shape == (3, 6)
    assert output["cumulative_event_probability"].shape == (3, 6)
    assert output["water_quantiles"].shape == (3, 6, 3)
    assert all(torch.isfinite(value).all() for value in output.values())


def test_hybrid_variant_forward_and_partial_missing_forecast() -> None:
    torch.manual_seed(23)
    model = ImpactNet(_small_config("hybrid_tcn")).eval()
    future = torch.randn(2, 6, 3)
    missing = torch.zeros_like(future, dtype=torch.bool)
    missing[0, :2] = True
    future[0, :2] = torch.nan
    baseline = torch.randn(2, 6)
    output = model(
        torch.randn(2, 12, 4),
        future_forecasts=future,
        static_features=torch.randn(2, 2),
        future_time_features=torch.randn(2, 6, 2),
        physics_baseline=baseline,
        future_missing_mask=missing,
    )

    assert output["hazard_logits"].shape == (2, 6)
    assert output["water_quantiles"].shape == (2, 6, 3)
    assert all(torch.isfinite(value).all() for value in output.values())
    assert torch.all(output["water_quantiles"][..., 0] <= output["water_quantiles"][..., 1])
    assert torch.all(output["water_quantiles"][..., 1] <= output["water_quantiles"][..., 2])


@pytest.mark.parametrize("use_none", [True, False])
def test_hybrid_rejects_completely_absent_forecast(use_none: bool) -> None:
    model = ImpactNet(_small_config("hybrid_tcn")).eval()
    kwargs: dict[str, object] = {
        "static_features": torch.randn(2, 2),
        "future_time_features": torch.randn(2, 6, 2),
        "physics_baseline": torch.randn(2, 6),
    }
    if not use_none:
        kwargs["future_forecasts"] = torch.zeros(2, 6, 3)
        kwargs["future_missing_mask"] = torch.ones(2, 6, 3, dtype=torch.bool)

    with pytest.raises(MissingFutureForecastError, match="obs_only|absent"):
        model(torch.randn(2, 12, 4), **kwargs)  # type: ignore[arg-type]


def test_hybrid_rejects_zero_only_forecast_without_availability_mask() -> None:
    model = ImpactNet(_small_config("hybrid_tcn")).eval()
    with pytest.raises(MissingFutureForecastError, match="zero-only"):
        model(
            torch.randn(1, 12, 4),
            future_forecasts=torch.zeros(1, 6, 3),
            static_features=torch.randn(1, 2),
            future_time_features=torch.randn(1, 6, 2),
            physics_baseline=torch.zeros(1, 6),
        )


def test_valid_zero_forecast_is_not_mistaken_for_absence() -> None:
    model = ImpactNet(_small_config("hybrid_tcn")).eval()
    output = model(
        torch.randn(1, 12, 4),
        future_forecasts=torch.zeros(1, 6, 3),
        future_missing_mask=torch.zeros(1, 6, 3, dtype=torch.bool),
        static_features=torch.randn(1, 2),
        future_time_features=torch.randn(1, 6, 2),
        physics_baseline=torch.zeros(1, 6),
    )
    assert torch.isfinite(output["hazard_logits"]).all()


def test_dataset_observed_mask_aliases_are_supported() -> None:
    model = ImpactNet(_small_config("hybrid_tcn")).eval()
    output = model(
        torch.zeros(1, 12, 4),
        future_forecasts=torch.zeros(1, 6, 3),
        static_features=torch.zeros(1, 2),
        future_time_features=torch.zeros(1, 6, 2),
        physics_baseline=torch.zeros(1, 6),
        past_mask=torch.ones(1, 12, 4, dtype=torch.bool),
        future_mask=torch.ones(1, 6, 3, dtype=torch.bool),
        static_mask=torch.ones(1, 2, dtype=torch.bool),
    )
    assert torch.isfinite(output["hazard_logits"]).all()


def test_resolved_config_round_trip() -> None:
    original = _small_config("hybrid_tcn")
    restored = ImpactNetConfig.from_dict(original.to_dict())
    assert restored.to_dict() == original.to_dict()
