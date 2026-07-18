from __future__ import annotations

from pathlib import Path

from novel.core.io import atomic_write_model_json, load_json_model
from novel.core.schemas import SessionProgress, SessionProgressEvent, SessionProgressStatus
from novel.core.security import redact_secret_text
from novel.core.timeutil import utc_now

_EVENT_LIMIT = 50


def load_session_progress(root: Path, session_id: str) -> SessionProgress:
    path = _progress_path(root.resolve(), session_id)
    if not path.exists():
        return SessionProgress(session_id=session_id, status="idle")
    return load_json_model(path, SessionProgress)


def start_session_progress(root: Path, session_id: str, *, message: str) -> SessionProgress:
    now = utc_now()
    event = SessionProgressEvent(stage="session_start", message=message, created_at=now)
    progress = SessionProgress(
        session_id=session_id,
        status="running",
        current_stage="session_start",
        current_message=message,
        events=[event],
        started_at=now,
        updated_at=now,
    )
    _write_session_progress(root, progress)
    return progress


def record_session_progress(
    root: Path,
    session_id: str,
    *,
    status: SessionProgressStatus,
    stage: str,
    message: str,
    chapter_number: int | None = None,
    round_number: int | None = None,
    error: str | None = None,
) -> SessionProgress:
    now = utc_now()
    existing = load_session_progress(root, session_id)
    next_status = status
    if existing.status == "cancel_requested" and status == "running":
        next_status = "cancel_requested"
    event = SessionProgressEvent(
        stage=stage,
        message=message,
        chapter_number=chapter_number,
        round_number=round_number,
        created_at=now,
    )
    events = [*existing.events, event][-_EVENT_LIMIT:]
    progress = SessionProgress(
        session_id=session_id,
        status=next_status,
        current_stage=stage,
        current_message=message,
        current_chapter=chapter_number,
        current_round=round_number,
        events=events,
        started_at=existing.started_at or now,
        updated_at=now,
        completed_at=now if next_status in {"cancelled", "completed", "failed"} else existing.completed_at,
        cancel_requested_at=(
            now
            if next_status == "cancel_requested" and not existing.cancel_requested_at
            else existing.cancel_requested_at
        ),
        error=_safe_error(error) if error else None,
    )
    _write_session_progress(root, progress)
    return progress


def _write_session_progress(root: Path, progress: SessionProgress) -> None:
    atomic_write_model_json(_progress_path(root, progress.session_id), progress)


def _progress_path(root: Path, session_id: str) -> Path:
    return root / "memory" / "sessions" / session_id / "progress.json"


def _safe_error(value: str) -> str:
    text = redact_secret_text(value)
    return text if len(text) <= 500 else text[:497] + "..."


__all__ = ["load_session_progress", "record_session_progress", "start_session_progress"]
