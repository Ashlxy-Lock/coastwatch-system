from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import uvicorn

DEPLOYMENT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
APP_TARGET_FILES = {
    "app.main:app": Path("app/main.py"),
    "app.gateway:app": Path("app/gateway.py"),
}
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--deployment-id-file", required=True, type=Path)
    parser.add_argument("--identity-file", required=True, type=Path)
    return parser.parse_args()


def _is_reparse_point(path: Path) -> bool:
    details = path.lstat()
    return path.is_symlink() or bool(
        getattr(details, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _validated_app_dir(path: Path, app_target: str) -> Path:
    if not path.is_absolute():
        raise RuntimeError("Runtime application directory must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("Runtime application directory is unavailable") from exc
    if not resolved.is_dir() or _is_reparse_point(path):
        raise RuntimeError("Runtime application directory is unsafe")
    target_relative = APP_TARGET_FILES.get(app_target)
    if target_relative is None:
        raise RuntimeError("Unsupported runtime application target")
    package_dir = resolved / "app"
    target_file = resolved / target_relative
    models_dir = resolved / "models"
    model_file = models_dir / "coastal_risk_v1.json"
    for required in (package_dir, target_file, models_dir, model_file):
        if not required.exists() or _is_reparse_point(required):
            raise RuntimeError("Runtime application payload is incomplete or unsafe")
    if (
        not package_dir.is_dir()
        or not target_file.is_file()
        or not models_dir.is_dir()
        or not model_file.is_file()
    ):
        raise RuntimeError("Runtime application payload has an invalid type")
    return resolved


def _deployment_id(path: Path) -> str:
    value = path.read_text(encoding="ascii").strip()
    if DEPLOYMENT_ID_PATTERN.fullmatch(value) is None:
        raise RuntimeError("Invalid runtime deployment identifier")
    return value


def _write_identity(path: Path, *, deployment_id: str, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = {
        "deployment_id": deployment_id,
        "pid": os.getpid(),
        "port": port,
        "started_at_unix": int(time.time()),
    }
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _clear_own_identity(path: Path, *, deployment_id: str) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("deployment_id") == deployment_id
            and payload.get("pid") == os.getpid()
        ):
            path.unlink(missing_ok=True)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return


def main() -> None:
    args = _parse_args()
    if not args.deployment_id_file.is_absolute() or not args.identity_file.is_absolute():
        raise RuntimeError("Runtime identity paths must be absolute")
    deployment_id_file = args.deployment_id_file.resolve(strict=True)
    identity_file = args.identity_file.resolve(strict=False)
    app_dir = _validated_app_dir(args.app_dir, args.app)
    deployment_id = _deployment_id(deployment_id_file)
    original_cwd = Path.cwd()
    app_dir_text = str(app_dir)
    inserted_app_dir = not sys.path or sys.path[0] != app_dir_text
    identity_written = False
    try:
        os.chdir(app_dir)
        if inserted_app_dir:
            sys.path.insert(0, app_dir_text)
        _write_identity(
            identity_file,
            deployment_id=deployment_id,
            port=args.port,
        )
        identity_written = True
        uvicorn.run(
            args.app,
            host=args.host,
            port=args.port,
            access_log=False,
            app_dir=app_dir_text,
        )
    finally:
        if identity_written:
            _clear_own_identity(identity_file, deployment_id=deployment_id)
        if inserted_app_dir:
            try:
                sys.path.remove(app_dir_text)
            except ValueError:
                pass
        os.chdir(original_cwd)


if __name__ == "__main__":
    main()
