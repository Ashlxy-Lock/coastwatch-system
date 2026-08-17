# ImpactNet v2 Shadow Deployment Runbook

1. Verify bundle SHA-256 values before loading.
2. Confirm `shadow_mode=true`, label mode, synthetic flag, approved sites, feature
   schema and model variant in `manifest.json`.
3. Refuse synthetic bundles outside local engineering tests.
4. Start the separate v2 FastAPI app; do not replace the legacy `/api/v1/risk` route.
5. Exercise health, model-info, feature prediction, corrupt-bundle rejection, and
   insufficient-data paths.
6. Confirm source issue times are not later than prediction time.
7. Monitor missingness, age, range, prediction distribution, latency and fallback
   rate. Drift alerts never trigger automatic retraining.
8. Roll back by restoring the previous verified bundle path; retain prediction logs
   and both bundle manifests.

Public traffic must remain behind an authenticated server-side integration. Never
expose data-source or device credentials to browser code.

