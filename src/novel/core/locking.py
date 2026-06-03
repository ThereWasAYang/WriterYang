from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path


LOCK_FILE_NAME = ".writeryang.lock"
DEFAULT_STALE_AFTER = timedelta(hours=12)


class ProjectLockError(RuntimeError):
    """Raised when a project write lock cannot be acquired."""


@dataclass(frozen=True)
class ProjectLockInfo:
    path: Path
    pid: int | None
    task: str | None
    created_at: str | None


class ProjectLock:
    def __init__(
        self,
        root: Path,
        *,
        task: str,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.task = task
        self.stale_after = stale_after
        self.path = self.root / LOCK_FILE_NAME
        self._acquired = False

    def __enter__(self) -> ProjectLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()

    def acquire(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "task": self.task,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._is_stale():
                    self._remove_stale_lock()
                    continue
                info = read_project_lock(self.root)
                raise ProjectLockError(
                    "project is locked by another process "
                    f"(pid={info.pid or 'unknown'}, task={info.task or 'unknown'}, "
                    f"created_at={info.created_at or 'unknown'}, lock={self.path})"
                )
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            self._acquired = True
            return

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            data = _read_lock_payload(self.path)
            if data.get("pid") == os.getpid():
                self.path.unlink(missing_ok=True)
        finally:
            self._acquired = False

    def _is_stale(self) -> bool:
        data = _read_lock_payload(self.path)
        pid = _int_or_none(data.get("pid"))
        if pid is not None and not _pid_exists(pid):
            return True
        created_at = _parse_timestamp(data.get("created_at"))
        if created_at is not None and datetime.now(timezone.utc) - created_at > self.stale_after:
            return True
        return False

    def _remove_stale_lock(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ProjectLockError(f"stale project lock could not be removed: {self.path}") from exc


def read_project_lock(root: Path) -> ProjectLockInfo:
    path = root.expanduser().resolve() / LOCK_FILE_NAME
    data = _read_lock_payload(path)
    raw_task = data.get("task")
    raw_created_at = data.get("created_at")
    return ProjectLockInfo(
        path=path,
        pid=_int_or_none(data.get("pid")),
        task=raw_task if isinstance(raw_task, str) else None,
        created_at=raw_created_at if isinstance(raw_created_at, str) else None,
    )


def _read_lock_payload(path: Path) -> dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
