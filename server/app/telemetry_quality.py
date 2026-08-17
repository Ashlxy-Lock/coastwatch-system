"""Shared physical validity rules for ultrasonic telemetry.

This module deliberately has no application-layer imports so persistence,
training and live inference can all enforce the same sensor contract without
creating dependency cycles.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

ULTRASONIC_HEALTH_BIT = 1 << 0
ULTRASONIC_MIN_DISTANCE_MM = 20
ULTRASONIC_MAX_DISTANCE_MM = 4_000
TELEMETRY_WINDOW_MAX_RECEIVED_GAP_SECONDS = 5.0
TELEMETRY_WINDOW_MAX_UPTIME_GAP_MS = 6_000
TELEMETRY_WINDOW_MAX_SEQ_GAP = 64


def ultrasonic_sample_is_valid(sample: Mapping[str, Any]) -> bool:
    """Return whether one row has a healthy, physically usable echo."""

    try:
        distance_mm = int(sample.get("distance_mm", 0))
        return (
            ULTRASONIC_MIN_DISTANCE_MM <= distance_mm <= ULTRASONIC_MAX_DISTANCE_MM
            and int(sample.get("health_flags", 0)) & ULTRASONIC_HEALTH_BIT != 0
        )
    except (TypeError, ValueError):
        return False


def telemetry_timestamp(sample: Mapping[str, Any]) -> datetime | None:
    """Return a UTC timestamp for a persisted telemetry row, if parseable."""

    received = sample.get("received_at")
    if isinstance(received, datetime):
        timestamp = received
    elif isinstance(received, str):
        try:
            timestamp = datetime.fromisoformat(received.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def telemetry_samples_are_contiguous(
    older: Mapping[str, Any], newer: Mapping[str, Any]
) -> bool:
    """Apply the shared live/training boundary rule to two sensor rows."""

    if not ultrasonic_sample_is_valid(older) or not ultrasonic_sample_is_valid(newer):
        return False
    if older.get("device_id") != newer.get("device_id"):
        return False
    if _telemetry_epoch(older) != _telemetry_epoch(newer):
        return False

    older_received = telemetry_timestamp(older)
    newer_received = telemetry_timestamp(newer)
    if older_received is None or newer_received is None:
        return False
    received_gap = (newer_received - older_received).total_seconds()
    if not 0.0 < received_gap <= TELEMETRY_WINDOW_MAX_RECEIVED_GAP_SECONDS:
        return False

    older_seq = _optional_integer(older.get("seq"))
    newer_seq = _optional_integer(newer.get("seq"))
    older_uptime = _optional_integer(older.get("uptime_ms"))
    newer_uptime = _optional_integer(newer.get("uptime_ms"))
    if (
        older_seq is None
        or newer_seq is None
        or older_uptime is None
        or newer_uptime is None
    ):
        return False
    if not 1 <= newer_seq - older_seq <= TELEMETRY_WINDOW_MAX_SEQ_GAP:
        return False
    if not 1 <= newer_uptime - older_uptime <= TELEMETRY_WINDOW_MAX_UPTIME_GAP_MS:
        return False

    older_id = _optional_integer(older.get("id"))
    newer_id = _optional_integer(newer.get("id"))
    return older_id is None or newer_id is None or older_id < newer_id


def _optional_integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _telemetry_epoch(sample: Mapping[str, Any]) -> str | None:
    value = sample.get("simulation_session_id", sample.get("session_id"))
    return None if value is None else str(value)


__all__ = [
    "TELEMETRY_WINDOW_MAX_RECEIVED_GAP_SECONDS",
    "TELEMETRY_WINDOW_MAX_SEQ_GAP",
    "TELEMETRY_WINDOW_MAX_UPTIME_GAP_MS",
    "ULTRASONIC_HEALTH_BIT",
    "ULTRASONIC_MAX_DISTANCE_MM",
    "ULTRASONIC_MIN_DISTANCE_MM",
    "telemetry_samples_are_contiguous",
    "telemetry_timestamp",
    "ultrasonic_sample_is_valid",
]
