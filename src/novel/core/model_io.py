from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

from novel.core.io import atomic_write_text


MODEL_IO_MAX_FILES_ENV = "WRITERYANG_MODEL_IO_MAX_FILES"
MODEL_IO_MAX_BYTES_ENV = "WRITERYANG_MODEL_IO_MAX_BYTES"
MODEL_IO_MODE_ENV = "WRITERYANG_MODEL_IO_MODE"
DEFAULT_MODEL_IO_MAX_FILES = 500
DEFAULT_MODEL_IO_MAX_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class ModelIORetentionPolicy:
    max_files: int | None = DEFAULT_MODEL_IO_MAX_FILES
    max_bytes: int | None = DEFAULT_MODEL_IO_MAX_BYTES
    mode: str = "metadata"


@dataclass(frozen=True)
class ModelIORecord:
    entry: dict[str, object]
    path: Path
    size: int


def model_io_retention_policy_from_env(env: Mapping[str, str] | None = None) -> ModelIORetentionPolicy:
    values = os.environ if env is None else env
    return ModelIORetentionPolicy(
        max_files=_optional_positive_int(values.get(MODEL_IO_MAX_FILES_ENV), DEFAULT_MODEL_IO_MAX_FILES),
        max_bytes=_optional_positive_int(values.get(MODEL_IO_MAX_BYTES_ENV), DEFAULT_MODEL_IO_MAX_BYTES),
        mode=_model_io_mode(values.get(MODEL_IO_MODE_ENV)),
    )


def compact_model_io_payload(payload: dict[str, object]) -> dict[str, object]:
    compacted = dict(payload)
    request = payload.get("request")
    if isinstance(request, dict):
        compacted["request"] = {
            "system_prompt": "[omitted by WRITERYANG_MODEL_IO_MODE=metadata]",
            "user_prompt": "[omitted by WRITERYANG_MODEL_IO_MODE=metadata]",
            "context": "[omitted by WRITERYANG_MODEL_IO_MODE=metadata]",
            "prompt_version": request.get("prompt_version"),
            "hashes": request.get("hashes"),
            "payload": "[omitted by WRITERYANG_MODEL_IO_MODE=metadata]",
        }
    response = payload.get("response")
    if isinstance(response, dict):
        compacted["response"] = {
            "content": "[omitted by WRITERYANG_MODEL_IO_MODE=metadata]" if response.get("content") is not None else None,
            "reasoning_content": "[omitted by WRITERYANG_MODEL_IO_MODE=metadata]"
            if response.get("reasoning_content") is not None
            else None,
            "finish_reason": response.get("finish_reason"),
            "raw_response": "[omitted by WRITERYANG_MODEL_IO_MODE=metadata]"
            if response.get("raw_response") is not None
            else None,
            "hashes": response.get("hashes"),
        }
    return compacted


def prune_model_io_dir(model_io_dir: Path, policy: ModelIORetentionPolicy) -> None:
    if policy.max_files is None and policy.max_bytes is None:
        return
    index_path = model_io_dir / "index.jsonl"
    entries = _read_index_entries(index_path)
    if not entries:
        return
    records = [_record_for_entry(model_io_dir, entry) for entry in entries]
    existing_records = [record for record in records if record is not None]
    keep_paths = _select_records_to_keep(existing_records, policy)
    for record in existing_records:
        if record.path not in keep_paths:
            try:
                record.path.unlink(missing_ok=True)
            except OSError:
                pass
    kept_entries = [
        record.entry
        for record in existing_records
        if record.path in keep_paths and record.path.exists()
    ]
    atomic_write_text(index_path, "".join(json.dumps(entry, ensure_ascii=False, default=str) + "\n" for entry in kept_entries))


def _optional_positive_int(raw: str | None, default: int) -> int | None:
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default
    return value if value > 0 else None


def _model_io_mode(raw: str | None) -> str:
    mode = (raw or "metadata").strip().lower()
    return mode if mode in {"full", "metadata"} else "metadata"


def content_sha256(value: object | None) -> str | None:
    if value is None:
        return None
    serialized = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _read_index_entries(index_path: Path) -> list[dict[str, object]]:
    if not index_path.exists():
        return []
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: list[dict[str, object]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            entries.append(data)
    return entries


def _record_for_entry(model_io_dir: Path, entry: dict[str, object]) -> ModelIORecord | None:
    rel_path = entry.get("model_io_path")
    if not isinstance(rel_path, str) or not rel_path.startswith("runs/model_io/") or not rel_path.endswith(".json"):
        return None
    path = (model_io_dir.parent.parent / rel_path).resolve()
    try:
        path.relative_to(model_io_dir.resolve())
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return ModelIORecord(entry=entry, path=path, size=stat.st_size)


def _select_records_to_keep(records: list[ModelIORecord], policy: ModelIORetentionPolicy) -> set[Path]:
    kept = list(records)
    if policy.max_files is not None and len(kept) > policy.max_files:
        kept = kept[-policy.max_files :]
    if policy.max_bytes is not None:
        total = sum(record.size for record in kept)
        while kept and total > policy.max_bytes:
            removed = kept.pop(0)
            total -= removed.size
    return {record.path for record in kept}
