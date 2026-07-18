from __future__ import annotations

import logging
import sys
from pathlib import Path

from novel.core.io import append_jsonl
from novel.core.security import redact_secret_text
from novel.core.timeutil import utc_now_iso


def log_app_warning(root: Path, event: str, **fields: object) -> None:
    _write_app_log(root, "warning", event, fields)


def _write_app_log(root: Path, level: str, event: str, fields: dict[str, object]) -> None:
    try:
        resolved = root.expanduser().resolve()
        payload: dict[str, object] = {
            "created_at": utc_now_iso(),
            "level": level,
            "event": event,
        }
        payload.update(
            {
                key: _sanitize(value, limit=4000 if key == "traceback" else 1000)
                for key, value in fields.items()
                if value is not None
            }
        )
        append_jsonl(resolved / "runs" / "app.log", payload)
    except Exception as exc:
        logging.getLogger(__name__).debug("app log write failed", exc_info=exc)
        if sys.stderr.isatty():
            print("WriterYang 警告：本地诊断日志写入失败。", file=sys.stderr)


def _sanitize(value: object, *, limit: int = 1000) -> object:
    if isinstance(value, Path):
        return _truncate(redact_secret_text(str(value)), limit=limit)
    if isinstance(value, str):
        return _truncate(redact_secret_text(value), limit=limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_sanitize(item, limit=limit) for item in value[:50]]
    if isinstance(value, tuple):
        return [_sanitize(item, limit=limit) for item in value[:50]]
    if isinstance(value, dict):
        return {str(key): _sanitize(item, limit=limit) for key, item in list(value.items())[:50]}
    return _truncate(redact_secret_text(str(value)), limit=limit)


def _truncate(value: str, limit: int = 1000) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 20].rstrip() + "... truncated ..."
