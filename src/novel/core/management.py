from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Literal

from novel.core.io import atomic_write_text
from novel.core.schemas import ManagementEvent, ManagementEventType


def record_management_event(
    root: Path,
    event_type: ManagementEventType,
    message: str,
    *,
    source: str | None = None,
    target_files: list[str] | None = None,
    status: Literal["info", "success", "warning", "error"] = "info",
    details: dict[str, object] | None = None,
) -> ManagementEvent:
    event = ManagementEvent(
        event_id=_new_event_id(),
        event_type=event_type,
        message=message,
        source=source,
        target_files=target_files or [],
        status=status,
        details=details or {},
        created_at=_utc_now(),
    )
    path = management_events_path(root)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    atomic_write_text(path, existing + event.model_dump_json() + "\n")
    return event


def load_management_events(root: Path, *, limit: int = 20) -> list[ManagementEvent]:
    path = management_events_path(root)
    if not path.exists():
        return []
    events: list[ManagementEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(ManagementEvent.model_validate(json.loads(line)))
        except Exception:
            continue
    return events[-limit:]


def management_events_path(root: Path) -> Path:
    return root.resolve() / "memory" / "management_events.jsonl"


def _new_event_id() -> str:
    return "mgmt_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
