import pandas as pd

from coastwatch_impact.evaluation import summarize_leave_one_site_out


def test_loso_summary_exposes_worst_site_variance_and_failure():
    result = summarize_leave_one_site_out(
        pd.DataFrame(
            {
                "held_out_site_id": ["a", "b", "c"],
                "event_recall": [0.8, 0.0, 0.4],
            }
        )
    )
    assert result["worst_site_id"] == "b"
    assert result["worst_site_event_recall"] == 0.0
    assert result["complete_failure_sites"] == ["b"]
    assert result["has_complete_failure"] is True
    assert result["event_recall_variance"] > 0.0
    assert result["insufficient_evidence"] is False
