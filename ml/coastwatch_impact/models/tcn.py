"""Temporal convolutional encoder for past coastal observations."""

from __future__ import annotations

from collections.abc import Sequence

from torch import Tensor, nn

from .causal_conv import CausalResidualBlock


class TCNEncoder(nn.Module):
    """Residual causal TCN.

    Inputs use the project-facing ``[batch, time, features]`` layout while the
    returned sequence follows the conventional convolution layout
    ``[batch, channels, time]``.
    """

    def __init__(
        self,
        input_channels: int,
        hidden_channels: int = 64,
        *,
        dilations: Sequence[int] = (1, 2, 4, 8, 16),
        kernel_size: int = 3,
        dropout: float = 0.2,
        normalization: str = "group_norm",
        activation: str = "gelu",
    ) -> None:
        super().__init__()
        if input_channels < 1 or hidden_channels < 1:
            raise ValueError("input_channels and hidden_channels must be positive")
        if not dilations:
            raise ValueError("at least one dilation is required")
        if any(int(dilation) < 1 for dilation in dilations):
            raise ValueError("all dilations must be positive")
        self.input_channels = int(input_channels)
        self.hidden_channels = int(hidden_channels)
        self.dilations = tuple(int(dilation) for dilation in dilations)
        self.kernel_size = int(kernel_size)

        blocks: list[nn.Module] = []
        current_channels = input_channels
        for dilation in self.dilations:
            blocks.append(
                CausalResidualBlock(
                    current_channels,
                    hidden_channels,
                    kernel_size=kernel_size,
                    dilation=dilation,
                    dropout=dropout,
                    normalization=normalization,
                    activation=activation,
                )
            )
            current_channels = hidden_channels
        self.blocks = nn.Sequential(*blocks)

    @property
    def receptive_field(self) -> int:
        """Number of input steps that can affect the last encoded step."""

        return 1 + 2 * (self.kernel_size - 1) * sum(self.dilations)

    def forward(self, past_sequence: Tensor) -> Tensor:
        if past_sequence.ndim != 3:
            raise ValueError("past_sequence must have shape [batch, time, features]")
        if past_sequence.shape[-1] != self.input_channels:
            raise ValueError(
                f"expected {self.input_channels} input features, received {past_sequence.shape[-1]}"
            )
        return self.blocks(past_sequence.transpose(1, 2))

    def context(self, past_sequence: Tensor) -> Tensor:
        """Return the final causal history context ``[batch, channels]``."""

        return self.forward(past_sequence)[..., -1]


TemporalConvNet = TCNEncoder


__all__ = ["TCNEncoder", "TemporalConvNet"]
