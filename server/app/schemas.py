from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

DeviceId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Stable device identifier, for example COAST_01",
    ),
]
SimulationSessionId = Annotated[
    str,
    Field(
        min_length=8,
        max_length=48,
        pattern=r"^sim_[A-Za-z0-9_-]+$",
        description="Server-issued synthetic collection session identifier",
    ),
]
DisplayLocation = Annotated[
    str,
    Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9 ._-]+$",
        description="Short ASCII-only location label shown on the ESP32 LCD",
    ),
]
LocationPresetId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=24,
        pattern=r"^[a-z0-9_-]+$",
        description="Server-issued built-in or geo_<provider-id> location identifier",
    ),
]
ModelId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=48,
        pattern=r"^[a-z0-9_-]+$",
        description="Server-side model registry identifier",
    ),
]
UInt32 = Annotated[int, Field(strict=True, ge=0, le=4_294_967_295)]
Int32 = Annotated[int, Field(strict=True, ge=-2_147_483_648, le=2_147_483_647)]
LocationKind = Literal["coast", "place"]


class TelemetryIn(BaseModel):
    """Exact JSON payload sent by the ESP32 gateway."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

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
    simulation_session_id: SimulationSessionId | None = None


class TelemetryRecord(TelemetryIn):
    id: int
    received_at: datetime


class EnvironmentResponse(BaseModel):
    location: str
    display_location: str | None = None
    kind: LocationKind
    weather: str
    weather_code: int | None = None
    air_temperature_c: float | None = None
    humidity_percent: float | None = None
    wind_speed_kmh: float | None = None
    wind_direction_deg: float | None = None
    water_temperature_c: float | None = None
    wave_height_m: float | None = None
    wave_period_s: float | None = None
    sea_level_height_m: float | None = None
    tide_status: str | None = None
    ocean_current_velocity_kmh: float | None = None
    ocean_current_direction_deg: float | None = None
    source: Literal["open-meteo", "demo", "stale", "manual"]
    provider: str
    stale: bool
    updated_at: datetime


RiskName = Literal["safe", "advisory", "warning", "critical"]
RiskDataQuality = Literal["ok", "fault", "stale"]
RiskModelSource = Literal["model", "rule-fallback"]
RiskDeploymentMode = Literal["shadow", "active", "fallback"]


class RiskResponse(BaseModel):
    device_id: DeviceId
    location: str
    risk_level: Annotated[int, Field(ge=0, le=3)]
    risk_name: RiskName
    risk_score: Annotated[float, Field(ge=0.0, le=1.0)]
    environmental_level: Annotated[int, Field(ge=0, le=3)]
    environmental_probability: Annotated[float, Field(ge=0.0, le=1.0)]
    local_alarm_level: Annotated[int, Field(ge=0, le=4)]
    data_quality: RiskDataQuality
    model_source: RiskModelSource
    deployment_mode: RiskDeploymentMode
    model_version: str
    forecast_horizon_hours: Annotated[int, Field(ge=0, le=72)]
    degraded: bool
    reason_codes: list[str]
    missing_features: list[str]
    telemetry_id: int
    predicted_at: datetime
    environment_updated_at: datetime


class LocationSearchResult(BaseModel):
    provider_id: int | None = None
    name: str | None = None
    admin1: str | None = None
    admin2: str | None = None
    country: str | None = None
    feature_code: str | None = None
    population: int | None = None
    kind: LocationKind = "place"
    location: str
    display_location: DisplayLocation
    latitude: Annotated[float, Field(ge=-90.0, le=90.0)]
    longitude: Annotated[float, Field(ge=-180.0, le=180.0)]


class DeviceLocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: DeviceId
    kind: LocationKind = "place"
    location: Annotated[str, Field(min_length=1, max_length=80)]
    display_location: DisplayLocation
    latitude: Annotated[float, Field(ge=-90.0, le=90.0)]
    longitude: Annotated[float, Field(ge=-180.0, le=180.0)]


class DeviceLocationRecord(DeviceLocationIn):
    updated_at: datetime


class DeviceLocationPreset(BaseModel):
    id: LocationPresetId
    kind: LocationKind
    name: str
    display_location: DisplayLocation
    lat: Annotated[float, Field(ge=-90.0, le=90.0)]
    lon: Annotated[float, Field(ge=-180.0, le=180.0)]


class DeviceLocationPresetIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: DeviceId
    location_id: LocationPresetId


class DeviceLocationPresetSelection(DeviceLocationPreset):
    device_id: DeviceId


class ModelDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: ModelId
    display_name: Annotated[str, Field(min_length=1, max_length=48)]
    status: Literal["ready", "unavailable", "not_trained"]
    mode: Annotated[str, Field(min_length=1, max_length=32)]
    description: Annotated[str, Field(min_length=1, max_length=120)]


class ModelCatalogResponse(BaseModel):
    selected_model_id: ModelId
    models: list[ModelDescriptor]


class DeviceModelSelectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: DeviceId
    model_id: ModelId


class DeviceModelSelection(BaseModel):
    device_id: DeviceId
    selected_model_id: ModelId
    selected_at: datetime


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]
    server_time: datetime
