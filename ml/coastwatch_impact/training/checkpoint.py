"""Safe-ish local checkpoints: safetensors model plus weights-only optimizer state."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import torch
from safetensors.torch import load_file, save_file


@dataclass(frozen=True)
class CheckpointState:
    epoch: int
    best_score: float
    epochs_without_improvement: int
    complete: bool = False


def save_checkpoint(
    directory: str | Path,
    *,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: Any | None,
    state: CheckpointState,
) -> Path:
    """Write a resumable checkpoint owned by the current training run."""

    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    weights = {
        name: value.detach().cpu().contiguous() for name, value in model.state_dict().items()
    }
    save_file(weights, str(root / "model.safetensors"))
    torch.save(optimizer.state_dict(), root / "optimizer.pt")
    if scheduler is not None:
        torch.save(scheduler.state_dict(), root / "scheduler.pt")
    (root / "state.json").write_text(
        json.dumps(asdict(state), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def load_checkpoint(
    directory: str | Path,
    *,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer | None = None,
    scheduler: Any | None = None,
    device: str | torch.device = "cpu",
) -> CheckpointState:
    """Load only a trusted checkpoint directory created by this run."""

    root = Path(directory)
    required = {"model.safetensors", "optimizer.pt", "state.json"}
    missing = sorted(name for name in required if not (root / name).is_file())
    if missing:
        raise FileNotFoundError(f"checkpoint is incomplete: {missing}")
    model.load_state_dict(load_file(str(root / "model.safetensors"), device=str(device)))
    if optimizer is not None:
        optimizer.load_state_dict(
            torch.load(root / "optimizer.pt", map_location=device, weights_only=True)
        )
    scheduler_file = root / "scheduler.pt"
    if scheduler is not None and scheduler_file.is_file():
        scheduler.load_state_dict(
            torch.load(scheduler_file, map_location=device, weights_only=True)
        )
    payload = json.loads((root / "state.json").read_text(encoding="utf-8"))
    return CheckpointState(**payload)


__all__ = ["CheckpointState", "load_checkpoint", "save_checkpoint"]
