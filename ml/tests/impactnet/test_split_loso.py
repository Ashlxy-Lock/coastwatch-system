from __future__ import annotations

import pandas as pd

from coastwatch_impact.data.split import build_leave_one_site_out_folds


def test_loso_keeps_final_test_frozen_and_removes_held_out_training_rows():
    rows = [
        {"site_id": site, "split": split, "row": f"{site}-{split}"}
        for site in ("a", "b", "c")
        for split in ("train", "validation", "test")
    ]
    folds = build_leave_one_site_out_folds(pd.DataFrame(rows))
    assert set(folds) == {"a", "b", "c"}
    fold = folds["a"]
    training = fold.loc[fold["cv_split"] == "train"]
    validation = fold.loc[fold["cv_split"] == "validation"]
    assert set(training["site_id"]) == {"b", "c"}
    assert set(training["split"]) == {"train"}
    assert validation[["site_id", "split"]].to_dict("records") == [
        {"site_id": "a", "split": "validation"}
    ]
    assert fold.loc[fold["split"] == "test", "cv_split"].eq("excluded").all()
