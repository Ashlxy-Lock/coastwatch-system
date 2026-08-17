"""Typed command-line orchestration for the isolated ImpactNet v2 pipeline."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar, cast

import numpy as np
import pandas as pd
import typer
from numpy.typing import NDArray
from pydantic import BaseModel

from . import __version__

app = typer.Typer(
    name="cwml",
    help="Leakage-aware CoastWatch ImpactNet v2 research commands.",
    no_args_is_help=True,
    invoke_without_command=True,
    pretty_exceptions_enable=False,
)
audit_app = typer.Typer(help="Read-only regression and provenance audits.", no_args_is_help=True)
config_app = typer.Typer(
    help="Validate fully resolved ImpactNet configuration.", no_args_is_help=True
)
data_app = typer.Typer(help="Create or import versioned datasets.", no_args_is_help=True)
dataset_app = typer.Typer(help="Inspect and audit canonical datasets.", no_args_is_help=True)
export_app = typer.Typer(help="Verify or export non-pickle model bundles.", no_args_is_help=True)
sites_app = typer.Typer(
    help="Build and validate human-reviewed site mappings.", no_args_is_help=True
)
e2e_app = typer.Typer(help="Run synthetic-only engineering proofs.", no_args_is_help=True)
labels_app = typer.Typer(help="Build and validate reviewed event labels.", no_args_is_help=True)
train_app = typer.Typer(help="Fit versioned models from dataset artifacts.", no_args_is_help=True)
evaluate_app = typer.Typer(
    help="Evaluate validation, frozen test, and spatial generalisation.",
    no_args_is_help=True,
)
calibrate_app = typer.Typer(
    help="Fit validation-only probability calibration.", no_args_is_help=True
)
thresholds_app = typer.Typer(
    help="Select validation-only alert operating points.", no_args_is_help=True
)
replay_app = typer.Typer(help="Replay archived feature requests offline.", no_args_is_help=True)
monitor_app = typer.Typer(
    help="Build report-only monitoring, delayed-label, and drift artifacts.",
    no_args_is_help=True,
)
app.add_typer(audit_app, name="audit")
app.add_typer(config_app, name="config")
app.add_typer(data_app, name="data")
app.add_typer(dataset_app, name="dataset")
app.add_typer(export_app, name="export")
app.add_typer(sites_app, name="sites")
app.add_typer(e2e_app, name="e2e")
app.add_typer(labels_app, name="labels")
app.add_typer(train_app, name="train")
app.add_typer(evaluate_app, name="evaluate")
app.add_typer(calibrate_app, name="calibrate")
app.add_typer(thresholds_app, name="thresholds")
app.add_typer(replay_app, name="replay")
app.add_typer(monitor_app, name="monitor")


T = TypeVar("T")


LEGACY_SHA256 = {
    "ml/data/processed/coastal_history.csv.gz": (
        "9f1680094e6a9af79023635934df0b297489178230b1b6f08fb74570b34e7d1b"
    ),
    "ml/reports/coastal_risk_v1_metrics.json": (
        "34b978f0f9f1990ef16754b4946c67fbd51fdd99438fa1a0c90b29b9181919f4"
    ),
    "server/models/coastal_risk_v1.json": (
        "a766e49533176a085ecf4ee61e226cff42c961fd3f4534ffb20eecb8c1102680"
    ),
    "ml/coastal_risk/locations.json": (
        "dc8ad17d5e649f2011cb0ee2ded7f7efb2b9a8fc83539aee6ef3774e3e4149b9"
    ),
    "ml/coastal_risk/train.py": (
        "4ddc55112186aa0fcf9c200e815febbc00c4fddce70c16ea195a0fe70b8b8a3c"
    ),
    "server/app/risk_model.py": (
        "055f3d68b31993f5945a9e9a5e9267a3ef329464818e9ad319ecdab9e0bd7566"
    ),
    "website/app/model.ts": ("8084e346a9a197ef3ed9501e65d28df967642cd7a2f85ac0c125732950cb30b3"),
    "website/app/page.tsx": ("9fbd1a83c2f7c18fbf23332dec9bd56a16f37a9405117c43b6c499ee4867f915"),
}


def _json_ready(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _json_ready(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if value is pd.NA:
        return None
    return value


def _emit(command: str, status: str, *, error_stream: bool = False, **fields: Any) -> None:
    record = {
        "command": command,
        "status": status,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        **fields,
    }
    typer.echo(
        json.dumps(_json_ready(record), ensure_ascii=False, sort_keys=True, allow_nan=False),
        err=error_stream,
    )


def _run(command: str, operation: Callable[[], T]) -> T:
    try:
        return operation()
    except typer.Exit:
        raise
    except Exception as error:
        _emit(
            command,
            "error",
            error_stream=True,
            error_type=type(error).__name__,
            message=str(error),
        )
        raise typer.Exit(code=1) from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_root(explicit: Path | None = None) -> Path:
    starts = [explicit] if explicit is not None else [Path.cwd(), Path(__file__).resolve()]
    for start in starts:
        candidate = start.resolve()
        if candidate.is_file():
            candidate = candidate.parent
        for current in (candidate, *candidate.parents):
            if (current / "ml" / "coastal_risk").is_dir() and (current / "server").is_dir():
                return current
    raise FileNotFoundError(
        "could not locate the CoastWatch workspace (expected ml/coastal_risk and server)"
    )


def _resolve_dataset(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    if not candidate.is_dir():
        raise FileNotFoundError(
            f"dataset directory does not exist: {candidate}; this CLI does not guess a real "
            "dataset from an unresolved identifier"
        )
    return candidate


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _table_summary(path: Path) -> dict[str, Any]:
    import pyarrow.parquet as parquet

    parquet_file = parquet.ParquetFile(path)
    columns = list(parquet_file.schema_arrow.names)
    time_columns = [
        name
        for name in (
            "timestamp_utc",
            "prediction_time_utc",
            "issue_time_utc",
            "valid_time_utc",
            "onset_time_utc",
        )
        if name in columns
    ]
    ranges: dict[str, dict[str, str | None]] = {}
    for column in time_columns:
        values = pd.to_datetime(pd.read_parquet(path, columns=[column])[column], utc=True)
        ranges[column] = {
            "min": None if values.dropna().empty else values.min().isoformat(),
            "max": None if values.dropna().empty else values.max().isoformat(),
        }
    return {
        "filename": path.name,
        "rows": int(parquet_file.metadata.num_rows),
        "columns": columns,
        "time_ranges_utc": ranges,
        "sha256": _sha256(path),
    }


def _dataset_summary(path: Path) -> dict[str, Any]:
    tables = {item.stem: _table_summary(item) for item in sorted(path.glob("*.parquet"))}
    if not tables:
        raise FileNotFoundError(f"dataset contains no Parquet tables: {path}")
    marker_path = path / "SYNTHETIC_ONLY.json"
    marker = _read_json(marker_path) if marker_path.is_file() else None
    integrity_errors: list[str] = []
    if marker is not None:
        inventory = marker.get("tables")
        if not isinstance(inventory, dict):
            integrity_errors.append("synthetic marker has no table inventory")
        else:
            for name, expected in inventory.items():
                if name not in tables:
                    integrity_errors.append(f"marker table is missing: {name}")
                    continue
                if not isinstance(expected, dict):
                    integrity_errors.append(f"invalid marker record: {name}")
                    continue
                if expected.get("sha256") != tables[name]["sha256"]:
                    integrity_errors.append(f"checksum mismatch: {name}")
                if expected.get("rows") != tables[name]["rows"]:
                    integrity_errors.append(f"row-count mismatch: {name}")
    return {
        "dataset": str(path),
        "synthetic_data": bool(marker and marker.get("synthetic_data") is True),
        "scientific_use_allowed": None if marker is None else marker.get("scientific_use_allowed"),
        "tables": tables,
        "integrity_valid": not integrity_errors,
        "integrity_errors": integrity_errors,
    }


def _raw_forecast_audit(forecasts: pd.DataFrame) -> dict[str, Any]:
    required = {"site_id", "issue_time_utc", "valid_time_utc"}
    missing = required.difference(forecasts.columns)
    if missing:
        raise KeyError(f"forecast table missing columns: {sorted(missing)}")
    issue = pd.to_datetime(forecasts["issue_time_utc"], utc=True, errors="raise")
    valid = pd.to_datetime(forecasts["valid_time_utc"], utc=True, errors="raise")
    bad = issue >= valid
    if bad.any():
        examples = forecasts.loc[bad, list(required)].head(5).to_dict(orient="records")
        raise ValueError(f"forecast rows require issue_time < valid_time: {examples}")
    result: dict[str, Any] = {
        "valid": True,
        "rows": int(len(forecasts)),
        "rule_checked": "issue_time_utc < valid_time_utc",
    }
    if "lead_hours" in forecasts:
        calculated = (valid - issue).dt.total_seconds() / 3600.0
        declared = pd.to_numeric(forecasts["lead_hours"], errors="coerce")
        mismatch = declared.isna() | ~np.isclose(calculated, declared, rtol=0.0, atol=1e-6)
        if mismatch.any():
            raise ValueError(f"forecast lead_hours mismatch on {int(mismatch.sum())} rows")
        result["lead_hours_valid"] = True
    return result


def _asof_selection_audit(
    samples: pd.DataFrame,
    forecasts: pd.DataFrame,
    *,
    horizon_hours: int,
    max_samples: int,
) -> dict[str, Any]:
    from .data.temporal import audit_forecast_asof

    required = {"site_id", "prediction_time_utc"}
    missing = required.difference(samples.columns)
    if missing:
        raise KeyError(f"sample index missing columns: {sorted(missing)}")
    base = samples.copy()
    if "split" in base:
        base = base[base["split"].astype(str).isin(["train", "validation", "test"])]
    base = base[["site_id", "prediction_time_utc"]].drop_duplicates().reset_index(drop=True)
    total_samples = len(base)
    if total_samples == 0:
        raise ValueError("sample index has no train/validation/test prediction rows to audit")
    if max_samples and total_samples > max_samples:
        indices: NDArray[np.int64] = np.linspace(
            0, total_samples - 1, num=max_samples, dtype=np.int64
        )
        base = base.iloc[np.unique(indices)].reset_index(drop=True)
    base["prediction_time_utc"] = pd.to_datetime(
        base["prediction_time_utc"], utc=True, errors="raise"
    )
    base["request_id"] = np.arange(len(base), dtype=np.int64)
    requests = base.loc[base.index.repeat(horizon_hours)].reset_index(drop=True)
    requests["requested_lead_hour"] = np.tile(
        np.arange(1, horizon_hours + 1, dtype=np.int16), len(base)
    )
    requests["valid_time_utc"] = requests["prediction_time_utc"] + pd.to_timedelta(
        requests["requested_lead_hour"], unit="h"
    )
    candidates = forecasts[["site_id", "issue_time_utc", "valid_time_utc"]].copy()
    candidates["issue_time_utc"] = pd.to_datetime(
        candidates["issue_time_utc"], utc=True, errors="raise"
    )
    candidates["valid_time_utc"] = pd.to_datetime(
        candidates["valid_time_utc"], utc=True, errors="raise"
    )
    joined = requests.merge(candidates, on=["site_id", "valid_time_utc"], how="left")
    eligible = joined[
        joined["issue_time_utc"].notna()
        & (joined["issue_time_utc"] <= joined["prediction_time_utc"])
    ]
    selected = (
        eligible.sort_values(
            ["request_id", "requested_lead_hour", "issue_time_utc"], kind="mergesort"
        )
        .groupby(["request_id", "requested_lead_hour"], sort=False, as_index=False)
        .tail(1)
    )
    check = audit_forecast_asof(selected, prediction_time_col="prediction_time_utc")
    requested_rows = int(len(requests))
    return {
        **check,
        "samples_available": int(total_samples),
        "samples_checked": int(len(base)),
        "requested_leads": requested_rows,
        "available_leads": int(len(selected)),
        "missing_leads": requested_rows - int(len(selected)),
        "sample_limited": bool(max_samples and total_samples > max_samples),
        "rule_checked": "issue_time_utc <= prediction_time_utc < valid_time_utc",
    }


def _manual_source_import(
    *,
    command: str,
    adapter: Any,
    input_path: Path,
    table_name: str,
    parse_method: str = "parse",
    parse_kwargs: dict[str, Any] | None = None,
    dry_run: bool,
) -> None:
    """Preserve raw bytes, parse once, and write a versioned Parquet table."""

    source = input_path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if dry_run:
        _emit(
            command,
            "planned",
            input=source,
            data_root=adapter.data_root,
            source_name=adapter.metadata.name,
            parser_version=adapter.parser_version,
            dry_run=True,
        )
        return
    imported = adapter.import_file(source)
    parser = getattr(adapter, parse_method)
    frame = parser(imported.raw_path, **(parse_kwargs or {}))
    output = adapter.write_versioned_frame(
        frame,
        raw_sha256=imported.sha256,
        table_name=table_name,
    )
    _emit(
        command,
        "ok",
        input=source,
        raw_path=imported.raw_path,
        raw_manifest=imported.manifest_path,
        raw_sha256=imported.sha256,
        raw_created=imported.created,
        parsed_output=output,
        rows=len(frame),
        source_name=adapter.metadata.name,
        synthetic_data=False,
    )


@app.callback()
def root_callback(
    version: Annotated[
        bool,
        typer.Option("--version", help="Print the installed CLI version and exit."),
    ] = False,
) -> None:
    if version:
        _emit("version", "ok", version=__version__)
        raise typer.Exit()


@audit_app.command("legacy")
def audit_legacy(
    workspace: Annotated[
        Path | None,
        typer.Option("--workspace", help="Workspace root or its ml subdirectory."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Audit is read-only; this records intent explicitly."),
    ] = False,
) -> None:
    """Verify immutable v1 files against the pre-v2 SHA-256 anchors."""

    command = "audit legacy"

    def operation() -> None:
        root = _workspace_root(workspace)
        records: list[dict[str, Any]] = []
        for relative, expected in LEGACY_SHA256.items():
            path = root / relative
            actual = _sha256(path) if path.is_file() else None
            records.append(
                {
                    "path": relative,
                    "expected_sha256": expected,
                    "actual_sha256": actual,
                    "valid": actual == expected,
                }
            )
        valid = all(record["valid"] for record in records)
        _emit(
            command,
            "ok" if valid else "error",
            error_stream=not valid,
            workspace=root,
            dry_run=dry_run,
            read_only=True,
            valid=valid,
            files=records,
        )
        if not valid:
            raise typer.Exit(code=1)

    _run(command, operation)


@config_app.command("validate")
def config_validate(
    config: Annotated[Path, typer.Option("--config", help="YAML configuration to validate.")],
    resolved_output: Annotated[
        Path | None,
        typer.Option("--resolved-output", help="Optional path for the resolved YAML."),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Allow replacing an existing resolved-output file."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate but do not write resolved-output."),
    ] = False,
) -> None:
    """Reject unknown, unresolved, temporally unsafe, or non-UTC configuration."""

    command = "config validate"

    def operation() -> None:
        from .config import load_config, write_resolved_config

        resolved = load_config(config)
        output = resolved_output.resolve() if resolved_output is not None else None
        if output is not None and output.exists() and not overwrite and not dry_run:
            raise FileExistsError(f"refusing to overwrite resolved config: {output}")
        if output is not None and not dry_run:
            write_resolved_config(resolved, output)
        _emit(
            command,
            "ok",
            config=config.resolve(),
            model_name=resolved.model_name,
            model_variant=resolved.mode.model_variant,
            data_mode=resolved.mode.data_mode,
            label_mode=resolved.scope.label_mode,
            shadow_mode=resolved.project.shadow_mode,
            synthetic_data=resolved.project.synthetic_data,
            dry_run=dry_run,
            resolved_output=output,
            wrote_resolved_output=bool(output is not None and not dry_run),
        )

    _run(command, operation)


@data_app.command("synthetic")
def data_synthetic(
    output: Annotated[Path, typer.Option("--output", help="New dataset directory to create.")],
    start_utc: Annotated[
        str,
        typer.Option("--start-utc", help="Timezone-aware first synthetic observation."),
    ] = "2025-01-01T00:00:00Z",
    duration_days: Annotated[
        int,
        typer.Option("--duration-days", min=5, help="Synthetic coverage in days."),
    ] = 180,
    seed: Annotated[int, typer.Option("--seed", help="Deterministic generator seed.")] = 20260813,
    sample_stride_hours: Annotated[
        int,
        typer.Option("--sample-stride-hours", min=1, help="Window-index sampling stride."),
    ] = 6,
    include_sample_index: Annotated[
        bool,
        typer.Option(
            "--include-sample-index/--no-sample-index",
            help="Also write the leakage-auditable lazy-window index.",
        ),
    ] = True,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate the plan without generating or writing data."),
    ] = False,
) -> None:
    """Write deterministic engineering fixtures with explicit synthetic markers."""

    command = "data synthetic"

    def operation() -> None:
        from .data.synthetic import build_synthetic_sample_index, generate_synthetic_dataset

        destination = output.expanduser().resolve()
        if destination.exists():
            raise FileExistsError(f"refusing to replace an existing dataset path: {destination}")
        if dry_run:
            pd.Timestamp(start_utc).tz_convert("UTC")
            _emit(
                command,
                "planned",
                output=destination,
                start_utc=start_utc,
                duration_days=duration_days,
                seed=seed,
                sample_stride_hours=sample_stride_hours,
                include_sample_index=include_sample_index,
                dry_run=True,
                synthetic_data=True,
                scientific_use_allowed=False,
            )
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent)
        )
        try:
            bundle = generate_synthetic_dataset(
                start_utc=start_utc,
                duration_days=duration_days,
                seed=seed,
            )
            bundle.write(staging)
            if include_sample_index:
                samples = build_synthetic_sample_index(
                    bundle,
                    stride_hours=sample_stride_hours,
                )
                sample_path = staging / "sample_index.parquet"
                samples.to_parquet(sample_path, index=False, engine="pyarrow")
                marker_path = staging / "SYNTHETIC_ONLY.json"
                marker = _read_json(marker_path)
                marker.setdefault("tables", {})["sample_index"] = {
                    "filename": sample_path.name,
                    "rows": int(len(samples)),
                    "sha256": _sha256(sample_path),
                }
                marker_path.write_text(
                    json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            summary = _dataset_summary(staging)
            staging.rename(destination)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        _emit(
            command,
            "ok",
            output=destination,
            dry_run=False,
            synthetic_data=True,
            scientific_use_allowed=False,
            tables={name: table["rows"] for name, table in summary["tables"].items()},
        )

    _run(command, operation)


@data_app.command("import-historic-warnings")
def import_historic_warnings(
    input_path: Annotated[Path, typer.Option("--input", help="Downloaded EA ZIP/CSV/JSON.")],
    data_root: Annotated[Path, typer.Option("--data-root", help="ImpactNet data root.")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Import official-warning evidence; this does not create impact labels."""

    command = "data import-historic-warnings"

    def operation() -> None:
        from .data.sources import EAHistoricWarningsAdapter

        _manual_source_import(
            command=command,
            adapter=EAHistoricWarningsAdapter(data_root),
            input_path=input_path,
            table_name="historic_warnings",
            dry_run=dry_run,
        )

    _run(command, operation)


@data_app.command("import-warning-areas")
def import_warning_areas(
    input_path: Annotated[Path, typer.Option("--input", help="EA FWA geospatial file.")],
    data_root: Annotated[Path, typer.Option("--data-root", help="ImpactNet data root.")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Import Flood Warning Areas with explicit CRS checks."""

    command = "data import-warning-areas"

    def operation() -> None:
        from .data.sources import WarningAreasManualAdapter

        _manual_source_import(
            command=command,
            adapter=WarningAreasManualAdapter(data_root),
            input_path=input_path,
            table_name="warning_areas",
            dry_run=dry_run,
        )

    _run(command, operation)


@data_app.command("import-flood-outlines")
def import_flood_outlines(
    input_path: Annotated[Path, typer.Option("--input", help="Historic flood outlines file.")],
    data_root: Annotated[Path, typer.Option("--data-root", help="ImpactNet data root.")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Import flood-outline evidence without inventing onset hours."""

    command = "data import-flood-outlines"

    def operation() -> None:
        from .data.sources import FloodOutlinesManualAdapter

        _manual_source_import(
            command=command,
            adapter=FloodOutlinesManualAdapter(data_root),
            input_path=input_path,
            table_name="flood_outlines",
            dry_run=dry_run,
        )

    _run(command, operation)


@data_app.command("import-ea-tide-archive")
def import_ea_tide_archive(
    input_path: Annotated[Path, typer.Option("--input", help="EA tide CSV/JSON/ZIP.")],
    data_root: Annotated[Path, typer.Option("--data-root", help="ImpactNet data root.")],
    station_id: Annotated[str | None, typer.Option("--station-id")] = None,
    site_id: Annotated[str | None, typer.Option("--site-id")] = None,
    coastal_zone_id: Annotated[str | None, typer.Option("--coastal-zone-id")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Import tide observations while preserving local/mAOD datum semantics."""

    command = "data import-ea-tide-archive"

    def operation() -> None:
        from .data.sources import EATideGaugeArchiveAdapter

        _manual_source_import(
            command=command,
            adapter=EATideGaugeArchiveAdapter(data_root),
            input_path=input_path,
            table_name="ea_tide_observations",
            parse_kwargs={
                "station_id": station_id,
                "site_id": site_id,
                "coastal_zone_id": coastal_zone_id,
            },
            dry_run=dry_run,
        )

    _run(command, operation)


@data_app.command("import-wavenet")
def import_wavenet(
    input_path: Annotated[Path, typer.Option("--input", help="WaveNet CSV/ZIP archive.")],
    data_root: Annotated[Path, typer.Option("--data-root", help="ImpactNet data root.")],
    direction_reference: Annotated[str, typer.Option("--direction-reference")] = "unknown",
    direction_convention: Annotated[str, typer.Option("--direction-convention")] = "unknown",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Import WaveNet data without guessing direction reference or convention."""

    command = "data import-wavenet"

    def operation() -> None:
        from .data.sources import WaveNetManualArchiveAdapter

        _manual_source_import(
            command=command,
            adapter=WaveNetManualArchiveAdapter(data_root),
            input_path=input_path,
            table_name="wavenet_observations",
            parse_method="parse_csv",
            parse_kwargs={
                "direction_reference": direction_reference,
                "direction_convention": direction_convention,
            },
            dry_run=dry_run,
        )

    _run(command, operation)


@data_app.command("import-ntslf-forecast")
def import_ntslf_forecast(
    input_path: Annotated[Path, typer.Option("--input", help="Archived NTSLF issued runs.")],
    data_root: Annotated[Path, typer.Option("--data-root", help="ImpactNet data root.")],
    default_site_id: Annotated[str | None, typer.Option("--default-site-id")] = None,
    default_model_run_id: Annotated[str | None, typer.Option("--default-model-run-id")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Import genuine issued forecasts with issue/valid-time validation."""

    command = "data import-ntslf-forecast"

    def operation() -> None:
        from .data.sources import NTSLFForecastManualAdapter

        _manual_source_import(
            command=command,
            adapter=NTSLFForecastManualAdapter(data_root),
            input_path=input_path,
            table_name="ntslf_issued_forecasts",
            parse_kwargs={
                "default_site_id": default_site_id,
                "default_model_run_id": default_model_run_id,
            },
            dry_run=dry_run,
        )

    _run(command, operation)


@data_app.command("import-static-geo")
def import_static_geo(
    input_path: Annotated[Path, typer.Option("--input", help="Reviewed static feature file.")],
    data_root: Annotated[Path, typer.Option("--data-root", help="ImpactNet data root.")],
    source_kind: Annotated[str, typer.Option("--source-kind", help="static, aims, or rofrs.")] = (
        "static"
    ),
    source_version: Annotated[str | None, typer.Option("--source-version")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Import reviewed zone-level static data with snapshot and datum fields."""

    command = "data import-static-geo"

    def operation() -> None:
        from .data.sources import (
            AIMSStaticGeoAdapter,
            RoFRSStaticGeoAdapter,
            StaticGeoManualAdapter,
        )

        adapters = {
            "static": StaticGeoManualAdapter,
            "aims": AIMSStaticGeoAdapter,
            "rofrs": RoFRSStaticGeoAdapter,
        }
        if source_kind not in adapters:
            raise ValueError("source-kind must be static, aims, or rofrs")
        _manual_source_import(
            command=command,
            adapter=adapters[source_kind](data_root),
            input_path=input_path,
            table_name=f"{source_kind}_static_features",
            parse_kwargs={"source_version": source_version},
            dry_run=dry_run,
        )

    _run(command, operation)


@data_app.command("sync-ea-tide")
def sync_ea_tide(
    data_root: Annotated[Path, typer.Option("--data-root", help="ImpactNet data root.")],
    station_id: Annotated[str, typer.Option("--station-id")],
    start: Annotated[str | None, typer.Option("--start", help="Timezone-aware UTC.")] = None,
    end: Annotated[str | None, typer.Option("--end", help="Timezone-aware UTC.")] = None,
    allow_network: Annotated[
        bool, typer.Option("--allow-network", help="Explicitly permit the EA request.")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Opt-in EA tide fetch; empty responses create no raw files."""

    command = "data sync-ea-tide"

    def operation() -> None:
        from .data.sources import EATideGaugeRealtimeAdapter

        if dry_run:
            _emit(
                command,
                "planned",
                station_id=station_id,
                start=start,
                end=end,
                allow_network=allow_network,
                data_root=data_root.resolve(),
                dry_run=True,
            )
            return
        adapter = EATideGaugeRealtimeAdapter(data_root, allow_network=allow_network)
        imported = adapter.fetch_readings(station_id, since=start, until=end)
        frame = adapter.parse(imported.raw_path, station_id=station_id)
        output = adapter.write_versioned_frame(
            frame,
            raw_sha256=imported.sha256,
            table_name="ea_tide_realtime",
        )
        _emit(
            command,
            "ok",
            station_id=station_id,
            raw_path=imported.raw_path,
            raw_manifest=imported.manifest_path,
            parsed_output=output,
            rows=len(frame),
            synthetic_data=False,
        )

    _run(command, operation)


@dataset_app.command("summary")
def dataset_summary(
    dataset: Annotated[Path, typer.Option("--dataset", help="Canonical dataset directory.")],
) -> None:
    """Report Parquet schemas, row counts, coverage, hashes, and synthetic status."""

    command = "dataset summary"

    def operation() -> None:
        summary = _dataset_summary(_resolve_dataset(dataset))
        valid = bool(summary["integrity_valid"])
        _emit(command, "ok" if valid else "error", error_stream=not valid, **summary)
        if not valid:
            raise typer.Exit(code=1)

    _run(command, operation)


@sites_app.command("review-report")
def sites_review_report(
    legacy_locations: Annotated[
        Path, typer.Option("--legacy-locations", help="Exact v1 locations.json.")
    ],
    sites_config: Annotated[Path, typer.Option("--sites-config", help="v2 sites YAML.")],
    output: Annotated[Path, typer.Option("--output", help="New human-review CSV.")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Generate an unapproved mapping review; never auto-approve a nearest station."""

    command = "sites review-report"

    def operation() -> None:
        from .data.spatial import build_site_mapping_review, write_site_mapping_review

        frame = build_site_mapping_review(legacy_locations, sites_config)
        if not dry_run:
            write_site_mapping_review(frame, output)
        _emit(
            command,
            "planned" if dry_run else "ok",
            rows=len(frame),
            active_candidates=int(frame["active_candidate"].sum()),
            approved=int(frame["approved"].sum()),
            output=output.resolve(),
            dry_run=dry_run,
        )

    _run(command, operation)


@sites_app.command("validate-review")
def sites_validate_review(
    review_csv: Annotated[Path, typer.Option("--review-csv", help="Reviewed mapping CSV.")],
) -> None:
    """Fail if an approved mapping violates distance, coast, CRS, or review guards."""

    command = "sites validate-review"

    def operation() -> None:
        from .data.spatial import validate_site_mappings

        frame = pd.read_csv(review_csv)
        approved = validate_site_mappings(frame)
        _emit(
            command,
            "ok",
            review_csv=review_csv.resolve(),
            approved_site_ids=[record.site_id for record in approved],
            approved_count=len(approved),
        )

    _run(command, operation)


@e2e_app.command("synthetic")
def e2e_synthetic(
    output: Annotated[Path, typer.Option("--output", help="New run directory.")],
    duration_days: Annotated[int, typer.Option("--duration-days", min=12)] = 180,
    epochs: Annotated[int, typer.Option("--epochs", min=1)] = 2,
    batch_size: Annotated[int, typer.Option("--batch-size", min=1)] = 64,
    seed: Annotated[int, typer.Option("--seed")] = 20260813,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Run generator→train→calibrate→test→bundle→Shadow API on CPU."""

    command = "e2e synthetic"

    def operation() -> None:
        from .synthetic_e2e import SyntheticE2EConfig, run_synthetic_e2e

        config = SyntheticE2EConfig(
            duration_days=duration_days,
            epochs=epochs,
            batch_size=batch_size,
            seed=seed,
        )
        if dry_run:
            _emit(
                command,
                "planned",
                output=output.resolve(),
                config=asdict(config),
                dry_run=True,
                synthetic_only=True,
                scientific_result=False,
            )
            return
        result = run_synthetic_e2e(output, config)
        _emit(
            command,
            "ok",
            run_directory=result.run_directory,
            bundle_directory=result.bundle_directory,
            run_manifest=result.run_manifest_path,
            test_metrics=result.test_metrics_path,
            model_sha256=result.model_sha256,
            api_status_code=result.api_status_code,
            synthetic_only=True,
            scientific_result=False,
        )

    _run(command, operation)


@dataset_app.command("audit-leakage")
def dataset_audit_leakage(
    dataset: Annotated[Path, typer.Option("--dataset", help="Canonical dataset directory.")],
    max_samples: Annotated[
        int,
        typer.Option(
            "--max-samples",
            min=0,
            help="Maximum prediction rows for as-of audit; 0 checks all rows.",
        ),
    ] = 5000,
) -> None:
    """Audit raw forecast timing, split purge/groups, and selected forecast availability."""

    command = "dataset audit-leakage"

    def operation() -> None:
        from .data.split import GlobalSplitConfig, audit_global_split

        root = _resolve_dataset(dataset)
        forecast_path = root / "forecasts_hourly.parquet"
        if not forecast_path.is_file():
            raise FileNotFoundError(f"required forecast table is missing: {forecast_path}")
        forecasts = pd.read_parquet(forecast_path)
        audits: dict[str, Any] = {"raw_forecasts": _raw_forecast_audit(forecasts)}
        sample_path = root / "sample_index.parquet"
        marker_path = root / "SYNTHETIC_ONLY.json"
        complete = True
        if sample_path.is_file():
            samples = pd.read_parquet(sample_path)
            horizon_hours = 24
            marker = _read_json(marker_path) if marker_path.is_file() else {}
            boundaries = marker.get("default_split_boundaries")
            if isinstance(boundaries, dict):
                split_config = GlobalSplitConfig(
                    train_end_utc=boundaries["train_end_utc"],
                    validation_end_utc=boundaries["validation_end_utc"],
                    test_end_utc=boundaries["test_end_utc"],
                    forecast_horizon_hours=horizon_hours,
                )
                audits["split_and_groups"] = audit_global_split(samples, split_config)
            else:
                complete = False
                audits["split_and_groups"] = {
                    "status": "not_checked",
                    "reason": "dataset has no recorded split boundaries",
                }
            audits["forecast_asof_selection"] = _asof_selection_audit(
                samples,
                forecasts,
                horizon_hours=horizon_hours,
                max_samples=max_samples,
            )
        else:
            complete = False
            audits["split_and_groups"] = {
                "status": "not_checked",
                "reason": "sample_index.parquet is absent",
            }
            audits["forecast_asof_selection"] = {
                "status": "not_checked",
                "reason": "sample_index.parquet is absent; raw archives have no prediction time",
            }
        _emit(
            command,
            "ok" if complete else "partial",
            dataset=root,
            complete=complete,
            leakage_found=False,
            audits=audits,
        )

    _run(command, operation)


@dataset_app.command("loso-summary")
def dataset_loso_summary(
    dataset: Annotated[Path, typer.Option("--dataset", help="Canonical dataset directory.")],
) -> None:
    """Summarise leave-one-site-out folds without fitting or evaluating a model."""

    command = "dataset loso-summary"

    def operation() -> None:
        from .data import build_leave_one_site_out_folds

        root = _resolve_dataset(dataset)
        sample_path = root / "sample_index.parquet"
        if not sample_path.is_file():
            raise FileNotFoundError(f"required sample index is missing: {sample_path}")
        samples = pd.read_parquet(sample_path)
        unavailable_reason: str | None
        try:
            folds = build_leave_one_site_out_folds(samples)
            unavailable_reason = None
        except ValueError as error:
            if "has no validation rows" not in str(error):
                raise
            unavailable_reason = str(error)
            sites = sorted(samples["site_id"].dropna().astype(str).unique())
            site_values = samples["site_id"].astype(str)
            split_values = samples["split"].astype(str)
            folds = {}
            for held_out in sites:
                fold = samples.copy()
                fold["held_out_site_id"] = held_out
                fold["cv_split"] = "excluded"
                fold.loc[
                    site_values.ne(held_out) & split_values.eq("train"),
                    "cv_split",
                ] = "train"
                fold.loc[
                    site_values.eq(held_out) & split_values.eq("validation"),
                    "cv_split",
                ] = "validation"
                folds[held_out] = fold
        summaries: list[dict[str, Any]] = []
        for held_out, fold in folds.items():
            training = fold[fold["cv_split"].astype(str) == "train"]
            validation = fold[fold["cv_split"].astype(str) == "validation"]
            test_rows = fold[fold["split"].astype(str) == "test"]
            final_test_frozen = bool(test_rows["cv_split"].astype(str).eq("excluded").all())
            if not final_test_frozen:
                raise ValueError(f"LOSO fold {held_out!r} reused the frozen final test rows")
            summaries.append(
                {
                    "held_out_site_id": held_out,
                    "train_rows": int(len(training)),
                    "validation_rows": int(len(validation)),
                    "excluded_rows": int(fold["cv_split"].astype(str).eq("excluded").sum()),
                    "training_site_ids": sorted(training["site_id"].astype(str).unique()),
                    "validation_site_ids": sorted(validation["site_id"].astype(str).unique()),
                    "trainable": bool(len(training) and len(validation)),
                    "frozen_final_test_rows": int(len(test_rows)),
                    "final_test_frozen": final_test_frozen,
                }
            )
        _emit(
            command,
            "ok" if unavailable_reason is None else "partial",
            dataset=root,
            fold_count=len(summaries),
            folds=summaries,
            trained_models=0,
            final_test_evaluated=False,
            unavailable_reason=unavailable_reason,
        )

    _run(command, operation)


@labels_app.command("validate")
def labels_validate(
    input_path: Annotated[
        Path,
        typer.Option("--input", help="Canonical event catalog in Parquet, CSV, or JSON."),
    ],
) -> None:
    """Validate an event catalog without creating or inferring any labels."""

    command = "labels validate"

    def operation() -> None:
        from .workflow import validate_event_catalog_artifact

        result = validate_event_catalog_artifact(input_path)
        _emit(command, "ok", **result)

    _run(command, operation)


@labels_app.command("build-event-catalog")
def labels_build_event_catalog(
    input_path: Annotated[
        Path,
        typer.Option("--input", help="Reviewed event records in Parquet, CSV, or JSON."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", help="New canonical event_catalog.parquet path."),
    ],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Normalise reviewed records; never infer an event or confirmed impact."""

    command = "labels build-event-catalog"

    def operation() -> None:
        from .workflow import build_event_catalog_artifact

        result = build_event_catalog_artifact(input_path, output, dry_run=dry_run)
        _emit(command, "planned" if dry_run else "ok", dry_run=dry_run, **result)

    _run(command, operation)


@dataset_app.command("build")
def dataset_build(
    input_directory: Annotated[
        Path,
        typer.Option("--input", help="Directory containing the five canonical Parquet tables."),
    ],
    config: Annotated[Path, typer.Option("--config", help="Resolved experiment YAML.")],
    feature_schema: Annotated[
        Path,
        typer.Option("--feature-schema", help="Explicit JSON feature and target contract."),
    ],
    output: Annotated[Path, typer.Option("--output", help="New dataset artifact directory.")],
    stride_hours: Annotated[
        int,
        typer.Option("--stride-hours", min=1, help="Prediction-time sampling stride."),
    ] = 1,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Build labels, global time splits, and a lazy-window dataset artifact."""

    command = "dataset build"

    def operation() -> None:
        from .workflow import build_dataset_artifact

        result = build_dataset_artifact(
            input_directory,
            config,
            feature_schema,
            output,
            stride_hours=stride_hours,
            dry_run=dry_run,
        )
        _emit(command, "planned" if dry_run else "ok", dry_run=dry_run, **result)

    _run(command, operation)


@train_app.command("impactnet")
def train_impactnet(
    dataset: Annotated[Path, typer.Option("--dataset", help="Built dataset artifact.")],
    config: Annotated[Path, typer.Option("--config", help="Matching experiment YAML.")],
    output: Annotated[Path, typer.Option("--output", help="New run artifact directory.")],
    variant: Annotated[
        str | None,
        typer.Option("--variant", help="obs_only_tcn or hybrid_tcn; defaults to config."),
    ] = None,
    max_epochs: Annotated[int | None, typer.Option("--max-epochs", min=1)] = None,
    batch_size: Annotated[int | None, typer.Option("--batch-size", min=1)] = None,
    device: Annotated[str, typer.Option("--device")] = "cpu",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Fit ImpactNet from an immutable dataset contract."""

    command = "train impactnet"

    def operation() -> None:
        from .workflow import train_impactnet_artifact

        if variant not in {None, "obs_only_tcn", "hybrid_tcn"}:
            raise ValueError("variant must be obs_only_tcn or hybrid_tcn")
        resolved_variant = cast(
            Literal["obs_only_tcn", "hybrid_tcn"] | None,
            variant,
        )
        result = train_impactnet_artifact(
            dataset,
            config,
            output,
            variant=resolved_variant,
            max_epochs=max_epochs,
            batch_size=batch_size,
            device=device,
            dry_run=dry_run,
        )
        _emit(command, "planned" if dry_run else "ok", dry_run=dry_run, **result)

    _run(command, operation)


@train_app.command("baseline-logistic")
def train_baseline_logistic(
    dataset: Annotated[Path, typer.Option("--dataset", help="Built dataset artifact.")],
    config: Annotated[Path, typer.Option("--config", help="Matching experiment YAML.")],
    output: Annotated[Path, typer.Option("--output", help="New baseline run directory.")],
    include_future: Annotated[
        bool,
        typer.Option(
            "--include-future/--obs-only",
            help="Include issued-forecast summaries or fit an observation-only baseline.",
        ),
    ] = True,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Fit the deterministic logistic event baseline."""

    command = "train baseline-logistic"

    def operation() -> None:
        from .workflow import train_logistic_artifact

        result = train_logistic_artifact(
            dataset,
            config,
            output,
            include_future=include_future,
            dry_run=dry_run,
        )
        _emit(command, "planned" if dry_run else "ok", dry_run=dry_run, **result)

    _run(command, operation)


@calibrate_app.command("temperature")
def calibrate_temperature(
    run: Annotated[Path, typer.Option("--run", "--run-id", help="ImpactNet run directory.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", help="New calibration JSON; defaults inside the run."),
    ] = None,
    iterations: Annotated[int, typer.Option("--iterations", min=8)] = 96,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Fit one temperature on validation predictions only."""

    command = "calibrate temperature"

    def operation() -> None:
        from .workflow import calibrate_temperature_artifact

        destination = output or (run / "calibration.json")
        result = calibrate_temperature_artifact(
            run,
            destination,
            iterations=iterations,
            dry_run=dry_run,
        )
        _emit(command, "planned" if dry_run else "ok", dry_run=dry_run, **result)

    _run(command, operation)


@thresholds_app.command("select")
def thresholds_select(
    run: Annotated[Path, typer.Option("--run", "--run-id", help="ImpactNet run directory.")],
    calibration: Annotated[
        Path | None,
        typer.Option("--calibration", help="Calibration JSON; defaults inside the run."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="New thresholds JSON; defaults inside the run."),
    ] = None,
    candidates: Annotated[
        str | None,
        typer.Option("--candidates", help="Comma-separated probability thresholds."),
    ] = None,
    max_false_alert_episodes: Annotated[
        int | None,
        typer.Option("--max-false-alert-episodes", min=0),
    ] = None,
    max_false_alerts_per_site_month: Annotated[
        float | None,
        typer.Option("--max-false-alerts-per-site-month", min=0.0),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Choose alert operating points using validation episodes only."""

    command = "thresholds select"

    def operation() -> None:
        from .workflow import select_thresholds_artifact

        values: list[float] | None = None
        if candidates is not None:
            try:
                values = [float(value.strip()) for value in candidates.split(",") if value.strip()]
            except ValueError as error:
                raise ValueError("candidates must be comma-separated numbers") from error
            if not values:
                raise ValueError("candidates must contain at least one number")
        calibration_path = calibration or (run / "calibration.json")
        destination = output or (run / "thresholds.json")
        result = select_thresholds_artifact(
            run,
            calibration_path,
            destination,
            candidate_thresholds=values,
            max_false_alert_episodes=max_false_alert_episodes,
            max_false_alerts_per_site_month=max_false_alerts_per_site_month,
            dry_run=dry_run,
        )
        _emit(command, "planned" if dry_run else "ok", dry_run=dry_run, **result)

    _run(command, operation)


@evaluate_app.command("run")
def evaluate_run(
    run: Annotated[Path, typer.Option("--run", "--run-id", help="ImpactNet run directory.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", help="New metrics JSON; defaults inside the run."),
    ] = None,
    split: Annotated[
        str,
        typer.Option("--split", help="Only validation is allowed; use final-test separately."),
    ] = "validation",
    calibration: Annotated[Path | None, typer.Option("--calibration")] = None,
    thresholds: Annotated[Path | None, typer.Option("--thresholds")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Evaluate reusable validation predictions; frozen test is a separate command."""

    command = "evaluate run"

    def operation() -> None:
        from .workflow import evaluate_validation_artifact

        if split != "validation":
            raise ValueError(
                "evaluate run only accepts --split validation; use evaluate final-test"
            )
        destination = output or (run / "validation_metrics.json")
        result = evaluate_validation_artifact(
            run,
            destination,
            calibration_path=calibration,
            thresholds_path=thresholds,
            dry_run=dry_run,
        )
        _emit(command, "planned" if dry_run else "ok", dry_run=dry_run, **result)

    _run(command, operation)


@evaluate_app.command("final-test")
def evaluate_final_test(
    run: Annotated[Path, typer.Option("--run", "--run-id", help="ImpactNet run directory.")],
    calibration: Annotated[
        Path | None,
        typer.Option("--calibration", help="Frozen calibration JSON; defaults inside run."),
    ] = None,
    thresholds: Annotated[
        Path | None,
        typer.Option("--thresholds", help="Frozen threshold JSON; defaults inside run."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="New final-test directory; defaults inside run."),
    ] = None,
    device: Annotated[str, typer.Option("--device")] = "cpu",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Evaluate the frozen test split once and create an immutable lock."""

    command = "evaluate final-test"

    def operation() -> None:
        from .workflow import evaluate_final_test_artifact

        result = evaluate_final_test_artifact(
            run,
            calibration or (run / "calibration.json"),
            thresholds or (run / "thresholds.json"),
            output or (run / "final-test"),
            device=device,
            dry_run=dry_run,
        )
        _emit(command, "planned" if dry_run else "ok", dry_run=dry_run, **result)

    _run(command, operation)


@evaluate_app.command("leave-one-site-out")
def evaluate_leave_one_site_out(
    dataset: Annotated[Path, typer.Option("--dataset", help="Built dataset artifact.")],
    config: Annotated[Path, typer.Option("--config", help="Matching experiment YAML.")],
    output: Annotated[Path, typer.Option("--output", help="New LOSO artifact directory.")],
    variant: Annotated[
        str | None,
        typer.Option("--variant", help="obs_only_tcn or hybrid_tcn; defaults to config."),
    ] = None,
    max_epochs: Annotated[int | None, typer.Option("--max-epochs", min=1)] = None,
    batch_size: Annotated[int | None, typer.Option("--batch-size", min=1)] = None,
    device: Annotated[str, typer.Option("--device")] = "cpu",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Train spatial LOSO folds while leaving the global final test untouched."""

    command = "evaluate leave-one-site-out"

    def operation() -> None:
        from .workflow import run_loso_artifact

        if variant not in {None, "obs_only_tcn", "hybrid_tcn"}:
            raise ValueError("variant must be obs_only_tcn or hybrid_tcn")
        resolved_variant = cast(
            Literal["obs_only_tcn", "hybrid_tcn"] | None,
            variant,
        )
        result = run_loso_artifact(
            dataset,
            config,
            output,
            variant=resolved_variant,
            max_epochs=max_epochs,
            batch_size=batch_size,
            device=device,
            dry_run=dry_run,
        )
        _emit(command, "planned" if dry_run else "ok", dry_run=dry_run, **result)

    _run(command, operation)


@export_app.command("bundle")
def export_bundle(
    run: Annotated[Path, typer.Option("--run", "--run-id", help="ImpactNet run directory.")],
    output: Annotated[Path, typer.Option("--output", help="New model bundle directory.")],
    model_version: Annotated[str, typer.Option("--model-version")],
    coverage_scope: Annotated[
        str,
        typer.Option("--coverage-scope", help="Explicit geographic and temporal scope statement."),
    ],
    calibration: Annotated[
        Path | None,
        typer.Option("--calibration", help="Frozen calibration JSON; defaults inside run."),
    ] = None,
    thresholds: Annotated[
        Path | None,
        typer.Option("--thresholds", help="Frozen thresholds JSON; defaults inside run."),
    ] = None,
    model_card: Annotated[Path | None, typer.Option("--model-card")] = None,
    device: Annotated[str, typer.Option("--device")] = "cpu",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Export a safe bundle only after the one-time frozen test is complete."""

    command = "export bundle"

    def operation() -> None:
        from .workflow import export_bundle_artifact

        result = export_bundle_artifact(
            run,
            calibration or (run / "calibration.json"),
            thresholds or (run / "thresholds.json"),
            output,
            model_version=model_version,
            coverage_scope=coverage_scope,
            model_card_path=model_card,
            device=device,
            dry_run=dry_run,
        )
        _emit(command, "planned" if dry_run else "ok", dry_run=dry_run, **result)

    _run(command, operation)


@replay_app.command("shadow")
def replay_shadow(
    bundle: Annotated[Path, typer.Option("--bundle", help="Verified primary model bundle.")],
    input_path: Annotated[
        Path,
        typer.Option("--input", help="JSONL containing FeaturePredictionRequest objects."),
    ],
    output: Annotated[Path, typer.Option("--output", help="New prediction JSONL artifact.")],
    obs_only_bundle: Annotated[
        Path | None,
        typer.Option("--obs-only-bundle", help="Optional independent degraded bundle."),
    ] = None,
    device: Annotated[str, typer.Option("--device")] = "cpu",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Replay strict feature requests without opening a socket."""

    command = "replay shadow"

    def operation() -> None:
        from .workflow import replay_shadow_artifact

        result = replay_shadow_artifact(
            bundle,
            input_path,
            output,
            obs_only_bundle=obs_only_bundle,
            device=device,
            dry_run=dry_run,
        )
        _emit(command, "planned" if dry_run else "ok", dry_run=dry_run, **result)

    _run(command, operation)


def _declared_data_kind(value: Literal["auto", "synthetic", "real"]) -> bool | None:
    return None if value == "auto" else value == "synthetic"


@monitor_app.command("aggregate")
def monitor_aggregate(
    logs: Annotated[Path, typer.Option("--logs", help="Historical service JSONL logs.")],
    output: Annotated[Path, typer.Option("--output", help="Atomic monitoring report JSON.")],
    data_kind: Annotated[
        Literal["auto", "synthetic", "real"],
        typer.Option(
            "--data-kind",
            help="Use auto only when logs carry synthetic_data; never infer real data.",
        ),
    ] = "auto",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Aggregate freshness, quality, API, latency, OOD, and fallback evidence."""

    command = "monitor aggregate"

    def operation() -> None:
        from .monitoring import build_monitoring_report, write_atomic_hashed_report

        report = build_monitoring_report(
            logs,
            declared_synthetic_data=_declared_data_kind(data_kind),
        )
        artifact = write_atomic_hashed_report(output, report, dry_run=dry_run)
        _emit(
            command,
            "planned" if dry_run else "ok",
            dry_run=dry_run,
            report_type=report["report_type"],
            prediction_records=report["record_counts"]["predictions"],
            evidence_warning_count=len(report["evidence_warnings"]),
            synthetic_data=report["synthetic_data"],
            retraining_triggered=False,
            **artifact,
        )

    _run(command, operation)


@monitor_app.command("delayed-evaluate")
def monitor_delayed_evaluate(
    logs: Annotated[Path, typer.Option("--logs", help="Immutable prediction audit JSONL.")],
    event_catalog: Annotated[
        Path, typer.Option("--event-catalog", help="New reviewed event catalog.")
    ],
    output: Annotated[Path, typer.Option("--output", help="Atomic delayed-evaluation JSON.")],
    threshold: Annotated[float, typer.Option("--threshold", min=0.0, max=1.0)],
    sites: Annotated[
        Path | None,
        typer.Option("--sites", help="Reviewed site mapping when events lack site_id."),
    ] = None,
    horizon: Annotated[str, typer.Option("--horizon")] = "24h",
    window_days: Annotated[int, typer.Option("--window-days", min=1)] = 90,
    step_days: Annotated[int, typer.Option("--step-days", min=1)] = 30,
    merge_gap_hours: Annotated[int, typer.Option("--merge-gap-hours", min=0)] = 2,
    cooldown_hours: Annotated[int, typer.Option("--cooldown-hours", min=0)] = 6,
    lookahead_hours: Annotated[int, typer.Option("--lookahead-hours", min=1)] = 24,
    minimum_events: Annotated[int, typer.Option("--minimum-events", min=1)] = 5,
    data_kind: Annotated[
        Literal["auto", "synthetic", "real"],
        typer.Option(
            "--data-kind",
            help="Required when legacy logs omit synthetic_data; no classification is guessed.",
        ),
    ] = "auto",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Re-match historical predictions to delayed labels and update rolling metrics."""

    command = "monitor delayed-evaluate"

    def operation() -> None:
        from .monitoring import build_delayed_evaluation_report, write_atomic_hashed_report

        report = build_delayed_evaluation_report(
            logs,
            event_catalog,
            sites=sites,
            horizon=horizon,
            threshold=threshold,
            window_days=window_days,
            step_days=step_days,
            merge_gap_hours=merge_gap_hours,
            cooldown_hours=cooldown_hours,
            lookahead_hours=lookahead_hours,
            minimum_events_for_evidence=minimum_events,
            declared_synthetic_data=_declared_data_kind(data_kind),
        )
        artifact = write_atomic_hashed_report(output, report, dry_run=dry_run)
        _emit(
            command,
            "planned" if dry_run else "ok",
            dry_run=dry_run,
            report_type=report["report_type"],
            rolling_windows=len(report["rolling_event_metrics"]),
            confirmed_events=report["overall_event_metrics"]["confirmed_events"],
            insufficient_evidence=report["overall_event_metrics"]["insufficient_evidence"],
            synthetic_data=report["synthetic_data"],
            retraining_triggered=False,
            **artifact,
        )

    _run(command, operation)


@monitor_app.command("drift")
def monitor_drift(
    reference_logs: Annotated[
        Path,
        typer.Option(
            "--reference-logs",
            help="Frozen train/reference replay JSONL with optional input_summary evidence.",
        ),
    ],
    live_logs: Annotated[Path, typer.Option("--live-logs", help="Live Shadow audit JSONL.")],
    output: Annotated[Path, typer.Option("--output", help="Atomic drift report JSON.")],
    psi_review_threshold: Annotated[
        float, typer.Option("--psi-review-threshold", min=0.000001)
    ] = 0.2,
    missingness_review_delta: Annotated[
        float, typer.Option("--missingness-review-delta", min=0.0, max=1.0)
    ] = 0.1,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Report reference/live drift and provenance changes without retraining."""

    command = "monitor drift"

    def operation() -> None:
        from .monitoring import build_drift_report, write_atomic_hashed_report

        report = build_drift_report(
            reference_logs,
            live_logs,
            psi_review_threshold=psi_review_threshold,
            missingness_review_delta=missingness_review_delta,
        )
        artifact = write_atomic_hashed_report(output, report, dry_run=dry_run)
        _emit(
            command,
            "planned" if dry_run else "ok",
            dry_run=dry_run,
            report_type=report["report_type"],
            feature_count=len(report["feature_drift"]),
            site_count=len(report["site_specific_drift"]),
            evidence_warning_count=len(report["evidence_warnings"]),
            manual_review_required=report["manual_review_required"],
            retraining_triggered=False,
            **artifact,
        )

    _run(command, operation)


@export_app.command("verify")
def export_verify(
    bundle: Annotated[Path, typer.Option("--bundle", help="Versioned model bundle directory.")],
    load_model: Annotated[
        bool,
        typer.Option(
            "--load-model/--hashes-only",
            help="Also reconstruct architecture and load safetensors after hash verification.",
        ),
    ] = True,
    device: Annotated[
        str, typer.Option("--device", help="Torch device used for load check.")
    ] = "cpu",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Verification is read-only; this records intent."),
    ] = False,
) -> None:
    """Verify bundle inventory/hashes and, by default, safe model reconstruction."""

    command = "export verify"

    def operation() -> None:
        from .export.model_bundle import load_model_bundle, verify_model_bundle

        path = bundle.expanduser().resolve()
        manifest = verify_model_bundle(path)
        if load_model:
            load_model_bundle(path, device=device)
        _emit(
            command,
            "ok",
            bundle=path,
            dry_run=dry_run,
            read_only=True,
            hashes_valid=True,
            model_loaded=load_model,
            device=device if load_model else None,
            manifest=manifest,
        )

    _run(command, operation)


@app.command("serve")
def serve(
    bundle: Annotated[Path, typer.Option("--bundle", help="Primary verified model bundle.")],
    obs_only_bundle: Annotated[
        Path | None,
        typer.Option("--obs-only-bundle", help="Optional independently trained degraded bundle."),
    ] = None,
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8000,
    device: Annotated[str, typer.Option("--device", help="Torch inference device.")] = "cpu",
    allow_synthetic: Annotated[
        bool,
        typer.Option(
            "--allow-synthetic",
            help="Explicitly allow a synthetic engineering bundle to open a network socket.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Load and verify bundles without opening a socket."),
    ] = False,
) -> None:
    """Run the research-only FastAPI service; every response remains Shadow Mode."""

    command = "serve"

    def operation() -> None:
        from .logging_utils import uvicorn_json_log_config
        from .serve.app import create_app
        from .serve.model_loader import BundlePredictor

        predictor = BundlePredictor(
            bundle.expanduser().resolve(),
            obs_only=(
                obs_only_bundle.expanduser().resolve() if obs_only_bundle is not None else None
            ),
            device=device,
        )
        manifest = predictor.model_info()
        synthetic_data = bool(manifest.get("synthetic_data", False))
        if synthetic_data and not dry_run and not allow_synthetic:
            raise ValueError(
                "synthetic bundles require --allow-synthetic before opening a network socket"
            )
        _emit(
            command,
            "ready" if dry_run else "starting",
            bundle=bundle.expanduser().resolve(),
            obs_only_bundle=(
                obs_only_bundle.expanduser().resolve() if obs_only_bundle is not None else None
            ),
            loaded_models=predictor.loaded_versions,
            host=host,
            port=port,
            device=device,
            shadow_mode=True,
            synthetic_data=synthetic_data,
            allow_synthetic=allow_synthetic,
            dry_run=dry_run,
        )
        if dry_run:
            return
        import uvicorn

        uvicorn.run(
            create_app(predictor),
            host=host,
            port=port,
            log_config=uvicorn_json_log_config(),
        )

    _run(command, operation)


if __name__ == "__main__":  # pragma: no cover - convenience for python -m
    app()
