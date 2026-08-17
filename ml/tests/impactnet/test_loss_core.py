from __future__ import annotations

import torch
from torch.nn import functional as F

from coastwatch_impact.models import (
    masked_bce_hazard_loss,
    masked_quantile_loss,
    multitask_loss,
)


def test_masked_hazard_bce_ignores_nan_targets_and_weights_valid_entries() -> None:
    logits = torch.tensor([[0.0, 1.0, -2.0], [3.0, -1.0, 0.5]], requires_grad=True)
    target = torch.tensor([[0.0, float("nan"), 1.0], [1.0, 0.0, float("nan")]])
    mask = torch.tensor([[True, False, True], [True, True, False]])
    sample_weight = torch.tensor([1.0, 2.0])

    actual = masked_bce_hazard_loss(
        logits,
        target,
        mask,
        sample_weight=sample_weight,
    )
    raw = F.binary_cross_entropy_with_logits(
        logits[mask],
        target[mask],
        reduction="none",
    )
    expected_weights = torch.tensor([1.0, 1.0, 2.0, 2.0])
    expected = (raw * expected_weights).sum() / expected_weights.sum()
    torch.testing.assert_close(actual, expected)
    actual.backward()
    assert torch.isfinite(logits.grad).all()
    assert torch.all(logits.grad[~mask] == 0)


def test_fully_masked_hazard_loss_is_zero_and_differentiable() -> None:
    logits = torch.randn(2, 4, requires_grad=True)
    target = torch.full((2, 4), float("nan"))
    loss = masked_bce_hazard_loss(logits, target, torch.zeros(2, 4, dtype=torch.bool))
    assert loss.item() == 0.0
    loss.backward()
    assert logits.grad is not None
    assert torch.all(logits.grad == 0)


def test_masked_quantile_pinball_loss_matches_manual_result() -> None:
    prediction = torch.tensor(
        [
            [[0.0, 1.0, 2.0], [8.0, 9.0, 10.0]],
            [[1.0, 2.0, 3.0], [0.0, 1.0, 2.0]],
        ],
        requires_grad=True,
    )
    target = torch.tensor([[1.0, float("nan")], [2.0, 2.0]])
    mask = torch.tensor([[True, False], [True, True]])

    actual = masked_quantile_loss(prediction, target, mask)
    tau = torch.tensor([0.1, 0.5, 0.9])
    error = target[mask, None] - prediction[mask]
    expected = torch.maximum(tau * error, (tau - 1) * error).mean()
    torch.testing.assert_close(actual, expected)
    actual.backward()
    assert prediction.grad is not None
    assert torch.all(prediction.grad[0, 1] == 0)


def test_multitask_loss_combines_masked_tasks_and_auxiliary_loss() -> None:
    hazard_logits = torch.randn(2, 3, requires_grad=True)
    water_quantiles = torch.randn(2, 3, 3, requires_grad=True)
    aux = torch.tensor(2.0, requires_grad=True)
    result = multitask_loss(
        hazard_logits=hazard_logits,
        hazard_target=torch.tensor([[0.0, 1.0, 0.0], [1.0, 0.0, 1.0]]),
        hazard_mask=torch.ones(2, 3, dtype=torch.bool),
        water_quantiles=water_quantiles,
        water_target=torch.zeros(2, 3),
        water_mask=torch.tensor([[True, True, False], [True, False, True]]),
        event_weight=1.0,
        water_weight=0.4,
        auxiliary_losses={"severity": aux},
        auxiliary_weights={"severity": 0.2},
    )
    expected = result["event_loss"] + 0.4 * result["water_loss"] + 0.2 * aux
    torch.testing.assert_close(result["loss"], expected)
    assert result["loss"] is result["total_loss"]
    result["loss"].backward()
    assert torch.isfinite(hazard_logits.grad).all()
    assert torch.isfinite(water_quantiles.grad).all()
    assert aux.grad is not None
