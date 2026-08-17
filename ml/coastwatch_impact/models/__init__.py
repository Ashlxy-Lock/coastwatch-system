"""ImpactNet v2 model architecture, losses, and safe baselines."""

from .baselines import (
    LogisticBaseline,
    LogisticEventBaseline,
    PersistenceBaseline,
    PersistenceWaterBaseline,
    PhysicsBaseline,
    build_logistic_summary_features,
)
from .causal_conv import (
    CausalConv1d,
    CausalResidualBlock,
    CausalResidualTCNBlock,
    TimewiseGroupNorm,
)
from .heads import (
    HazardHead,
    LeadDecoder,
    StaticEncoder,
    WaterQuantileHead,
    cumulative_event_probability,
)
from .impactnet import (
    ImpactNet,
    ImpactNetConfig,
    MissingFutureForecastError,
    build_hybrid_tcn,
    build_obs_only_tcn,
)
from .losses import (
    ImpactNetLoss,
    MultiTaskLoss,
    masked_bce_hazard_loss,
    masked_hazard_bce_loss,
    masked_mean,
    masked_quantile_loss,
    multitask_loss,
    quantile_loss,
)
from .tcn import TCNEncoder, TemporalConvNet

__all__ = [
    "CausalConv1d",
    "CausalResidualBlock",
    "CausalResidualTCNBlock",
    "HazardHead",
    "ImpactNet",
    "ImpactNetConfig",
    "ImpactNetLoss",
    "LeadDecoder",
    "LogisticBaseline",
    "LogisticEventBaseline",
    "MissingFutureForecastError",
    "MultiTaskLoss",
    "PersistenceBaseline",
    "PersistenceWaterBaseline",
    "PhysicsBaseline",
    "StaticEncoder",
    "TCNEncoder",
    "TemporalConvNet",
    "TimewiseGroupNorm",
    "WaterQuantileHead",
    "build_hybrid_tcn",
    "build_logistic_summary_features",
    "build_obs_only_tcn",
    "cumulative_event_probability",
    "masked_bce_hazard_loss",
    "masked_hazard_bce_loss",
    "masked_mean",
    "masked_quantile_loss",
    "multitask_loss",
    "quantile_loss",
]
