"""Guard the audited v1 boundary while ImpactNet v2 evolves in parallel."""

from __future__ import annotations

import hashlib
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[3]


def _sha256(relative_path: str) -> str:
    digest = hashlib.sha256()
    with (WORKSPACE / relative_path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def test_v1_production_artifacts_and_integrations_are_unchanged() -> None:
    expected = {
        "ml/data/processed/coastal_history.csv.gz": (
            "9F1680094E6A9AF79023635934DF0B297489178230B1B6F08FB74570B34E7D1B"
        ),
        "ml/reports/coastal_risk_v1_metrics.json": (
            "34B978F0F9F1990EF16754B4946C67FBD51FDD99438FA1A0C90B29B9181919F4"
        ),
        "server/models/coastal_risk_v1.json": (
            "A766E49533176A085ECF4EE61E226CFF42C961FD3F4534FFB20EECB8C1102680"
        ),
        "ml/coastal_risk/locations.json": (
            "DC8AD17D5E649F2011CB0EE2DED7F7EFB2B9A8FC83539AEE6EF3774E3E4149B9"
        ),
        "ml/coastal_risk/train.py": (
            "4DDC55112186AA0FCF9C200E815FEBBC00C4FDDCE70C16EA195A0FE70B8B8A3C"
        ),
        "server/app/risk_model.py": (
            "055F3D68B31993F5945A9E9A5E9267A3EF329464818E9AD319ECDAB9E0BD7566"
        ),
        "website/app/model.ts": (
            "8084E346A9A197EF3ED9501E65D28DF967642CD7A2F85AC0C125732950CB30B3"
        ),
        "website/app/page.tsx": (
            "9FBD1A83C2F7C18FBF23332DEC9BD56A16F37A9405117C43B6C499EE4867F915"
        ),
    }
    assert {path: _sha256(path) for path in expected} == expected
