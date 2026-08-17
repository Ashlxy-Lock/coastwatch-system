from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from coastwatch_impact.cli import app
from coastwatch_impact.export.model_bundle import create_model_bundle
from coastwatch_impact.models.impactnet import ImpactNet, ImpactNetConfig

RUNNER = CliRunner()
ML_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = ML_ROOT.parent


def _records(result) -> list[dict]:
    records = []
    for stream in (result.stdout, result.stderr):
        for line in stream.splitlines():
            if line.startswith("{"):
                records.append(json.loads(line))
    return records


def _last_record(result) -> dict:
    records = _records(result)
    assert records, result.output
    return records[-1]


def _bundle(tmp_path: Path) -> Path:
    model = ImpactNet(
        ImpactNetConfig(
            past_feature_dim=2,
            forecast_feature_dim=0,
            static_feature_dim=1,
            time_feature_dim=0,
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
    return create_model_bundle(
        tmp_path / "bundle",
        model,
        model_version="cli-synthetic-v1",
        model_name="CoastWatch Synthetic-Test TCN",
        label_mode="confirmed_impact",
        coverage_scope="Synthetic CLI test only",
        horizons_hours=(1, 3),
        sites=[{"site_id": "synthetic_cli_01"}],
        synthetic_data=True,
    )


def test_cli_version_is_structured_json() -> None:
    result = RUNNER.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    record = _last_record(result)
    assert record["command"] == "version"
    assert record["status"] == "ok"


def test_cli_config_validate_and_dry_run_do_not_write(tmp_path: Path) -> None:
    output = tmp_path / "resolved.yaml"
    result = RUNNER.invoke(
        app,
        [
            "config",
            "validate",
            "--config",
            str(ML_ROOT / "configs" / "synthetic_phase1.yaml"),
            "--resolved-output",
            str(output),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not output.exists()
    record = _last_record(result)
    assert record["model_name"] == "CoastWatch Synthetic-Test TCN"
    assert record["wrote_resolved_output"] is False


def test_cli_invalid_config_has_nonzero_structured_error(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("project: {}\n", encoding="utf-8")
    result = RUNNER.invoke(app, ["config", "validate", "--config", str(invalid)])
    assert result.exit_code == 1
    record = _last_record(result)
    assert record["status"] == "error"
    assert record["error_type"] == "ValidationError"


def test_cli_legacy_audit_matches_protected_v1() -> None:
    result = RUNNER.invoke(
        app,
        ["audit", "legacy", "--workspace", str(WORKSPACE_ROOT), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    record = _last_record(result)
    assert record["valid"] is True
    assert len(record["files"]) == 8
    assert all(item["valid"] for item in record["files"])


def test_cli_synthetic_summary_and_full_leakage_audit(tmp_path: Path) -> None:
    dataset = tmp_path / "synthetic_dataset"
    generated = RUNNER.invoke(
        app,
        [
            "data",
            "synthetic",
            "--output",
            str(dataset),
            "--duration-days",
            "10",
            "--seed",
            "17",
        ],
    )
    assert generated.exit_code == 0, generated.output
    generated_record = _last_record(generated)
    assert generated_record["scientific_use_allowed"] is False
    assert (dataset / "SYNTHETIC_ONLY.json").is_file()
    assert (dataset / "sample_index.parquet").is_file()

    summary = RUNNER.invoke(app, ["dataset", "summary", "--dataset", str(dataset)])
    assert summary.exit_code == 0, summary.output
    summary_record = _last_record(summary)
    assert summary_record["integrity_valid"] is True
    assert summary_record["synthetic_data"] is True
    assert summary_record["tables"]["sample_index"]["rows"] > 0

    audit = RUNNER.invoke(
        app,
        [
            "dataset",
            "audit-leakage",
            "--dataset",
            str(dataset),
            "--max-samples",
            "100",
        ],
    )
    assert audit.exit_code == 0, audit.output
    audit_record = _last_record(audit)
    assert audit_record["complete"] is True
    assert audit_record["leakage_found"] is False
    assert audit_record["audits"]["forecast_asof_selection"]["missing_leads"] == 0
    assert audit_record["audits"]["split_and_groups"]["group_exclusivity"]["valid"] is True

    loso = RUNNER.invoke(app, ["dataset", "loso-summary", "--dataset", str(dataset)])
    assert loso.exit_code == 0, loso.output
    loso_record = _last_record(loso)
    assert loso_record["fold_count"] == 3
    assert loso_record["trained_models"] == 0
    assert loso_record["final_test_evaluated"] is False
    assert all(fold["final_test_frozen"] for fold in loso_record["folds"])


def test_cli_synthetic_dry_run_has_no_filesystem_side_effect(tmp_path: Path) -> None:
    destination = tmp_path / "not_created"
    result = RUNNER.invoke(
        app,
        [
            "data",
            "synthetic",
            "--output",
            str(destination),
            "--duration-days",
            "10",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not destination.exists()
    assert _last_record(result)["status"] == "planned"


def test_cli_export_verify_and_serve_dry_run(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    verified = RUNNER.invoke(app, ["export", "verify", "--bundle", str(bundle)])
    assert verified.exit_code == 0, verified.output
    verify_record = _last_record(verified)
    assert verify_record["hashes_valid"] is True
    assert verify_record["model_loaded"] is True

    served = RUNNER.invoke(app, ["serve", "--bundle", str(bundle), "--dry-run"])
    assert served.exit_code == 0, served.output
    serve_record = _last_record(served)
    assert serve_record["status"] == "ready"
    assert serve_record["shadow_mode"] is True
    assert serve_record["synthetic_data"] is True

    refused = RUNNER.invoke(app, ["serve", "--bundle", str(bundle)])
    assert refused.exit_code == 1
    refuse_record = _last_record(refused)
    assert refuse_record["status"] == "error"
    assert "--allow-synthetic" in refuse_record["message"]


def test_cli_export_verify_rejects_corrupt_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    architecture = bundle / "architecture.json"
    architecture.write_text("{}\n", encoding="utf-8")
    result = RUNNER.invoke(app, ["export", "verify", "--bundle", str(bundle)])
    assert result.exit_code == 1
    record = _last_record(result)
    assert record["status"] == "error"
    assert record["error_type"] == "BundleIntegrityError"


def test_cli_real_import_dry_run_and_site_review_are_non_fabricating(tmp_path: Path) -> None:
    source = tmp_path / "warnings.csv"
    source.write_text(
        "warning_area_id,issued_time_utc\narea-1,2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    data_root = tmp_path / "real-data"
    planned = RUNNER.invoke(
        app,
        [
            "data",
            "import-historic-warnings",
            "--input",
            str(source),
            "--data-root",
            str(data_root),
            "--dry-run",
        ],
    )
    assert planned.exit_code == 0, planned.output
    assert _last_record(planned)["status"] == "planned"
    assert not data_root.exists()

    review = tmp_path / "site-review.csv"
    report = RUNNER.invoke(
        app,
        [
            "sites",
            "review-report",
            "--legacy-locations",
            str(ML_ROOT / "coastal_risk" / "locations.json"),
            "--sites-config",
            str(ML_ROOT / "configs" / "sites.yaml"),
            "--output",
            str(review),
        ],
    )
    assert report.exit_code == 0, report.output
    record = _last_record(report)
    assert record["active_candidates"] == 3
    assert record["approved"] == 0
    assert review.is_file()


def test_cli_e2e_dry_run_does_not_train_or_write(tmp_path: Path) -> None:
    output = tmp_path / "no-run"
    result = RUNNER.invoke(
        app,
        [
            "e2e",
            "synthetic",
            "--output",
            str(output),
            "--duration-days",
            "12",
            "--epochs",
            "1",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    record = _last_record(result)
    assert record["status"] == "planned"
    assert record["synthetic_only"] is True
    assert record["scientific_result"] is False
    assert not output.exists()
