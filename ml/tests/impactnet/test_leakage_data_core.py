from __future__ import annotations

import pandas as pd
import pytest

from coastwatch_impact.data.split import (
    GlobalSplitConfig,
    SplitLeakageError,
    audit_target_window_purge,
)


def test_manually_injected_cross_boundary_target_fails_audit() -> None:
    config = GlobalSplitConfig(
        train_end_utc="2025-01-10T00:00:00Z",
        validation_end_utc="2025-01-20T00:00:00Z",
        test_end_utc="2025-01-30T00:00:00Z",
    )
    bad = pd.DataFrame(
        {
            "prediction_time_utc": ["2025-01-09T12:00:00Z"],
            "split": ["train"],
        }
    )
    with pytest.raises(SplitLeakageError, match="target horizon crosses"):
        audit_target_window_purge(bad, config)
