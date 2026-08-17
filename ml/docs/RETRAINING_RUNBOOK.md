# ImpactNet v2 Retraining Runbook

1. Import immutable raw data and manifests; never train directly from mutable web
   responses.
2. Version and review the event catalogue. Do not turn unknown hours into negatives.
3. Build a new processed dataset and run leakage audits.
4. Freeze global split boundaries and storm-group assignments.
5. Fit preprocessing, positive weights and baselines on train only.
6. Train candidate networks; use validation only for early stopping, calibration and
   operating thresholds.
7. Freeze all choices, then run final test once and produce prediction-level audit
   files, event metrics and storm-group bootstrap intervals.
8. Export a hash-verified bundle and run Shadow Mode replay.
9. Compare the candidate against the current version. Human review is required to
   change the default bundle. Online continuous learning is prohibited.

Monitoring and delayed-label jobs are described in `MONITORING_RUNBOOK.md`.
Their drift flags and rolling event metrics are evidence for human review only;
they never invoke this retraining workflow automatically.
