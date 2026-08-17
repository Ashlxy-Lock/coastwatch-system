"""Application-level aggregation for simulation visualization and training."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from typing import Any

from .schemas import EnvironmentResponse
from .simulation_model import (
    FEATURE_ORDER,
    RAW_SIMULATED_SCENARIO_FIELDS,
    SIMULATED_ENVIRONMENT_FEATURE_ORDER,
    WINDOW_EPOCH_FIELD,
    assess_simulation_training_data,
)
from .simulation_schemas import SIMULATION_DATA_WARNING
from .simulation_store import (
    SimulationConflictError,
    SimulationValidationError,
    get_simulation_scenario,
    get_simulation_session,
    list_labeled_training_rows,
    list_simulation_session_summaries,
    list_simulation_sessions,
)
from .telemetry_quality import (
    telemetry_samples_are_contiguous,
    ultrasonic_sample_is_valid,
)

# Evidence-quality recommendations only.  Leakage-safe split feasibility is the
# sole hard training gate.
MINIMUM_ELIGIBLE_SESSIONS = 12
RECOMMENDED_ELIGIBLE_SESSIONS = 30
MINIMUM_LABELLED_SAMPLES = 240
MINIMUM_SAMPLES_PER_CLASS = 80
MINIMUM_SESSIONS_PER_CLASS = 6
MINIMUM_MIXED_LABEL_SESSIONS = 4
MINIMUM_DISTINCT_SCENARIOS = 3


def build_simulated_environment(scenario: dict[str, Any]) -> EnvironmentResponse:
    """Map explicit operator context onto the existing firmware-safe schema."""

    scenario_name = str(scenario["scenario_name"])
    display_name = (
        unicodedata.normalize("NFKD", scenario_name)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    display_name = " ".join(display_name.split()) or "SIMULATED COAST"
    return EnvironmentResponse(
        location=_truncate_utf8(scenario_name, 63),
        display_location=_truncate_utf8(display_name, 35),
        kind="coast",
        weather="OPERATOR SIMULATION",
        air_temperature_c=float(scenario["sim_air_temperature_c"]),
        humidity_percent=float(scenario["sim_humidity_percent"]),
        wind_speed_kmh=float(scenario["sim_wind_speed_kmh"]),
        water_temperature_c=float(scenario["sim_water_temperature_c"]),
        wave_height_m=float(scenario["sim_wave_height_m"]),
        wave_period_s=float(scenario["sim_wave_period_s"]),
        sea_level_height_m=float(scenario["sim_sea_level_height_m"]),
        tide_status="SIMULATED",
        ocean_current_velocity_kmh=float(scenario["sim_ocean_current_velocity_kmh"]),
        source="manual",
        provider="CoastWatch manual scenario",
        stale=False,
        updated_at=scenario["updated_at"],
    )


def _truncate_utf8(value: str, maximum_bytes: int) -> str:
    encoded = value.encode("utf-8")[:maximum_bytes]
    while encoded:
        try:
            return encoded.decode("utf-8")
        except UnicodeDecodeError:
            encoded = encoded[:-1]
    return ""


def _valid_training_rows_with_epochs(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Drop invalid rows while preserving their rolling-window boundary.

    Epoch markers are intentionally independent from ``session_id``: model
    features reset after a bad echo, while train/test grouping continues to use
    the original collection session and therefore cannot leak between splits.
    """

    valid_rows: list[dict[str, Any]] = []
    epoch = -1
    starts_new_epoch = True
    previous_valid: dict[str, Any] | None = None
    for row in rows:
        if not ultrasonic_sample_is_valid(row):
            starts_new_epoch = True
            previous_valid = None
            continue
        if previous_valid is not None and not telemetry_samples_are_contiguous(
            previous_valid, row
        ):
            starts_new_epoch = True
        if starts_new_epoch:
            epoch += 1
            starts_new_epoch = False
        item = dict(row)
        item[WINDOW_EPOCH_FIELD] = epoch
        valid_rows.append(item)
        previous_valid = item
    return valid_rows, epoch + 1


def build_simulation_overview(
    device_id: str,
    *,
    label_version: int = 1,
    limit: int = 100,
) -> dict[str, Any]:
    sessions = list_simulation_session_summaries(
        device_id, version=label_version, limit=limit
    )
    sample_count = sum(int(session["sample_count"]) for session in sessions)
    valid_count = sum(int(session["valid_ultrasonic_samples"]) for session in sessions)
    invalid_count = sum(
        int(session["invalid_ultrasonic_samples"]) for session in sessions
    )
    label_counts = {
        name: sum(int(session["label_counts"][name]) for session in sessions)
        for name in ("safe", "danger", "unknown")
    }
    labelled_count = label_counts["safe"] + label_counts["danger"]
    return {
        "device_id": device_id,
        "label_version": label_version,
        "generated_at": datetime.now(timezone.utc),
        "totals": {
            "session_count": len(sessions),
            "active_session_count": sum(
                session["state"] == "active" for session in sessions
            ),
            "completed_session_count": sum(
                session["state"] == "completed" for session in sessions
            ),
            "sample_count": sample_count,
            "valid_ultrasonic_samples": valid_count,
            "invalid_ultrasonic_samples": invalid_count,
            "label_counts": label_counts,
            "labelled_sample_count": labelled_count,
            "label_coverage": (labelled_count / sample_count if sample_count else 0.0),
        },
        "sessions": sessions,
    }


def build_training_dataset(
    device_id: str,
    *,
    label_version: int = 1,
    selected_session_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build the only dataset allowed to enter custom model training."""

    all_completed_sessions = list_simulation_sessions(device_id, "completed", limit=500)
    available_completed_session_ids = sorted(
        str(session["session_id"]) for session in all_completed_sessions
    )
    if selected_session_ids is None:
        selection_mode = "all_completed"
        requested_session_ids: list[str] = []
        completed_sessions = all_completed_sessions
    else:
        selection_mode = "explicit"
        requested_session_ids = [
            str(session_id).strip() for session_id in selected_session_ids
        ]
        if not requested_session_ids:
            raise SimulationValidationError(
                "selected_session_ids must contain at least one session"
            )
        if any(not session_id for session_id in requested_session_ids):
            raise SimulationValidationError("selected session IDs must not be empty")
        if len(set(requested_session_ids)) != len(requested_session_ids):
            raise SimulationValidationError(
                "selected_session_ids must not contain duplicates"
            )
        completed_sessions = []
        for session_id in sorted(requested_session_ids):
            session = get_simulation_session(session_id)
            if session is None:
                raise SimulationValidationError(
                    f"selected simulation session {session_id} does not exist"
                )
            if str(session["device_id"]) != device_id:
                raise SimulationConflictError(
                    f"selected simulation session {session_id} belongs to a different device"
                )
            if str(session["state"]) != "completed":
                raise SimulationConflictError(
                    f"selected simulation session {session_id} is not completed"
                )
            if get_simulation_scenario(session_id, device_id) is None:
                raise SimulationValidationError(
                    f"selected simulation session {session_id} has no scenario snapshot"
                )
            completed_sessions.append(session)

    resolved_session_ids = sorted(
        str(session["session_id"]) for session in completed_sessions
    )
    valid_rows: list[dict[str, Any]] = []
    collection_sessions: list[dict[str, Any]] = []
    scenario_by_session: dict[str, dict[str, Any]] = {}
    collected_count = 0
    invalid_count = 0
    eligible_session_ids: list[str] = []
    missing_scenario_session_ids: list[str] = []
    label_counts = {"safe": 0, "danger": 0, "unknown": 0}

    for session in completed_sessions:
        session_id = str(session["session_id"])
        scenario = get_simulation_scenario(session_id, device_id)
        rows = list_labeled_training_rows(
            session_id,
            device_id,
            version=label_version,
            include_unknown=True,
        )
        collected_count += len(rows)
        current_valid, valid_run_count = _valid_training_rows_with_epochs(rows)
        invalid_count += len(rows) - len(current_valid)
        session_label_counts = {
            name: sum(row["label"] == name for row in current_valid)
            for name in ("safe", "danger", "unknown")
        }
        for name in label_counts:
            label_counts[name] += session_label_counts[name]
        has_labels = session_label_counts["safe"] + session_label_counts["danger"] > 0
        if scenario is None:
            if has_labels:
                missing_scenario_session_ids.append(session_id)
        else:
            environment = {
                name: float(scenario[name])
                for name in SIMULATED_ENVIRONMENT_FEATURE_ORDER
            }
            for row in current_valid:
                row.update(environment)
                row["scenario_hash"] = scenario["scenario_hash"]
            scenario_by_session[session_id] = scenario
        if has_labels and scenario is not None:
            eligible_session_ids.append(session_id)
        collection_sessions.append(
            {
                "session_id": session_id,
                "collected_sample_count": len(rows),
                "valid_ultrasonic_samples": len(current_valid),
                "excluded_invalid_ultrasonic_samples": len(rows) - len(current_valid),
                "valid_run_count": valid_run_count,
                "label_counts": session_label_counts,
                "scenario_configured": scenario is not None,
                "scenario_hash": scenario["scenario_hash"] if scenario else None,
                "scenario_snapshot": scenario,
            }
        )
        if scenario is not None:
            valid_rows.extend(current_valid)

    labelled_count = label_counts["safe"] + label_counts["danger"]
    candidate_assessment = assess_simulation_training_data(valid_rows)
    candidate_session_label_counts = candidate_assessment.get(
        "session_label_counts", {}
    )
    effective_session_ids = sorted(
        session_id
        for session_id, counts in candidate_session_label_counts.items()
        if int(counts.get("safe", 0)) + int(counts.get("danger", 0)) > 0
    )
    if selection_mode == "explicit":
        ineffective_session_ids = sorted(
            set(resolved_session_ids) - set(effective_session_ids)
        )
        if ineffective_session_ids:
            raise SimulationValidationError(
                "selected simulation sessions have no five-sample-window-eligible "
                "safe/danger targets: " + ", ".join(ineffective_session_ids)
            )
    effective_session_id_set = set(effective_session_ids)
    training_rows = [
        row for row in valid_rows if str(row["session_id"]) in effective_session_id_set
    ]
    assessment = assess_simulation_training_data(training_rows)
    eligible_labelled_count = int(assessment.get("labelled_sample_count", 0))
    eligible_class_counts = assessment.get("class_counts", {"safe": 0, "danger": 0})
    session_label_counts = assessment.get("session_label_counts", {})
    eligible_session_ids = sorted(
        session_id
        for session_id, counts in session_label_counts.items()
        if int(counts.get("safe", 0)) + int(counts.get("danger", 0)) > 0
    )
    canonical_selection = json.dumps(
        {
            "device_id": device_id,
            "label_version": label_version,
            "mode": selection_mode,
            "requested_session_ids": sorted(requested_session_ids),
            "selected_session_ids": resolved_session_ids,
            "effective_session_ids": eligible_session_ids,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    selection = {
        "mode": selection_mode,
        "requested_session_ids": sorted(requested_session_ids),
        "selected_session_ids": resolved_session_ids,
        "effective_session_ids": eligible_session_ids,
        "available_completed_session_ids": available_completed_session_ids,
        "available_completed_session_count": len(available_completed_session_ids),
        "selection_hash": hashlib.sha256(canonical_selection).hexdigest(),
    }
    effective_collection_sessions = [
        session
        for session in collection_sessions
        if session["session_id"] in set(eligible_session_ids)
    ]
    effective_collected_count = sum(
        int(session["collected_sample_count"])
        for session in effective_collection_sessions
    )
    effective_invalid_count = sum(
        int(session["excluded_invalid_ultrasonic_samples"])
        for session in effective_collection_sessions
    )
    effective_label_counts = {
        name: sum(
            int(session["label_counts"][name])
            for session in effective_collection_sessions
        )
        for name in ("safe", "danger", "unknown")
    }
    effective_labelled_count = (
        effective_label_counts["safe"] + effective_label_counts["danger"]
    )
    label_coverage = labelled_count / len(valid_rows) if valid_rows else 0.0
    eligible_scenarios = [
        scenario_by_session[session_id]
        for session_id in eligible_session_ids
        if session_id in scenario_by_session
    ]
    safe_session_count = sum(
        int(counts.get("safe", 0)) > 0 for counts in session_label_counts.values()
    )
    danger_session_count = sum(
        int(counts.get("danger", 0)) > 0 for counts in session_label_counts.values()
    )
    mixed_label_session_count = sum(
        int(counts.get("safe", 0)) > 0 and int(counts.get("danger", 0)) > 0
        for counts in session_label_counts.values()
    )
    scenario_distinct_values = {
        name: len({float(scenario[name]) for scenario in eligible_scenarios})
        for name in RAW_SIMULATED_SCENARIO_FIELDS
    }
    scenario_distinct_values.update(
        {
            "sim_time_of_day_context": len(
                {
                    (
                        float(scenario["sim_hour_sin"]),
                        float(scenario["sim_hour_cos"]),
                    )
                    for scenario in eligible_scenarios
                }
            ),
            "sim_day_of_year_context": len(
                {
                    (
                        float(scenario["sim_day_of_year_sin"]),
                        float(scenario["sim_day_of_year_cos"]),
                    )
                    for scenario in eligible_scenarios
                }
            ),
        }
    )
    distinct_scenario_count = len(
        {
            tuple(float(scenario[name]) for name in SIMULATED_ENVIRONMENT_FEATURE_ORDER)
            for scenario in eligible_scenarios
        }
    )
    data_quality = {
        "completed_session_count": len(completed_sessions),
        "collected_sample_count": collected_count,
        "valid_ultrasonic_samples": len(valid_rows),
        "excluded_invalid_ultrasonic_samples": invalid_count,
        "label_counts": label_counts,
        "eligible_class_counts": {
            "safe": int(eligible_class_counts.get("safe", 0)),
            "danger": int(eligible_class_counts.get("danger", 0)),
            "unknown": 0,
        },
        "labelled_sample_count": labelled_count,
        "eligible_labelled_sample_count": eligible_labelled_count,
        "excluded_unknown_samples": int(assessment.get("excluded_unknown_samples", 0)),
        "excluded_warmup_samples": int(assessment.get("excluded_warmup_samples", 0)),
        "label_coverage": label_coverage,
        "eligible_session_count": len(eligible_session_ids),
        "eligible_session_ids": eligible_session_ids,
        "scenario_configured_session_count": len(scenario_by_session),
        "missing_scenario_session_count": len(missing_scenario_session_ids),
        "missing_scenario_session_ids": missing_scenario_session_ids,
        "excluded_legacy_session_count": len(missing_scenario_session_ids),
        "excluded_legacy_session_ids": missing_scenario_session_ids,
        "distinct_scenario_count": distinct_scenario_count,
        "safe_session_count": safe_session_count,
        "danger_session_count": danger_session_count,
        "mixed_label_session_count": mixed_label_session_count,
        "scenario_distinct_values": scenario_distinct_values,
    }
    blockers = list(assessment.get("blockers", []))
    warnings: list[str] = []
    if missing_scenario_session_ids:
        warnings.append(
            f"{len(missing_scenario_session_ids)} legacy labelled sessions without "
            "a predeclared scenario snapshot are permanently excluded; scenarios "
            "cannot be backfilled after outcomes are known"
        )
    evidence_criteria = {
        "eligible_sessions": {
            "actual": len(eligible_session_ids),
            "recommended": MINIMUM_ELIGIBLE_SESSIONS,
        },
        "eligible_labelled_samples": {
            "actual": eligible_labelled_count,
            "recommended": MINIMUM_LABELLED_SAMPLES,
        },
        "safe_samples": {
            "actual": int(eligible_class_counts.get("safe", 0)),
            "recommended": MINIMUM_SAMPLES_PER_CLASS,
        },
        "danger_samples": {
            "actual": int(eligible_class_counts.get("danger", 0)),
            "recommended": MINIMUM_SAMPLES_PER_CLASS,
        },
        "safe_sessions": {
            "actual": safe_session_count,
            "recommended": MINIMUM_SESSIONS_PER_CLASS,
        },
        "danger_sessions": {
            "actual": danger_session_count,
            "recommended": MINIMUM_SESSIONS_PER_CLASS,
        },
        "mixed_label_sessions": {
            "actual": mixed_label_session_count,
            "recommended": MINIMUM_MIXED_LABEL_SESSIONS,
        },
        "distinct_scenarios": {
            "actual": distinct_scenario_count,
            "recommended": MINIMUM_DISTINCT_SCENARIOS,
        },
    }
    for name, criterion in evidence_criteria.items():
        criterion["met"] = criterion["actual"] >= criterion["recommended"]
        if not criterion["met"]:
            warnings.append(
                f"evidence recommendation not met: {name} is {criterion['actual']} "
                f"(recommended {criterion['recommended']})"
            )
    evidence_recommendations_met = all(
        bool(criterion["met"]) for criterion in evidence_criteria.values()
    )
    planned_split = assessment.get("planned_split")
    scenario_generalization_evaluable = bool(
        planned_split and planned_split.get("scenario_generalization_evaluable")
    )
    environment_effects_learnable = bool(
        planned_split and planned_split.get("environment_effects_learnable")
    )
    if blockers:
        evidence_tier = "blocked"
        evaluation_scope = "blocked"
        evidence_summary = "No leakage-safe training/test split is currently possible."
    elif not scenario_generalization_evaluable:
        evidence_tier = "exploratory"
        evaluation_scope = "single_scenario_session_holdout"
        evidence_summary = (
            "Training uses independent sessions from one scenario; environmental "
            "effects and cross-scenario generalization cannot be evaluated."
        )
        warnings.append(
            "only one simulated scenario is selected: environmental effects and "
            "cross-scenario generalization cannot be evaluated; fusion may behave "
            "like the ultrasonic-only model"
        )
    elif not environment_effects_learnable:
        evidence_tier = "exploratory"
        evaluation_scope = "cross_scenario_group_holdout"
        evidence_summary = (
            "The holdout tests another scenario, but the training split has no "
            "environmental variation; environment coefficients are not interpretable."
        )
    elif not evidence_recommendations_met:
        evidence_tier = "exploratory"
        evaluation_scope = "cross_scenario_group_holdout"
        evidence_summary = (
            "Cross-scenario evaluation is possible, but metrics are exploratory "
            "and high variance."
        )
    elif len(eligible_session_ids) < RECOMMENDED_ELIGIBLE_SESSIONS:
        evidence_tier = "course_demo"
        evaluation_scope = "cross_scenario_group_holdout"
        evidence_summary = (
            "The selection meets the course-demo evidence recommendations."
        )
    else:
        evidence_tier = "stronger_demo"
        evaluation_scope = "cross_scenario_group_holdout"
        evidence_summary = "The selection has broader repeated-session evidence for a course demonstration."
    if not blockers and not environment_effects_learnable:
        warnings.append(
            "the training split contains fewer than two scenario groups: "
            "environment coefficients are not interpretable"
        )
    evidence_quality = {
        "tier": evidence_tier,
        "summary": evidence_summary,
        "evaluation_scope": evaluation_scope,
        "scenario_generalization_evaluable": scenario_generalization_evaluable,
        "environment_effects_learnable": environment_effects_learnable,
        "criteria": evidence_criteria,
    }
    for name, distinct_count in scenario_distinct_values.items():
        if distinct_count < 2:
            warnings.append(
                f"{name} is constant; its effect cannot be learned from this dataset"
            )
        elif distinct_count < 3:
            warnings.append(
                f"{name} has only {distinct_count} distinct values; at least 3 is recommended"
            )
    if invalid_count:
        warnings.append(
            f"{invalid_count} samples are excluded because ultrasonic data is invalid"
        )
    if int(assessment.get("excluded_unknown_samples", 0)):
        warnings.append(
            f"{assessment['excluded_unknown_samples']} unknown samples provide context "
            "but are not targets"
        )
    if int(assessment.get("excluded_warmup_samples", 0)):
        warnings.append(
            f"{assessment['excluded_warmup_samples']} warm-up samples are excluded so "
            "training matches the live five-sample window"
        )
    if training_rows and label_coverage < 0.8:
        warnings.append("less than 80% of valid samples have operator labels")
    if len(eligible_session_ids) < RECOMMENDED_ELIGIBLE_SESSIONS:
        warnings.append(
            f"fewer than {RECOMMENDED_ELIGIBLE_SESSIONS} independent sessions: "
            "22-feature estimates remain high variance"
        )
    warnings.append(SIMULATION_DATA_WARNING)

    return {
        "rows": training_rows,
        "readiness": {
            "device_id": device_id,
            "label_version": label_version,
            "ready": not blockers,
            "blockers": list(dict.fromkeys(blockers)),
            "warnings": warnings,
            "data_quality": data_quality,
            "planned_split": planned_split,
            "selection": selection,
            "evidence_quality": evidence_quality,
            "feature_count": len(FEATURE_ORDER),
            "data_kind": "operator_supplied_simulation",
            "warning": SIMULATION_DATA_WARNING,
        },
        "source_context": {
            "device_id": device_id,
            "label_version": label_version,
            "data_origin": "operator_supplied_simulation",
            "scenario_schema": "coastwatch.operator-simulated-coast",
            "scenario_schema_version": 1,
            "feature_order": list(FEATURE_ORDER),
            "warning": SIMULATION_DATA_WARNING,
            "selection": selection,
            "evidence_quality": evidence_quality,
            **data_quality,
            "training_input_collected_sample_count": effective_collected_count,
            "training_input_valid_ultrasonic_samples": len(training_rows),
            "training_input_excluded_invalid_ultrasonic_samples": (
                effective_invalid_count
            ),
            "training_input_label_counts": effective_label_counts,
            "training_input_labelled_sample_count": effective_labelled_count,
            "collection_sessions": effective_collection_sessions,
        },
    }


__all__ = [
    "build_simulated_environment",
    "build_simulation_overview",
    "build_training_dataset",
]
