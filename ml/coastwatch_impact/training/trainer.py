"""Leakage-aware neural training loop with validation-only model selection."""

from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from numpy.typing import NDArray
from torch import Tensor
from torch.nn.utils import clip_grad_norm_
from torch.utils.data import DataLoader

from coastwatch_impact.evaluation.calibration import cumulative_event_probability
from coastwatch_impact.evaluation.metrics import compute_horizon_metrics
from coastwatch_impact.models.impactnet import ImpactNet
from coastwatch_impact.models.losses import MultiTaskLoss

from .checkpoint import CheckpointState, load_checkpoint, save_checkpoint
from .reproducibility import seed_everything


@dataclass(frozen=True)
class EpochRecord:
    epoch: int
    train_loss: float
    validation_loss: float
    validation_event_pr_auc: float | None
    learning_rate: float


@dataclass(frozen=True)
class PredictionBatch:
    hazard_logits: NDArray[np.float64]
    cumulative_probabilities: NDArray[np.float64]
    hazard_targets: NDArray[np.float64]
    hazard_masks: NDArray[np.bool_]
    water_quantiles: NDArray[np.float64]
    water_targets: NDArray[np.float64]
    water_masks: NDArray[np.bool_]
    sample_weights: NDArray[np.float64]


@dataclass(frozen=True)
class TrainingResult:
    history: tuple[EpochRecord, ...]
    best_epoch: int
    best_validation_score: float
    stopped_early: bool
    validation_predictions: PredictionBatch
    determinism: dict[str, Any]

    def history_dicts(self) -> list[dict[str, Any]]:
        return [asdict(record) for record in self.history]


def _tensor(batch: dict[str, Any], name: str, device: torch.device) -> Tensor:
    value = batch[name]
    if not isinstance(value, Tensor):
        value = torch.as_tensor(value)
    return value.to(device)


def _optional_tensor(batch: dict[str, Any], name: str, device: torch.device) -> Tensor | None:
    value = batch.get(name)
    if value is None:
        return None
    if not isinstance(value, Tensor):
        value = torch.as_tensor(value)
    return value.to(device)


class ImpactTrainer:
    """Train one model; test evaluation is intentionally a separate call."""

    def __init__(
        self,
        model: ImpactNet,
        *,
        learning_rate: float = 1e-3,
        weight_decay: float = 1e-4,
        event_weight: float = 1.0,
        water_weight: float = 0.4,
        pos_weight: float | None = None,
        grad_clip_norm: float = 1.0,
        max_epochs: int = 100,
        early_stopping_patience: int = 12,
        seed: int = 20260813,
        mixed_precision: bool = True,
        device: str | torch.device | None = None,
    ) -> None:
        if learning_rate <= 0 or weight_decay < 0 or grad_clip_norm <= 0:
            raise ValueError("invalid optimizer or gradient parameters")
        if max_epochs < 1 or early_stopping_patience < 1:
            raise ValueError("training epochs and patience must be positive")
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        self.model = model.to(self.device)
        self.optimizer = torch.optim.AdamW(
            self.model.parameters(),
            lr=learning_rate,
            weight_decay=weight_decay,
        )
        self.scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            self.optimizer,
            mode="max",
            factor=0.5,
            patience=max(1, early_stopping_patience // 3),
        )
        self.criterion = MultiTaskLoss(
            event_weight=event_weight,
            water_weight=water_weight,
            pos_weight=pos_weight,
        ).to(self.device)
        self.grad_clip_norm = grad_clip_norm
        self.max_epochs = max_epochs
        self.early_stopping_patience = early_stopping_patience
        self.seed = seed
        self.use_amp = mixed_precision and self.device.type == "cuda"
        self.scaler = torch.amp.GradScaler("cuda", enabled=self.use_amp)

    def _model_inputs(self, batch: dict[str, Any]) -> dict[str, Tensor]:
        inputs: dict[str, Tensor] = {
            "past_observations": _tensor(batch, "past_values", self.device).float(),
            "past_mask": _tensor(batch, "past_mask", self.device).bool(),
        }
        static = _optional_tensor(batch, "static_values", self.device)
        if self.model.config.static_feature_dim:
            if static is None:
                raise ValueError("dataset is missing static_values")
            inputs["static_features"] = static.float()
            static_mask = _optional_tensor(batch, "static_mask", self.device)
            if static_mask is not None:
                inputs["static_mask"] = static_mask.bool()
        if self.model.config.variant == "hybrid_tcn":
            future = _optional_tensor(batch, "future_values", self.device)
            future_mask = _optional_tensor(batch, "future_mask", self.device)
            if future is None or future_mask is None:
                raise ValueError("hybrid dataset is missing future values or masks")
            inputs["future_forecasts"] = future.float()
            inputs["future_mask"] = future_mask.bool()
        if self.model.config.time_feature_dim:
            lead = _optional_tensor(batch, "lead_features", self.device)
            if lead is None:
                raise ValueError("dataset is missing lead_features")
            inputs["future_time_features"] = lead.float()
        if self.model.config.water_target_mode == "residual":
            physics = _optional_tensor(batch, "physics_baseline", self.device)
            if physics is None:
                # Dataset builders may expose the physical reference under a
                # more explicit future-tide key.
                physics = _optional_tensor(batch, "future_tide_baseline", self.device)
            if physics is None:
                raise ValueError("residual target mode requires a physics baseline")
            physics_mask = _optional_tensor(batch, "physics_mask", self.device)
            if physics_mask is not None and not bool(physics_mask.bool().all()):
                raise ValueError(
                    "residual target mode requires a valid physics baseline at every lead"
                )
            inputs["physics_baseline"] = physics.float()
        return inputs

    def _loss(self, outputs: dict[str, Tensor], batch: dict[str, Any]) -> dict[str, Tensor]:
        sample_weight = _optional_tensor(batch, "sample_weight", self.device)
        if sample_weight is not None:
            sample_weight = sample_weight.float()
        return self.criterion(
            hazard_logits=outputs["hazard_logits"],
            hazard_target=_tensor(batch, "hazard_target", self.device).float(),
            hazard_mask=_tensor(batch, "hazard_mask", self.device).bool(),
            water_quantiles=outputs["water_quantiles"],
            water_target=_tensor(batch, "water_target", self.device).float(),
            water_mask=_tensor(batch, "water_mask", self.device).bool(),
            event_sample_weight=sample_weight,
            # Per-event normalisation is for the rare onset objective. Water
            # observations retain equal weight within the already sampled set.
            water_sample_weight=None,
        )

    def _training_epoch(self, loader: DataLoader[Any]) -> float:
        self.model.train()
        weighted_loss = 0.0
        samples = 0
        for batch in loader:
            self.optimizer.zero_grad(set_to_none=True)
            batch_size = int(_tensor(batch, "past_values", self.device).shape[0])
            with torch.autocast(
                device_type=self.device.type,
                dtype=torch.float16,
                enabled=self.use_amp,
            ):
                outputs = self.model(**self._model_inputs(batch))
                loss = self._loss(outputs, batch)["loss"]
            if not torch.isfinite(loss):
                raise FloatingPointError("non-finite training loss")
            self.scaler.scale(loss).backward()
            self.scaler.unscale_(self.optimizer)
            clip_grad_norm_(self.model.parameters(), self.grad_clip_norm)
            self.scaler.step(self.optimizer)
            self.scaler.update()
            weighted_loss += float(loss.detach().cpu()) * batch_size
            samples += batch_size
        if samples == 0:
            raise ValueError("training loader is empty")
        return weighted_loss / samples

    def predict(self, loader: DataLoader[Any]) -> tuple[PredictionBatch, float]:
        """Predict a complete ordered timeline without changing model state."""

        self.model.eval()
        collected: dict[str, list[NDArray[Any]]] = {
            "hazard_logits": [],
            "hazard_targets": [],
            "hazard_masks": [],
            "water_quantiles": [],
            "water_targets": [],
            "water_masks": [],
            "sample_weights": [],
        }
        weighted_loss = 0.0
        samples = 0
        with torch.inference_mode():
            for batch in loader:
                outputs = self.model(**self._model_inputs(batch))
                losses = self._loss(outputs, batch)
                batch_size = int(outputs["hazard_logits"].shape[0])
                weighted_loss += float(losses["loss"].detach().cpu()) * batch_size
                samples += batch_size
                collected["hazard_logits"].append(
                    outputs["hazard_logits"].detach().cpu().double().numpy()
                )
                collected["water_quantiles"].append(
                    outputs["water_quantiles"].detach().cpu().double().numpy()
                )
                collected["hazard_targets"].append(
                    _tensor(batch, "hazard_target", self.device).cpu().double().numpy()
                )
                collected["hazard_masks"].append(
                    _tensor(batch, "hazard_mask", self.device).cpu().bool().numpy()
                )
                collected["water_targets"].append(
                    _tensor(batch, "water_target", self.device).cpu().double().numpy()
                )
                collected["water_masks"].append(
                    _tensor(batch, "water_mask", self.device).cpu().bool().numpy()
                )
                weights = _optional_tensor(batch, "sample_weight", self.device)
                if weights is None:
                    weights = torch.ones(batch_size, device=self.device)
                collected["sample_weights"].append(weights.cpu().double().numpy())
        if samples == 0:
            raise ValueError("prediction loader is empty")
        arrays = {name: np.concatenate(values, axis=0) for name, values in collected.items()}
        logits = arrays["hazard_logits"].astype(np.float64, copy=False)
        prediction = PredictionBatch(
            hazard_logits=logits,
            cumulative_probabilities=cumulative_event_probability(logits),
            hazard_targets=arrays["hazard_targets"].astype(np.float64, copy=False),
            hazard_masks=arrays["hazard_masks"].astype(bool, copy=False),
            water_quantiles=arrays["water_quantiles"].astype(np.float64, copy=False),
            water_targets=arrays["water_targets"].astype(np.float64, copy=False),
            water_masks=arrays["water_masks"].astype(bool, copy=False),
            sample_weights=arrays["sample_weights"].astype(np.float64, copy=False),
        )
        return prediction, weighted_loss / samples

    @staticmethod
    def _validation_score(predictions: PredictionBatch) -> tuple[float, float | None]:
        leads = predictions.hazard_logits.shape[1]
        candidates = tuple(value for value in (1, 3, 6, 12, 24) if value <= leads)
        metrics = compute_horizon_metrics(
            predictions.cumulative_probabilities,
            predictions.hazard_targets,
            predictions.hazard_masks,
            horizons=candidates,
        )
        pr_values = [
            float(item["pr_auc"]) for item in metrics.values() if item.get("pr_auc") is not None
        ]
        if not pr_values:
            return float("-inf"), None
        score = float(np.mean(pr_values))
        preferred_key = "6h" if "6h" in metrics else f"{candidates[-1]}h"
        preferred = metrics[preferred_key].get("pr_auc")
        return score, None if preferred is None else float(preferred)

    def fit(
        self,
        train_loader: DataLoader[Any],
        validation_loader: DataLoader[Any],
        *,
        checkpoint_dir: str | Path | None = None,
        resume: bool = False,
    ) -> TrainingResult:
        """Train and select weights exclusively on validation predictions."""

        determinism = seed_everything(self.seed)
        start_epoch = 1
        best_score = float("-inf")
        best_state: dict[str, Tensor] | None = None
        best_epoch = 0
        without_improvement = 0
        history: list[EpochRecord] = []

        if resume:
            if checkpoint_dir is None:
                raise ValueError("resume requires checkpoint_dir")
            state = load_checkpoint(
                checkpoint_dir,
                model=self.model,
                optimizer=self.optimizer,
                scheduler=self.scheduler,
                device=self.device,
            )
            start_epoch = state.epoch + 1
            best_score = state.best_score
            without_improvement = state.epochs_without_improvement

        stopped_early = False
        for epoch in range(start_epoch, self.max_epochs + 1):
            train_loss = self._training_epoch(train_loader)
            validation, validation_loss = self.predict(validation_loader)
            event_score, preferred_pr_auc = self._validation_score(validation)
            selection_score = event_score
            # With no known validation event, fall back explicitly to loss so
            # engineering smoke runs can still exercise checkpointing. This is
            # never presented as event-skill evidence.
            if not math.isfinite(selection_score):
                selection_score = -validation_loss
            self.scheduler.step(selection_score)
            learning_rate = float(self.optimizer.param_groups[0]["lr"])
            history.append(
                EpochRecord(
                    epoch=epoch,
                    train_loss=train_loss,
                    validation_loss=validation_loss,
                    validation_event_pr_auc=preferred_pr_auc,
                    learning_rate=learning_rate,
                )
            )
            if selection_score > best_score + 1e-12:
                best_score = selection_score
                best_epoch = epoch
                without_improvement = 0
                best_state = copy.deepcopy(self.model.state_dict())
            else:
                without_improvement += 1

            if checkpoint_dir is not None:
                save_checkpoint(
                    checkpoint_dir,
                    model=self.model,
                    optimizer=self.optimizer,
                    scheduler=self.scheduler,
                    state=CheckpointState(
                        epoch=epoch,
                        best_score=best_score,
                        epochs_without_improvement=without_improvement,
                    ),
                )
            if without_improvement >= self.early_stopping_patience:
                stopped_early = True
                break

        if best_state is None:
            raise RuntimeError("training did not produce a finite validation checkpoint")
        self.model.load_state_dict(best_state)
        final_validation, _ = self.predict(validation_loader)
        if checkpoint_dir is not None:
            save_checkpoint(
                Path(checkpoint_dir) / "best",
                model=self.model,
                optimizer=self.optimizer,
                scheduler=self.scheduler,
                state=CheckpointState(
                    epoch=best_epoch,
                    best_score=best_score,
                    epochs_without_improvement=without_improvement,
                    complete=True,
                ),
            )
        return TrainingResult(
            history=tuple(history),
            best_epoch=best_epoch,
            best_validation_score=best_score,
            stopped_early=stopped_early,
            validation_predictions=final_validation,
            determinism=determinism,
        )

    def evaluate_final_test(self, test_loader: DataLoader[Any]) -> PredictionBatch:
        """Run the frozen test timeline once; no fitting happens in this method."""

        predictions, _ = self.predict(test_loader)
        return predictions


__all__ = ["EpochRecord", "ImpactTrainer", "PredictionBatch", "TrainingResult"]
