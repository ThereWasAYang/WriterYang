from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
from datetime import datetime, timezone
from typing import TypeVar

import yaml
from pydantic import BaseModel


ModelT = TypeVar("ModelT", bound=BaseModel)


class AtomicWriteError(RuntimeError):
    """Raised when an atomic write cannot be completed."""


class BackupError(RuntimeError):
    """Raised when a backup cannot be created."""


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_yaml(path: Path) -> object:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    return {} if data is None else data


def load_json_model(path: Path, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(load_json(path))


def load_yaml_model(path: Path, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(load_yaml(path))


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    data = content.encode(encoding)
    atomic_write_bytes(path, data)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = -1
    temp_name: str | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        with os.fdopen(fd, "wb") as file:
            fd = -1
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
        _fsync_directory(path.parent)
    except Exception as exc:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise AtomicWriteError(f"atomic write failed for {path}: {exc}") from exc


def atomic_write_json(path: Path, data: object) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def atomic_write_model_json(path: Path, model: BaseModel) -> None:
    atomic_write_text(path, model.model_dump_json(indent=2) + "\n")


def atomic_write_yaml(path: Path, data: object) -> None:
    atomic_write_text(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def append_jsonl(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")


def backup_file(path: Path, *, reason: str | None = None) -> Path:
    path = path.expanduser()
    if not path.exists():
        raise BackupError(f"cannot back up missing file: {path}")
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    reason_part = f".{_safe_backup_reason(reason)}" if reason else ""
    backup_path = path.with_name(f"{path.name}.bak_{suffix}{reason_part}")
    counter = 1
    while backup_path.exists():
        backup_path = path.with_name(f"{path.name}.bak_{suffix}{reason_part}.{counter}")
        counter += 1
    try:
        shutil.copy2(path, backup_path)
    except Exception as exc:
        raise BackupError(f"backup failed for {path}: {exc}") from exc
    return backup_path


def backup_if_exists(path: Path, *, reason: str | None = None) -> Path | None:
    if not path.exists():
        return None
    return backup_file(path, reason=reason)


def _safe_backup_reason(reason: str | None) -> str:
    if not reason:
        return "backup"
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in reason)
    return safe[:40] or "backup"


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
