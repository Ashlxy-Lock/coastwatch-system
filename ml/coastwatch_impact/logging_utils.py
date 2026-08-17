"""Small JSON logging helpers for the long-running CLI service command."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any


class JsonLogFormatter(logging.Formatter):
    """Render standard and Uvicorn records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "request_id",
            "path",
            "status_code",
            "model_version",
            "model_variant",
            "site_id",
            "prediction_time_utc",
            "feature_manifest_hash",
            "source_issue_times",
            "issued_forecast_provenance",
            "data_quality",
            "raw_logits",
            "calibrated_probabilities",
            "research_band",
            "calibrated",
            "latency_ms",
            "shadow_mode",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def uvicorn_json_log_config() -> dict[str, Any]:
    """Return a self-contained logging config accepted by ``uvicorn.run``."""

    formatter_path = "coastwatch_impact.logging_utils.JsonLogFormatter"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": formatter_path}},
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {
                "handlers": ["access"],
                "level": "INFO",
                "propagate": False,
            },
            "coastwatch_impact.serve": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }


__all__ = ["JsonLogFormatter", "uvicorn_json_log_config"]
