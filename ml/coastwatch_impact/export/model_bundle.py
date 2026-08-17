"""Versioned ImpactNet bundles without executable pickle payloads."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from safetensors.torch import load_file, save_file

from coastwatch_impact.config import model_name_for_label_mode
from coastwatch_impact.models.impactnet import ImpactNet, ImpactNetConfig

BUNDLE_SCHEMA_VERSION = "1.0"
REQUIRED_PAYLOADS = {
    "manifest.json",
    "model.safetensors",
    "architecture.json",
    "preprocessing.json",
    "preprocessing_arrays.npz",
    "calibration.json",
    "thresholds.json",
    "feature_schema.json",
    "sites.json",
    "MODEL_CARD.md",
}


class BundleIntegrityError(ValueError):
    """Raised before any model state is loaded when a bundle is incomplete."""


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json(path: Path, payload: Any) -> None:
    path.write_bytes(_json_bytes(payload))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_npz_arrays(
    arrays: dict[str, NDArray[np.generic] | list[float]] | None,
) -> dict[str, NDArray[np.generic]]:
    output: dict[str, NDArray[np.generic]] = {}
    for name, value in (arrays or {}).items():
        if not name or "/" in name or "\\" in name:
            raise ValueError(f"unsafe preprocessing array name: {name!r}")
        array = np.asarray(value)
        if array.dtype.hasobject:
            raise ValueError("preprocessing arrays must not use object dtype")
        output[name] = array
    # NPZ must exist even when a model has no fitted numeric arrays.
    if not output:
        output["bundle_format_marker"] = np.asarray([1], dtype=np.int8)
    return output


def create_model_bundle(
    destination: str | Path,
    model: ImpactNet,
    *,
    model_version: str,
    model_name: str,
    label_mode: str,
    coverage_scope: str,
    horizons_hours: list[int] | tuple[int, ...] = (1, 3, 6, 12, 24),
    preprocessing: dict[str, Any] | None = None,
    preprocessing_arrays: dict[str, NDArray[np.generic] | list[float]] | None = None,
    calibration: dict[str, Any] | None = None,
    thresholds: dict[str, Any] | None = None,
    feature_schema: dict[str, Any] | None = None,
    sites: list[dict[str, Any]] | dict[str, Any] | None = None,
    model_card: str | None = None,
    synthetic_data: bool = False,
    overwrite: bool = False,
) -> Path:
    """Create an atomic-ish, hash-addressed model bundle.

    Existing non-empty destinations are rejected by default so a registered
    model cannot be silently replaced.  Synthetic bundles carry an explicit
    non-deployable marker in both the manifest and model card.
    """

    target = Path(destination)
    if target.exists() and any(target.iterdir()):
        if not overwrite:
            raise FileExistsError(f"bundle destination is not empty: {target}")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    if not model_version.strip() or any(part in model_version for part in ("/", "\\")):
        raise ValueError("model_version must be a non-empty path-safe identifier")
    if label_mode not in {"weak_rule", "official_warning", "confirmed_impact"}:
        raise ValueError(f"unsupported label_mode: {label_mode!r}")
    permitted_name = model_name_for_label_mode(
        label_mode,  # type: ignore[arg-type]
        synthetic_data=synthetic_data,
    )
    if model_name != permitted_name:
        raise ValueError(
            f"model_name {model_name!r} is incompatible with label_mode={label_mode!r}; "
            f"expected {permitted_name!r}"
        )
    resolved_horizons = [int(value) for value in horizons_hours]
    if (
        not resolved_horizons
        or resolved_horizons != sorted(set(resolved_horizons))
        or resolved_horizons[-1] > model.config.forecast_hours
    ):
        raise ValueError("horizons must be increasing and within forecast_hours")

    state = {
        name: tensor.detach().cpu().contiguous() for name, tensor in model.state_dict().items()
    }
    save_file(state, str(target / "model.safetensors"))
    _write_json(target / "architecture.json", model.config.to_dict())
    _write_json(target / "preprocessing.json", preprocessing or {"fitted_on": "train"})
    arrays_payload: Any = _normalise_npz_arrays(preprocessing_arrays)
    np.savez_compressed(target / "preprocessing_arrays.npz", **arrays_payload)
    calibration_payload = calibration or {
        "method": "identity",
        "temperature": 1.0,
        "fitted_split": "validation",
        "calibrated": False,
    }
    _validate_selection_payloads(calibration_payload, thresholds or {})
    _write_json(target / "calibration.json", calibration_payload)
    _write_json(target / "thresholds.json", thresholds or {})
    _write_json(target / "feature_schema.json", feature_schema or {})
    _write_json(target / "sites.json", sites or [])

    synthetic_warning = (
        "\n\n> SYNTHETIC ENGINEERING ARTIFACT: this bundle must not be used as "
        "evidence of real predictive performance or for operational warnings.\n"
        if synthetic_data
        else ""
    )
    card = model_card or (
        f"# {model_name}\n\nResearch-only shadow model. Official warnings remain authoritative.\n"
    )
    (target / "MODEL_CARD.md").write_text(card.rstrip() + synthetic_warning, encoding="utf-8")

    manifest = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "model_name": model_name,
        "model_version": model_version,
        "model_variant": model.config.variant,
        "label_mode": label_mode,
        "coverage_scope": coverage_scope,
        "horizons_hours": resolved_horizons,
        "shadow_mode": True,
        "synthetic_data": bool(synthetic_data),
        # Engineering export is never an operational approval. A reviewed real
        # evidence gate belongs to a separate release process, not this caller.
        "deployable_as_real": False,
        "required_payloads": sorted(REQUIRED_PAYLOADS),
    }
    _write_json(target / "manifest.json", manifest)

    lines = [f"{_sha256(target / name)}  {name}" for name in sorted(REQUIRED_PAYLOADS)]
    (target / "sha256sums.txt").write_text("\n".join(lines) + "\n", encoding="ascii")
    verify_model_bundle(target)
    return target


def _parse_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        parts = line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise BundleIntegrityError(f"invalid checksum line {line_number}")
        digest, name = parts
        try:
            int(digest, 16)
        except ValueError as error:
            raise BundleIntegrityError(f"invalid checksum on line {line_number}") from error
        candidate = Path(name)
        if candidate.is_absolute() or len(candidate.parts) != 1 or name in checksums:
            raise BundleIntegrityError(f"unsafe or duplicate bundle path: {name!r}")
        checksums[name] = digest.lower()
    return checksums


def _validate_selection_payloads(calibration: dict[str, Any], thresholds: dict[str, Any]) -> None:
    method = calibration.get("method")
    if method not in {"identity", "global_temperature"}:
        raise BundleIntegrityError(f"unsupported calibration method: {method!r}")
    try:
        temperature = float(calibration.get("temperature", 1.0))
    except (TypeError, ValueError) as error:
        raise BundleIntegrityError("calibration temperature must be numeric") from error
    if not np.isfinite(temperature) or temperature <= 0.0:
        raise BundleIntegrityError("calibration temperature must be finite and positive")
    if method == "global_temperature" and calibration.get("fitted_split") != "validation":
        raise BundleIntegrityError("temperature calibration must be fitted on validation")
    calibrated = calibration.get("calibrated")
    if not isinstance(calibrated, bool):
        raise BundleIntegrityError("calibration must explicitly declare calibrated=true/false")
    if method == "identity" and calibrated:
        raise BundleIntegrityError("identity calibration cannot be marked calibrated")
    if method == "global_temperature" and not calibrated:
        raise BundleIntegrityError("global temperature calibration must declare calibrated=true")
    if thresholds and thresholds.get("fitted_split") != "validation":
        raise BundleIntegrityError("operating thresholds must be selected on validation")


def verify_model_bundle(bundle: str | Path) -> dict[str, Any]:
    """Verify all required files and hashes before parsing model state."""

    root = Path(bundle)
    if not root.is_dir():
        raise BundleIntegrityError(f"bundle directory does not exist: {root}")
    checksum_file = root / "sha256sums.txt"
    if not checksum_file.is_file():
        raise BundleIntegrityError("bundle is missing sha256sums.txt")
    checksums = _parse_checksums(checksum_file)
    if set(checksums) != REQUIRED_PAYLOADS:
        missing = sorted(REQUIRED_PAYLOADS - set(checksums))
        unexpected = sorted(set(checksums) - REQUIRED_PAYLOADS)
        raise BundleIntegrityError(
            f"checksum inventory mismatch; missing={missing}, unexpected={unexpected}"
        )
    for name, expected in checksums.items():
        payload = root / name
        if not payload.is_file():
            raise BundleIntegrityError(f"bundle payload is missing: {name}")
        actual = _sha256(payload)
        if actual != expected:
            raise BundleIntegrityError(f"bundle hash mismatch: {name}")

    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise BundleIntegrityError("manifest.json is invalid") from error
    if manifest.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION:
        raise BundleIntegrityError("unsupported bundle schema version")
    if manifest.get("shadow_mode") is not True:
        raise BundleIntegrityError("ImpactNet bundles must enforce shadow_mode=true")
    if set(manifest.get("required_payloads", [])) != REQUIRED_PAYLOADS:
        raise BundleIntegrityError("manifest payload inventory is incomplete")
    if manifest.get("deployable_as_real") is not False:
        raise BundleIntegrityError(
            "research Shadow bundles must not claim operational real deployment approval"
        )
    label_mode = manifest.get("label_mode")
    if label_mode not in {"weak_rule", "official_warning", "confirmed_impact"}:
        raise BundleIntegrityError("bundle label_mode is unsupported")
    expected_name = model_name_for_label_mode(
        label_mode,
        synthetic_data=bool(manifest.get("synthetic_data", False)),
    )
    if manifest.get("model_name") != expected_name:
        raise BundleIntegrityError("bundle model_name does not match label semantics")
    return manifest


@dataclass
class LoadedBundle:
    root: Path
    manifest: dict[str, Any]
    architecture: ImpactNetConfig
    model: ImpactNet
    preprocessing: dict[str, Any]
    preprocessing_arrays: dict[str, NDArray[np.generic]]
    calibration: dict[str, Any]
    thresholds: dict[str, Any]
    feature_schema: dict[str, Any]
    sites: list[dict[str, Any]] | dict[str, Any]


def load_model_bundle(bundle: str | Path, *, device: str = "cpu") -> LoadedBundle:
    """Verify and load a trusted local bundle using safetensors only."""

    root = Path(bundle)
    manifest = verify_model_bundle(root)
    try:
        architecture_payload = json.loads((root / "architecture.json").read_text(encoding="utf-8"))
        architecture = ImpactNetConfig.from_dict(architecture_payload)
        if architecture.variant != manifest["model_variant"]:
            raise BundleIntegrityError("architecture/model variant mismatch")
        model = ImpactNet(architecture)
        state = load_file(str(root / "model.safetensors"), device=device)
        model.load_state_dict(state, strict=True)
        model.to(device)
        model.eval()
        with np.load(root / "preprocessing_arrays.npz", allow_pickle=False) as archive:
            arrays = {name: archive[name].copy() for name in archive.files}
        preprocessing = json.loads((root / "preprocessing.json").read_text("utf-8"))
        calibration = json.loads((root / "calibration.json").read_text("utf-8"))
        thresholds = json.loads((root / "thresholds.json").read_text("utf-8"))
        _validate_selection_payloads(calibration, thresholds)
        feature_schema = json.loads((root / "feature_schema.json").read_text("utf-8"))
        sites = json.loads((root / "sites.json").read_text("utf-8"))
    except BundleIntegrityError:
        raise
    except Exception as error:
        raise BundleIntegrityError(f"bundle payload cannot be loaded: {error}") from error
    return LoadedBundle(
        root=root,
        manifest=manifest,
        architecture=architecture,
        model=model,
        preprocessing=preprocessing,
        preprocessing_arrays=arrays,
        calibration=calibration,
        thresholds=thresholds,
        feature_schema=feature_schema,
        sites=sites,
    )


# Concise aliases used by CLI and downstream integrations.
export_bundle = create_model_bundle
load_bundle = load_model_bundle
verify_bundle = verify_model_bundle


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "BundleIntegrityError",
    "LoadedBundle",
    "create_model_bundle",
    "export_bundle",
    "load_bundle",
    "load_model_bundle",
    "verify_bundle",
    "verify_model_bundle",
]
