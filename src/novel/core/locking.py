from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import uuid

from novel.core.io import append_jsonl
from novel.core.timeutil import utc_now, utc_now_iso, utc_now_precise


LOCK_FILE_NAME = ".writeryang.lock"
DEFAULT_STALE_AFTER = timedelta(hours=12)
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0
_PROCESS_STARTED_AT = utc_now_iso()


class ProjectLockError(RuntimeError):
    """Raised when a project write lock cannot be acquired."""


@dataclass(frozen=True)
class ProjectLockInfo:
    path: Path
    pid: int | None
    task: str | None
    created_at: str | None
    heartbeat_at: str | None
    host: str | None
    process_start_time: str | None
    workflow_run_id: str | None
    command_id: str | None
    lock_id: str | None


class ProjectLock:
    def __init__(
        self,
        root: Path,
        *,
        task: str,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
        workflow_run_id: str | None = None,
        command_id: str | None = None,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.task = task
        self.stale_after = stale_after
        self.workflow_run_id = workflow_run_id
        self.command_id = command_id
        self.lock_id = f"lock_{uuid.uuid4().hex}"
        self.heartbeat_interval_seconds = heartbeat_interval_seconds or min(
            DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
            max(0.1, stale_after.total_seconds() / 3),
        )
        self.path = self.root / LOCK_FILE_NAME
        self._acquired = False
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def __enter__(self) -> ProjectLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()

    def acquire(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        now = _heartbeat_timestamp()
        payload = {
            "lock_id": self.lock_id,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "process_start_time": _process_start_time(os.getpid()),
            "task": self.task,
            "workflow_run_id": self.workflow_run_id,
            "command_id": self.command_id,
            "created_at": now,
            "heartbeat_at": now,
        }
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                stale_reason = self._stale_reason()
                if stale_reason:
                    self._remove_stale_lock(stale_reason)
                    continue
                info = read_project_lock(self.root)
                raise ProjectLockError(
                    "project is locked by another process "
                    f"(pid={info.pid or 'unknown'}, task={info.task or 'unknown'}, "
                    f"host={info.host or 'unknown'}, heartbeat_at={info.heartbeat_at or 'unknown'}, "
                    f"workflow_run_id={info.workflow_run_id or 'unknown'}, lock={self.path})"
                )
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            self._acquired = True
            self._start_heartbeat()
            return

    def release(self) -> None:
        if not self._acquired:
            return
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=max(1.0, self.heartbeat_interval_seconds * 2))
        try:
            data = _read_lock_payload(self.path)
            if data.get("lock_id") == self.lock_id:
                self.path.unlink(missing_ok=True)
        finally:
            self._acquired = False
            self._heartbeat_thread = None

    def _stale_reason(self) -> str | None:
        data = _read_lock_payload(self.path)
        pid = _int_or_none(data.get("pid"))
        host = data.get("host")
        if host == socket.gethostname() and pid is not None:
            if not _pid_exists(pid):
                return "process_not_running"
            recorded_start = data.get("process_start_time")
            actual_start = _process_start_time(pid)
            if isinstance(recorded_start, str) and actual_start and recorded_start != actual_start:
                return "process_identity_mismatch"
        heartbeat = _parse_timestamp(data.get("heartbeat_at")) or _parse_timestamp(data.get("created_at"))
        if heartbeat is not None and utc_now() - heartbeat > self.stale_after:
            return "heartbeat_timeout"
        if heartbeat is None and not data:
            return "invalid_lock_payload"
        return None

    def _remove_stale_lock(self, reason: str) -> None:
        previous = _read_lock_payload(self.path)
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ProjectLockError(f"stale project lock could not be removed: {self.path}") from exc
        append_jsonl(
            self.root / "runs" / "lock_events.jsonl",
            {
                "event": "stale_lock_reclaimed",
                "reason": reason,
                "removed_at": utc_now_iso(),
                "removed_by_pid": os.getpid(),
                "removed_by_host": socket.gethostname(),
                "previous_lock": previous,
            },
        )

    def _start_heartbeat(self) -> None:
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"writeryang-lock-heartbeat-{self.lock_id[-8:]}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(self.heartbeat_interval_seconds):
            data = _read_lock_payload(self.path)
            if data.get("lock_id") != self.lock_id:
                return
            data["heartbeat_at"] = _heartbeat_timestamp()
            try:
                _write_lock_payload(self.path, data, self.lock_id)
            except OSError:
                return


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
        heartbeat_at=_str_or_none(data.get("heartbeat_at")),
        host=_str_or_none(data.get("host")),
        process_start_time=_str_or_none(data.get("process_start_time")),
        workflow_run_id=_str_or_none(data.get("workflow_run_id")),
        command_id=_str_or_none(data.get("command_id")),
        lock_id=_str_or_none(data.get("lock_id")),
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


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _heartbeat_timestamp() -> str:
    return utc_now_precise().isoformat(timespec="microseconds").replace("+00:00", "Z")


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


def _process_start_time(pid: int) -> str | None:
    try:
        completed = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return _PROCESS_STARTED_AT if pid == os.getpid() else None
    raw = completed.stdout.strip()
    if not raw:
        return _PROCESS_STARTED_AT if pid == os.getpid() else None
    try:
        parsed = datetime.strptime(raw, "%a %b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return raw
    return parsed.isoformat().replace("+00:00", "Z")


def _write_lock_payload(path: Path, payload: dict[str, object], lock_id: str) -> None:
    if _read_lock_payload(path).get("lock_id") != lock_id:
        return
    temp = path.with_name(f"{path.name}.{lock_id}.tmp")
    fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        if _read_lock_payload(path).get("lock_id") != lock_id:
            return
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
