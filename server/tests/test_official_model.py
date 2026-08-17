from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.official_dataset import (
    DATASET_SCHEMA,
    NON_WATER_FEATURE_ORDER,
    OFFICIAL_FEATURE_ORDER,
    OFFICIAL_FEATURE_UNITS,
    OfficialDatasetError,
    discover_official_dataset_bundles,
    freeze_official_sensor_context,
    load_registered_official_dataset,
    register_official_dataset,
    validate_official_dataset,
)
from app.official_model import (
    MODEL_ID,
    OfficialModelError,
    assess_official_training_data,
    load_official_model,
    train_official_model,
)
from app.sensor_proxy_model import (
    SensorProxyError,
    build_sensor_proxy_profile,
    load_sensor_proxy_profile,
    run_sensor_proxy_external_test,
)


def _write_fixture_bundle(
    tmp_path: Path,
    *,
    data_origin: str = "synthetic_test_fixture",
    positive_storm_group_override: str | None = None,
    feature_overrides: dict[str, float] | None = None,
    force_safe_site_split: tuple[str, str] | None = None,
    split_site_level_offsets: dict[tuple[str, str], float] | None = None,
) -> tuple[Path, Path, Path, dict[str, object]]:
    root = tmp_path / "protected-official"
    bundle = root / "fixture-uk-coasts" / "v1"
    bundle.mkdir(parents=True)
    table_path = bundle / "harmonized.csv"
    sites = ("site-a", "site-b", "site-c")
    periods = {
        "train": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "validation": datetime(2024, 1, 4, tzinfo=timezone.utc),
        "frozen_test": datetime(2024, 1, 7, tzinfo=timezone.utc),
    }
    header = [
        "timestamp",
        "site_id",
        "storm_group_id",
        "target_extreme_water",
        *OFFICIAL_FEATURE_ORDER,
    ]
    rows: list[dict[str, object]] = []
    for split_index, (split, start) in enumerate(periods.items()):
        for hour in range(30):
            base_target = int(hour % 5 == 0 or hour % 11 == 0)
            for site_index, site_id in enumerate(sites):
                target = 0 if force_safe_site_split == (site_id, split) else base_target
                timestamp = start + timedelta(hours=hour)
                hour_angle = 2.0 * math.pi * timestamp.hour / 24.0
                days_in_year = 366.0
                day_angle = (
                    2.0 * math.pi * (timestamp.timetuple().tm_yday - 1) / days_in_year
                )
                level = 0.2 + target * 1.4 + hour * 0.004 + site_index * 0.03
                if split_site_level_offsets:
                    level += split_site_level_offsets.get((split, site_id), 0.0)
                row: dict[str, object] = {
                    "timestamp": timestamp.isoformat(),
                    "site_id": site_id,
                    "storm_group_id": (
                        (
                            positive_storm_group_override
                            if positive_storm_group_override is not None
                            else f"{split}-storm-{hour}"
                        )
                        if target
                        else "background"
                    ),
                    "target_extreme_water": target,
                    "relative_water_level_m": level,
                    "predicted_tide_relative_m": 0.15 + hour * 0.003,
                    "significant_wave_height_m": 0.8 + target * 1.1,
                    "wave_period_s": 6.0 + target * 2.0,
                    "wind_speed_m_s": 4.0 + target * 8.0 + split_index * 0.1,
                    "wind_gust_m_s": 7.0 + target * 10.0,
                    "surface_pressure_hpa": 1015.0 - target * 15.0,
                    "rainfall_mm_h": target * 2.0,
                    "air_temperature_c": 12.0 + target,
                    "relative_humidity_percent": 75.0 + target * 5.0,
                    "water_temperature_c": 10.0 + site_index,
                    "ocean_current_velocity_m_s": 0.4 + target * 0.2,
                    "hour_sin": math.sin(hour_angle),
                    "hour_cos": math.cos(hour_angle),
                    "day_of_year_sin": math.sin(day_angle),
                    "day_of_year_cos": math.cos(day_angle),
                    "latitude": 50.0 + site_index,
                    "longitude": -3.0 + site_index,
                }
                if feature_overrides:
                    row.update(feature_overrides)
                rows.append(row)
    with table_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    table_hash = hashlib.sha256(table_path.read_bytes()).hexdigest()
    units = dict(OFFICIAL_FEATURE_UNITS)
    manifest: dict[str, object] = {
        "schema": DATASET_SCHEMA,
        "schema_version": 1,
        "data_origin": data_origin,
        "dataset_id": "fixture-uk-coasts",
        "version": "v1",
        "sources": [
            {
                "name": "Synthetic official-shape fixture",
                "owner": "CoastWatch test suite",
                "citation": "Test fixture; not UK official data",
                "license": "Test-only",
                "source_url": "https://example.invalid/coastwatch-fixture",
                "retrieved_at": "2026-08-17T12:00:00Z",
                "original_filename": "synthetic-fixture.csv",
                "sha256": "",
            }
        ],
        "table": {
            "file": "harmonized.csv",
            "format": "csv",
            "sha256": table_hash,
            "row_count": len(rows),
        },
        "site_ids": list(sites),
        "site_metadata": {
            site_id: {
                "name": f"Synthetic {site_id}",
                "datum": "synthetic-relative-datum",
                "latitude": 50.0 + index,
                "longitude": -3.0 + index,
            }
            for index, site_id in enumerate(sites)
        },
        "date_range": {
            "start": periods["train"].isoformat(),
            "end": (periods["frozen_test"] + timedelta(hours=29)).isoformat(),
        },
        "feature_schema": {
            "feature_order": list(OFFICIAL_FEATURE_ORDER),
            "units": units,
        },
        "label_definition": {
            "column": "target_extreme_water",
            "positive_class": 1,
            "target_time_relation": "future",
            "forecast_horizon_hours": 3,
            "derivation": "Synthetic future target for pipeline testing only",
            "official_reference": "Synthetic fixture; never an official claim",
        },
        "splits": {
            "train": {
                "start": periods["train"].isoformat(),
                "end": (periods["train"] + timedelta(hours=29)).isoformat(),
            },
            "validation": {
                "start": periods["validation"].isoformat(),
                "end": (periods["validation"] + timedelta(hours=29)).isoformat(),
            },
            "frozen_test": {
                "start": periods["frozen_test"].isoformat(),
                "end": (periods["frozen_test"] + timedelta(hours=29)).isoformat(),
            },
            "leakage_gap_hours": 24,
        },
    }
    raw_directory = bundle / "raw"
    raw_directory.mkdir()
    raw_path = raw_directory / "synthetic-fixture.csv"
    raw_path.write_bytes(b"synthetic fixture source archive\n")
    manifest["sources"][0]["sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    manifest_path = bundle / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return root, manifest_path, table_path, manifest


def _registered_fixture(tmp_path: Path):
    root, manifest_path, table_path, _ = _write_fixture_bundle(tmp_path)
    return register_official_dataset(
        manifest_path,
        table_path,
        tmp_path / "registry",
        dataset_root=root,
        allow_synthetic_test_fixture=True,
    )


def test_runtime_rejects_fixture_and_current_state_or_weak_provenance(
    tmp_path: Path,
) -> None:
    root, manifest_path, table_path, manifest = _write_fixture_bundle(tmp_path)
    with pytest.raises(OfficialDatasetError, match="disabled outside explicit tests"):
        register_official_dataset(
            manifest_path,
            table_path,
            tmp_path / "registry",
            dataset_root=root,
        )

    current_state = json.loads(json.dumps(manifest))
    current_state["label_definition"]["target_time_relation"] = "current"
    with pytest.raises(OfficialDatasetError, match="future target"):
        validate_official_dataset(
            current_state, table_path, allow_synthetic_test_fixture=True
        )

    short_gap = json.loads(json.dumps(manifest))
    short_gap["splits"]["leakage_gap_hours"] = 2
    with pytest.raises(OfficialDatasetError, match="at least forecast_horizon_hours"):
        validate_official_dataset(
            short_gap, table_path, allow_synthetic_test_fixture=True
        )

    bad_url = json.loads(json.dumps(manifest))
    bad_url["sources"][0]["source_url"] = "http://example.invalid/file.csv"
    with pytest.raises(OfficialDatasetError, match="HTTPS URL"):
        validate_official_dataset(
            bad_url, table_path, allow_synthetic_test_fixture=True
        )

    missing_owner = json.loads(json.dumps(manifest))
    del missing_owner["sources"][0]["owner"]
    with pytest.raises(OfficialDatasetError, match=r"sources\[0\]\.owner"):
        validate_official_dataset(
            missing_owner, table_path, allow_synthetic_test_fixture=True
        )

    bad_time = json.loads(json.dumps(manifest))
    bad_time["sources"][0]["retrieved_at"] = "2026-08-17T12:00:00+01:00"
    with pytest.raises(OfficialDatasetError, match="must use UTC"):
        validate_official_dataset(
            bad_time, table_path, allow_synthetic_test_fixture=True
        )

    for invalid_horizon in (0, 73, 3.0):
        bad_horizon = json.loads(json.dumps(manifest))
        bad_horizon["label_definition"]["forecast_horizon_hours"] = invalid_horizon
        with pytest.raises(OfficialDatasetError, match="forecast_horizon_hours"):
            validate_official_dataset(
                bad_horizon, table_path, allow_synthetic_test_fixture=True
            )

    long_site = "s" * 64
    bad_site = json.loads(json.dumps(manifest))
    bad_site["site_ids"][0] = long_site
    bad_site["site_metadata"][long_site] = bad_site["site_metadata"].pop("site-a")
    with pytest.raises(OfficialDatasetError, match="site_ids"):
        validate_official_dataset(
            bad_site, table_path, allow_synthetic_test_fixture=True
        )

    bad_unit = json.loads(json.dumps(manifest))
    bad_unit["feature_schema"]["units"]["wind_speed_m_s"] = "km/h"
    with pytest.raises(OfficialDatasetError, match=r"wind_speed_m_s must be m/s"):
        validate_official_dataset(
            bad_unit, table_path, allow_synthetic_test_fixture=True
        )


def test_registration_is_confined_and_rechecks_file_hashes(tmp_path: Path) -> None:
    root, manifest_path, table_path, _ = _write_fixture_bundle(tmp_path)
    discovered = discover_official_dataset_bundles(root)
    assert discovered == [(manifest_path.resolve(), table_path.resolve())]
    registered = register_official_dataset(
        manifest_path,
        table_path,
        tmp_path / "registry",
        dataset_root=root,
        allow_synthetic_test_fixture=True,
    )
    reloaded = load_registered_official_dataset(
        registered.registration_path,
        dataset_root=root,
        allow_synthetic_test_fixture=True,
    )
    assert reloaded.registration_sha256 == registered.registration_sha256
    assert not reloaded.activatable

    outside = tmp_path / "outside.csv"
    outside.write_bytes(table_path.read_bytes())
    with pytest.raises(OfficialDatasetError, match="same bundle directory"):
        register_official_dataset(
            manifest_path,
            outside,
            tmp_path / "registry-2",
            dataset_root=root,
            allow_synthetic_test_fixture=True,
        )

    table_path.write_text(
        table_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    with pytest.raises(OfficialDatasetError, match="sha256"):
        load_registered_official_dataset(
            registered.registration_path,
            dataset_root=root,
            allow_synthetic_test_fixture=True,
        )


def test_raw_source_archive_is_required_and_rehashed_on_reload(tmp_path: Path) -> None:
    root, manifest_path, table_path, _manifest = _write_fixture_bundle(tmp_path)
    registered = register_official_dataset(
        manifest_path,
        table_path,
        tmp_path / "registry",
        dataset_root=root,
        allow_synthetic_test_fixture=True,
    )
    raw_path = manifest_path.parent / "raw" / "synthetic-fixture.csv"
    raw_path.write_bytes(b"tampered raw archive\n")
    with pytest.raises(OfficialDatasetError, match="raw archive sha256"):
        load_registered_official_dataset(
            registered.registration_path,
            dataset_root=root,
            allow_synthetic_test_fixture=True,
        )


@pytest.mark.parametrize(
    ("feature", "value"),
    [
        ("relative_water_level_m", 20.01),
        ("predicted_tide_relative_m", -20.01),
        ("significant_wave_height_m", -0.01),
        ("wave_period_s", 120.01),
        ("wind_speed_m_s", 139.0),
        ("wind_gust_m_s", 139.0),
        ("surface_pressure_hpa", 799.9),
        ("rainfall_mm_h", -0.01),
        ("air_temperature_c", 100.01),
        ("relative_humidity_percent", 100.01),
        ("water_temperature_c", 60.01),
        ("ocean_current_velocity_m_s", 28.0),
        ("hour_sin", 1.01),
        ("day_of_year_cos", -1.01),
        ("latitude", 90.01),
        ("longitude", 180.01),
    ],
)
def test_official_feature_values_outside_contract_are_rejected(
    tmp_path: Path, feature: str, value: float
) -> None:
    _root, _manifest_path, table_path, manifest = _write_fixture_bundle(
        tmp_path, feature_overrides={feature: value}
    )
    with pytest.raises(OfficialDatasetError, match=feature):
        validate_official_dataset(
            manifest, table_path, allow_synthetic_test_fixture=True
        )


@pytest.mark.parametrize(
    ("feature", "value", "message"),
    [
        ("latitude", 50.5, "does not match site_metadata"),
        ("hour_sin", 0.25, "do not match UTC timestamp"),
        ("day_of_year_sin", 0.25, "do not match UTC timestamp"),
    ],
)
def test_official_derived_feature_inconsistency_is_rejected(
    tmp_path: Path, feature: str, value: float, message: str
) -> None:
    _root, _manifest_path, table_path, manifest = _write_fixture_bundle(
        tmp_path, feature_overrides={feature: value}
    )
    with pytest.raises(OfficialDatasetError, match=message):
        validate_official_dataset(
            manifest, table_path, allow_synthetic_test_fixture=True
        )


@pytest.mark.parametrize("storm_group_id", ["", "background", "unsafe/event"])
def test_positive_targets_require_a_safe_event_storm_group(
    tmp_path: Path, storm_group_id: str
) -> None:
    _root, _manifest_path, table_path, manifest = _write_fixture_bundle(
        tmp_path, positive_storm_group_override=storm_group_id
    )
    expected = (
        "requires an event storm_group_id"
        if storm_group_id in {"", "background"}
        else "not a safe identifier"
    )
    with pytest.raises(OfficialDatasetError, match=expected):
        validate_official_dataset(
            manifest, table_path, allow_synthetic_test_fixture=True
        )


def test_manual_official_training_is_leakage_safe_and_auditable(tmp_path: Path) -> None:
    dataset = _registered_fixture(tmp_path)
    readiness = assess_official_training_data(dataset)
    assert readiness["ready"] is True
    assert readiness["activation_ready"] is False
    assert readiness["evidence_tier"] == "course_demo_three_plus_sites"
    assert readiness["resource_estimate"]["automatic_training"] is False
    assert readiness["storm_group_overlap"] == {
        "train_validation": [],
        "train_frozen_test": [],
        "validation_frozen_test": [],
    }

    artifact = train_official_model(
        dataset,
        output_path=tmp_path / "model.json",
        version="fixture-run-1",
        created_at="2026-08-17T12:00:00Z",
    )
    assert artifact["model_id"] == MODEL_ID
    assert len(artifact["feature_order"]) == 18
    assert "surge_residual_m" not in artifact["feature_order"]
    assert artifact["source_manifest"]["data_origin"] == "synthetic_test_fixture"
    assert artifact["source_manifest"]["provenance_assurance"] == (
        "synthetic_test_fixture_nonactivatable"
    )
    assert (
        artifact["source_manifest"]["deterministic_importer_replay_verified"] is False
    )
    assert artifact["activatable"] is False
    assert artifact["training_config"]["scaler_fit_split"] == "train"
    assert artifact["training_config"]["decision_threshold_selected_on"] == "validation"
    assert (
        artifact["training_config"]["water_level_threshold_selection_split"]
        == "validation"
    )
    assert artifact["training_config"]["final_metrics_split"] == "frozen_test"
    assert artifact["training_config"]["sample_weight"].startswith(
        "equal_total_weight_per_site"
    )
    contract = artifact["data_contract"]
    assert contract["fit_data_kinds"] == ["synthetic_test_fixture"]
    assert contract["sensor_rows_used_for_fit"] == 0
    assert contract["sensor_rows_used_for_scaler"] == 0
    assert contract["sensor_rows_used_for_threshold"] == 0
    assert contract["frozen_test_rows_used_for_fit"] == 0
    assert len(artifact["sensor_test_contexts"]) == 3
    assert set(artifact["sensor_test_contexts"][0]["features"]) == set(
        NON_WATER_FEATURE_ORDER
    )

    metrics = artifact["metrics"]
    assert 0 <= metrics["frozen_test"]["pr_auc"] <= 1
    assert "false_positive_rows_per_day" in metrics["frozen_test"]
    assert "false_alarms_per_day" not in metrics["frozen_test"]
    water_baseline = metrics["baselines"]["water_level_threshold"]
    assert water_baseline["threshold_selection_split"] == "validation"
    assert water_baseline["frozen_test_rows_used_for_threshold"] == 0
    assert water_baseline["selected_site_coverage"]["complete_coverage"] is True
    threshold_metrics = water_baseline["frozen_test"]
    assert threshold_metrics["pr_auc"] is None
    assert threshold_metrics["brier"] is None
    assert threshold_metrics["reliability"] is None
    assert (
        metrics["baselines"]["observable_water_level_persistence"]["available"] is False
    )
    delta = metrics["delta_vs_water_level_threshold"]
    assert delta["available"] is True
    assert delta["comparison_level"] == "site_macro_frozen_test"
    assert delta["probability_metric_deltas"] is None
    assert delta["verdict"] in {
        "outperforms_threshold_on_comparable_frozen_test_metrics",
        "mixed_no_clear_demonstrated_gain",
        "no_demonstrated_gain_threshold_is_sufficient",
    }

    with pytest.raises(OfficialModelError, match="not activatable"):
        load_official_model(artifact)
    loaded = load_official_model(artifact, require_activatable=False)
    prediction = loaded.predict_features(
        {name: dataset.rows[0][name] for name in OFFICIAL_FEATURE_ORDER}
    )
    assert prediction["model_id"] == MODEL_ID
    assert 0 <= prediction["extreme_water_probability"] <= 1

    tampered = json.loads(json.dumps(artifact))
    tampered["coefficients"][0] += 1
    with pytest.raises(OfficialModelError, match="sha256"):
        load_official_model(tampered, require_activatable=False)


def test_site_selection_is_flexible_and_scope_is_explicit(tmp_path: Path) -> None:
    dataset = _registered_fixture(tmp_path)
    readiness = assess_official_training_data(dataset, selected_site_ids=["site-a"])
    assert readiness["ready"] is True
    assert readiness["selected_site_ids"] == ["site-a"]
    assert readiness["evidence_tier"] == "exploratory_single_site"
    assert "cross-site claim" in " ".join(readiness["evidence_warnings"])
    artifact = train_official_model(
        dataset,
        selected_site_ids=["site-a"],
        version="one-site",
    )
    assert artifact["source_manifest"]["site_ids"] == ["site-a"]


def test_incomplete_per_site_class_coverage_cannot_claim_macro_primary(
    tmp_path: Path,
) -> None:
    root, manifest_path, table_path, _manifest = _write_fixture_bundle(
        tmp_path, force_safe_site_split=("site-c", "frozen_test")
    )
    dataset = register_official_dataset(
        manifest_path,
        table_path,
        tmp_path / "registry",
        dataset_root=root,
        allow_synthetic_test_fixture=True,
    )
    readiness = assess_official_training_data(dataset)
    assert readiness["ready"] is True
    assert readiness["activation_ready"] is False
    coverage = readiness["site_macro_evaluation"]
    assert coverage == {
        "selected_site_count": 3,
        "eligible_site_count": 2,
        "eligible_site_ids": ["site-a", "site-b"],
        "ineligible_site_ids": ["site-c"],
        "complete_coverage": False,
    }
    assert any(
        "frozen_test sites lack both target classes: site-c" in blocker
        for blocker in readiness["activation_blockers"]
    )

    artifact = train_official_model(dataset, version="incomplete-site-macro")
    metrics = artifact["metrics"]
    assert metrics["primary_metric"] is None
    assert metrics["primary_metric_available"] is False
    per_site = metrics["per_site_frozen_test"]
    assert per_site["selected_site_count"] == 3
    assert per_site["eligible_site_count"] == 2
    assert per_site["complete_coverage"] is False
    assert per_site["macro_average"] is None
    assert per_site["eligible_subset_macro_average"] is not None
    assert per_site["subset_macro_is_primary"] is False
    baseline = metrics["baselines"]["water_level_threshold"]
    assert baseline["selected_site_coverage"]["complete_coverage"] is False
    assert baseline["per_site_frozen_test"]["macro_average"] is None
    delta = metrics["delta_vs_water_level_threshold"]
    assert delta["available"] is False
    assert delta["verdict"] is None


def test_water_level_baseline_selects_distinct_site_thresholds_on_validation(
    tmp_path: Path,
) -> None:
    offsets = {
        ("validation", "site-a"): 0.0,
        ("validation", "site-b"): 2.0,
        ("validation", "site-c"): 4.0,
    }
    root, manifest_path, table_path, _manifest = _write_fixture_bundle(
        tmp_path,
        split_site_level_offsets=offsets,
    )
    dataset = register_official_dataset(
        manifest_path,
        table_path,
        tmp_path / "registry",
        dataset_root=root,
        allow_synthetic_test_fixture=True,
    )
    artifact = train_official_model(dataset, version="per-site-baseline")
    baseline = artifact["metrics"]["baselines"]["water_level_threshold"]

    assert baseline["threshold_selection_split"] == "validation"
    assert baseline["frozen_test_rows_used_for_threshold"] == 0
    thresholds = baseline["per_site_thresholds"]
    assert set(thresholds) == {"site-a", "site-b", "site-c"}
    assert len(set(thresholds.values())) == 3
    assert thresholds["site-c"] > 4.0
    assert baseline["selected_site_coverage"]["complete_coverage"] is True
    assert baseline["per_site_frozen_test"]["macro_average"] is not None


def test_water_level_baseline_never_uses_frozen_test_for_tuning(
    tmp_path: Path,
) -> None:
    validation_offsets = {
        ("validation", "site-a"): 0.0,
        ("validation", "site-b"): 1.5,
        ("validation", "site-c"): 3.0,
    }
    first_root, first_manifest, first_table, _ = _write_fixture_bundle(
        tmp_path / "first",
        split_site_level_offsets={
            **validation_offsets,
            ("frozen_test", "site-a"): -5.0,
            ("frozen_test", "site-b"): 0.0,
            ("frozen_test", "site-c"): 5.0,
        },
    )
    second_root, second_manifest, second_table, _ = _write_fixture_bundle(
        tmp_path / "second",
        split_site_level_offsets={
            **validation_offsets,
            ("frozen_test", "site-a"): 5.0,
            ("frozen_test", "site-b"): -4.0,
            ("frozen_test", "site-c"): -2.0,
        },
    )
    first_dataset = register_official_dataset(
        first_manifest,
        first_table,
        tmp_path / "first-registry",
        dataset_root=first_root,
        allow_synthetic_test_fixture=True,
    )
    second_dataset = register_official_dataset(
        second_manifest,
        second_table,
        tmp_path / "second-registry",
        dataset_root=second_root,
        allow_synthetic_test_fixture=True,
    )

    first_artifact = train_official_model(first_dataset, version="frozen-a")
    second_artifact = train_official_model(second_dataset, version="frozen-b")
    first_baseline = first_artifact["metrics"]["baselines"]["water_level_threshold"]
    second_baseline = second_artifact["metrics"]["baselines"]["water_level_threshold"]
    assert (
        first_baseline["per_site_thresholds"] == second_baseline["per_site_thresholds"]
    )
    assert first_baseline["frozen_test_rows_used_for_threshold"] == 0
    assert second_baseline["frozen_test_rows_used_for_threshold"] == 0


def test_activation_minima_block_small_and_single_site_official_runs(
    tmp_path: Path,
) -> None:
    root, manifest_path, table_path, _ = _write_fixture_bundle(
        tmp_path,
        data_origin="uk_official_archive",
    )
    dataset = register_official_dataset(
        manifest_path,
        table_path,
        tmp_path / "registry",
        dataset_root=root,
    )
    readiness = assess_official_training_data(
        dataset,
        selected_site_ids=["site-a"],
    )
    assert readiness["ready"] is True
    assert readiness["activation_ready"] is False
    blockers = " ".join(readiness["activation_blockers"])
    assert "at least 3 selected sites" in blockers
    assert "fewer than 200 rows" in blockers

    artifact = train_official_model(
        dataset,
        selected_site_ids=["site-a"],
        version="small-single-site",
    )
    assert artifact["activatable"] is False


def test_frozen_context_and_sensor_proxy_never_mutate_model(tmp_path: Path) -> None:
    dataset = _registered_fixture(tmp_path)
    artifact = train_official_model(dataset, version="sensor-test")
    model = load_official_model(artifact, require_activatable=False)
    context = freeze_official_sensor_context(
        dataset, site_id="site-a", split="frozen_test"
    )
    assert context == model.sensor_test_contexts[0]
    profile_payload = build_sensor_proxy_profile(
        profile_id="formal-profile-1",
        official_model=model,
        official_context=context,
        calibration_water_rise_mm=[0.0, 25.0, 50.0, 75.0, 100.0],
        calibration_source={"session_id": "calibration-1", "device_id": "COAST_01"},
        created_at="2026-08-17T12:00:00Z",
    )
    profile = load_sensor_proxy_profile(profile_payload, official_model=model)
    assert profile.exploratory is False
    assert "surge_residual_m" not in model.feature_order
    assert "surge_residual_m" not in profile.context_features
    assert profile.gain_m_per_m > 0
    before = model.artifact_sha256
    result = run_sensor_proxy_external_test(
        model,
        profile,
        [
            {"captured_at": "2026-08-17T12:00:00Z", "water_rise_mm": 0.0},
            {"captured_at": "2026-08-17T12:00:01Z", "water_rise_mm": 5000.0},
        ],
        session_id="esp32-session-1",
    )
    assert model.artifact_sha256 == before
    assert result["model_artifact_unchanged"] is True
    assert result["official_frozen_test_metrics_modified"] is False
    assert result["out_of_distribution_count"] >= 1
    assert result["mapping"]["clipping"] is False
    assert (
        result["rows"][1]["proxy_relative_water_level_m"]
        > result["rows"][0]["proxy_relative_water_level_m"]
    )
    with pytest.raises(SensorProxyError, match="independent of calibration"):
        run_sensor_proxy_external_test(
            model,
            profile,
            [{"water_rise_mm": 10.0}],
            session_id="calibration-1",
        )

    with pytest.raises(SensorProxyError, match="require exploratory"):
        build_sensor_proxy_profile(
            profile_id="bad-formal",
            official_model=model,
            official_context=context,
            manual_gain=2.0,
            manual_reference_level_m=0.0,
        )
    exploratory = build_sensor_proxy_profile(
        profile_id="manual-exploration",
        official_model=model,
        official_context=context,
        manual_gain=2.0,
        manual_reference_level_m=0.1,
        exploratory=True,
    )
    assert exploratory["formal_metrics_eligible"] is False
    assert exploratory["mode"] == "exploratory_manual_linear"

    tampered = json.loads(json.dumps(profile_payload))
    tampered["mapping"]["gain_m_per_m"] += 1
    with pytest.raises(SensorProxyError, match="sha256"):
        load_sensor_proxy_profile(tampered, official_model=model)
