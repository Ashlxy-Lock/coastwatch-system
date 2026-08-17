from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

MODULE_PATH = Path(__file__).resolve().parents[1] / "run-uvicorn.py"
SPEC = importlib.util.spec_from_file_location("coastal_run_uvicorn", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SERVER_DIR = MODULE_PATH.parents[1] / "server"


class RuntimeIdentityTests(unittest.TestCase):
    def test_identity_is_atomic_and_bound_to_current_pid_and_deployment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="coastal-runtime-identity-") as root:
            identity_path = Path(root) / "identity.json"
            deployment_id = "a" * 32

            MODULE._write_identity(
                identity_path,
                deployment_id=deployment_id,
                port=8000,
            )

            payload = json.loads(identity_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["deployment_id"], deployment_id)
            self.assertEqual(payload["pid"], os.getpid())
            self.assertEqual(payload["port"], 8000)
            self.assertEqual(list(Path(root).glob(".*.tmp")), [])

            MODULE._clear_own_identity(
                identity_path,
                deployment_id=deployment_id,
            )
            self.assertFalse(identity_path.exists())

    def test_identity_cleanup_does_not_delete_another_process_record(self) -> None:
        with tempfile.TemporaryDirectory(prefix="coastal-runtime-identity-") as root:
            identity_path = Path(root) / "identity.json"
            identity_path.write_text(
                json.dumps(
                    {
                        "deployment_id": "b" * 32,
                        "pid": os.getpid() + 1,
                        "port": 8001,
                    }
                ),
                encoding="utf-8",
            )

            MODULE._clear_own_identity(
                identity_path,
                deployment_id="b" * 32,
            )
            self.assertTrue(identity_path.exists())

    def test_deployment_id_rejects_malformed_values(self) -> None:
        with tempfile.TemporaryDirectory(prefix="coastal-runtime-identity-") as root:
            deployment_path = Path(root) / "deployment-id.txt"
            deployment_path.write_text("not-a-deployment-id", encoding="ascii")
            with self.assertRaisesRegex(
                RuntimeError,
                "Invalid runtime deployment identifier",
            ):
                MODULE._deployment_id(deployment_path)


class RuntimeApplicationContractTests(unittest.TestCase):
    def _exercise_application(self, app_target: str, port: int) -> None:
        with tempfile.TemporaryDirectory(prefix="coastal-runtime-app-") as root:
            root_path = Path(root)
            non_server_cwd = root_path / "working"
            non_server_cwd.mkdir()
            deployment_path = root_path / "deployment-id.txt"
            deployment_id = "c" * 32
            deployment_path.write_text(deployment_id, encoding="ascii")
            identity_path = root_path / "identity.json"
            device_token = "d" * 32
            environment = {
                "COAST_DEVICE_TOKEN": device_token,
                "COAST_ADMIN_PASSWORD_HASH": (
                    "pbkdf2_sha256$310000$" + "11" * 16 + "$" + "22" * 32
                ),
                "COAST_ADMIN_SESSION_SECRET": "s" * 43,
                "COASTAL_DB_PATH": str(root_path / "runtime.db"),
                "COAST_CUSTOM_MODEL_PATH": str(root_path / "custom-model.json"),
                "COAST_RISK_MODEL_PATH": "",
            }
            args = SimpleNamespace(
                app=app_target,
                host="127.0.0.1",
                port=port,
                app_dir=SERVER_DIR.resolve(),
                deployment_id_file=deployment_path.resolve(),
                identity_file=identity_path.resolve(),
            )

            def short_run(app: str, **kwargs: object) -> None:
                self.assertEqual(Path.cwd(), SERVER_DIR.resolve())
                self.assertEqual(sys.path[0], str(SERVER_DIR.resolve()))
                self.assertEqual(kwargs["app_dir"], str(SERVER_DIR.resolve()))
                module_name, attribute_name = app.split(":", 1)
                application = getattr(
                    importlib.import_module(module_name),
                    attribute_name,
                )
                headers = (
                    {"X-Device-Token": device_token}
                    if app_target == "app.gateway:app"
                    else {}
                )
                with TestClient(application) as client:
                    response = client.get("/api/v1/health", headers=headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["status"], "ok")
                    self.assertIsNotNone(application.state.risk_model)
                identity = json.loads(identity_path.read_text(encoding="utf-8"))
                self.assertEqual(identity["deployment_id"], deployment_id)
                self.assertEqual(identity["pid"], os.getpid())
                self.assertEqual(identity["port"], port)

            original_cwd = Path.cwd()
            try:
                os.chdir(non_server_cwd)
                with (
                    mock.patch.object(MODULE, "_parse_args", return_value=args),
                    mock.patch.object(MODULE.uvicorn, "run", side_effect=short_run),
                    mock.patch.dict(os.environ, environment, clear=True),
                ):
                    MODULE.main()
                self.assertEqual(Path.cwd(), non_server_cwd)
                self.assertFalse(identity_path.exists())
            finally:
                os.chdir(original_cwd)

    def test_main_imports_and_starts_from_non_server_directory(self) -> None:
        self._exercise_application("app.main:app", 8000)

    def test_gateway_imports_and_starts_from_non_server_directory(self) -> None:
        self._exercise_application("app.gateway:app", 8001)


if __name__ == "__main__":
    unittest.main()
