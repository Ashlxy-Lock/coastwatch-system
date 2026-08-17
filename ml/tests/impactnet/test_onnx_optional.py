from __future__ import annotations

import importlib.util

import pytest

from coastwatch_impact.export.onnx_export import OnnxUnavailableError, export_onnx


def test_onnx_export_fails_clearly_when_optional_dependencies_are_absent(tmp_path):
    if all(importlib.util.find_spec(name) is not None for name in ("onnx", "onnxruntime")):
        pytest.skip("ONNX extras are installed; parity is covered by optional integration CI")
    with pytest.raises(OnnxUnavailableError, match="install coastwatch-impact\[onnx\]"):
        export_onnx(tmp_path / "missing-bundle", tmp_path / "model.onnx")
