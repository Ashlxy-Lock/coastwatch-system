"""Provision one shared device token without ever printing its value."""

from __future__ import annotations

import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_TOKEN_FILE = ROOT / "server" / ".device_token"
FIRMWARE_HEADER = (
    ROOT / "firmware" / "esp32" / "include" / "tunnel_secret.h"
)


def load_or_create_token() -> str:
    if SERVER_TOKEN_FILE.exists():
        token = SERVER_TOKEN_FILE.read_text(encoding="ascii").strip()
        if len(token) < 32:
            raise RuntimeError("Existing device token is unexpectedly short")
        return token

    token = secrets.token_urlsafe(32)
    SERVER_TOKEN_FILE.write_text(token + "\n", encoding="ascii")
    return token


def main() -> None:
    token = load_or_create_token()
    FIRMWARE_HEADER.write_text(
        "#pragma once\n\n"
        "// Generated locally by tools/ensure_tunnel_secret.py.\n"
        "// This file is ignored by Git; never paste the token into logs.\n"
        f'#define DEVICE_TOKEN "{token}"\n',
        encoding="ascii",
    )
    print("Tunnel device credential is ready; value intentionally hidden.")


if __name__ == "__main__":
    main()
