from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Literal

from novel.core.io import append_jsonl
from novel.core.schemas import ManagementEvent, ManagementEventType
from novel.core.timeutil import new_request_id, utc_now


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
    append_jsonl(path, event.model_dump(mode="json"))
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
    return new_request_id("mgmt")


def _utc_now() -> datetime:
    return utc_now()
