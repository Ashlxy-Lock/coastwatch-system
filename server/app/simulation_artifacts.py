"""Cross-process coordination and provenance checks for simulation artifacts.

The standalone internal API and authenticated gateway run as separate Python
processes against the same database and model directory.  Training and
destructive dataset maintenance therefore share both an in-process re-entrant
lock and an operating-system byte lock.  A session cannot disappear between
the training dataset read and atomic artifact replacement in either process.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from threading import RLock, local
from types import TracebackType
from typing import Any, BinaryIO, Self

from .model_registry import custom_model_path
from .simulation_model import SimulationModelError, load_simulation_model

_LOCK_FILE_NAME = ".simulation-artifacts.lock"
_LOCK_ACQUIRE_TIMEOUT_SECONDS = 60.0
_LOCK_RETRY_SECONDS = 0.05


class SimulationArtifactLockError(RuntimeError):
    """The cross-process artifact maintenance lock is unavailable."""


def _lock_file_path() -> Path:
    return custom_model_path().parent / _LOCK_FILE_NAME


def _try_lock_file(handle: BinaryIO) -> bool:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import errno
    import fcntl

    fcntl_runtime: Any = fcntl
    try:
        fcntl_runtime.flock(
            handle.fileno(), fcntl_runtime.LOCK_EX | fcntl_runtime.LOCK_NB
        )
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise
    return True


def _unlock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl_runtime: Any = fcntl
    fcntl_runtime.flock(handle.fileno(), fcntl_runtime.LOCK_UN)


def _acquire_cross_process_lock(deadline: float) -> BinaryIO:
    path = _lock_file_path()
    handle: BinaryIO | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        while not _try_lock_file(handle):
            if time.monotonic() >= deadline:
                raise SimulationArtifactLockError(
                    "simulation artifact maintenance lock timed out"
                )
            time.sleep(_LOCK_RETRY_SECONDS)
        return handle
    except SimulationArtifactLockError:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        raise
    except (OSError, ValueError) as exc:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        raise SimulationArtifactLockError(
            "simulation artifact maintenance lock is unavailable"
        ) from exc


class _SimulationArtifactLock:
    """A re-entrant thread lock backed by one shared OS lock file."""

    def __init__(self) -> None:
        self._thread_lock = RLock()
        self._state = local()

    def __enter__(self) -> Self:
        deadline = time.monotonic() + _LOCK_ACQUIRE_TIMEOUT_SECONDS
        if not self._thread_lock.acquire(timeout=_LOCK_ACQUIRE_TIMEOUT_SECONDS):
            raise SimulationArtifactLockError(
                "simulation artifact maintenance lock timed out"
            )
        try:
            depth = int(getattr(self._state, "depth", 0))
            if depth == 0:
                self._state.handle = _acquire_cross_process_lock(deadline)
            self._state.depth = depth + 1
            return self
        except BaseException:
            self._thread_lock.release()
            raise

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        depth = int(getattr(self._state, "depth", 0))
        try:
            if depth <= 0:  # pragma: no cover - context manager invariant
                raise RuntimeError("simulation artifact lock exit without enter")
            if depth == 1:
                handle = self._state.handle
                try:
                    _unlock_file(handle)
                finally:
                    try:
                        handle.close()
                    finally:
                        del self._state.handle
                        del self._state.depth
            else:
                self._state.depth = depth - 1
        finally:
            self._thread_lock.release()


SIMULATION_ARTIFACT_LOCK = _SimulationArtifactLock()


class SimulationArtifactVerificationError(RuntimeError):
    """An artifact cannot be trusted enough to prove deletion is safe."""


def _artifact_error(path: Path) -> SimulationArtifactVerificationError:
    return SimulationArtifactVerificationError(
        f"training artifact {path.name} cannot be verified; session deletion is blocked"
    )


def _artifact_candidates() -> list[Path]:
    """Return the current model and every archived/staged run artifact.

    Archive entries and staged files are intentionally not filtered by suffix.
    A stale or unexpected entry makes deletion fail closed.  A legitimate
    training temporary file cannot be observed here because both operations
    hold ``SIMULATION_ARTIFACT_LOCK``.
    """

    current = custom_model_path()
    candidates: list[Path] = []

    if os.path.lexists(current):
        if current.is_symlink() or not current.is_file():
            raise _artifact_error(current)
        candidates.append(current)

    parent = current.parent
    if parent.exists():
        try:
            staged = sorted(
                (
                    entry
                    for entry in parent.iterdir()
                    if entry.name.startswith(f".{current.name}.")
                    and entry.name.endswith(".tmp")
                ),
                key=lambda entry: entry.name,
            )
        except OSError as exc:
            raise _artifact_error(current) from exc
        candidates.extend(staged)

    archive_directory = parent / "runs"
    if os.path.lexists(archive_directory):
        if archive_directory.is_symlink() or not archive_directory.is_dir():
            raise _artifact_error(archive_directory)
        try:
            candidates.extend(
                sorted(archive_directory.iterdir(), key=lambda entry: entry.name)
            )
        except OSError as exc:
            raise _artifact_error(archive_directory) from exc

    verified_paths: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.is_symlink() or not path.is_file():
            raise _artifact_error(path)
        verified_paths.append(path)
    return verified_paths


def _session_id_list(value: Any, path: Path) -> list[str]:
    if not isinstance(value, list) or not value:
        raise _artifact_error(path)
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise _artifact_error(path)
        result.append(item.strip())
    if len(result) != len(set(result)):
        raise _artifact_error(path)
    return result


def _session_ids_from_records(value: Any, path: Path) -> list[str]:
    if not isinstance(value, list) or not value:
        raise _artifact_error(path)
    result: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise _artifact_error(path)
        session_id = item.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise _artifact_error(path)
        result.append(session_id.strip())
    if len(result) != len(set(result)):
        raise _artifact_error(path)
    return result


def _verified_source_session_ids(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise _artifact_error(path)
        # This validates the complete schema, feature layout and content hash.
        load_simulation_model(payload)
    except SimulationArtifactVerificationError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        SimulationModelError,
        TypeError,
        ValueError,
    ) as exc:
        raise _artifact_error(path) from exc

    manifest = payload.get("source_manifest")
    if not isinstance(manifest, Mapping):
        raise _artifact_error(path)
    session_ids = _session_id_list(manifest.get("session_ids"), path)
    session_id_set = set(session_ids)
    session_count = manifest.get("session_count")
    if (
        isinstance(session_count, bool)
        or not isinstance(session_count, int)
        or session_count != len(session_ids)
    ):
        raise _artifact_error(path)

    # These are provenance records for rows that actually entered training.
    # ``selection.available_completed_session_ids`` is intentionally excluded:
    # it lists datasets that existed at the time but were not necessarily used.
    record_ids = _session_ids_from_records(manifest.get("sessions"), path)
    if set(record_ids) != session_id_set:
        raise _artifact_error(path)
    if "collection_sessions" in manifest:
        collection_ids = _session_ids_from_records(
            manifest.get("collection_sessions"), path
        )
        if set(collection_ids) != session_id_set:
            raise _artifact_error(path)
    if "eligible_session_ids" in manifest:
        eligible_ids = _session_id_list(manifest.get("eligible_session_ids"), path)
        if set(eligible_ids) != session_id_set:
            raise _artifact_error(path)
    selection = manifest.get("selection")
    if selection is not None:
        if not isinstance(selection, Mapping):
            raise _artifact_error(path)
        effective_ids = _session_id_list(
            selection.get("effective_session_ids"),
            path,
        )
        if set(effective_ids) != session_id_set:
            raise _artifact_error(path)
    return session_id_set


def referencing_training_artifact(session_id: str) -> str | None:
    """Return the first verified artifact that used ``session_id``."""

    with SIMULATION_ARTIFACT_LOCK:
        for path in _artifact_candidates():
            if session_id in _verified_source_session_ids(path):
                return path.name
    return None


__all__ = [
    "SIMULATION_ARTIFACT_LOCK",
    "SimulationArtifactLockError",
    "SimulationArtifactVerificationError",
    "referencing_training_artifact",
]
