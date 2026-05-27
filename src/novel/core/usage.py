from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from novel.core.io import atomic_write_json


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


@dataclass
class UsageSummary:
    log_path: Path
    generated_at: str
    total: UsageBucket = field(default_factory=UsageBucket)
    by_provider: dict[str, UsageBucket] = field(default_factory=dict)
    by_model: dict[str, UsageBucket] = field(default_factory=dict)
    by_status: dict[str, UsageBucket] = field(default_factory=dict)
    malformed_line_count: int = 0
    last_call: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "log_path": str(self.log_path),
            "total": self.total.as_dict(),
            "by_provider": {key: bucket.as_dict() for key, bucket in sorted(self.by_provider.items())},
            "by_model": {key: bucket.as_dict() for key, bucket in sorted(self.by_model.items())},
            "by_status": {key: bucket.as_dict() for key, bucket in sorted(self.by_status.items())},
            "malformed_line_count": self.malformed_line_count,
            "last_call": self.last_call,
        }


def provider_usage_path(root: Path) -> Path:
    return root.resolve() / "runs" / "provider_usage.json"


def provider_call_log_path(root: Path) -> Path:
    return root.resolve() / "runs" / "provider_calls.jsonl"


def summarize_provider_usage(root: Path) -> UsageSummary:
    log_path = provider_call_log_path(root)
    return summarize_provider_call_log(log_path)


def summarize_provider_call_log(log_path: Path) -> UsageSummary:
    summary = UsageSummary(log_path=log_path, generated_at=_utc_now())
    if not log_path.exists():
        return summary
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise UsageError(f"could not read provider call log: {log_path}") from exc

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
    return summary


def refresh_provider_usage_summary(root: Path) -> Path:
    summary = summarize_provider_usage(root)
    output_path = provider_usage_path(root)
    atomic_write_json(output_path, summary.as_dict())
    return output_path


def refresh_provider_usage_summary_for_log(log_path: Path) -> Path:
    if log_path.parent.name == "runs":
        root = log_path.parent.parent
        return refresh_provider_usage_summary(root)
    summary = summarize_provider_call_log(log_path)
    output_path = log_path.with_name("provider_usage.json")
    atomic_write_json(output_path, summary.as_dict())
    return output_path


def _add_entry(summary: UsageSummary, entry: dict[str, Any]) -> None:
    summary.total.add(entry)
    provider = str(entry.get("provider") or "unknown")
    model = str(entry.get("model") or "unknown")
    status = str(entry.get("status") or "unknown")
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
        "json_schema_name",
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
