from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from coastwatch_impact.data.preprocessing import (
    TrainingDataLeakageError,
    TrainOnlyPreprocessor,
)


def test_preprocessor_fits_train_only_and_preserves_missing_mask() -> None:
    train = pd.DataFrame(
        {
            "split": ["train", "train", "train"],
            "prediction_time_utc": pd.date_range("2025-01-01", periods=3, freq="h", tz="UTC"),
            "x": [1.0, np.nan, 3.0],
        }
    )
    preprocessor = TrainOnlyPreprocessor(["x"]).fit(train)
    batch = preprocessor.transform(pd.DataFrame({"x": [2.0, np.nan, 1_000_000.0]}))
    assert batch.values.shape == (3, 1)
    assert batch.observed_mask[:, 0].tolist() == [True, False, True]
    assert np.isfinite(batch.values).all()
    assert preprocessor.provenance_.fit_split == "train"


def test_preprocessor_rejects_validation_or_test_rows_during_fit() -> None:
    mixed = pd.DataFrame({"split": ["train", "test"], "x": [0.0, 1_000.0]})
    with pytest.raises(TrainingDataLeakageError, match="only split='train'"):
        TrainOnlyPreprocessor(["x"]).fit(mixed)
