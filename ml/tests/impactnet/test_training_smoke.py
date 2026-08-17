from __future__ import annotations

import torch
from torch.utils.data import DataLoader, Dataset

from coastwatch_impact.models.impactnet import ImpactNet, ImpactNetConfig
from coastwatch_impact.training import ImpactTrainer, load_checkpoint


class TinyWindowDataset(Dataset):
    def __init__(self, samples: int = 32) -> None:
        generator = torch.Generator().manual_seed(20260813)
        self.past = torch.randn(samples, 4, 2, generator=generator)
        signal = self.past[:, -1, 0]
        event = (signal > 0).float()
        self.hazard = torch.zeros(samples, 3)
        self.hazard[:, 0] = event
        self.hazard_mask = torch.ones(samples, 3, dtype=torch.bool)
        self.hazard_mask[event.bool(), 1:] = False
        self.water = signal[:, None].repeat(1, 3)

    def __len__(self) -> int:
        return len(self.past)

    def __getitem__(self, index: int):
        return {
            "past_values": self.past[index],
            "past_mask": torch.ones(4, 2, dtype=torch.bool),
            "static_values": torch.tensor([1.0]),
            "static_mask": torch.ones(1, dtype=torch.bool),
            "hazard_target": self.hazard[index],
            "hazard_mask": self.hazard_mask[index],
            "water_target": self.water[index],
            "water_mask": torch.ones(3, dtype=torch.bool),
            "sample_weight": torch.tensor(1.0),
        }


class TinyHybridWindowDataset(TinyWindowDataset):
    def __getitem__(self, index: int):
        sample = super().__getitem__(index)
        sample.update(
            future_values=torch.tensor([[0.2], [0.3], [0.4]]),
            future_mask=torch.ones(3, 1, dtype=torch.bool),
            lead_features=torch.tensor([[1 / 3], [2 / 3], [1.0]]),
            physics_baseline=torch.tensor([0.1, 0.1, 0.1]),
            physics_mask=torch.ones(3, dtype=torch.bool),
        )
        return sample


def _model():
    return ImpactNet(
        ImpactNetConfig(
            past_feature_dim=2,
            static_feature_dim=1,
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


def test_cpu_training_checkpoint_and_frozen_test(tmp_path):
    loader = DataLoader(TinyWindowDataset(), batch_size=8, shuffle=False)
    trainer = ImpactTrainer(
        _model(),
        max_epochs=2,
        early_stopping_patience=2,
        mixed_precision=False,
        device="cpu",
    )
    result = trainer.fit(loader, loader, checkpoint_dir=tmp_path / "checkpoint")
    assert len(result.history) == 2
    assert result.best_epoch in {1, 2}
    assert result.validation_predictions.hazard_logits.shape == (32, 3)
    final_test = trainer.evaluate_final_test(loader)
    assert final_test.water_quantiles.shape == (32, 3, 3)
    assert (final_test.water_quantiles[:, :, 0] <= final_test.water_quantiles[:, :, 1]).all()
    state = load_checkpoint(
        tmp_path / "checkpoint" / "best",
        model=_model(),
        device="cpu",
    )
    assert state.complete is True


def test_hybrid_variant_trains_and_checkpoints_independently_on_cpu(tmp_path):
    model = ImpactNet(
        ImpactNetConfig(
            past_feature_dim=2,
            forecast_feature_dim=1,
            static_feature_dim=1,
            time_feature_dim=1,
            variant="hybrid_tcn",
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
            water_target_mode="residual",
        )
    )
    loader = DataLoader(TinyHybridWindowDataset(16), batch_size=8, shuffle=False)
    trainer = ImpactTrainer(
        model,
        max_epochs=1,
        early_stopping_patience=1,
        mixed_precision=False,
        device="cpu",
    )
    result = trainer.fit(loader, loader, checkpoint_dir=tmp_path / "hybrid-checkpoint")
    assert result.best_epoch == 1
    assert result.validation_predictions.hazard_logits.shape == (16, 3)
    loaded = load_checkpoint(
        tmp_path / "hybrid-checkpoint" / "best",
        model=ImpactNet(model.config),
        device="cpu",
    )
    assert loaded.complete is True
