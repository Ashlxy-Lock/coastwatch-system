"""Leave-one-site-out spatial generalisation summaries."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def summarize_leave_one_site_out(
    site_metrics: pd.DataFrame,
    *,
    site_col: str = "held_out_site_id",
    recall_col: str = "event_recall",
) -> dict[str, Any]:
    """Summarise frozen per-site folds without hiding complete failures."""

    required = {site_col, recall_col}
    missing = required.difference(site_metrics.columns)
    if missing:
        raise ValueError(f"LOSO metrics are missing columns: {sorted(missing)}")
    if site_metrics.empty or site_metrics[site_col].astype(str).duplicated().any():
        raise ValueError("LOSO metrics require one row per held-out site")
    recalls = pd.to_numeric(site_metrics[recall_col], errors="coerce")
    if recalls.isna().any() or (~recalls.between(0.0, 1.0)).any():
        raise ValueError("LOSO event recall must be finite and lie in [0, 1]")
    values = recalls.to_numpy(dtype=np.float64)
    worst_index = int(np.argmin(values))
    records = site_metrics.copy()
    records[site_col] = records[site_col].astype(str)
    return {
        "folds": int(len(records)),
        "per_site": records.to_dict(orient="records"),
        "mean_event_recall": float(np.mean(values)),
        "event_recall_variance": float(np.var(values)),
        "worst_site_id": str(records.iloc[worst_index][site_col]),
        "worst_site_event_recall": float(values[worst_index]),
        "complete_failure_sites": records.loc[recalls.eq(0.0), site_col].tolist(),
        "has_complete_failure": bool(np.any(values == 0.0)),
        "insufficient_evidence": len(records) < 3,
    }


__all__ = ["summarize_leave_one_site_out"]
