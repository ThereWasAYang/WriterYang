from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from novel.core.io import atomic_write_json
from novel.core.timeutil import utc_now_iso


class UsageError(RuntimeError):
    """Raised when provider usage cannot be read."""


@dataclass
class UsageBucket:
    call_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    unknown_token_call_count: int = 0

    def add(self, entry: dict[str, Any]) -> None:
        self.call_count += 1
        status = entry.get("status")
        if status == "success":
            self.success_count += 1
        elif status == "failed":
            self.failed_count += 1

        prompt = _optional_int(entry.get("prompt_tokens"))
        completion = _optional_int(entry.get("completion_tokens"))
        total = _optional_int(entry.get("total_tokens"))
        if prompt is None and completion is None and total is None:
            self.unknown_token_call_count += 1
            return
        self.prompt_tokens += prompt or 0
        self.completion_tokens += completion or 0
        self.total_tokens += total if total is not None else (prompt or 0) + (completion or 0)

    def as_dict(self) -> dict[str, int]:
        return {
            "call_count": self.call_count,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "unknown_token_call_count": self.unknown_token_call_count,
        }

    @classmethod
    def from_dict(cls, data: object) -> "UsageBucket":
        if not isinstance(data, dict):
            return cls()
        return cls(
            call_count=_optional_int(data.get("call_count")) or 0,
            success_count=_optional_int(data.get("success_count")) or 0,
            failed_count=_optional_int(data.get("failed_count")) or 0,
            prompt_tokens=_optional_int(data.get("prompt_tokens")) or 0,
            completion_tokens=_optional_int(data.get("completion_tokens")) or 0,
            total_tokens=_optional_int(data.get("total_tokens")) or 0,
            unknown_token_call_count=_optional_int(data.get("unknown_token_call_count")) or 0,
        )


@dataclass
class UsageSummary:
    log_path: Path
    generated_at: str
    total: UsageBucket = field(default_factory=UsageBucket)
    by_task: dict[str, UsageBucket] = field(default_factory=dict)
    by_provider: dict[str, UsageBucket] = field(default_factory=dict)
    by_model: dict[str, UsageBucket] = field(default_factory=dict)
    by_status: dict[str, UsageBucket] = field(default_factory=dict)
    malformed_line_count: int = 0
    last_call: dict[str, Any] | None = None
    aggregation: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "generated_at": self.generated_at,
            "log_path": str(self.log_path),
            "total": self.total.as_dict(),
            "by_task": {key: bucket.as_dict() for key, bucket in sorted(self.by_task.items())},
            "by_provider": {key: bucket.as_dict() for key, bucket in sorted(self.by_provider.items())},
            "by_model": {key: bucket.as_dict() for key, bucket in sorted(self.by_model.items())},
            "by_status": {key: bucket.as_dict() for key, bucket in sorted(self.by_status.items())},
            "malformed_line_count": self.malformed_line_count,
            "last_call": self.last_call,
        }
        if self.aggregation is not None:
            data["_aggregation"] = self.aggregation
        return data

    @classmethod
    def from_dict(cls, data: object, *, log_path: Path) -> "UsageSummary | None":
        if not isinstance(data, dict):
            return None
        summary = cls(
            log_path=log_path,
            generated_at=str(data.get("generated_at") or utc_now_iso()),
            total=UsageBucket.from_dict(data.get("total")),
            by_task=_buckets_from_dict(data.get("by_task")),
            by_provider=_buckets_from_dict(data.get("by_provider")),
            by_model=_buckets_from_dict(data.get("by_model")),
            by_status=_buckets_from_dict(data.get("by_status")),
            malformed_line_count=_optional_int(data.get("malformed_line_count")) or 0,
            last_call=data.get("last_call") if isinstance(data.get("last_call"), dict) else None,
            aggregation=data.get("_aggregation") if isinstance(data.get("_aggregation"), dict) else None,
        )
        return summary


def provider_usage_path(root: Path) -> Path:
    return root.resolve() / "runs" / "provider_usage.json"


def provider_call_log_path(root: Path) -> Path:
    return root.resolve() / "runs" / "provider_calls.jsonl"


def summarize_provider_usage(root: Path) -> UsageSummary:
    log_path = provider_call_log_path(root)
    cached = _cached_usage_summary_if_current(provider_usage_path(root), log_path)
    if cached is not None:
        return cached
    return summarize_provider_call_log(log_path)


def summarize_provider_call_log(log_path: Path) -> UsageSummary:
    summary = UsageSummary(log_path=log_path, generated_at=utc_now_iso())
    if not log_path.exists():
        return summary
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise UsageError(f"could not read provider call log: {log_path}") from exc

    _consume_jsonl_lines(summary, lines)
    summary.aggregation = _aggregation_metadata(log_path)
    return summary


def refresh_provider_usage_summary(root: Path) -> Path:
    return refresh_provider_usage_summary_for_log(provider_call_log_path(root))


def refresh_provider_usage_summary_for_log(log_path: Path) -> Path:
    if log_path.parent.name == "runs":
        root = log_path.parent.parent
        output_path = provider_usage_path(root)
    else:
        output_path = log_path.with_name("provider_usage.json")
    summary = _incremental_provider_usage_summary(log_path, output_path)
    atomic_write_json(output_path, summary.as_dict())
    return output_path


def _incremental_provider_usage_summary(log_path: Path, output_path: Path) -> UsageSummary:
    stat = _log_stat(log_path)
    if stat is None:
        return UsageSummary(log_path=log_path, generated_at=utc_now_iso(), aggregation=_empty_aggregation())
    cached = _read_cached_usage_summary(output_path, log_path)
    if cached is None:
        return summarize_provider_call_log(log_path)
    offset = _aggregation_log_size(cached.aggregation)
    if offset is None or offset > stat["log_size_bytes"]:
        return summarize_provider_call_log(log_path)
    cached.generated_at = utc_now_iso()
    if offset < stat["log_size_bytes"]:
        try:
            with log_path.open("rb") as file:
                file.seek(offset)
                new_text = file.read().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise UsageError(f"could not read provider call log: {log_path}") from exc
        _consume_jsonl_lines(cached, new_text.splitlines())
    cached.aggregation = _aggregation_metadata(log_path)
    return cached


def _cached_usage_summary_if_current(output_path: Path, log_path: Path) -> UsageSummary | None:
    cached = _read_cached_usage_summary(output_path, log_path)
    if cached is None:
        return None
    current = _aggregation_metadata(log_path)
    if (
        _aggregation_log_size(cached.aggregation) == current["log_size_bytes"]
        and cached.aggregation
        and cached.aggregation.get("log_mtime_ns") == current["log_mtime_ns"]
    ):
        return cached
    return None


def _read_cached_usage_summary(output_path: Path, log_path: Path) -> UsageSummary | None:
    if not output_path.exists():
        return None
    try:
        data = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return UsageSummary.from_dict(data, log_path=log_path)


def _consume_jsonl_lines(summary: UsageSummary, lines: list[str]) -> None:
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            summary.malformed_line_count += 1
            continue
        if not isinstance(entry, dict):
            summary.malformed_line_count += 1
            continue
        _add_entry(summary, entry)


def _buckets_from_dict(data: object) -> dict[str, UsageBucket]:
    if not isinstance(data, dict):
        return {}
    return {str(key): UsageBucket.from_dict(value) for key, value in data.items()}


def _aggregation_log_size(aggregation: dict[str, object] | None) -> int | None:
    if not isinstance(aggregation, dict):
        return None
    return _optional_int(aggregation.get("log_size_bytes"))


def _aggregation_metadata(log_path: Path) -> dict[str, object]:
    stat = _log_stat(log_path)
    if stat is None:
        return _empty_aggregation()
    return {
        "schema_version": 1,
        "mode": "incremental",
        **stat,
    }


def _empty_aggregation() -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "incremental",
        "log_size_bytes": 0,
        "log_mtime_ns": None,
    }


def _log_stat(log_path: Path) -> dict[str, int] | None:
    if not log_path.exists():
        return None
    try:
        stat = log_path.stat()
    except OSError:
        return None
    return {
        "log_size_bytes": stat.st_size,
        "log_mtime_ns": stat.st_mtime_ns,
    }


def _add_entry(summary: UsageSummary, entry: dict[str, Any]) -> None:
    summary.total.add(entry)
    task = str(entry.get("agent_name") or "unknown")
    provider = str(entry.get("provider") or "unknown")
    model = str(entry.get("model") or "unknown")
    status = str(entry.get("status") or "unknown")
    summary.by_task.setdefault(task, UsageBucket()).add(entry)
    summary.by_provider.setdefault(provider, UsageBucket()).add(entry)
    summary.by_model.setdefault(model, UsageBucket()).add(entry)
    summary.by_status.setdefault(status, UsageBucket()).add(entry)
    summary.last_call = _safe_last_call(entry)


def _safe_last_call(entry: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "request_id",
        "provider",
        "model",
        "endpoint",
        "started_at",
        "ended_at",
        "status",
        "attempt_count",
        "duration_ms",
        "stream",
        "agent_name",
        "json_schema_name",
        "finish_reason",
        "http_status",
        "error_type",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "model_io_path",
    }
    return {key: entry.get(key) for key in allowed if key in entry}


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
