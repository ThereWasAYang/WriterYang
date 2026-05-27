from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import difflib
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs

from pydantic import BaseModel, Field
import yaml

from novel.core.auditing import ChapterAuditOptions, audit_chapter, load_audit_provider
from novel.core.drafting import ChapterDraftingOptions, load_drafting_provider, write_chapter_draft
from novel.core.exporting import MarkdownExportOptions, export_markdown, parse_chapter_selector
from novel.core.inspection import format_canon, get_project_status
from novel.core.io import load_json, load_json_model, load_yaml
from novel.core.locking import ProjectLock, ProjectLockError
from novel.core.planning import ChapterPlanningOptions, load_planning_provider, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, load_polishing_provider, polish_chapter
from novel.core.schemas import ChapterPlan
from novel.core.session import (
    SessionActionOptions,
    SessionInstructionOptions,
    SessionRunOptions,
    SessionStartOptions,
    accept_session,
    approve_outline,
    archive_session,
    load_session,
    parse_range,
    revise_content,
    revise_outline,
    run_session,
    start_session,
)
from novel.core.usage import summarize_provider_usage
from novel.core.workflow import GenerateChapterOptions, generate_chapter


APIResponse = tuple[int, dict[str, object]]
SAFE_FILE_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".yaml", ".yml"}
EXCLUDED_DIRS = {".git", ".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache"}
EXCLUDED_FILENAMES = {
    "search_index.json",
    "search_index.sqlite",
    ".DS_Store",
}


class WebErrorPayload(BaseModel):
    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)
    request_id: str


class WebResponsePayload(BaseModel):
    ok: bool
    data: dict[str, object] | None = None
    error: WebErrorPayload | None = None


class WebAPIError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or {}


def handle_api_request(
    method: str,
    path: str,
    query_string: str = "",
    body: bytes | str | None = None,
) -> APIResponse:
    request_id = _request_id()
    query = {key: values[-1] for key, values in parse_qs(query_string).items()}
    try:
        if method == "GET" and path == "/api/projects":
            return _success({"projects": _list_projects(Path(query.get("root", ".")))})
        if method == "GET" and path == "/api/project/status":
            root = _root_from_query(query)
            status = get_project_status(root)
            payload = asdict(status)
            payload["latest_run_log"] = str(status.latest_run_log) if status.latest_run_log else None
            return _success({"status": payload})
        if method == "GET" and path == "/api/canon":
            return _success({"summary": format_canon(_root_from_query(query))})
        if method == "GET" and path == "/api/chapters":
            return _success({"chapters": _list_chapters(_root_from_query(query))})
        if method == "GET" and path == "/api/chapter-file":
            return _success(_read_chapter_file(_root_from_query(query), query))
        if method == "GET" and path == "/api/file-tree":
            return _success({"files": _file_tree(_root_from_query(query))})
        if method == "GET" and path == "/api/read-file":
            return _success(_read_workspace_file(_root_from_query(query), query.get("file") or ""))
        if method == "GET" and path == "/api/runs":
            return _success(_runs_summary(_root_from_query(query)))
        if method == "GET" and path == "/api/usage":
            return _success({"usage": summarize_provider_usage(_root_from_query(query)).as_dict()})
        if method == "GET" and path == "/api/provider-config":
            return _success(_provider_config_summary(_root_from_query(query)))
        if method == "GET" and path == "/api/state-timeline":
            return _success(_state_timeline_summary(_root_from_query(query)))
        if method == "GET" and path == "/api/session":
            root = _root_from_query(query)
            session = load_session(root, query.get("session_id") or "")
            return _success({"session": session.model_dump(mode="json")})
        if method == "GET" and path == "/api/diff":
            return _success(
                _workspace_diff(
                    _root_from_query(query),
                    query.get("left") or "",
                    query.get("right") or "",
                )
            )

        data = _json_body(body)
        if method == "POST" and path == "/api/plan-chapter":
            return _success(_locked_write(data, "web plan-chapter", _plan_chapter))
        if method == "POST" and path == "/api/write-chapter":
            return _success(_locked_write(data, "web write-chapter", _write_chapter))
        if method == "POST" and path == "/api/polish-chapter":
            return _success(_locked_write(data, "web polish-chapter", _polish_chapter))
        if method == "POST" and path == "/api/audit-chapter":
            return _success(_locked_write(data, "web audit-chapter", _audit_chapter))
        if method == "POST" and path == "/api/export/markdown":
            return _success(_locked_write(data, "web export markdown", _export_markdown))
        if method == "POST" and path == "/api/generate-chapter":
            return _success(_locked_write(data, "web generate-chapter", _generate_chapter))
        if method == "POST" and path == "/api/session/start":
            return _success(_locked_write(data, "web session start", _session_start))
        if method == "POST" and path == "/api/session/revise-outline":
            return _success(_locked_write(data, "web session revise-outline", _session_revise_outline))
        if method == "POST" and path == "/api/session/approve-outline":
            return _success(_locked_write(data, "web session approve-outline", _session_approve_outline))
        if method == "POST" and path == "/api/session/run":
            return _success(_locked_write(data, "web session run", _session_run))
        if method == "POST" and path == "/api/session/revise-content":
            return _success(_locked_write(data, "web session revise-content", _session_revise_content))
        if method == "POST" and path == "/api/session/accept":
            return _success(_locked_write(data, "web session accept", _session_accept))
        if method == "POST" and path == "/api/session/archive":
            return _success(_locked_write(data, "web session archive", _session_archive))
    except WebAPIError as exc:
        return _failure(exc.status, exc.code, str(exc), request_id=request_id, details=exc.details)
    except ProjectLockError as exc:
        return _failure(409, "project_locked", str(exc), request_id=request_id)
    except FileNotFoundError as exc:
        return _failure(404, "file_not_found", str(exc), request_id=request_id)
    except PermissionError as exc:
        return _failure(403, "forbidden_file", str(exc), request_id=request_id)
    except json.JSONDecodeError:
        return _failure(400, "invalid_json", "request body must be valid JSON", request_id=request_id)
    except ValueError as exc:
        return _failure(400, "invalid_request", str(exc), request_id=request_id)
    except Exception as exc:
        return _failure(400, "operation_failed", str(exc), request_id=request_id)
    return _failure(404, "not_found", "not found", request_id=request_id)


def _locked_write(data: dict[str, object], task: str, handler) -> dict[str, object]:
    root = _root_from_body(data)
    with ProjectLock(root, task=task):
        return handler(data)


def _success(data: dict[str, object], status: int = 200) -> APIResponse:
    payload = WebResponsePayload(ok=True, data=data)
    return status, payload.model_dump(mode="json", exclude_none=True)


def _failure(
    status: int,
    code: str,
    message: str,
    *,
    request_id: str,
    details: dict[str, object] | None = None,
) -> APIResponse:
    payload = WebResponsePayload(
        ok=False,
        error=WebErrorPayload(
            code=code,
            message=_safe_error(message),
            details=details or {},
            request_id=request_id,
        ),
    )
    return status, payload.model_dump(mode="json", exclude_none=True)


def _plan_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    chapter_number = _chapter_number(data)
    provider = load_planning_provider(
        root,
        str(data.get("provider") or "mock"),
        chapter_number=chapter_number,
    )
    result = plan_chapter(
        ChapterPlanningOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            use_search_context=bool(data.get("use_search_context")),
        ),
        provider,
    )
    return {
        "plan_json_path": str(result.plan_json_path),
        "plan_markdown_path": str(result.plan_markdown_path),
        "validation_ok": result.validation_report.ok,
    }


def _write_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    provider = load_drafting_provider(root, str(data.get("provider") or "mock"))
    result = write_chapter_draft(
        ChapterDraftingOptions(
            root=root,
            chapter_number=_chapter_number(data),
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            target_words=_optional_int(data.get("target_words")),
            style_note=_optional_string(data.get("style_note")),
            use_search_context=bool(data.get("use_search_context")),
        ),
        provider,
    )
    return {"draft_path": str(result.draft_path), "warnings": list(result.warnings)}


def _polish_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    provider = load_polishing_provider(root, str(data.get("provider") or "mock"))
    result = polish_chapter(
        ChapterPolishingOptions(
            root=root,
            chapter_number=_chapter_number(data),
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            style_note=_optional_string(data.get("style_note")),
            keep_length=bool(data.get("keep_length")),
            edit_mode=str(data.get("edit_mode") or "normal"),  # type: ignore[arg-type]
        ),
        provider,
    )
    return {"polished_path": str(result.polished_path), "warnings": list(result.warnings)}


def _audit_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    chapter_number = _chapter_number(data)
    audited_file = str(data.get("audited_file") or "polished.md")
    provider = load_audit_provider(
        root,
        str(data.get("provider") or "mock"),
        chapter_number=chapter_number,
        audited_file=audited_file,  # type: ignore[arg-type]
    )
    result = audit_chapter(
        ChapterAuditOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            strict=bool(data.get("strict")),
            focus=tuple(str(item) for item in data.get("focus", []) if item)  # type: ignore[union-attr]
            if isinstance(data.get("focus"), list)
            else (),
            audited_file=audited_file,  # type: ignore[arg-type]
            use_search_context=bool(data.get("use_search_context")),
        ),
        provider,
    )
    return {
        "audit_path": str(result.audit_path),
        "overall_status": result.report.overall_status,
        "issue_count": len(result.report.issues),
        "warnings": list(result.warnings),
    }


def _export_markdown(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    result = export_markdown(
        MarkdownExportOptions(
            root=root,
            chapters=parse_chapter_selector(_optional_string(data.get("chapters"))),
            from_chapter=_optional_int(data.get("from_chapter")),
            to_chapter=_optional_int(data.get("to_chapter")),
            include_unaccepted=bool(data.get("include_unaccepted")),
            output_path=Path(str(data["output"])) if data.get("output") else None,
            title=_optional_string(data.get("title")),
            force=bool(data.get("force")),
        )
    )
    return {
        "output_path": str(result.output_path),
        "manifest_path": str(result.manifest_path),
        "chapters": list(result.exported_chapters),
        "warnings": list(result.warnings),
    }


def _generate_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    result = generate_chapter(
        GenerateChapterOptions(
            root=root,
            chapter_number=_chapter_number(data),
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            provider_name=str(data.get("provider") or "mock"),
            target_words=_optional_int(data.get("target_words")),
            style_note=_optional_string(data.get("style_note")),
            skip_polish=bool(data.get("skip_polish")),
            skip_audit=bool(data.get("skip_audit")),
            stop_after=_optional_string(data.get("stop_after")),  # type: ignore[arg-type]
        )
    )
    return {
        "message": result.message,
        "run_log_path": str(result.run_log_path),
        "status": result.run_log.status,
    }


def _session_start(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    chapter_range = parse_range(str(data.get("chapters") or data.get("chapter") or "1"))
    segment_range = parse_range(str(data["segments"])) if data.get("segments") else None
    result = start_session(
        SessionStartOptions(
            root=root,
            user_intent=str(data.get("intent") or ""),
            chapter_range=chapter_range,
            segment_range=segment_range,
            provider_name=str(data.get("provider") or "mock"),
            force=bool(data.get("force")),
        )
    )
    return _session_result_payload(result)


def _session_revise_outline(data: dict[str, object]) -> dict[str, object]:
    result = revise_outline(
        SessionInstructionOptions(
            root=_root_from_body(data),
            session_id=str(data.get("session_id") or ""),
            instruction=str(data.get("instruction") or ""),
            provider_name=str(data.get("provider") or "mock"),
            force=bool(data.get("force")),
        )
    )
    return _session_result_payload(result)


def _session_approve_outline(data: dict[str, object]) -> dict[str, object]:
    result = approve_outline(
        SessionActionOptions(
            root=_root_from_body(data),
            session_id=str(data.get("session_id") or ""),
            force=bool(data.get("force")),
        )
    )
    return _session_result_payload(result)


def _session_run(data: dict[str, object]) -> dict[str, object]:
    result = run_session(
        SessionRunOptions(
            root=_root_from_body(data),
            session_id=str(data.get("session_id") or ""),
            provider_name=str(data.get("provider") or "mock"),
            force=bool(data.get("force")),
            max_auto_revision_rounds=_optional_int(data.get("max_auto_revision_rounds")),
        )
    )
    return _session_result_payload(result)


def _session_revise_content(data: dict[str, object]) -> dict[str, object]:
    result = revise_content(
        SessionInstructionOptions(
            root=_root_from_body(data),
            session_id=str(data.get("session_id") or ""),
            instruction=str(data.get("instruction") or ""),
            provider_name=str(data.get("provider") or "mock"),
            force=bool(data.get("force")),
        )
    )
    return _session_result_payload(result)


def _session_accept(data: dict[str, object]) -> dict[str, object]:
    result = accept_session(
        SessionActionOptions(
            root=_root_from_body(data),
            session_id=str(data.get("session_id") or ""),
            provider_name=str(data.get("provider") or "mock"),
            force=bool(data.get("force")),
        )
    )
    return _session_result_payload(result)


def _session_archive(data: dict[str, object]) -> dict[str, object]:
    result = archive_session(
        SessionActionOptions(
            root=_root_from_body(data),
            session_id=str(data.get("session_id") or ""),
            force=bool(data.get("force")),
        )
    )
    return _session_result_payload(result)


def _session_result_payload(result) -> dict[str, object]:
    return {
        "session": result.session.model_dump(mode="json"),
        "session_path": str(result.session_path),
        "message": result.message,
    }


def _list_projects(root: Path) -> list[dict[str, str]]:
    base = root.expanduser().resolve()
    candidates = []
    if (base / "project.yaml").exists():
        candidates.append(base)
    if base.exists() and base.is_dir():
        candidates.extend(path for path in base.iterdir() if (path / "project.yaml").exists())
    return [{"path": str(path)} for path in sorted(set(candidates))]


def _file_tree(root: Path) -> list[dict[str, object]]:
    _require_workspace(root)
    files: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        rel = _relative(root, path)
        if not _is_safe_tree_path(rel, path):
            continue
        files.append(
            {
                "path": rel,
                "name": path.name,
                "type": "directory" if path.is_dir() else "file",
                "size": path.stat().st_size if path.is_file() else None,
            }
        )
    return files


def _read_workspace_file(root: Path, rel_path: str) -> dict[str, object]:
    _require_workspace(root)
    path = _safe_workspace_file(root, rel_path)
    if not path.exists():
        raise FileNotFoundError(f"{rel_path} does not exist")
    return {
        "path": _relative(root, path),
        "content": path.read_text(encoding="utf-8"),
        "exists": True,
    }


def _runs_summary(root: Path) -> dict[str, object]:
    _require_workspace(root)
    runs_dir = root / "runs"
    run_logs: list[dict[str, object]] = []
    if runs_dir.exists():
        for path in sorted(runs_dir.glob("*.json"), reverse=True):
            try:
                data = load_json(path)
            except Exception:
                data = {}
            run_logs.append(
                {
                    "path": _relative(root, path),
                    "run_id": data.get("run_id") if isinstance(data, dict) else None,
                    "task": data.get("task") if isinstance(data, dict) else None,
                    "chapter_number": data.get("chapter_number") if isinstance(data, dict) else None,
                    "status": data.get("status") if isinstance(data, dict) else None,
                    "started_at": data.get("started_at") if isinstance(data, dict) else None,
                    "ended_at": data.get("ended_at") if isinstance(data, dict) else None,
                    "error_count": len(data.get("errors", [])) if isinstance(data, dict) and isinstance(data.get("errors"), list) else 0,
                }
            )
    provider_calls = _provider_call_summary(runs_dir / "provider_calls.jsonl")
    return {
        "run_logs": run_logs,
        "provider_calls": provider_calls,
        "model_io_logs": _model_io_summary(runs_dir / "model_io" / "index.jsonl"),
        "provider_usage": summarize_provider_usage(root).as_dict(),
    }


def _provider_config_summary(root: Path) -> dict[str, object]:
    _require_workspace(root)
    return {
        "agents": _safe_config_file(root / "config" / "agents.yaml"),
        "embeddings": _safe_config_file(root / "config" / "embeddings.yaml"),
    }


def _state_timeline_summary(root: Path) -> dict[str, object]:
    _require_workspace(root)
    state = _safe_json(root / "memory" / "state" / "current_state.json")
    timeline = _safe_json(root / "memory" / "state" / "timeline.json")
    return {
        "state": state,
        "timeline": timeline,
        "summary": {
            "character_state_count": len(state.get("character_states", [])) if isinstance(state, dict) else 0,
            "item_state_count": len(state.get("item_states", [])) if isinstance(state, dict) else 0,
            "location_state_count": len(state.get("location_states", [])) if isinstance(state, dict) else 0,
            "timeline_event_count": len(timeline.get("events", [])) if isinstance(timeline, dict) else 0,
        },
    }


def _workspace_diff(root: Path, left: str, right: str) -> dict[str, object]:
    left_path = _safe_workspace_file(root, left)
    right_path = _safe_workspace_file(root, right)
    if not left_path.exists() or not right_path.exists():
        raise FileNotFoundError("both diff files must exist")
    left_lines = left_path.read_text(encoding="utf-8").splitlines(keepends=True)
    right_lines = right_path.read_text(encoding="utf-8").splitlines(keepends=True)
    diff = "".join(
        difflib.unified_diff(
            left_lines,
            right_lines,
            fromfile=_relative(root, left_path),
            tofile=_relative(root, right_path),
        )
    )
    return {"left": _relative(root, left_path), "right": _relative(root, right_path), "diff": diff}


def _list_chapters(root: Path) -> list[dict[str, object]]:
    chapters_dir = root / "memory" / "chapters"
    chapters: list[dict[str, object]] = []
    if not chapters_dir.exists():
        return chapters
    for child in sorted(chapters_dir.iterdir()):
        if not child.is_dir() or not child.name.isdigit():
            continue
        chapter_number = int(child.name)
        entry: dict[str, object] = {
            "chapter_number": chapter_number,
            "has_plan": (child / "plan.json").exists(),
            "has_draft": (child / "draft.md").exists(),
            "has_polished": (child / "polished.md").exists(),
            "has_audit": (child / "audit.json").exists(),
            "status": None,
            "title": None,
            "audit_status": None,
        }
        _merge_plan_metadata(child / "plan.json", entry)
        _merge_polished_metadata(child / "polished.md", entry)
        if (child / "audit.json").exists():
            data = load_json(child / "audit.json")
            if isinstance(data, dict):
                entry["audit_status"] = data.get("overall_status")
        chapters.append(entry)
    return chapters


def _merge_plan_metadata(path: Path, entry: dict[str, object]) -> None:
    if not path.exists():
        return
    try:
        plan = load_json_model(path, ChapterPlan)
    except Exception:
        return
    entry["title"] = plan.title


def _read_chapter_file(root: Path, query: dict[str, str]) -> dict[str, object]:
    chapter_number = int(query.get("chapter", "0"))
    file_type = query.get("file", "")
    mapping = {
        "plan": "plan.json",
        "draft": "draft.md",
        "polished": "polished.md",
        "audit": "audit.json",
    }
    if chapter_number < 1 or file_type not in mapping:
        raise ValueError("invalid chapter or file type")
    rel_path = f"memory/chapters/{chapter_number:03d}/{mapping[file_type]}"
    path = root / rel_path
    if not path.exists():
        return {"path": str(path), "relative_path": rel_path, "content": "", "exists": False}
    return {
        "path": str(path),
        "relative_path": rel_path,
        "content": path.read_text(encoding="utf-8"),
        "exists": True,
    }


def _merge_polished_metadata(path: Path, entry: dict[str, object]) -> None:
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        return
    try:
        _, metadata_text, _ = content.split("---\n", 2)
        metadata = yaml.safe_load(metadata_text) or {}
    except Exception:
        return
    if isinstance(metadata, dict):
        entry["status"] = metadata.get("status")
        entry["title"] = metadata.get("title")


def _provider_call_summary(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-50:]
    calls: list[dict[str, object]] = []
    for line in lines:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            calls.append(
                {
                    "request_id": data.get("request_id"),
                    "provider": data.get("provider"),
                    "model": data.get("model"),
                    "endpoint": data.get("endpoint"),
                    "status": data.get("status"),
                    "started_at": data.get("started_at"),
                    "ended_at": data.get("ended_at"),
                    "duration_ms": data.get("duration_ms"),
                    "attempt_count": data.get("attempt_count"),
                    "error_type": data.get("error_type"),
                    "http_status": data.get("http_status"),
                    "model_io_path": data.get("model_io_path"),
                }
            )
    return calls


def _model_io_summary(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-50:]
    logs: list[dict[str, object]] = []
    for line in lines:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            logs.append(
                {
                    "request_id": data.get("request_id"),
                    "agent_name": data.get("agent_name"),
                    "provider": data.get("provider"),
                    "model": data.get("model"),
                    "status": data.get("status"),
                    "started_at": data.get("started_at"),
                    "ended_at": data.get("ended_at"),
                    "stream": data.get("stream"),
                    "json_schema_name": data.get("json_schema_name"),
                    "model_io_path": data.get("model_io_path"),
                }
            )
    return logs


def _safe_config_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"path": str(path), "exists": False, "content": None, "env": []}
    data = load_yaml(path)
    env_names = sorted(_collect_env_names(data))
    return {
        "path": str(path),
        "exists": True,
        "content": _sanitize_config(data),
        "env": [{"name": name, "exists": bool(os.environ.get(name))} for name in env_names],
    }


def _safe_json(path: Path) -> object:
    if not path.exists():
        return {}
    try:
        return load_json(path)
    except Exception:
        return {}


def _collect_env_names(value: object) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"api_key_env", "base_url_env"} and isinstance(item, str):
                names.add(item)
            names.update(_collect_env_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(_collect_env_names(item))
    return names


def _sanitize_config(value: object) -> object:
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            if key in {"api_key", "token", "secret"}:
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = _sanitize_config(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_config(item) for item in value]
    if isinstance(value, str):
        return _safe_error(value)
    return value


def _safe_workspace_file(root: Path, rel_path: str) -> Path:
    _require_workspace(root)
    if not rel_path or Path(rel_path).is_absolute():
        raise PermissionError("file must be a relative workspace path")
    path = (root / rel_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PermissionError("file must stay inside the workspace") from exc
    rel = _relative(root, path)
    if not _is_safe_file_rel_path(rel, path):
        raise PermissionError(f"file is not readable through the Web API: {rel_path}")
    return path


def _is_safe_tree_path(rel_path: str, path: Path) -> bool:
    parts = Path(rel_path).parts
    if any(part in EXCLUDED_DIRS for part in parts):
        return False
    if any(part.startswith(".env") for part in parts):
        return False
    if path.name in EXCLUDED_FILENAMES:
        return False
    if path.name.startswith(".") and path.is_file():
        return False
    if path.is_file() and not _is_safe_file_rel_path(rel_path, path):
        return False
    return True


def _is_safe_file_rel_path(rel_path: str, path: Path) -> bool:
    parts = Path(rel_path).parts
    if any(part in EXCLUDED_DIRS for part in parts):
        return False
    if any(part.startswith(".env") for part in parts):
        return False
    if path.name in EXCLUDED_FILENAMES:
        return False
    if ".bak_" in path.name:
        return False
    return path.suffix in SAFE_FILE_SUFFIXES


def _require_workspace(root: Path) -> None:
    if not (root / "project.yaml").exists():
        raise WebAPIError("invalid_project", f"{root} does not look like a novel workspace", status=400)


def _json_body(body: bytes | str | None) -> dict[str, object]:
    if not body:
        return {}
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("request body must be a JSON object")
    return data


def _root_from_query(query: dict[str, str]) -> Path:
    return Path(query.get("path") or ".").expanduser().resolve()


def _root_from_body(data: dict[str, object]) -> Path:
    return Path(str(data.get("path") or ".")).expanduser().resolve()


def _chapter_number(data: dict[str, object]) -> int:
    value = int(data.get("chapter_number") or 0)
    if value < 1:
        raise ValueError("chapter_number must be a positive integer")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _request_id() -> str:
    return "web_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _safe_error(exc: Exception | str) -> str:
    message = str(exc)
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[redacted-api-key]", message)
    redacted = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", redacted)
    for key, value in os.environ.items():
        if value and ("KEY" in key or "TOKEN" in key or "SECRET" in key):
            redacted = redacted.replace(value, "[redacted]")
    return redacted
