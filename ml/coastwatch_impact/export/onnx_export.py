"""Optional ONNX export with PyTorch/ONNX Runtime parity validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import torch
from torch import Tensor, nn

from .model_bundle import LoadedBundle, load_model_bundle


class OnnxUnavailableError(RuntimeError):
    """The optional ONNX export dependencies are not installed."""


class _OnnxWrapper(nn.Module):
    def __init__(self, bundle: LoadedBundle) -> None:
        super().__init__()
        self.model = bundle.model
        self.config = bundle.architecture

    def forward(
        self,
        past: Tensor,
        past_missing: Tensor,
        future: Tensor,
        future_missing: Tensor,
        static: Tensor,
        lead: Tensor,
        physics: Tensor,
    ) -> tuple[Tensor, Tensor, Tensor]:
        kwargs: dict[str, Tensor] = {
            "past_observations": past,
            "past_missing_mask": past_missing,
        }
        if self.config.variant == "hybrid_tcn":
            kwargs["future_forecasts"] = future
            kwargs["future_missing_mask"] = future_missing
        if self.config.static_feature_dim:
            kwargs["static_features"] = static
        if self.config.time_feature_dim:
            kwargs["future_time_features"] = lead
        if self.config.water_target_mode == "residual":
            kwargs["physics_baseline"] = physics
        output = self.model(**kwargs)
        return (
            output["hazard_logits"],
            output["cumulative_event_probability"],
            output["water_quantiles"],
        )


def _require_onnx_dependencies() -> None:
    missing = [
        package for package in ("onnx", "onnxruntime") if importlib.util.find_spec(package) is None
    ]
    if missing:
        raise OnnxUnavailableError(
            "ONNX export is optional; install coastwatch-impact[onnx]. Missing: "
            + ", ".join(missing)
        )


def export_onnx(
    bundle: str | Path | LoadedBundle,
    destination: str | Path,
    *,
    opset_version: int = 18,
    verify: bool = True,
    absolute_tolerance: float = 1e-5,
    relative_tolerance: float = 1e-4,
) -> Path:
    """Export a verified local bundle and optionally compare ONNX Runtime output."""

    _require_onnx_dependencies()
    loaded = bundle if isinstance(bundle, LoadedBundle) else load_model_bundle(bundle)
    output_path = Path(destination)
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite ONNX model: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    config = loaded.architecture
    batch = 2
    examples = (
        torch.zeros(batch, config.history_hours, config.past_feature_dim),
        torch.zeros(batch, config.history_hours, config.past_feature_dim, dtype=torch.bool),
        torch.ones(batch, config.forecast_hours, max(1, config.forecast_feature_dim)),
        torch.zeros(
            batch,
            config.forecast_hours,
            max(1, config.forecast_feature_dim),
            dtype=torch.bool,
        ),
        torch.zeros(batch, max(1, config.static_feature_dim)),
        torch.zeros(batch, config.forecast_hours, max(1, config.time_feature_dim)),
        torch.zeros(batch, config.forecast_hours),
    )
    wrapper = _OnnxWrapper(loaded).eval()
    input_names = [
        "past_values",
        "past_missing_mask",
        "future_values",
        "future_missing_mask",
        "static_values",
        "lead_features",
        "physics_baseline",
    ]
    output_names = ["hazard_logits", "cumulative_event_probability", "water_quantiles"]
    dynamic_axes = {name: {0: "batch"} for name in [*input_names, *output_names]}
    torch.onnx.export(
        wrapper,
        examples,
        output_path,
        input_names=input_names,
        output_names=output_names,
        dynamic_axes=dynamic_axes,
        opset_version=opset_version,
        do_constant_folding=True,
    )

    if verify:
        import onnxruntime as ort

        with torch.inference_mode():
            expected = [value.detach().cpu().numpy() for value in wrapper(*examples)]
        session = ort.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
        active_inputs = {item.name for item in session.get_inputs()}
        feed = {
            name: value.detach().cpu().numpy()
            for name, value in zip(input_names, examples, strict=True)
            if name in active_inputs
        }
        actual = session.run(output_names, feed)
        for name, reference, candidate in zip(output_names, expected, actual, strict=True):
            if not np.allclose(
                reference,
                candidate,
                atol=absolute_tolerance,
                rtol=relative_tolerance,
            ):
                output_path.unlink(missing_ok=True)
                raise RuntimeError(f"ONNX/PyTorch parity check failed for {name}")
    return output_path


__all__ = ["OnnxUnavailableError", "export_onnx"]
