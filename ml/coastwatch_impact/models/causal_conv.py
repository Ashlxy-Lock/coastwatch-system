"""Causal convolution building blocks used by ImpactNet.

The normalisation in this module is intentionally time-local.  PyTorch's
``GroupNorm`` normally reduces across the temporal axis as well as channels;
using it directly on ``[B, C, T]`` would let an early output depend on later
time steps.  ``TimewiseGroupNorm`` reshapes time into the batch dimension so
normalisation preserves the TCN's causal contract.
"""

from __future__ import annotations

from collections.abc import Callable

from torch import Tensor, nn
from torch.nn import functional as F


class CausalConv1d(nn.Module):
    """A one-dimensional convolution with padding on the left only."""

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        *,
        dilation: int = 1,
        bias: bool = True,
    ) -> None:
        super().__init__()
        if kernel_size < 1:
            raise ValueError("kernel_size must be at least 1")
        if dilation < 1:
            raise ValueError("dilation must be at least 1")
        self.left_padding = (kernel_size - 1) * dilation
        self.conv = nn.Conv1d(
            in_channels,
            out_channels,
            kernel_size,
            padding=0,
            dilation=dilation,
            bias=bias,
        )

    def forward(self, inputs: Tensor) -> Tensor:
        if inputs.ndim != 3:
            raise ValueError("CausalConv1d expects [batch, channels, time]")
        if self.left_padding:
            inputs = F.pad(inputs, (self.left_padding, 0))
        return self.conv(inputs)


class TimewiseGroupNorm(nn.Module):
    """Apply GroupNorm independently at every time step."""

    def __init__(self, channels: int, max_groups: int = 8) -> None:
        super().__init__()
        self.norm: nn.Module
        if channels < 1:
            raise ValueError("channels must be positive")
        if channels == 1:
            # A singleton channel cannot be variance-normalised time-locally.
            self.norm = nn.Identity()
            return
        groups = min(max_groups, channels)
        # One channel at one time step has zero variance and would collapse the
        # convolutional branch. Prefer at least two channels per group.
        while groups > 1 and (channels % groups or channels // groups < 2):
            groups -= 1
        self.norm = nn.GroupNorm(groups, channels)

    def forward(self, inputs: Tensor) -> Tensor:
        if inputs.ndim != 3:
            raise ValueError("TimewiseGroupNorm expects [batch, channels, time]")
        batch, channels, time = inputs.shape
        timewise = inputs.transpose(1, 2).reshape(batch * time, channels, 1)
        normalised = self.norm(timewise)
        return normalised.reshape(batch, time, channels).transpose(1, 2)


def _normalisation_factory(name: str, channels: int) -> nn.Module:
    normalised_name = name.lower().replace("-", "_")
    if normalised_name in {"group_norm", "groupnorm"}:
        return TimewiseGroupNorm(channels)
    if normalised_name in {"none", "identity"}:
        return nn.Identity()
    raise ValueError(f"Unsupported causal normalisation: {name!r}")


def _activation_factory(name: str) -> Callable[[], nn.Module]:
    normalised_name = name.lower()
    if normalised_name == "gelu":
        return nn.GELU
    if normalised_name == "relu":
        return nn.ReLU
    raise ValueError(f"Unsupported activation: {name!r}")


class CausalResidualBlock(nn.Module):
    """Two causal convolutions with a same-time residual connection."""

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        *,
        kernel_size: int = 3,
        dilation: int = 1,
        dropout: float = 0.2,
        normalization: str = "group_norm",
        activation: str = "gelu",
    ) -> None:
        super().__init__()
        if not 0.0 <= dropout < 1.0:
            raise ValueError("dropout must be in [0, 1)")
        activation_type = _activation_factory(activation)
        self.conv1 = CausalConv1d(
            in_channels,
            out_channels,
            kernel_size,
            dilation=dilation,
        )
        self.norm1 = _normalisation_factory(normalization, out_channels)
        self.activation1 = activation_type()
        self.dropout1 = nn.Dropout(dropout)
        self.conv2 = CausalConv1d(
            out_channels,
            out_channels,
            kernel_size,
            dilation=dilation,
        )
        self.norm2 = _normalisation_factory(normalization, out_channels)
        self.activation2 = activation_type()
        self.dropout2 = nn.Dropout(dropout)
        self.residual = (
            nn.Identity()
            if in_channels == out_channels
            else nn.Conv1d(in_channels, out_channels, kernel_size=1)
        )

    def forward(self, inputs: Tensor) -> Tensor:
        residual = self.residual(inputs)
        hidden = self.dropout1(self.activation1(self.norm1(self.conv1(inputs))))
        hidden = self.dropout2(self.activation2(self.norm2(self.conv2(hidden))))
        return hidden + residual


# A concise alias used by some callers and model cards.
CausalResidualTCNBlock = CausalResidualBlock


__all__ = [
    "CausalConv1d",
    "CausalResidualBlock",
    "CausalResidualTCNBlock",
    "TimewiseGroupNorm",
]
