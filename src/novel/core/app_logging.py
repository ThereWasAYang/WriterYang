from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from novel.core.security import redact_secret_text


def log_app_warning(root: Path, event: str, **fields: object) -> None:
    _write_app_log(root, "warning", event, fields)


def _write_app_log(root: Path, level: str, event: str, fields: dict[str, object]) -> None:
    try:
        resolved = root.expanduser().resolve()
        logger = _logger_for_root(resolved)
        payload: dict[str, object] = {
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
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
        logger.warning(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    except Exception:
        return


def _logger_for_root(root: Path) -> logging.Logger:
    logger = logging.getLogger(f"novel.app.{hash(root)}")
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    if logger.handlers:
        return logger
    log_path = root / "runs" / "app.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


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
