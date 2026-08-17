"""Strict request/response schemas for user-recorded simulation datasets.

Simulation data is deliberately marked synthetic.  It is useful for teaching,
prototyping and comparing models, but it must not be presented as observations
of a real coastal disaster.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import DeviceId, Int32, SimulationSessionId, UInt32

SimulationSessionState = Literal["active", "completed"]
SimulationLabelName = Literal["safe", "danger", "unknown"]
PositiveVersion = Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]
SIMULATION_DATA_WARNING = (
    "Operator-supplied tabletop simulation only; these values are not real coastal "
    "observations and the model must not be used for public-safety decisions."
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SimulationSessionStartIn(_StrictModel):
    device_id: DeviceId
    name: Annotated[str, Field(min_length=1, max_length=80)]
    session_id: SimulationSessionId | None = None


class SimulationSessionStopIn(_StrictModel):
    device_id: DeviceId


class SimulationScenarioUpsertIn(_StrictModel):
    """One complete, operator-defined environment for a tabletop session."""

    device_id: DeviceId
    scenario_name: Annotated[str, Field(min_length=1, max_length=80)]
    simulated_at: datetime
    sim_air_temperature_c: Annotated[float, Field(strict=True, ge=-80.0, le=60.0)]
    sim_humidity_percent: Annotated[float, Field(strict=True, ge=0.0, le=100.0)]
    sim_wind_speed_kmh: Annotated[float, Field(strict=True, ge=0.0, le=400.0)]
    sim_wave_height_m: Annotated[float, Field(strict=True, ge=0.0, le=40.0)]
    sim_wave_period_s: Annotated[float, Field(strict=True, ge=0.1, le=60.0)]
    sim_water_temperature_c: Annotated[float, Field(strict=True, ge=-5.0, le=45.0)]
    sim_sea_level_height_m: Annotated[float, Field(strict=True, ge=-20.0, le=20.0)]
    sim_ocean_current_velocity_kmh: Annotated[
        float, Field(strict=True, ge=0.0, le=50.0)
    ]
    sim_latitude: Annotated[float, Field(strict=True, ge=-90.0, le=90.0)]
    sim_longitude: Annotated[float, Field(strict=True, ge=-180.0, le=180.0)]
    note: Annotated[str, Field(max_length=500)] = ""

    @model_validator(mode="after")
    def require_timezone(self) -> "SimulationScenarioUpsertIn":
        if self.simulated_at.tzinfo is None or self.simulated_at.utcoffset() is None:
            raise ValueError("simulated_at must include a timezone offset")
        return self


class SimulationDeviceScenarioRecord(SimulationScenarioUpsertIn):
    sim_hour_sin: Annotated[float, Field(ge=-1.0, le=1.0)]
    sim_hour_cos: Annotated[float, Field(ge=-1.0, le=1.0)]
    sim_day_of_year_sin: Annotated[float, Field(ge=-1.0, le=1.0)]
    sim_day_of_year_cos: Annotated[float, Field(ge=-1.0, le=1.0)]
    data_kind: Literal["operator_supplied_simulation"]
    scenario_schema: Literal["coastwatch.operator-simulated-coast"]
    scenario_schema_version: Literal[1]
    scenario_hash: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    updated_at: datetime
    warning: Literal[
        "Operator-supplied tabletop simulation only; these values are not real coastal observations and the model must not be used for public-safety decisions."
    ]


class SimulationScenarioRecord(SimulationDeviceScenarioRecord):
    session_id: SimulationSessionId


class SimulationSessionRecord(_StrictModel):
    session_id: SimulationSessionId
    device_id: DeviceId
    name: str
    state: SimulationSessionState
    started_at: datetime
    ended_at: datetime | None
    baseline_distance_mm: Annotated[int, Field(ge=1, le=4_294_967_295)] | None
    synthetic: Literal[True]
    sample_count: Annotated[int, Field(ge=0)] = 0


class SimulationSessionDeleteCounts(_StrictModel):
    sessions: Literal[1]
    samples: Annotated[int, Field(ge=0)]
    labels: Annotated[int, Field(ge=0)]
    scenario_snapshots: Annotated[int, Field(ge=0)]


class SimulationSessionDeleteResponse(_StrictModel):
    status: Literal["deleted"]
    session_id: SimulationSessionId
    device_id: DeviceId
    deleted_counts: SimulationSessionDeleteCounts
    detached_telemetry_count: Annotated[int, Field(ge=0)]


class SimulationSampleData(_StrictModel):
    """Telemetry fields persisted as one immutable simulation sample."""

    device_id: DeviceId
    seq: UInt32
    uptime_ms: UInt32
    distance_mm: UInt32
    water_rise_mm: Int32
    rise_rate_mm_s: Int32
    person_detected: Annotated[bool, Field(strict=True)]
    alarm_level: Annotated[int, Field(strict=True, ge=0, le=4)]
    health_flags: UInt32
    wifi_rssi: Annotated[int, Field(strict=True, ge=-127, le=0)]


class SimulationTelemetryIn(SimulationSampleData):
    simulation_session_id: SimulationSessionId


class SimulationSampleRecord(SimulationSampleData):
    id: Annotated[int, Field(ge=1)]
    session_id: SimulationSessionId
    telemetry_id: Annotated[int, Field(ge=1)] | None
    received_at: datetime


class SimulationLabelUpsertIn(_StrictModel):
    session_id: SimulationSessionId
    device_id: DeviceId
    start_seq: UInt32
    end_seq: UInt32
    label: SimulationLabelName
    note: Annotated[str, Field(max_length=500)] = ""
    version: PositiveVersion = 1


class SimulationLabelRecord(SimulationLabelUpsertIn):
    id: Annotated[int, Field(ge=1)]
    created_at: datetime
    updated_at: datetime


class SimulationTrainIn(_StrictModel):
    device_id: DeviceId
    label_version: PositiveVersion = 1
    session_ids: (
        Annotated[list[SimulationSessionId], Field(min_length=1, max_length=500)] | None
    ) = None

    @model_validator(mode="after")
    def require_unique_session_ids(self) -> "SimulationTrainIn":
        if self.session_ids is not None and len(set(self.session_ids)) != len(
            self.session_ids
        ):
            raise ValueError("session_ids must not contain duplicates")
        return self


class SimulationTrainingRow(_StrictModel):
    session_id: SimulationSessionId
    device_id: DeviceId
    baseline_distance_mm: Annotated[int, Field(ge=1, le=4_294_967_295)] | None
    seq: UInt32
    uptime_ms: UInt32
    distance_mm: UInt32
    water_rise_mm: Int32
    rise_rate_mm_s: Int32
    person_detected: bool
    alarm_level: Annotated[int, Field(ge=0, le=4)]
    health_flags: UInt32
    wifi_rssi: Annotated[int, Field(ge=-127, le=0)]
    received_at: datetime
    label: SimulationLabelName
    label_version: PositiveVersion
    label_note: str


class SimulationLabelCounts(_StrictModel):
    safe: Annotated[int, Field(ge=0)] = 0
    danger: Annotated[int, Field(ge=0)] = 0
    unknown: Annotated[int, Field(ge=0)] = 0


class SimulationSessionSummary(SimulationSessionRecord):
    """A chart-ready summary without transferring every raw sample."""

    valid_ultrasonic_samples: Annotated[int, Field(ge=0)] = 0
    invalid_ultrasonic_samples: Annotated[int, Field(ge=0)] = 0
    label_counts: SimulationLabelCounts
    labelled_sample_count: Annotated[int, Field(ge=0)] = 0
    label_coverage: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    first_seq: UInt32 | None = None
    last_seq: UInt32 | None = None
    first_received_at: datetime | None = None
    last_received_at: datetime | None = None
    distance_min_mm: UInt32 | None = None
    distance_max_mm: UInt32 | None = None
    water_rise_min_mm: Int32 | None = None
    water_rise_max_mm: Int32 | None = None


class SimulationOverviewTotals(_StrictModel):
    session_count: Annotated[int, Field(ge=0)] = 0
    active_session_count: Annotated[int, Field(ge=0)] = 0
    completed_session_count: Annotated[int, Field(ge=0)] = 0
    sample_count: Annotated[int, Field(ge=0)] = 0
    valid_ultrasonic_samples: Annotated[int, Field(ge=0)] = 0
    invalid_ultrasonic_samples: Annotated[int, Field(ge=0)] = 0
    label_counts: SimulationLabelCounts
    labelled_sample_count: Annotated[int, Field(ge=0)] = 0
    label_coverage: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0


class SimulationOverviewResponse(_StrictModel):
    device_id: DeviceId
    label_version: PositiveVersion
    generated_at: datetime
    totals: SimulationOverviewTotals
    sessions: list[SimulationSessionSummary]


class SimulationTimelinePoint(SimulationSampleRecord):
    label: SimulationLabelName
    label_note: str
    label_version: PositiveVersion
    valid_ultrasonic: bool


class SimulationTimelineResponse(_StrictModel):
    session: SimulationSessionSummary
    label_version: PositiveVersion
    points: list[SimulationTimelinePoint]
    labels: list[SimulationLabelRecord]


class SimulationTrainingDataQuality(_StrictModel):
    completed_session_count: Annotated[int, Field(ge=0)] = 0
    collected_sample_count: Annotated[int, Field(ge=0)] = 0
    valid_ultrasonic_samples: Annotated[int, Field(ge=0)] = 0
    excluded_invalid_ultrasonic_samples: Annotated[int, Field(ge=0)] = 0
    label_counts: SimulationLabelCounts
    eligible_class_counts: SimulationLabelCounts
    labelled_sample_count: Annotated[int, Field(ge=0)] = 0
    eligible_labelled_sample_count: Annotated[int, Field(ge=0)] = 0
    excluded_unknown_samples: Annotated[int, Field(ge=0)] = 0
    excluded_warmup_samples: Annotated[int, Field(ge=0)] = 0
    label_coverage: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    eligible_session_count: Annotated[int, Field(ge=0)] = 0
    eligible_session_ids: list[SimulationSessionId]
    scenario_configured_session_count: Annotated[int, Field(ge=0)] = 0
    missing_scenario_session_count: Annotated[int, Field(ge=0)] = 0
    missing_scenario_session_ids: list[SimulationSessionId]
    excluded_legacy_session_count: Annotated[int, Field(ge=0)] = 0
    excluded_legacy_session_ids: list[SimulationSessionId]
    distinct_scenario_count: Annotated[int, Field(ge=0)] = 0
    safe_session_count: Annotated[int, Field(ge=0)] = 0
    danger_session_count: Annotated[int, Field(ge=0)] = 0
    mixed_label_session_count: Annotated[int, Field(ge=0)] = 0
    scenario_distinct_values: dict[str, Annotated[int, Field(ge=0)]]


class SimulationTrainingSelection(_StrictModel):
    mode: Literal["all_completed", "explicit"]
    requested_session_ids: list[SimulationSessionId]
    selected_session_ids: list[SimulationSessionId]
    effective_session_ids: list[SimulationSessionId]
    available_completed_session_ids: list[SimulationSessionId]
    available_completed_session_count: Annotated[int, Field(ge=0)]
    selection_hash: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]


class SimulationEvidenceCriterion(_StrictModel):
    actual: Annotated[int, Field(ge=0)]
    recommended: Annotated[int, Field(ge=1)]
    met: bool


class SimulationEvidenceQuality(_StrictModel):
    tier: Literal["blocked", "exploratory", "course_demo", "stronger_demo"]
    summary: str
    evaluation_scope: Literal[
        "blocked",
        "single_scenario_session_holdout",
        "cross_scenario_group_holdout",
    ]
    scenario_generalization_evaluable: bool
    environment_effects_learnable: bool
    criteria: dict[str, SimulationEvidenceCriterion]


class SimulationTrainingReadinessResponse(_StrictModel):
    device_id: DeviceId
    label_version: PositiveVersion
    ready: bool
    blockers: list[str]
    warnings: list[str]
    data_quality: SimulationTrainingDataQuality
    planned_split: dict[str, Any] | None = None
    selection: SimulationTrainingSelection
    evidence_quality: SimulationEvidenceQuality
    feature_count: Annotated[int, Field(ge=1)]
    data_kind: Literal["operator_supplied_simulation"]
    warning: str


class SimulationTrainingResponse(_StrictModel):
    model_id: str
    version: str
    status: Literal["ready"]
    data_kind: Literal["simulation"]
    data_origin: Literal["operator_supplied_simulation"]
    deployment_mode: Literal["shadow"]
    session_count: Annotated[int, Field(ge=0)]
    sample_count: Annotated[int, Field(ge=0)]
    labelled_sample_count: Annotated[int, Field(ge=0)]
    excluded_unknown_samples: Annotated[int, Field(ge=0)]
    excluded_warmup_samples: Annotated[int, Field(ge=0)]
    excluded_invalid_ultrasonic_samples: Annotated[int, Field(ge=0)]
    artifact_hash: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    dataset_hash: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    artifact_file: str
    archived_artifact_file: str
    selection: SimulationTrainingSelection
    evidence_quality: SimulationEvidenceQuality
    training_config: dict[str, Any]
    source_manifest: dict[str, Any]
    metrics: dict[str, Any]


class SimulationModelResponse(_StrictModel):
    schema_name: str = Field(alias="schema", serialization_alias="schema")
    schema_version: PositiveVersion
    model_id: str
    version: str
    model_type: Literal["binary_logistic_regression"]
    class_order: list[Literal["safe", "danger"]]
    feature_order: list[str]
    window_size: Annotated[int, Field(ge=1)]
    scaler: dict[str, list[float]]
    coefs: list[float]
    intercept: float
    metrics: dict[str, Any]
    training_config: dict[str, Any] | None = None
    source_manifest: dict[str, Any] | None = None
    data_kind: Literal["simulation"]
    data_origin: Literal["operator_supplied_simulation"]
    intended_use: Literal["machine_learning_course_demonstration"]
    real_coast_claim_allowed: Literal[False]
    warning: str
    deployment_mode: Literal["shadow"]
    created_at: datetime
    hash: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
