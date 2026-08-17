from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

ADMIN_USERNAME = "admin"
ADMIN_SESSION_COOKIE = "coastwatch_admin_session"
ADMIN_CSRF_HEADER = "X-CSRF-Token"
ADMIN_COOKIE_PATH = "/admin"
SESSION_MAX_AGE_SECONDS = 8 * 60 * 60

PASSWORD_HASH_ENV = "COAST_ADMIN_PASSWORD_HASH"
PASSWORD_HASH_FILE_ENV = "COAST_ADMIN_PASSWORD_HASH_FILE"
SESSION_SECRET_ENV = "COAST_ADMIN_SESSION_SECRET"
SESSION_SECRET_FILE_ENV = "COAST_ADMIN_SESSION_SECRET_FILE"


class AdminLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AdminSessionResponse(BaseModel):
    authenticated: bool
    username: str
    csrf_token: str


@dataclass(frozen=True)
class AdminAuthConfig:
    password_iterations: int
    password_salt: bytes
    password_digest: bytes
    session_secret: bytes


@dataclass(frozen=True)
class AdminSession:
    username: str
    csrf_token: str
    issued_at: int
    expires_at: int


class _LoginFailureLimiter:
    def __init__(
        self, *, limit: int = 5, window_seconds: int = 300, max_keys: int = 4096
    ) -> None:
        if limit < 1 or window_seconds < 1 or max_keys < 1:
            raise ValueError("Login limiter bounds must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _prune(self, attempts: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()

    def _remove_expired(self, now: float) -> None:
        cutoff = now - self.window_seconds
        expired = [
            key
            for key, attempts in self._failures.items()
            if not attempts or attempts[-1] <= cutoff
        ]
        for key in expired:
            self._failures.pop(key, None)

    def retry_after(self, key: str, *, now: float | None = None) -> int | None:
        current = time.monotonic() if now is None else now
        with self._lock:
            self._remove_expired(current)
            attempts = self._failures.get(key)
            if attempts is None:
                return None
            self._prune(attempts, current)
            if not attempts:
                self._failures.pop(key, None)
                return None
            self._failures.move_to_end(key)
            if len(attempts) < self.limit:
                return None
            return max(1, int(self.window_seconds - (current - attempts[0]) + 0.999))

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        with self._lock:
            self._remove_expired(current)
            attempts = self._failures.setdefault(key, deque())
            self._prune(attempts, current)
            attempts.append(current)
            while len(attempts) > self.limit:
                attempts.popleft()
            self._failures.move_to_end(key)
            while len(self._failures) > self.max_keys:
                self._failures.popitem(last=False)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._failures)


_LOGIN_FAILURES = _LoginFailureLimiter()


def reset_login_failures() -> None:
    _LOGIN_FAILURES.reset()


def encode_admin_password_hash(
    password: str, *, salt: bytes | None = None, iterations: int = 310_000
) -> str:
    """Return the deployable PBKDF2 value; the plaintext is never persisted."""

    if iterations < 100_000:
        raise ValueError("PBKDF2 iterations must be at least 100000")
    chosen_salt = secrets.token_bytes(16) if salt is None else salt
    if len(chosen_salt) < 16:
        raise ValueError("Password salt must contain at least 16 bytes")
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), chosen_salt, iterations
    )
    return f"pbkdf2_sha256${iterations}${chosen_salt.hex()}${digest.hex()}"


def _read_setting_or_file(value_env: str, file_env: str) -> str:
    direct_value = os.getenv(value_env)
    configured_file = os.getenv(file_env)
    if direct_value is not None and configured_file is not None:
        raise RuntimeError(f"Set only one of {value_env} or {file_env}")
    if direct_value is not None:
        value = direct_value.strip()
    elif configured_file is not None:
        path_text = configured_file.strip()
        if not path_text:
            raise RuntimeError(f"{file_env} must not be blank")
        try:
            value = Path(path_text).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(f"Unable to read {file_env}") from exc
    else:
        raise RuntimeError(f"Set {value_env} or {file_env}")
    if not value:
        raise RuntimeError(f"{value_env} credential must not be blank")
    return value


def _parse_password_hash(encoded: str) -> tuple[int, bytes, bytes]:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = encoded.split("$", 3)
        iterations = int(iterations_text)
        salt = bytes.fromhex(salt_hex)
        digest = bytes.fromhex(digest_hex)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Administrator password hash is malformed") from exc
    if algorithm != "pbkdf2_sha256" or iterations < 100_000:
        raise RuntimeError("Administrator password must use strong PBKDF2-SHA256")
    if len(salt) < 16 or len(digest) != hashlib.sha256().digest_size:
        raise RuntimeError("Administrator password hash has invalid salt or digest")
    return iterations, salt, digest


def load_admin_auth_config() -> AdminAuthConfig:
    encoded_password = _read_setting_or_file(PASSWORD_HASH_ENV, PASSWORD_HASH_FILE_ENV)
    iterations, salt, password_digest = _parse_password_hash(encoded_password)
    session_secret_text = _read_setting_or_file(
        SESSION_SECRET_ENV, SESSION_SECRET_FILE_ENV
    )
    session_secret = session_secret_text.encode("utf-8")
    if len(session_secret) < 32:
        raise RuntimeError(
            "Administrator session secret must contain at least 32 bytes"
        )
    return AdminAuthConfig(
        password_iterations=iterations,
        password_salt=salt,
        password_digest=password_digest,
        session_secret=hashlib.sha256(session_secret).digest(),
    )


def verify_admin_credentials(
    config: AdminAuthConfig, username: str, password: str
) -> bool:
    candidate_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        config.password_salt,
        config.password_iterations,
    )
    username_matches = hmac.compare_digest(
        username.encode("utf-8"), ADMIN_USERNAME.encode("utf-8")
    )
    password_matches = hmac.compare_digest(candidate_digest, config.password_digest)
    # Bitwise AND ensures both constant-time comparisons are always evaluated.
    return bool(username_matches & password_matches)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def issue_admin_session(
    config: AdminAuthConfig, *, now: int | None = None
) -> tuple[str, AdminSession]:
    issued_at = int(time.time()) if now is None else now
    session = AdminSession(
        username=ADMIN_USERNAME,
        csrf_token=secrets.token_urlsafe(32),
        issued_at=issued_at,
        expires_at=issued_at + SESSION_MAX_AGE_SECONDS,
    )
    payload = json.dumps(
        {
            "v": 1,
            "sub": session.username,
            "csrf": session.csrf_token,
            "iat": session.issued_at,
            "exp": session.expires_at,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded_payload = _base64url_encode(payload)
    signature = hmac.new(
        config.session_secret, encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded_payload}.{_base64url_encode(signature)}", session


def decode_admin_session(
    config: AdminAuthConfig, token: str, *, now: int | None = None
) -> AdminSession | None:
    if not token or len(token) > 4096:
        return None
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        supplied_signature = _base64url_decode(encoded_signature)
        expected_signature = hmac.new(
            config.session_secret, encoded_payload.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return None
        payload = json.loads(_base64url_decode(encoded_payload).decode("utf-8"))
    except (
        ValueError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ):
        return None
    if not isinstance(payload, dict) or payload.get("v") != 1:
        return None
    username = payload.get("sub")
    csrf_token = payload.get("csrf")
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    if (
        not isinstance(username, str)
        or not isinstance(csrf_token, str)
        or not isinstance(issued_at, int)
        or isinstance(issued_at, bool)
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
    ):
        return None
    if not hmac.compare_digest(
        username.encode("utf-8"), ADMIN_USERNAME.encode("utf-8")
    ):
        return None
    current = int(time.time()) if now is None else now
    if issued_at > current + 60 or expires_at <= current:
        return None
    if expires_at - issued_at != SESSION_MAX_AGE_SECONDS:
        return None
    if len(csrf_token) < 32 or len(csrf_token) > 128:
        return None
    return AdminSession(username, csrf_token, issued_at, expires_at)


def current_admin_session(
    request: Request, config: AdminAuthConfig
) -> AdminSession | None:
    token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    return decode_admin_session(config, token)


def require_admin_session(request: Request, config: AdminAuthConfig) -> AdminSession:
    session = current_admin_session(request, config)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Administrator authentication required",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return session


def require_admin_csrf(request: Request, config: AdminAuthConfig) -> AdminSession:
    session = require_admin_session(request, config)
    supplied = request.headers.get(ADMIN_CSRF_HEADER, "")
    if not hmac.compare_digest(
        supplied.encode("utf-8"), session.csrf_token.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Valid CSRF token required",
        )
    return session


def login_client_key(request: Request) -> str:
    peer = request.client.host if request.client is not None else "unknown"
    try:
        peer_address = ipaddress.ip_address(peer)
    except ValueError:
        peer_address = None
    # cloudflared reaches this localhost-only gateway from loopback and
    # overwrites CF-Connecting-IP. Never trust X-Forwarded-For, or this header
    # from a non-loopback peer.
    if peer_address is not None and peer_address.is_loopback:
        connecting_ip = request.headers.get("CF-Connecting-IP", "").strip()
        try:
            return f"ip:{ipaddress.ip_address(connecting_ip).compressed}"
        except ValueError:
            pass
    return f"peer:{peer}"


def enforce_login_rate_limit(request: Request) -> str:
    key = login_client_key(request)
    retry_after = _LOGIN_FAILURES.retry_after(key)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts; try again later",
            headers={"Retry-After": str(retry_after)},
        )
    return key


def record_login_failure(key: str) -> None:
    _LOGIN_FAILURES.record_failure(key)


def clear_login_failures(key: str) -> None:
    _LOGIN_FAILURES.clear(key)


def set_admin_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path=ADMIN_COOKIE_PATH,
    )
    response.headers["Cache-Control"] = "no-store"


def clear_admin_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=ADMIN_SESSION_COOKIE,
        httponly=True,
        secure=True,
        samesite="lax",
        path=ADMIN_COOKIE_PATH,
    )
    response.headers["Cache-Control"] = "no-store"


LOGIN_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>海岸预警系统管理员登录</title>
<style>
:root{color-scheme:dark;--bg:#07151d;--panel:#102733;--line:#284958;--text:#eefbff;--muted:#8cabb7;--accent:#4bd6ff;--fault:#ff6b72}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at 20% 0,#123747 0,transparent 44%),var(--bg);font-family:Inter,Segoe UI,sans-serif;color:var(--text);padding:20px}
.card{width:min(420px,100%);background:linear-gradient(150deg,#122c39,#0b202a);border:1px solid var(--line);border-radius:18px;padding:28px;box-shadow:0 24px 80px #0008}
.eyebrow{color:var(--accent);font-size:12px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}h1{font-size:27px;margin:8px 0}.muted{color:var(--muted);font-size:14px;line-height:1.6;margin-bottom:22px}
label{display:block;color:var(--muted);font-size:13px;margin:14px 0 6px}input{width:100%;border:1px solid var(--line);border-radius:10px;background:#071820;color:var(--text);font:inherit;padding:12px 13px;outline:none}input:focus{border-color:var(--accent);box-shadow:0 0 0 3px #4bd6ff22}
button{width:100%;margin-top:20px;border:0;border-radius:10px;background:var(--accent);color:#06202a;font:inherit;font-weight:800;padding:12px;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.error{min-height:20px;color:var(--fault);font-size:13px;margin-top:12px}
</style></head><body><main class="card">
<div class="eyebrow">Protected console</div><h1>海岸预警系统管理员</h1>
<div class="muted">登录后才能访问数据标注、训练与设备管理后台。ESP32 设备接口使用独立凭据。</div>
<form id="loginForm"><label for="username">账号</label><input id="username" name="username" autocomplete="username" required maxlength="64" autofocus>
<label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="256">
<button id="submitLogin" type="submit">登录管理后台</button><div id="loginError" class="error" role="alert" aria-live="polite"></div></form>
</main><script>
const form=document.getElementById('loginForm'),button=document.getElementById('submitLogin'),error=document.getElementById('loginError');
form.addEventListener('submit',async event=>{event.preventDefault();button.disabled=true;error.textContent='';
  try{const response=await fetch('/admin/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:form.username.value,password:form.password.value})});
    if(!response.ok){let detail='登录失败';try{detail=(await response.json()).detail||detail}catch(_ignored){};throw new Error(response.status===429?'尝试次数过多，请稍后再试':detail)}
    window.location.replace('/admin/console');
  }catch(reason){error.textContent=String(reason.message||reason)}finally{button.disabled=false}
});
</script></body></html>"""
