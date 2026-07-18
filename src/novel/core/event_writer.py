from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from novel.core.timeutil import utc_now_iso

try:  # pragma: no cover - Windows uses the process-local lock fallback
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


DEFAULT_EVENT_FILE_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_EVENT_FILE_BACKUPS = 5
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class EventWriteError(RuntimeError):
    """Raised when a structured local event cannot be written safely."""


class EventWriter:
    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = DEFAULT_EVENT_FILE_MAX_BYTES,
        backup_count: int = DEFAULT_EVENT_FILE_BACKUPS,
        fsync: bool = True,
    ) -> None:
        self.path = path.expanduser()
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.fsync = fsync

    def append(self, data: object) -> None:
        payload = _normalize_event(data, event_type=self.path.stem)
        line = (json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        if len(line) > self.max_bytes:
            raise EventWriteError(
                f"event line for {self.path} exceeds file limit of {self.max_bytes} bytes"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        process_lock = _path_lock(lock_path)
        with process_lock:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if fcntl is not None:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                self._rotate_if_needed(len(line))
                fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
                try:
                    os.write(fd, line)
                    if self.fsync:
                        os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError as exc:
                raise EventWriteError(f"failed to append event to {self.path}: {exc}") from exc
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if not self.path.exists() or self.path.stat().st_size + incoming_bytes <= self.max_bytes:
            return
        if self.backup_count < 1:
            self.path.unlink(missing_ok=True)
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                source.replace(self.path.with_name(f"{self.path.name}.{index + 1}"))
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))


def _normalize_event(data: object, *, event_type: str) -> object:
    if not isinstance(data, Mapping):
        return {
            "timestamp": utc_now_iso(),
            "event_type": event_type,
            "data": data,
        }
    payload: dict[str, Any] = {str(key): value for key, value in data.items()}
    if not any(key in payload for key in ("timestamp", "created_at", "started_at", "updated_at")):
        payload["timestamp"] = utc_now_iso()
    payload.setdefault("event_type", payload.get("event") or event_type)
    return payload


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())
