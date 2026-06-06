from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import difflib
import json
import os
from pathlib import Path
import re
import sys
from typing import Literal, cast
from urllib.parse import parse_qs

from pydantic import BaseModel, Field
import yaml

from novel import __version__
from novel.core.auditing import ChapterAuditOptions, audit_chapter, load_audit_provider
from novel.core.canon import (
    CanonAppliedProposalRecord,
    CanonError,
    CanonSuggestOptions,
    apply_canon_proposal,
    load_canon_applied_proposals,
    load_canon_provider,
    suggest_canon,
)
from novel.core.chapter_memory import (
    ChapterMemoryOptions,
    accepted_chapter_numbers,
    chapter_memory_freshness_warnings,
    chapter_memory_path,
    generate_chapter_memory,
    load_chapter_memory_provider,
)
from novel.core.drafting import ChapterDraftingOptions, load_drafting_provider, write_chapter_draft
from novel.core.env import load_project_env
from novel.core.exporting import MarkdownExportOptions, export_markdown, parse_chapter_selector
from novel.core.inspiration import InspirationOptions, load_inspiration_provider, run_inspiration_agent
from novel.core.inspection import format_canon, get_project_status
from novel.core.io import atomic_write_model_json, atomic_write_text, atomic_write_yaml, backup_if_exists, load_json, load_json_model, load_yaml
from novel.core.locking import ProjectLock, ProjectLockError
from novel.core.management import load_management_events
from novel.core.memory_repair import (
    MemoryRepairError,
    SettingChangeSuggestionResult,
    answer_setting_change_clarification,
    apply_memory_repair,
    suggest_memory_repair,
    suggest_setting_change_interactive,
)
from novel.core.migration import MigrationError, migrate_project
from novel.core.planning import ChapterPlanningOptions, load_planning_provider, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, load_polishing_provider, polish_chapter
from novel.core.search import SearchError, refresh_search_index, search_index_status, search_project
from novel.core.setup_guide import (
    SetupGuideError,
    configure_default_provider,
    configure_embedding_provider,
    configure_web_port,
    find_available_port,
)
from novel.core.schemas import (
    AgentsConfig,
    AuditReport,
    ChapterMemory,
    ChapterPlan,
    CreationSession,
    EmbeddingsConfig,
    PolishMode,
    RevisionLog,
    RevisionRecord,
    SessionProgress,
    VectorContextMode,
    MemoryChangeStage,
)
from novel.core.security import validate_secret_config_file
from novel.core.session import (
    SessionActionOptions,
    SessionInstructionOptions,
    SessionRunOptions,
    SessionStartOptions,
    SessionRewriteControlOptions,
    accept_session,
    approve_outline,
    archive_session,
    load_session_progress,
    load_session,
    load_rewrite_events,
    parse_range,
    request_session_cancel,
    retry_rewrite,
    revise_audit,
    revise_content,
    revise_outline,
    run_session,
    start_session,
    undo_rewrite,
)
from novel.core.providers import ProviderFactory
from novel.core.usage import summarize_provider_usage
from novel.core.validation import ValidationMessage, validate_project
from novel.core.workflow import GenerateChapterOptions, ProviderName, generate_chapter
from novel.core.workspace import InitOptions, init_workspace, is_default_inspiration_placeholder


APIResponse = tuple[int, dict[str, object]]
SAFE_FILE_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".yaml", ".yml"}
EXCLUDED_DIRS = {".git", ".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache"}
EXCLUDED_FILENAMES = {
    "search_index.json",
    "search_index.sqlite",
    ".DS_Store",
}
EDITABLE_AGENT_NAMES = {
    "orchestrator",
    "inspiration",
    "canon",
    "plot",
    "writer",
    "polish",
    "audit",
    "state_update",
    "chapter_memory",
    "revision",
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
        if method == "GET":
            handler = _get_routes().get(path)
            if handler:
                return _success(handler(query))
        elif method == "POST":
            data = _json_body(body)
            route = _post_routes().get(path)
            if route:
                task, handler, locked = route
                return _success(_locked_write(data, task, handler) if locked else handler(data))
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
    except CanonError as exc:
        return _failure(400, "canon_error", str(exc), request_id=request_id)
    except MemoryRepairError as exc:
        return _failure(400, "memory_repair_error", str(exc), request_id=request_id)
    except SetupGuideError as exc:
        return _failure(400, "setup_guide_error", str(exc), request_id=request_id)
    except MigrationError as exc:
        return _failure(400, "migration_error", str(exc), request_id=request_id)
    except SearchError as exc:
        return _failure(400, "search_error", str(exc), request_id=request_id)
    except ValueError as exc:
        return _failure(400, "invalid_request", str(exc), request_id=request_id)
    except Exception as exc:
        return _failure(400, "operation_failed", str(exc), request_id=request_id)
    return _failure(404, "not_found", "not found", request_id=request_id)


def _get_routes():
    return {
        "/api/runtime": lambda query: {"runtime": _runtime_summary()},
        "/api/projects": lambda query: {"projects": _list_projects(Path(query.get("root", ".")))},
        "/api/project/status": _project_status_api,
        "/api/validate": lambda query: _validate_project(_root_from_query(query)),
        "/api/migration-status": lambda query: _migration_status(_root_from_query(query)),
        "/api/canon": lambda query: {"summary": format_canon(_root_from_query(query))},
        "/api/canon/applied-proposals": _canon_applied_proposals,
        "/api/chapters": lambda query: {"chapters": _list_chapters(_root_from_query(query))},
        "/api/chapter-file": lambda query: _read_chapter_file(_root_from_query(query), query),
        "/api/file-tree": lambda query: {"files": _file_tree(_root_from_query(query))},
        "/api/read-file": lambda query: _read_workspace_file(_root_from_query(query), query.get("file") or ""),
        "/api/runs": lambda query: _runs_summary(_root_from_query(query)),
        "/api/usage": lambda query: {"usage": summarize_provider_usage(_root_from_query(query)).as_dict()},
        "/api/search": _search_api,
        "/api/search-status": lambda query: {"search": search_index_status(_root_from_query(query)).as_dict()},
        "/api/setup/recommend-port": _setup_recommend_port,
        "/api/provider-config": lambda query: _provider_config_summary(_root_from_query(query)),
        "/api/state-timeline": lambda query: _state_timeline_summary(_root_from_query(query)),
        "/api/management-events": lambda query: _management_events(_root_from_query(query), _optional_int(query.get("limit")) or 20),
        "/api/audit-annotations": lambda query: _audit_annotations(_root_from_query(query), query),
        "/api/session": _session_api,
        "/api/session/progress": _session_progress_api,
        "/api/session/rewrite-events": _session_rewrite_events_api,
        "/api/diff": lambda query: _workspace_diff(
            _root_from_query(query),
            query.get("left") or "",
            query.get("right") or "",
        ),
    }


def _post_routes():
    return {
        "/api/plan-chapter": ("web plan-chapter", _plan_chapter, True),
        "/api/write-chapter": ("web write-chapter", _write_chapter, True),
        "/api/polish-chapter": ("web polish-chapter", _polish_chapter, True),
        "/api/audit-chapter": ("web audit-chapter", _audit_chapter, True),
        "/api/export/markdown": ("web export markdown", _export_markdown, True),
        "/api/generate-chapter": ("web generate-chapter", _generate_chapter, True),
        "/api/save-chapter-file": ("web save chapter file", _save_chapter_file, True),
        "/api/provider-config": ("web provider config", _save_provider_config, True),
        "/api/index/refresh": ("web index refresh", _index_refresh, True),
        "/api/migrate": ("web migrate", _migrate_project_api, True),
        "/api/init-project": ("web init project", _init_project, True),
        "/api/setup/default-provider": ("web setup default provider", _setup_default_provider, True),
        "/api/setup/embedding": ("web setup embedding", _setup_embedding, True),
        "/api/setup/web-port": ("web setup web port", _setup_web_port, True),
        "/api/setup/open-web": ("web setup open web", _setup_open_web, False),
        "/api/inspire": ("web inspire", _inspire, True),
        "/api/canon/suggest": ("web canon suggest", _canon_suggest, True),
        "/api/canon/apply": ("web canon apply", _canon_apply, True),
        "/api/orchestrator/memory-repair/suggest": ("web memory repair suggest", _memory_repair_suggest, True),
        "/api/orchestrator/memory-repair/apply": ("web memory repair apply", _memory_repair_apply, True),
        "/api/settings/change/suggest": ("web setting change suggest", _settings_change_suggest, True),
        "/api/settings/change/answer": ("web setting change answer", _settings_change_answer, True),
        "/api/settings/change/apply": ("web setting change apply", _settings_change_apply, True),
        "/api/chapter-memory/generate": ("web chapter memory generate", _chapter_memory_generate, True),
        "/api/chapter-memory/rebuild": ("web chapter memory rebuild", _chapter_memory_rebuild, True),
        "/api/session/start": ("web session start", _session_start, True),
        "/api/session/revise-outline": ("web session revise-outline", _session_revise_outline, True),
        "/api/session/approve-outline": ("web session approve-outline", _session_approve_outline, True),
        "/api/session/run": ("web session run", _session_run, True),
        "/api/session/cancel": ("web session cancel", _session_cancel, False),
        "/api/session/revise-content": ("web session revise-content", _session_revise_content, True),
        "/api/session/revise-audit": ("web session revise-audit", _session_revise_audit, True),
        "/api/session/retry-rewrite": ("web session retry-rewrite", _session_retry_rewrite, True),
        "/api/session/undo-rewrite": ("web session undo-rewrite", _session_undo_rewrite, True),
        "/api/session/accept": ("web session accept", _session_accept, True),
        "/api/session/archive": ("web session archive", _session_archive, True),
    }


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
        str(data.get("provider") or "config"),
        chapter_number=chapter_number,
    )
    result = plan_chapter(
        ChapterPlanningOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
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
    provider = load_drafting_provider(root, str(data.get("provider") or "config"))
    result = write_chapter_draft(
        ChapterDraftingOptions(
            root=root,
            chapter_number=_chapter_number(data),
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            target_words=_optional_int(data.get("target_words")),
            style_note=_optional_string(data.get("style_note")),
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
        ),
        provider,
    )
    return {"draft_path": str(result.draft_path), "warnings": list(result.warnings)}


def _polish_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    provider = load_polishing_provider(root, str(data.get("provider") or "config"))
    result = polish_chapter(
        ChapterPolishingOptions(
            root=root,
            chapter_number=_chapter_number(data),
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            style_note=_optional_string(data.get("style_note")),
            keep_length=bool(data.get("keep_length")),
            edit_mode=str(data.get("edit_mode") or "normal"),  # type: ignore[arg-type]
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
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
        str(data.get("provider") or "config"),
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
            focus=_audit_focus(data.get("focus")),
            audited_file=audited_file,  # type: ignore[arg-type]
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
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
            provider_name=_provider_name(data.get("provider")),
            target_words=_optional_int(data.get("target_words")),
            style_note=_optional_string(data.get("style_note")),
            polish_mode=_polish_mode(data),
            skip_polish=bool(data.get("skip_polish")),
            skip_audit=bool(data.get("skip_audit")),
            stop_after=_optional_string(data.get("stop_after")),  # type: ignore[arg-type]
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
        )
    )
    return {
        "message": result.message,
        "run_log_path": str(result.run_log_path),
        "status": result.run_log.status,
    }


def _chapter_memory_generate(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    _require_workspace(root)
    chapter_number = _chapter_number(data)
    force = True if "force" not in data else _truthy(data.get("force"))
    provider, provider_warnings = _load_web_chapter_memory_provider(root, data, chapter_number)
    result = generate_chapter_memory(
        ChapterMemoryOptions(root=root, chapter_number=chapter_number, force=force),
        provider,
        initial_warnings=tuple(provider_warnings),
    )
    return _chapter_memory_result_payload(root, result.memory_path, result.memory, result.warnings)


def _chapter_memory_rebuild(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    _require_workspace(root)
    mode = _optional_string(data.get("mode")) or "missing_or_stale"
    if mode not in {"missing", "missing_or_stale", "all"}:
        raise WebAPIError("invalid_request", "mode must be missing, missing_or_stale, or all", status=400)
    written: list[dict[str, object]] = []
    skipped: list[int] = []
    warnings: list[str] = []
    for chapter_number in accepted_chapter_numbers(root):
        path = chapter_memory_path(root, chapter_number)
        should_generate = mode == "all" or not path.exists()
        if not should_generate and mode == "missing_or_stale":
            try:
                memory = load_json_model(path, ChapterMemory)
                should_generate = bool(chapter_memory_freshness_warnings(root, memory))
            except Exception:
                should_generate = True
        if not should_generate:
            skipped.append(chapter_number)
            continue
        try:
            provider, provider_warnings = _load_web_chapter_memory_provider(root, data, chapter_number)
            result = generate_chapter_memory(
                ChapterMemoryOptions(root=root, chapter_number=chapter_number, force=True),
                provider,
                initial_warnings=tuple(provider_warnings),
            )
            written.append(_chapter_memory_result_payload(root, result.memory_path, result.memory, result.warnings))
            warnings.extend(f"chapter {chapter_number}: {warning}" for warning in result.warnings)
        except Exception as exc:
            warnings.append(f"chapter {chapter_number}: {exc}")
    return {
        "mode": mode,
        "written": written,
        "skipped": skipped,
        "warnings": warnings,
    }


def _load_web_chapter_memory_provider(root: Path, data: dict[str, object], chapter_number: int):
    warnings: list[str] = []
    try:
        return (
            load_chapter_memory_provider(root, _provider_name(data.get("provider")), chapter_number=chapter_number),
            warnings,
        )
    except Exception as exc:
        warnings.append(f"chapter memory provider unavailable; using deterministic fallback: {exc}")
        return None, warnings


def _chapter_memory_result_payload(
    root: Path,
    memory_path: Path,
    memory: ChapterMemory,
    warnings: tuple[str, ...],
) -> dict[str, object]:
    return {
        "chapter_number": memory.chapter_number,
        "memory_path": str(memory_path),
        "relative_path": _relative(root, memory_path),
        "generation_status": memory.generation_status,
        "warnings": list(warnings),
    }


def _save_chapter_file(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    _require_workspace(root)
    chapter_number = _chapter_number(data)
    target = str(data.get("target") or "")
    if target not in {"draft", "polished"}:
        raise WebAPIError("invalid_request", "target must be draft or polished", status=400)
    content = str(data.get("content") or "")
    if not content.strip():
        raise WebAPIError("invalid_request", "content must not be empty", status=400)
    chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
    source_name = str(data.get("source_file") or f"{target}.md")
    if not _is_allowed_chapter_version_name(source_name, target):
        raise WebAPIError("forbidden_file", "source_file is not an editable chapter version", status=403)
    source_path = chapter_dir / source_name
    if not source_path.exists():
        raise FileNotFoundError(f"{source_name} does not exist")
    if _is_archived_chapter(root, chapter_number):
        raise WebAPIError(
            "archived_content_read_only",
            "archived chapter content is read-only; create a new revision session instead",
            status=409,
        )

    output_path = _next_version_path(chapter_dir, target)
    atomic_write_text(output_path, content.rstrip() + "\n")
    record = RevisionRecord(
        id=_new_revision_id(),
        chapter_number=chapter_number,
        target=target,  # type: ignore[arg-type]
        source_file=source_name,
        output_file=output_path.name,
        instruction=_optional_string(data.get("instruction")) or "Web editor save as version",
        from_audit=False,
        audit_file="audit.json" if (chapter_dir / "audit.json").exists() else None,
        audit_issue_ids=[],
        created_at=datetime.now(timezone.utc).replace(microsecond=0),
        provider="web_editor",
    )
    log_path = chapter_dir / "revision_log.json"
    _append_web_revision_log(log_path, chapter_number, record)
    return {
        "output_path": str(output_path),
        "relative_path": _relative(root, output_path),
        "revision_log_path": str(log_path),
        "record": record.model_dump(mode="json"),
    }


def _save_provider_config(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    _require_workspace(root)
    config_path = root / "config" / "agents.yaml"
    raw_config = load_yaml(config_path)
    if not isinstance(raw_config, dict):
        raise WebAPIError("invalid_config", "config/agents.yaml must be a YAML mapping", status=400)
    agents_update = data.get("agents")
    default_update = data.get("default")
    clear_agents = data.get("clear_agents")
    if agents_update is None and default_update is None and clear_agents is None:
        raise WebAPIError("invalid_request", "default, agents, or clear_agents must be provided", status=400)
    if agents_update is None:
        agents_update = {}
    if not isinstance(agents_update, dict):
        raise WebAPIError("invalid_request", "agents must be a mapping", status=400)
    if clear_agents is None:
        clear_agents = []
    if not isinstance(clear_agents, list) or any(not isinstance(name, str) for name in clear_agents):
        raise WebAPIError("invalid_request", "clear_agents must be a list of agent names", status=400)
    updated = dict(raw_config)
    if default_update is not None:
        if not isinstance(default_update, dict):
            raise WebAPIError("invalid_request", "default must be a mapping", status=400)
        current_default = updated.get("default")
        if current_default is not None and not isinstance(current_default, dict):
            raise WebAPIError("invalid_config", "default config must be a mapping", status=400)
        updated["default"] = {**(current_default or {}), **_clean_agent_config_patch(default_update)}
    agents = dict(updated.get("agents") or {})
    cleared: list[str] = []
    for agent_name in clear_agents:
        if agent_name == "default":
            raise WebAPIError("invalid_request", "default config cannot be cleared", status=400)
        if agent_name not in EDITABLE_AGENT_NAMES and agent_name not in agents:
            raise WebAPIError("invalid_request", f"unknown agent: {agent_name}", status=400)
        if agent_name in agents:
            agents.pop(agent_name, None)
            cleared.append(agent_name)
    for agent_name, patch in agents_update.items():
        if not isinstance(agent_name, str) or not isinstance(patch, dict):
            raise WebAPIError("invalid_request", "agent updates must be mappings", status=400)
        if agent_name not in agents or not isinstance(agents[agent_name], dict):
            if agent_name not in EDITABLE_AGENT_NAMES:
                raise WebAPIError("invalid_request", f"unknown agent: {agent_name}", status=400)
            agents[agent_name] = {}
        cleaned = _clean_agent_config_patch(patch)
        agents[agent_name] = {**agents[agent_name], **cleaned}
    updated["agents"] = agents
    AgentsConfig.model_validate(updated)
    backup_path = backup_if_exists(config_path, reason="web_provider_config")
    atomic_write_yaml(config_path, updated)
    findings = validate_secret_config_file(config_path)
    if findings:
        if backup_path:
            atomic_write_text(config_path, backup_path.read_text(encoding="utf-8"))
        raise WebAPIError("unsafe_config_secret", "provider config contains unsafe secret-like values", status=400)
    summary = _provider_config_summary(root)
    return {
        "path": str(config_path),
        "backup_path": str(backup_path) if backup_path else None,
        "cleared_agents": cleared,
        "config": summary["agents"],
        "effective_agents": summary["effective_agents"],
    }


def _index_refresh(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    result = refresh_search_index(
        root,
        embedding_provider_name=str(data.get("embedding_provider") or "config"),
        with_embeddings=bool(data.get("with_embeddings")),
    )
    return {
        "index_path": str(result.index_path),
        "sqlite_path": str(result.sqlite_path),
        "manifest_path": str(result.manifest_path),
        "document_count": result.document_count,
        "refreshed_count": result.refreshed_count,
        "deleted_count": result.deleted_count,
        "embedding_document_count": result.embedding_document_count,
        "with_embeddings": result.with_embeddings,
        "search": search_index_status(root).as_dict(),
    }


def _init_project(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    title = _optional_string(data.get("title")) or root.name or "未命名小说"
    genre_value = data.get("genre")
    genre = _split_csv(str(genre_value)) if genre_value else None
    result = init_workspace(
        InitOptions(
            title=title,
            root=root,
            language=_optional_string(data.get("language")) or "zh-CN",
            genre=genre,
        )
    )
    return {
        "root": str(result.root),
        "created_files": [str(path) for path in result.created_files],
        "created_dirs": [str(path) for path in result.created_dirs],
        "setup_required": True,
    }


def _setup_recommend_port(query: dict[str, str]) -> dict[str, object]:
    start = _optional_int(query.get("start_port")) or 8765
    host = query.get("host") or "127.0.0.1"
    selected = find_available_port(start, host=host)
    return {
        "host": host,
        "requested_port": start,
        "selected_port": selected,
        "available": selected == start,
        "url": f"http://{host}:{selected}",
    }


def _setup_default_provider(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    result = configure_default_provider(
        root,
        provider=_optional_string(data.get("provider")) or "openai_compatible",
        base_url=_required_string(data.get("base_url"), "base_url"),
        api_key=_required_string(data.get("api_key"), "api_key"),
        model=_required_string(data.get("model"), "model"),
        thinking_type=_optional_string(data.get("thinking_type")) or "disabled",
        temperature=_optional_float(data.get("temperature"), 0.5),
        max_context_tokens=_optional_int(data.get("max_context_tokens")) or 128000,
        max_tokens=_optional_int(data.get("max_tokens")) or 8192,
        timeout_seconds=_optional_float(data.get("timeout_seconds"), 60.0),
        max_retries=_optional_int(data.get("max_retries")) or 1,
        ping=bool(data.get("ping", True)),
    )
    return {
        "config_path": str(result.config_path),
        "env_path": str(result.env_path),
        "provider": result.provider,
        "model": result.model,
        "api_key_env": result.api_key_env,
        "base_url_env": result.base_url_env,
        "ping_ok": result.ping_ok,
        "ping_message": result.ping_message,
        "message": "这组 API 配置已作为所有未单独配置 Agent 的默认配置。可在 config/agents.yaml 中单独覆盖每个 Agent 的模型、思考模式、温度等参数。",
    }


def _setup_embedding(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    if bool(data.get("skip")):
        return {"skipped": True, "message": "已跳过 embedding API 配置；关键词/FTS 检索仍可用。"}
    dimensions = _optional_int(data.get("dimensions"))
    result = configure_embedding_provider(
        root,
        provider=_optional_string(data.get("provider")) or "openai_compatible",
        provider_name=_optional_string(data.get("provider_name")) or "configured",
        base_url=_required_string(data.get("base_url"), "base_url"),
        api_key=_required_string(data.get("api_key"), "api_key"),
        model=_required_string(data.get("model"), "model"),
        dimensions=dimensions if dimensions and dimensions > 0 else None,
        batch_size=_optional_int(data.get("batch_size")) or 16,
        timeout_seconds=_optional_float(data.get("timeout_seconds"), 30.0),
        max_retries=_optional_int(data.get("max_retries")) or 1,
        ping=bool(data.get("ping", True)),
    )
    return {
        "config_path": str(result.config_path),
        "env_path": str(result.env_path),
        "active_provider": result.active_provider,
        "provider": result.provider,
        "model": result.model,
        "api_key_env": result.api_key_env,
        "base_url_env": result.base_url_env,
        "ping_ok": result.ping_ok,
        "ping_message": result.ping_message,
        "embedding_api": _embedding_api_config_summary(root),
    }


def _setup_web_port(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    host = _optional_string(data.get("host")) or "127.0.0.1"
    requested = _optional_int(data.get("port")) or 8765
    result = configure_web_port(root, requested_port=requested, host=host)
    return {
        "project_path": str(result.project_path),
        "host": result.host,
        "requested_port": result.requested_port,
        "selected_port": result.selected_port,
        "available": result.requested_port == result.selected_port,
        "url": result.url,
    }


def _setup_open_web(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    host = _optional_string(data.get("host")) or "127.0.0.1"
    port = _optional_int(data.get("port")) or _configured_web_port(root)
    return {"url": f"http://{host}:{port}", "opened": False}


def _inspire(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    provider = load_inspiration_provider(root, str(data.get("provider") or "config"))
    source_text = _optional_string(data.get("text")) or _optional_string(data.get("instruction"))
    if not source_text:
        raise WebAPIError("invalid_request", "inspiration text must not be empty", status=400)
    inspiration_path = root / "memory" / "inspiration.md"
    overwrite = bool(data.get("force")) or is_default_inspiration_placeholder(inspiration_path)
    result = run_inspiration_agent(
        InspirationOptions(
            root=root,
            source_text=source_text,
            source_type="web_text",
            write_json=bool(data.get("write_json")),
            overwrite=overwrite,
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
        ),
        provider,
    )
    return {
        "markdown_path": str(result.markdown_path),
        "json_path": str(result.json_path) if result.json_path else None,
    }


def _canon_suggest(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    provider = load_canon_provider(root, str(data.get("provider") or "config"))
    output = _optional_string(data.get("output"))
    output_path = _safe_workspace_file(root, output) if output else _default_canon_proposal_path(root)
    result = suggest_canon(
        CanonSuggestOptions(
            root=root,
            output_path=output_path,
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
        ),
        provider,
    )
    return {
        "output_path": str(result.output_path) if result.output_path else None,
        "relative_path": _relative(root, result.output_path) if result.output_path else None,
        "proposal": result.proposal.model_dump(mode="json"),
    }


def _canon_apply(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    proposal_file = _optional_string(data.get("proposal_file")) or _optional_string(data.get("proposal_path"))
    if not proposal_file:
        raise WebAPIError("invalid_request", "proposal_file is required", status=400)
    proposal_path = _safe_workspace_file(root, proposal_file)
    result = apply_canon_proposal(root, proposal_path)
    return {
        "proposal_path": str(proposal_path),
        "apply_log": result.apply_log.model_dump(mode="json"),
        "apply_log_path": str(result.apply_log_path),
        "apply_log_relative_path": _relative(root, result.apply_log_path),
        "proposal_snapshot_path": str(result.proposal_snapshot_path),
        "proposal_snapshot_relative_path": _relative(root, result.proposal_snapshot_path),
        "validation_ok": result.validation_report.ok,
        "errors": [message.message for message in result.validation_report.errors],
        "warnings": [message.message for message in result.validation_report.warnings],
    }


def _canon_applied_proposals(query: dict[str, str]) -> dict[str, object]:
    root = _root_from_query(query)
    _require_workspace(root)
    limit = _optional_int(query.get("limit")) or 20
    return {
        "applied_proposals": [
            _canon_applied_proposal_payload(root, record)
            for record in load_canon_applied_proposals(root, limit=limit)
        ]
    }


def _canon_applied_proposal_payload(root: Path, record: CanonAppliedProposalRecord) -> dict[str, object]:
    log = record.apply_log
    return {
        "id": log.id,
        "apply_log_path": _relative(root, record.apply_log_path),
        "original_proposal_path": log.original_proposal_path,
        "proposal_snapshot_path": log.proposal_snapshot_path,
        "target_files": log.target_files,
        "proposal_counts": log.proposal_counts.model_dump(mode="json"),
        "validation_warning_count": log.validation_warning_count,
        "applied_at": log.applied_at.isoformat(),
        "status": log.status,
    }


def _memory_repair_suggest(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    request = _optional_string(data.get("request")) or _optional_string(data.get("instruction"))
    if not request:
        raise WebAPIError("invalid_request", "request is required", status=400)
    provider = _optional_string(data.get("provider")) or "config"
    result = suggest_memory_repair(root, request, provider_name=provider)
    return {
        "proposal": result.proposal.model_dump(mode="json"),
        "proposal_path": str(result.proposal_path),
        "proposal_relative_path": _relative(root, result.proposal_path),
        "markdown_path": str(result.markdown_path),
        "markdown_relative_path": _relative(root, result.markdown_path),
        "management_events": _management_event_summary(root),
    }


def _memory_repair_apply(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    proposal_path_text = _optional_string(data.get("proposal_path")) or _optional_string(data.get("proposal_file"))
    if not proposal_path_text:
        raise WebAPIError("invalid_request", "proposal_path is required", status=400)
    proposal_path = _safe_workspace_file(root, proposal_path_text)
    result = apply_memory_repair(root, proposal_path)
    return {
        "proposal": result.proposal.model_dump(mode="json"),
        "apply_log": result.apply_log.model_dump(mode="json"),
        "apply_log_path": str(result.apply_log_path),
        "apply_log_relative_path": _relative(root, result.apply_log_path),
        "management_events": _management_event_summary(root),
    }


def _settings_change_suggest(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    request = _optional_string(data.get("request")) or _optional_string(data.get("instruction"))
    if not request:
        raise WebAPIError("invalid_request", "request is required", status=400)
    provider = _optional_string(data.get("provider")) or "config"
    audit_issue_ids = _string_list(data.get("audit_issue_ids"))
    result = suggest_setting_change_interactive(
        root,
        request,
        provider_name=provider,
        stage=_memory_change_stage(data.get("source_stage") or data.get("stage")),
        session_id=_optional_string(data.get("session_id")),
        chapter_number=_optional_int(data.get("chapter_number")) or _optional_int(data.get("chapter")),
        audit_issue_ids=audit_issue_ids,
    )
    return _setting_change_suggestion_payload(root, result)


def _settings_change_answer(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    clarification_id = _optional_string(data.get("clarification_id"))
    answer = _optional_string(data.get("answer")) or _optional_string(data.get("message"))
    if not clarification_id:
        raise WebAPIError("invalid_request", "clarification_id is required", status=400)
    if not answer:
        raise WebAPIError("invalid_request", "answer is required", status=400)
    result = answer_setting_change_clarification(
        root,
        clarification_id,
        answer,
        provider_name=_optional_string(data.get("provider")) or "config",
    )
    return _setting_change_suggestion_payload(root, result)


def _setting_change_suggestion_payload(root: Path, result: SettingChangeSuggestionResult) -> dict[str, object]:
    if result.status == "needs_clarification":
        if result.clarification is None:
            raise WebAPIError("internal_error", "missing clarification result", status=500)
        clarification = result.clarification
        return {
            "status": "needs_clarification",
            "clarification": clarification.model_dump(mode="json"),
            "clarification_id": clarification.clarification_id,
            "questions": clarification.questions,
            "conversation_turns": [turn.model_dump(mode="json") for turn in clarification.conversation_turns],
            "management_events": _management_event_summary(root),
        }
    if result.proposal_result is None:
        raise WebAPIError("internal_error", "missing setting change proposal result", status=500)
    proposal_result = result.proposal_result
    return {
        "status": "proposal_ready",
        "proposal": proposal_result.proposal.model_dump(mode="json"),
        "proposal_path": str(proposal_result.proposal_path),
        "proposal_relative_path": _relative(root, proposal_result.proposal_path),
        "markdown_path": str(proposal_result.markdown_path),
        "markdown_relative_path": _relative(root, proposal_result.markdown_path),
        "management_events": _management_event_summary(root),
    }


def _settings_change_apply(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    proposal_path_text = _optional_string(data.get("proposal_path")) or _optional_string(data.get("proposal_file"))
    if not proposal_path_text:
        raise WebAPIError("invalid_request", "proposal_path is required", status=400)
    proposal_path = _safe_workspace_file(root, proposal_path_text)
    result = apply_memory_repair(root, proposal_path)
    sync_result = {"status": "skipped", "reason": "sync_session is false"}
    if bool(data.get("sync_session")):
        sync_result = _sync_setting_change_session(
            root,
            result.proposal,
            session_id=_optional_string(data.get("session_id")),
            provider_name=_optional_string(data.get("provider")) or "config",
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
            polish_mode=_polish_mode(data),
        )
    return {
        "proposal": result.proposal.model_dump(mode="json"),
        "apply_log": result.apply_log.model_dump(mode="json"),
        "apply_log_path": str(result.apply_log_path),
        "apply_log_relative_path": _relative(root, result.apply_log_path),
        "sync_result": sync_result,
        "management_events": _management_event_summary(root),
    }


def _sync_setting_change_session(
    root: Path,
    proposal,
    *,
    session_id: str | None,
    provider_name: str,
    use_search_context: bool,
    use_vector_context: VectorContextMode,
    polish_mode: PolishMode | None,
) -> dict[str, object]:
    if not session_id:
        return {"status": "skipped", "reason": "session_id is missing"}
    try:
        session = load_session(root, session_id)
    except Exception as exc:
        return {"status": "failed", "reason": f"could not load session: {exc}"}
    if session.status in {"accepted", "archived"} or session.content_status in {"accepted", "archived"}:
        return {
            "status": "manual_review",
            "reason": "accepted or archived sessions are not rewritten automatically",
            "session_id": session_id,
        }
    instruction = (
        "设定变更已应用，请基于最新项目 memory 同步当前创作。\n"
        f"原始设定变更请求：{proposal.user_request}\n"
        f"影响分析：{proposal.impact.summary if proposal.impact else '无'}"
    )
    try:
        if session.content_status == "not_started":
            result = revise_outline(
                SessionInstructionOptions(
                    root=root,
                    session_id=session_id,
                    instruction=instruction,
                    provider_name=provider_name,
                    force=True,
                    use_search_context=use_search_context,
                    use_vector_context=use_vector_context,
                    polish_mode=polish_mode,
                )
            )
            return {"status": "synced", "action": "revise_outline", "session": _session_result_payload(result)}
        if session.content_status in {"needs_user_review", "needs_revision"}:
            result = revise_content(
                SessionInstructionOptions(
                    root=root,
                    session_id=session_id,
                    instruction=instruction,
                    provider_name=provider_name,
                    force=True,
                    use_search_context=use_search_context,
                    use_vector_context=use_vector_context,
                    polish_mode=polish_mode,
                )
            )
            return {"status": "synced", "action": "revise_content", "session": _session_result_payload(result)}
        return {
            "status": "manual_review",
            "reason": f"session status {session.status}/{session.content_status} is not safe for automatic sync",
            "session_id": session_id,
        }
    except Exception as exc:
        return {"status": "failed", "reason": str(exc), "session_id": session_id}


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
            provider_name=str(data.get("provider") or "config"),
            force=bool(data.get("force")),
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
            polish_mode=_polish_mode(data),
        )
    )
    return _session_result_payload(result)


def _session_revise_outline(data: dict[str, object]) -> dict[str, object]:
    result = revise_outline(
        SessionInstructionOptions(
            root=_root_from_body(data),
            session_id=str(data.get("session_id") or ""),
            instruction=str(data.get("instruction") or ""),
            provider_name=str(data.get("provider") or "config"),
            force=bool(data.get("force")),
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
            polish_mode=_polish_mode(data),
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
            provider_name=str(data.get("provider") or "config"),
            force=bool(data.get("force")),
            max_auto_revision_rounds=_optional_int(data.get("max_auto_revision_rounds")),
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
            polish_mode=_polish_mode(data),
        )
    )
    return _session_result_payload(result)


def _session_cancel(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    session_id = _required_string(data.get("session_id"), "session_id")
    progress = request_session_cancel(root, session_id)
    return {
        "session_id": session_id,
        "message": "取消已请求，将在当前章节或修复轮结束后生效。",
        "progress": _session_progress_payload(progress),
    }


def _session_revise_content(data: dict[str, object]) -> dict[str, object]:
    result = revise_content(
        SessionInstructionOptions(
            root=_root_from_body(data),
            session_id=str(data.get("session_id") or ""),
            instruction=str(data.get("instruction") or ""),
            provider_name=str(data.get("provider") or "config"),
            force=bool(data.get("force")),
            from_audit=bool(data.get("from_audit")),
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
            polish_mode=_polish_mode(data),
        )
    )
    return _session_result_payload(result)


def _session_revise_audit(data: dict[str, object]) -> dict[str, object]:
    result = revise_audit(
        SessionRewriteControlOptions(
            root=_root_from_body(data),
            session_id=str(data.get("session_id") or ""),
            event_id=str(data.get("event_id") or ""),
            instruction=str(data.get("instruction") or ""),
            provider_name=str(data.get("provider") or "config"),
            force=bool(data.get("force")),
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
            polish_mode=_polish_mode(data),
        )
    )
    return _session_result_payload(result)


def _session_retry_rewrite(data: dict[str, object]) -> dict[str, object]:
    result = retry_rewrite(
        SessionRewriteControlOptions(
            root=_root_from_body(data),
            session_id=str(data.get("session_id") or ""),
            event_id=str(data.get("event_id") or ""),
            instruction=_optional_string(data.get("instruction")),
            provider_name=str(data.get("provider") or "config"),
            force=bool(data.get("force")),
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
            polish_mode=_polish_mode(data),
        )
    )
    return _session_result_payload(result)


def _session_undo_rewrite(data: dict[str, object]) -> dict[str, object]:
    result = undo_rewrite(
        SessionRewriteControlOptions(
            root=_root_from_body(data),
            session_id=str(data.get("session_id") or ""),
            event_id=str(data.get("event_id") or ""),
            provider_name=str(data.get("provider") or "config"),
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
            polish_mode=_polish_mode(data),
        )
    )
    return _session_result_payload(result)


def _session_accept(data: dict[str, object]) -> dict[str, object]:
    result = accept_session(
        SessionActionOptions(
            root=_root_from_body(data),
            session_id=str(data.get("session_id") or ""),
            provider_name=str(data.get("provider") or "config"),
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


def _validate_project(root: Path) -> dict[str, object]:
    _require_workspace(root)
    report = validate_project(root)
    return {
        "valid": report.ok,
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "errors": [_validation_message_payload(root, message) for message in report.errors],
        "warnings": [_validation_message_payload(root, message) for message in report.warnings],
        "messages": [_validation_message_payload(root, message) for message in report.messages],
    }


def _project_status_api(query: dict[str, str]) -> dict[str, object]:
    root = _root_from_query(query)
    status = get_project_status(root)
    payload = asdict(status)
    payload["latest_run_log"] = str(status.latest_run_log) if status.latest_run_log else None
    return {"status": payload}


def _search_api(query: dict[str, str]) -> dict[str, object]:
    root = _root_from_query(query)
    search_query = _required_string(query.get("query") or query.get("q"), "query")
    search_type = _optional_string(query.get("type")) or "all"
    if search_type not in {"character", "location", "item", "event", "chapter", "chapter_memory", "all"}:
        raise WebAPIError(
            "invalid_request",
            "type must be character/location/item/event/chapter/chapter_memory/all",
            status=400,
        )
    limit = _optional_int(query.get("limit")) or 10
    chapter = _optional_int(query.get("chapter"))
    use_vector = _truthy(query.get("use_vector"))
    results = search_project(
        root,
        search_query,
        search_type=search_type,  # type: ignore[arg-type]
        limit=limit,
        chapter_number=chapter,
        highlight=_truthy(query.get("highlight")),
        use_vector=use_vector,
        embedding_provider_name=_optional_string(query.get("embedding_provider")) or "config",
    )
    return {
        "query": search_query,
        "type": search_type,
        "chapter": chapter,
        "limit": limit,
        "use_vector": use_vector,
        "results": [
            {
                "id": result.id,
                "type": result.type,
                "path": result.path,
                "title": result.title,
                "score": result.score,
                "matched_terms": list(result.matched_terms),
                "excerpt": result.excerpt,
                "highlighted_excerpt": result.highlighted_excerpt,
                "metadata": result.metadata,
            }
            for result in results
        ],
    }


def _migration_status(root: Path) -> dict[str, object]:
    _require_workspace(root)
    result = migrate_project(root, dry_run=True)
    return _migration_payload(root, result)


def _migrate_project_api(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    _require_workspace(root)
    result = migrate_project(root, dry_run=False)
    payload = _migration_payload(root, result)
    payload["validation"] = _validate_project(root)
    return payload


def _migration_payload(root: Path, result) -> dict[str, object]:
    return {
        "changed": result.changed,
        "from_version": result.from_version,
        "to_version": result.to_version,
        "updated_files": [_relative(root, path) for path in result.updated_files],
    }


def _session_api(query: dict[str, str]) -> dict[str, object]:
    root = _root_from_query(query)
    session = load_session(root, query.get("session_id") or "")
    return {
        "session": session.model_dump(mode="json"),
        "progress": _session_progress_payload(load_session_progress(root, session.session_id)),
        "audit_summary": _session_audit_summary(root, session),
        "rewrite_events": _session_rewrite_event_summary(root, session),
        "management_events": _management_event_summary(root),
    }


def _session_progress_api(query: dict[str, str]) -> dict[str, object]:
    root = _root_from_query(query)
    session_id = _required_string(query.get("session_id"), "session_id")
    load_session(root, session_id)
    return {
        "session_id": session_id,
        "progress": _session_progress_payload(load_session_progress(root, session_id)),
    }


def _session_rewrite_events_api(query: dict[str, str]) -> dict[str, object]:
    root = _root_from_query(query)
    session = load_session(root, query.get("session_id") or "")
    return {
        "session_id": session.session_id,
        "rewrite_events": _session_rewrite_event_summary(root, session),
    }


def _validation_message_payload(root: Path, message: ValidationMessage) -> dict[str, object]:
    return {
        "level": message.level,
        "path": _relative(root, message.path),
        "message": message.message,
    }


def _session_result_payload(result) -> dict[str, object]:
    root = _session_root_from_result_path(result.session_path)
    return {
        "session": result.session.model_dump(mode="json"),
        "session_path": str(result.session_path),
        "message": result.message,
        "progress": _session_progress_payload(load_session_progress(root, result.session.session_id)),
        "audit_summary": _session_audit_summary(root, result.session),
        "rewrite_events": _session_rewrite_event_summary(root, result.session),
        "revision_route": _session_latest_revision_route(result.session),
        "management_events": _management_event_summary(root),
    }


def _session_progress_payload(progress: SessionProgress) -> dict[str, object]:
    return _redact_progress_value(progress.model_dump(mode="json"))


def _redact_progress_value(value):
    if isinstance(value, str):
        return _safe_error(value)
    if isinstance(value, list):
        return [_redact_progress_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_progress_value(item) for key, item in value.items()}
    return value


def _session_root_from_result_path(session_path: Path) -> Path:
    for parent in session_path.parents:
        if (parent / "project.yaml").exists():
            return parent
    return session_path.parents[3]


def _session_latest_revision_route(session: CreationSession) -> dict[str, object] | None:
    if not session.revision_route_history:
        return None
    record = session.revision_route_history[-1]
    return record.model_dump(mode="json")


def _session_audit_summary(root: Path, session: CreationSession) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for chapter_number in session.chapter_range:
        audit_path = root / "memory" / "chapters" / f"{chapter_number:03d}" / "audit.json"
        if not audit_path.exists():
            summaries.append(
                {
                    "chapter_number": chapter_number,
                    "exists": False,
                    "overall_status": None,
                    "blocking_issue_count": 0,
                    "issues": [],
                    "path": _relative(root, audit_path),
                }
            )
            continue
        try:
            report = load_json_model(audit_path, AuditReport)
        except Exception as exc:
            summaries.append(
                {
                    "chapter_number": chapter_number,
                    "exists": True,
                    "overall_status": None,
                    "blocking_issue_count": 0,
                    "issues": [],
                    "error": str(exc),
                    "path": _relative(root, audit_path),
                }
            )
            continue
        issues = [
            {
                "id": issue.id,
                "severity": issue.severity,
                "type": issue.type,
                "description": issue.description,
                "suggested_fix": issue.suggested_fix,
            }
            for issue in report.issues
        ]
        summaries.append(
            {
                "chapter_number": chapter_number,
                "exists": True,
                "overall_status": report.overall_status,
                "blocking_issue_count": sum(
                    1 for issue in report.issues if issue.severity in {"medium", "high", "critical"}
                ),
                "summary": report.summary,
                "issues": issues,
                "path": _relative(root, audit_path),
            }
        )
    return summaries


def _session_rewrite_event_summary(root: Path, session: CreationSession) -> list[dict[str, object]]:
    events = load_rewrite_events(root, session.session_id)
    return [
        {
            "event_id": event.event_id,
            "chapter_number": event.chapter_number,
            "round_number": event.round_number,
            "action": event.action,
            "status": event.status,
            "trigger_audit_path": event.trigger_audit_path,
            "rejected_text_snapshot_path": event.rejected_text_snapshot_path,
            "before_output_path": event.before_output_path,
            "after_output_path": event.after_output_path,
            "can_undo": event.can_undo,
            "undo_status": event.undo_status,
            "restored_from_snapshot_path": event.restored_from_snapshot_path,
            "audit_revision_history": [
                revision.model_dump(mode="json") for revision in event.audit_revision_history
            ],
            "created_at": event.created_at.isoformat(),
            "updated_at": event.updated_at.isoformat() if event.updated_at else None,
            "blocking_issues": [
                {
                    "id": issue.id,
                    "severity": issue.severity,
                    "type": issue.type,
                    "description": issue.description,
                    "evidence": [evidence.model_dump(mode="json") for evidence in issue.evidence],
                    "suggested_fix": issue.suggested_fix,
                }
                for issue in event.blocking_issues
            ],
        }
        for event in events
    ]


def _management_events(root: Path, limit: int = 20) -> dict[str, object]:
    _require_workspace(root)
    return {"events": _management_event_summary(root, limit=limit)}


def _management_event_summary(root: Path, limit: int = 10) -> list[dict[str, object]]:
    return [event.model_dump(mode="json") for event in load_management_events(root, limit=limit)]


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
    agents_path = root / "config" / "agents.yaml"
    agents = _safe_config_file(agents_path)
    agents["warnings"] = _agent_config_warnings(root / "config" / "agents.yaml")
    return {
        "agents": agents,
        "embeddings": _safe_config_file(root / "config" / "embeddings.yaml"),
        "effective_agents": _effective_agent_config_summary(agents_path),
        "embedding_api": _embedding_api_config_summary(root),
    }


def _embedding_api_config_summary(root: Path) -> dict[str, object]:
    path = root / "config" / "embeddings.yaml"
    if not path.exists():
        return {
            "configured": False,
            "status": "not_configured",
            "active_provider": None,
            "provider": None,
            "model": None,
            "env_missing": [],
        }
    try:
        config = EmbeddingsConfig.model_validate(load_yaml(path))
    except Exception as exc:
        return {
            "configured": False,
            "status": "invalid_config",
            "active_provider": None,
            "provider": None,
            "model": None,
            "env_missing": [],
            "message": _safe_error(str(exc)),
        }
    selected = config.providers.get(config.active_provider)
    if selected is None:
        return {
            "configured": False,
            "status": "not_configured",
            "active_provider": config.active_provider,
            "provider": None,
            "model": None,
            "env_missing": [],
        }
    provider = selected.provider.lower()
    if provider == "local_hash":
        return {
            "configured": False,
            "status": "test_only",
            "active_provider": config.active_provider,
            "provider": provider,
            "model": selected.model,
            "env_missing": [],
        }
    env = load_project_env(root)
    missing: list[str] = []
    if selected.api_key_env and not env.get(selected.api_key_env):
        missing.append(selected.api_key_env)
    if provider == "openai_compatible" and selected.base_url_env and not env.get(selected.base_url_env):
        missing.append(selected.base_url_env)
    if not selected.api_key_env:
        missing.append("api_key_env")
    return {
        "configured": not missing,
        "status": "env_missing" if missing else "configured",
        "active_provider": config.active_provider,
        "provider": provider,
        "model": selected.model,
        "api_key_env": selected.api_key_env,
        "base_url_env": selected.base_url_env,
        "env_missing": list(dict.fromkeys(missing)),
    }


def _effective_agent_config_summary(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        raw = load_yaml(path)
        config = AgentsConfig.model_validate(raw)
    except Exception as exc:
        return {
            name: {"source": "unresolved", "source_label": "unresolved", "error": _safe_error(str(exc))}
            for name in sorted(EDITABLE_AGENT_NAMES)
        }
    raw_agents = raw.get("agents") if isinstance(raw, dict) else {}
    if not isinstance(raw_agents, dict):
        raw_agents = {}
    names = ["default", *sorted(set(EDITABLE_AGENT_NAMES) | set(raw_agents))]
    resolver = ProviderFactory(env={})
    summaries: dict[str, object] = {}
    for name in names:
        if name == "default":
            if config.default is None:
                summaries[name] = {"source": "unresolved", "source_label": "unresolved", "has_override": False}
                continue
            summaries[name] = {
                "source": "default",
                "source_label": "default",
                "has_override": False,
                "inherits_default": False,
                "override_fields": [],
                "config": _sanitize_config(config.default.model_dump(mode="json", exclude_none=True)),
            }
            continue
        has_override = name in config.agents
        selected = config.agents.get(name)
        override = (
            selected.model_dump(mode="json", exclude_unset=True, exclude_none=True)
            if selected is not None
            else {}
        )
        try:
            resolved = resolver.resolve_agent_config(config, name)
        except Exception as exc:
            summaries[name] = {
                "source": "unresolved",
                "source_label": "unresolved",
                "has_override": has_override,
                "inherits_default": False,
                "override_fields": sorted(override),
                "override": _sanitize_config(override),
                "error": _safe_error(str(exc)),
            }
            continue
        source = "default + agent override" if has_override and config.default is not None else "agent override" if has_override else "default"
        summaries[name] = {
            "source": source,
            "source_label": source,
            "has_override": has_override,
            "inherits_default": not has_override and config.default is not None,
            "override_fields": sorted(override),
            "override": _sanitize_config(override),
            "config": _sanitize_config(resolved.model_dump(mode="json", exclude_none=True)),
        }
    return summaries


def _state_timeline_summary(root: Path) -> dict[str, object]:
    _require_workspace(root)
    state = _safe_json(root / "memory" / "state" / "current_state.json")
    timeline = _safe_json(root / "memory" / "state" / "timeline.json")
    canon = {
        "characters": _safe_json(root / "memory" / "canon" / "characters.json"),
        "locations": _safe_json(root / "memory" / "canon" / "locations.json"),
        "items": _safe_json(root / "memory" / "canon" / "items.json"),
    }
    visual = _state_timeline_visual_summary(state, timeline, canon)
    return {
        "state": state,
        "timeline": timeline,
        "visual": visual,
        "summary": {
            "character_state_count": len(state.get("character_states", [])) if isinstance(state, dict) else 0,
            "item_state_count": len(state.get("item_states", [])) if isinstance(state, dict) else 0,
            "location_state_count": len(state.get("location_states", [])) if isinstance(state, dict) else 0,
            "timeline_event_count": len(timeline.get("events", [])) if isinstance(timeline, dict) else 0,
        },
    }


def _audit_annotations(root: Path, query: dict[str, str]) -> dict[str, object]:
    _require_workspace(root)
    chapter_number = int(query.get("chapter", "0"))
    audited_file = query.get("file") or "polished.md"
    if chapter_number < 1 or audited_file not in {"draft.md", "polished.md"}:
        raise ValueError("invalid chapter or audited file")
    chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
    audit_path = chapter_dir / "audit.json"
    text_path = chapter_dir / audited_file
    if not audit_path.exists():
        raise FileNotFoundError("audit.json does not exist")
    if not text_path.exists():
        raise FileNotFoundError(f"{audited_file} does not exist")
    report = load_json_model(audit_path, AuditReport)
    content = text_path.read_text(encoding="utf-8")
    issues = []
    for issue in report.issues:
        matches = []
        for evidence in issue.evidence:
            quote = evidence.quote.strip()
            location = _locate_quote(content, quote)
            matches.append(
                {
                    "source": evidence.source,
                    "quote": quote,
                    "matched": location is not None,
                    **(location or {}),
                }
            )
        issues.append(
            {
                "id": issue.id,
                "severity": issue.severity,
                "type": issue.type,
                "description": issue.description,
                "suggested_fix": issue.suggested_fix,
                "matches": matches,
            }
        )
    return {
        "audit_path": _relative(root, audit_path),
        "audited_file": audited_file,
        "issues": issues,
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
            "has_chapter_memory": (child / "chapter_memory.json").exists(),
            "status": None,
            "title": None,
            "audit_status": None,
            "chapter_memory_stale": None,
        }
        _merge_plan_metadata(child / "plan.json", entry)
        _merge_polished_metadata(child / "polished.md", entry)
        if (child / "audit.json").exists():
            data = load_json(child / "audit.json")
            if isinstance(data, dict):
                entry["audit_status"] = data.get("overall_status")
        if (child / "chapter_memory.json").exists():
            try:
                memory = load_json_model(child / "chapter_memory.json", ChapterMemory)
                entry["chapter_memory_stale"] = bool(chapter_memory_freshness_warnings(root, memory))
            except Exception:
                entry["chapter_memory_stale"] = True
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
        "chapter_memory": "chapter_memory.json",
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


def _state_timeline_visual_summary(state: object, timeline: object, canon: dict[str, object]) -> dict[str, object]:
    character_names = _name_map(canon.get("characters"), "characters")
    location_names = _name_map(canon.get("locations"), "locations")
    item_names = _name_map(canon.get("items"), "items")
    characters = []
    items = []
    locations = []
    conflicts = []
    if isinstance(state, dict):
        for character in state.get("character_states", []):
            if not isinstance(character, dict):
                continue
            entity_id = str(character.get("entity_id") or "")
            characters.append(
                {
                    "id": entity_id,
                    "name": character_names.get(entity_id, entity_id),
                    "location_id": character.get("location_id"),
                    "location_name": location_names.get(str(character.get("location_id") or ""), character.get("location_id")),
                    "health": character.get("health"),
                    "possessions": character.get("possessions") or [],
                    "knowledge_count": len(character.get("knowledge", [])) if isinstance(character.get("knowledge"), list) else 0,
                }
            )
        possession_owner: dict[str, str] = {}
        for character in state.get("character_states", []):
            if not isinstance(character, dict):
                continue
            for item_id in character.get("possessions", []) if isinstance(character.get("possessions"), list) else []:
                if item_id in possession_owner and possession_owner[item_id] != character.get("entity_id"):
                    conflicts.append(f"item {item_id} appears in possessions of multiple characters")
                possession_owner[str(item_id)] = str(character.get("entity_id") or "")
        for item in state.get("item_states", []):
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id") or "")
            holder_id = str(item.get("holder_id") or "")
            location_id = str(item.get("location_id") or "")
            if holder_id and location_id:
                conflicts.append(f"item {entity_id} has both holder and location")
            if holder_id and possession_owner.get(entity_id) and possession_owner[entity_id] != holder_id:
                conflicts.append(f"item {entity_id} holder conflicts with character possessions")
            items.append(
                {
                    "id": entity_id,
                    "name": item_names.get(entity_id, entity_id),
                    "holder_id": holder_id or None,
                    "holder_name": character_names.get(holder_id, holder_id) if holder_id else None,
                    "location_id": location_id or None,
                    "location_name": location_names.get(location_id, location_id) if location_id else None,
                    "condition": item.get("condition"),
                }
            )
        for location in state.get("location_states", []):
            if not isinstance(location, dict):
                continue
            entity_id = str(location.get("entity_id") or "")
            locations.append(
                {
                    "id": entity_id,
                    "name": location_names.get(entity_id, entity_id),
                    "accessibility": location.get("accessibility"),
                    "condition": location.get("condition"),
                    "active_events": location.get("active_events") or [],
                }
            )
    events = []
    by_chapter: dict[str, list[dict[str, object]]] = {}
    edges = []
    if isinstance(timeline, dict):
        for event in timeline.get("events", []):
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "")
            narrative = event.get("narrative_position") if isinstance(event.get("narrative_position"), dict) else {}
            story = event.get("story_position") if isinstance(event.get("story_position"), dict) else {}
            entry = {
                "id": event_id,
                "chapter": narrative.get("chapter", event.get("chapter")),
                "scene": narrative.get("scene", event.get("scene")),
                "sequence": narrative.get("sequence"),
                "story_time": story.get("time_label", event.get("in_story_time")),
                "story_order": story.get("order"),
                "story_thread_id": story.get("thread_id"),
                "event_role": event.get("event_role"),
                "summary": event.get("summary"),
                "location_id": event.get("location_id"),
                "location_name": location_names.get(str(event.get("location_id") or ""), event.get("location_id")),
                "participant_ids": event.get("participant_ids") or [],
                "participant_names": [
                    character_names.get(str(item), str(item))
                    for item in event.get("participant_ids", [])
                    if isinstance(event.get("participant_ids"), list)
                ],
            }
            events.append(entry)
            by_chapter.setdefault(str(entry.get("chapter") or "?"), []).append(entry)
            for cause in event.get("causes", []) if isinstance(event.get("causes"), list) else []:
                edges.append({"from": cause, "to": event_id, "type": "cause"})
            for effect in event.get("effects", []) if isinstance(event.get("effects"), list) else []:
                edges.append({"from": event_id, "to": effect, "type": "effect"})
    return {
        "characters": characters,
        "items": items,
        "locations": locations,
        "timeline_by_chapter": by_chapter,
        "timeline_events": events,
        "timeline_edges": edges,
        "conflicts": conflicts,
    }


def _name_map(data: object, key: str) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    values = data.get(key)
    if not isinstance(values, list):
        return {}
    result: dict[str, str] = {}
    for item in values:
        if isinstance(item, dict) and item.get("id"):
            result[str(item["id"])] = str(item.get("name") or item["id"])
    return result


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
    env = load_project_env(path.parent.parent)
    return {
        "path": str(path),
        "exists": True,
        "content": _sanitize_config(data),
        "env": [{"name": name, "exists": bool(env.get(name))} for name in env_names],
    }


def _agent_config_warnings(path: Path) -> list[str]:
    if not path.exists():
        return ["config/agents.yaml does not exist"]
    try:
        config = AgentsConfig.model_validate(load_yaml(path))
    except Exception as exc:
        return [f"config/agents.yaml is invalid: {_safe_error(str(exc))}"]
    warnings: list[str] = []
    if config.default is None:
        warnings.append("default API config is missing; unconfigured agents cannot use provider config")
    elif config.default.provider.lower() == "mock":
        warnings.append("default provider uses mock; mock is intended for tests only")
    for name, item in sorted(config.agents.items()):
        if item.provider and item.provider.lower() == "mock":
            warnings.append(f"agent {name} uses mock provider; mock is intended for tests only")
    return warnings


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


def _locate_quote(content: str, quote: str) -> dict[str, int] | None:
    if not quote:
        return None
    index = content.find(quote)
    if index < 0:
        compact_content = _compact_text(content)
        compact_quote = _compact_text(quote)
        if not compact_quote:
            return None
        compact_index = compact_content.find(compact_quote)
        if compact_index < 0:
            return None
        return {"line": 1, "column": compact_index + 1, "start_offset": compact_index, "end_offset": compact_index + len(compact_quote)}
    line = content.count("\n", 0, index) + 1
    line_start = content.rfind("\n", 0, index) + 1
    return {
        "line": line,
        "column": index - line_start + 1,
        "start_offset": index,
        "end_offset": index + len(quote),
    }


def _compact_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value).lower()


def _is_allowed_chapter_version_name(file_name: str, target: str) -> bool:
    return bool(re.fullmatch(rf"{re.escape(target)}(?:\.v[0-9]+)?\.md", file_name))


def _next_version_path(chapter_dir: Path, target: str) -> Path:
    existing = [1]
    pattern = re.compile(rf"^{re.escape(target)}\.v([0-9]+)\.md$")
    for path in chapter_dir.glob(f"{target}.v*.md"):
        match = pattern.match(path.name)
        if match:
            existing.append(int(match.group(1)))
    return chapter_dir / f"{target}.v{max(existing) + 1}.md"


def _new_revision_id() -> str:
    return "revision_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _append_web_revision_log(path: Path, chapter_number: int, record: RevisionRecord) -> None:
    if path.exists():
        log = load_json_model(path, RevisionLog)
        if log.chapter_number != chapter_number:
            raise WebAPIError("invalid_revision_log", "revision_log chapter_number does not match", status=400)
    else:
        log = RevisionLog(chapter_number=chapter_number, revisions=[])
    updated = log.model_copy(update={"revisions": [*log.revisions, record]})
    backup_if_exists(path, reason="web_revision_log")
    atomic_write_model_json(path, updated)


def _is_archived_chapter(root: Path, chapter_number: int) -> bool:
    archive_dir = root / "memory" / "archive"
    if not archive_dir.exists():
        return False
    chapter_fragment = f"chapters/{chapter_number:03d}/"
    for manifest_path in archive_dir.glob("session_*/manifest.json"):
        try:
            data = load_json(manifest_path)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        entries = data.get("entries")
        if isinstance(entries, list) and any(chapter_fragment in str(item) for item in entries):
            return True
        if chapter_fragment in json.dumps(data, ensure_ascii=False):
            return True
    return False


def _clean_agent_config_patch(patch: dict[object, object]) -> dict[str, object]:
    allowed = {
        "provider",
        "model",
        "base_url_env",
        "api_key_env",
        "reasoning",
        "thinking",
        "max_context_tokens",
        "max_tokens",
        "temperature",
        "timeout_seconds",
        "max_retries",
    }
    cleaned: dict[str, object] = {}
    for key, value in patch.items():
        key_text = str(key)
        if key_text not in allowed:
            raise WebAPIError("invalid_provider_config_field", f"field is not editable: {key_text}", status=400)
        if key_text in {"api_key", "token", "secret"}:
            raise WebAPIError("unsafe_config_secret", "raw secret fields are not allowed", status=400)
        cleaned[key_text] = value
    return cleaned


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
    raw_value = data.get("chapter_number") or 0
    if not isinstance(raw_value, (int, str)):
        raise ValueError("chapter_number must be a positive integer")
    value = int(raw_value)
    if value < 1:
        raise ValueError("chapter_number must be a positive integer")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _memory_change_stage(value: object) -> MemoryChangeStage:
    text = _optional_string(value)
    if text in {"pre_creation", "outline_discussion", "content_review", "post_chapter", "unknown"}:
        return cast(MemoryChangeStage, text)
    return "unknown"


def _vector_context_mode(data: dict[str, object]) -> VectorContextMode:
    if bool(data.get("use_vector_context")):
        return "on"
    value = _optional_string(data.get("vector_context"))
    if value in {"auto", "on", "off"}:
        return cast(VectorContextMode, value)
    return "auto"


def _polish_mode(data: dict[str, object]) -> PolishMode | None:
    value = _optional_string(data.get("polish_mode"))
    if not value:
        return None
    normalized = value.replace("-", "_")
    if normalized in {"single_pass", "auto", "review_gate"}:
        return cast(PolishMode, normalized)
    return None


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    if not isinstance(value, (int, str)):
        raise ValueError(f"expected integer-compatible value, got {type(value).__name__}")
    return int(value)


def _optional_float(value: object, default: float) -> float:
    if value in (None, ""):
        return default
    if not isinstance(value, (int, float, str)):
        raise ValueError(f"expected float-compatible value, got {type(value).__name__}")
    return float(value)


def _provider_name(value: object) -> ProviderName:
    provider = str(value or "config")
    if provider not in {"config", "mock", "openai", "openai_compatible", "deepseek", "zai"}:
        raise ValueError(f"unsupported provider: {provider}")
    return cast(ProviderName, provider)


def _audit_focus(value: object) -> tuple[
    Literal["canon", "state", "timeline", "style", "plot", "character_voice", "premature_reveal"],
    ...,
]:
    allowed = {"canon", "state", "timeline", "style", "plot", "character_voice", "premature_reveal"}
    if not isinstance(value, list):
        return ()
    focus: list[Literal["canon", "state", "timeline", "style", "plot", "character_voice", "premature_reveal"]] = []
    for item in value:
        text = str(item)
        if text in allowed:
            focus.append(cast(Literal["canon", "state", "timeline", "style", "plot", "character_voice", "premature_reveal"], text))
    return tuple(focus)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _required_string(value: object, field_name: str) -> str:
    text = _optional_string(value)
    if text is None:
        raise WebAPIError("invalid_request", f"{field_name} is required", status=400)
    return text


def _configured_web_port(root: Path) -> int:
    project_path = root / "project.yaml"
    if not project_path.exists():
        return 8765
    try:
        data = load_yaml(project_path)
    except Exception:
        return 8765
    web = data.get("web") if isinstance(data, dict) else None
    if isinstance(web, dict):
        try:
            return int(web.get("default_port") or 8765)
        except (TypeError, ValueError):
            return 8765
    return 8765


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _default_canon_proposal_path(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return root / "runs" / f"canon_proposal_{stamp}.json"


def _request_id() -> str:
    return "web_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _runtime_summary() -> dict[str, object]:
    conda_env = os.environ.get("CONDA_DEFAULT_ENV")
    virtual_env = os.environ.get("VIRTUAL_ENV")
    prefix_name = Path(sys.prefix).name
    environment = conda_env or (Path(virtual_env).name if virtual_env else prefix_name)
    managed = bool(re.fullmatch(r"WriterYang_\d{6}(?:\d{2})?", environment or ""))
    source = "conda" if conda_env else ("venv" if virtual_env else "python")
    return {
        "python": sys.executable,
        "python_prefix": sys.prefix,
        "environment": environment,
        "environment_source": source,
        "version": __version__,
        "managed_install": managed,
        "warning": "" if managed else "当前 Web UI 可能不是从 WriterYang 专用环境启动的，建议使用安装脚本生成的 WriterYang_WebUI.command 启动。",
    }


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
