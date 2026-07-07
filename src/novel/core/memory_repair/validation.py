# mypy: ignore-errors
# ruff: noqa: F403,F405
from __future__ import annotations

from .deps import *
from .models import *

def _format_preflight_errors(errors: list[str], *, max_chars: int = 10000) -> str:
    text = "\n".join(f"- {error}" for error in errors)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n... truncated ..."


def _group_operations(operations: list[MemoryRepairOperation]) -> dict[str, list[MemoryRepairOperation]]:
    grouped: dict[str, list[MemoryRepairOperation]] = {}
    for operation in operations:
        _ensure_allowed_file(operation.file)
        grouped.setdefault(operation.file, []).append(operation)
    return grouped


def _ensure_allowed_file(rel_path: str) -> None:
    if rel_path not in ALLOWED_MEMORY_FILES:
        raise MemoryRepairError(f"memory repair target is not allowed: {rel_path}")


def _validate_file_model(rel_path: str, data: object) -> None:
    model = ALLOWED_MEMORY_FILES[rel_path]
    try:
        model.model_validate(data)
    except ValidationError as exc:
        summary = _validation_error_summary(rel_path, data, exc)
        raise MemoryRepairError(f"schema validation failed for {rel_path}: {summary}") from exc


def _validation_error_summary(rel_path: str, data: object, exc: ValidationError) -> str:
    messages = [
        _human_validation_error(rel_path, data, error)
        for error in exc.errors()
    ]
    return "；".join(messages) if messages else "目标文件不符合 schema"


def _human_validation_error(rel_path: str, data: object, error: Mapping[str, object]) -> str:
    loc = error.get("loc")
    location = _format_validation_location(loc if isinstance(loc, tuple) else ())
    error_type = str(error.get("type") or "")
    if (
        rel_path == "memory/state/timeline.json"
        and location.endswith("narrative_position.chapter")
        and error_type in {"greater_than_equal", "greater_than", "int_parsing", "int_type"}
    ):
        event_id = _timeline_event_id_for_validation_error(data, loc if isinstance(loc, tuple) else ())
        event_label = f"‘{event_id}’" if event_id else location
        return (
            f"{location}: 时间线事件{event_label}的 narrative_position.chapter 必须大于等于 1；"
            "开篇前/背景事件请省略 narrative_position，并把故事世界时间写入 story_position.time_label"
        )
    if error_type == "missing":
        return f"{location}: 缺少必填字段"
    if error_type == "extra_forbidden":
        return f"{location}: 字段不在目标 schema 中"
    message = str(error.get("msg") or "不符合目标 schema")
    input_hint = _validation_input_hint(error.get("input"))
    return f"{location}: {message}{input_hint}"


def _validation_input_hint(value: object) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    text = repr(value)
    if len(text) > 120:
        text = text[:117] + "..."
    return f"（输入值：{text}）"


def _format_validation_location(loc: tuple[object, ...]) -> str:
    if not loc:
        return "<root>"
    return ".".join(str(part) for part in loc)


def _timeline_event_id_for_validation_error(data: object, loc: tuple[object, ...]) -> str | None:
    if len(loc) < 2 or loc[0] != "events" or not isinstance(loc[1], int) or not isinstance(data, dict):
        return None
    events = data.get("events")
    if not isinstance(events, list) or loc[1] >= len(events):
        return None
    event = events[loc[1]]
    if not isinstance(event, dict):
        return None
    event_id = event.get("id")
    return event_id if isinstance(event_id, str) and event_id else None


def _resolve_proposal_path(root: Path, proposal_path: Path) -> Path:
    path = proposal_path if proposal_path.is_absolute() else root / proposal_path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise MemoryRepairError("repair proposal must be inside the project workspace") from exc
    if path.name != "proposal.json" or "memory/repairs" not in str(path.relative_to(root)):
        raise MemoryRepairError("repair proposal must be memory/repairs/{repair_id}/proposal.json")
    return path


def _repair_dir(root: Path, repair_id: str) -> Path:
    return root / "memory" / "repairs" / repair_id


def _clarification_dir(root: Path, clarification_id: str) -> Path:
    if not re.fullmatch(r"clarify_[0-9]{8}_[0-9]{6}_[0-9]{6}", clarification_id):
        raise MemoryRepairError("invalid setting change clarification id")
    return root / "memory" / "repairs" / "clarifications" / clarification_id


def _clarification_path(root: Path, clarification_id: str) -> Path:
    path = _clarification_dir(root, clarification_id) / "session.json"
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise MemoryRepairError("setting change clarification must be inside the project workspace") from exc
    return resolved


def _new_repair_id() -> str:
    return new_request_id("repair")


def _new_clarification_id() -> str:
    return new_request_id("clarify")

__all__ = [name for name in globals() if not name.startswith("__")]
