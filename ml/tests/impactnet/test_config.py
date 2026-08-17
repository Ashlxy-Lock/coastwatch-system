from pathlib import Path

import pytest

from coastwatch_impact.config import (
    ImpactConfig,
    ScopeConfig,
    load_config,
    model_name_for_label_mode,
)
from coastwatch_impact.provenance import git_state

ML_ROOT = Path(__file__).resolve().parents[2]


def test_synthetic_config_is_shadow_and_honestly_named() -> None:
    config = load_config(ML_ROOT / "configs" / "synthetic_phase1.yaml")
    assert config.project.shadow_mode is True
    assert config.project.synthetic_data is True
    assert config.model_name == "CoastWatch Synthetic-Test TCN"
    assert config.windows.output_horizons_hours == [1, 3, 6, 12, 24]
    assert config.features.water_target_mode == "absolute"


def test_obs_only_rejects_residual_water_targets() -> None:
    payload = load_config(ML_ROOT / "configs" / "synthetic_phase1.yaml").model_dump()
    payload["features"]["water_target_mode"] = "residual"

    with pytest.raises(ValueError, match="obs_only_tcn requires water_target_mode='absolute'"):
        ImpactConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("mode", "name"),
    [
        ("weak_rule", "CoastWatch Proxy-TCN"),
        ("official_warning", "CoastWatch WarningNet"),
        ("confirmed_impact", "CoastWatch ImpactNet"),
    ],
)
def test_model_names_follow_label_semantics(mode: str, name: str) -> None:
    assert model_name_for_label_mode(mode) == name  # type: ignore[arg-type]


def test_workspace_without_git_is_reported_not_invented() -> None:
    state = git_state(ML_ROOT.parent)
    assert state["git_available"] is False
    assert state["commit"] is None


def test_label_confidence_must_match_model_semantics() -> None:
    warning = ScopeConfig(
        country="England",
        hazard="official_coastal_warning",
        label_mode="official_warning",
        allowed_label_confidence=["C"],
    )
    assert warning.allowed_label_confidence == ["C"]
    with pytest.raises(ValueError, match="C warning evidence only"):
        ScopeConfig(
            country="England",
            hazard="official_coastal_warning",
            label_mode="official_warning",
            allowed_label_confidence=["A"],
        )
    with pytest.raises(ValueError, match="warning-only evidence"):
        ScopeConfig(
            country="England",
            hazard="coastal_flood_impact",
            label_mode="confirmed_impact",
            allowed_label_confidence=["A", "C"],
        )
