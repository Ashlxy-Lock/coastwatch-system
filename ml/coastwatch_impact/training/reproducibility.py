"""Deterministic seeds and honest runtime metadata."""

from __future__ import annotations

import os
import random
from typing import Any

import numpy as np
import torch


def seed_everything(seed: int, *, deterministic: bool = True) -> dict[str, Any]:
    """Seed Python, NumPy and Torch, returning the actual determinism contract."""

    if seed < 0:
        raise ValueError("seed must be non-negative")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    deterministic_enabled = False
    if deterministic:
        try:
            torch.use_deterministic_algorithms(True, warn_only=True)
            deterministic_enabled = True
        except RuntimeError:
            deterministic_enabled = False
    return {
        "seed": seed,
        "python_hash_seed_inherited": os.environ.get("PYTHONHASHSEED"),
        "torch_deterministic_algorithms": deterministic_enabled,
        "bitwise_identical_claimed": False,
        "cuda_available": torch.cuda.is_available(),
    }


def seed_worker(worker_id: int) -> None:
    """DataLoader worker seed derived from Torch's initial worker seed."""

    del worker_id
    worker_seed = torch.initial_seed() % (2**32)
    random.seed(worker_seed)
    np.random.seed(worker_seed)


__all__ = ["seed_everything", "seed_worker"]
