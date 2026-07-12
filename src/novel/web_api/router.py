from __future__ import annotations

from .deps import (
    json,
    Path,
    traceback,
    parse_qs,
    log_app_warning,
    CanonError,
    format_canon,
    load_json,
    ProjectLock,
    ProjectLockError,
    MemoryRepairError,
    SearchError,
    search_index_status,
    SetupGuideError,
    new_request_id,
    web_launcher,
    CreationSessionError,
    ProviderContextLimitError,
    summarize_provider_usage,
    WorkspaceExistsError,
)
from novel.core.revision_workflow import RevisionWorkflowError
from novel.core.previewing import PreviewError
from novel.core.command_bus import DomainError

from .common import (
    APIResponse,
    WebPostHandler,
    RootResolver,
    PostRoute,
    WebErrorPayload,
    WebResponsePayload,
    WebAPIError,
    _json_body,
    _root_from_query,
    _root_from_body,
    _init_project_root_from_body,
    _optional_int,
    _runtime_summary,
    _safe_error,
)

from .generation import (
    _chapter_memory_generate, _chapter_memory_rebuild, _export_docx, _export_markdown,
)
from .config import (
    _generate_style_guide, _index_refresh, _init_project, _save_chapter_file, _save_provider_config, _save_style_guide,
    _setup_default_provider, _setup_embedding, _setup_open_web, _setup_recommend_port, _setup_web_port,
    _style_guide,
)
from .memory import (
    _canon_apply, _canon_applied_proposals, _canon_suggest, _inspire, _settings_change_answer,
    _settings_change_apply, _settings_change_suggest,
)
from .session import (
    _session_accept, _session_api, _session_approve_outline, _session_archive, _session_cancel,
    _session_latest_api, _session_progress_api, _session_retry_rewrite, _session_revise_audit,
    _session_revise_content, _session_revise_outline, _session_rewrite_events_api, _session_run,
    _session_start, _session_undo_rewrite, _project_status_api, _search_api, _validate_project_api,
)
from .inspection import (
    _audit_annotations, _file_tree, _list_chapters, _list_projects, _management_events,
    _provider_config_summary, _read_chapter_file, _read_workspace_file,
    _runs_summary, _state_timeline_summary, _workspace_diff,
)
from .revision_session import (
    _revision_accept,
    _revision_blocks_api,
    _revision_run,
    _revision_show_api,
    _revision_start,
)
from .preview import _preview_package

def handle_api_request(
    method: str,
    path: str,
    query_string: str = "",
    body: bytes | str | None = None,
) -> APIResponse:
    request_id = new_request_id("web")
    query = {key: values[-1] for key, values in parse_qs(query_string).items()}
    data_for_log: dict[str, object] | None = None
    root_resolver_for_log: RootResolver | None = None

    def log_failure(status: int, code: str, error: Exception) -> None:
        _log_web_api_failure(
            path,
            query,
            data_for_log,
            request_id=request_id,
            status=status,
            code=code,
            error=error,
            root_resolver=root_resolver_for_log,
        )

    try:
        if method == "GET":
            handler = _get_routes().get(path)
            if handler:
                return _success(handler(query))
        elif method == "POST":
            data = _json_body(body)
            data_for_log = data
            route = _post_routes().get(path)
            if route:
                task, handler, locked, root_resolver = _post_route_parts(route)
                root_resolver_for_log = root_resolver
                return _success(_locked_write(data, task, handler, root_resolver) if locked else handler(data))
    except WebAPIError as exc:
        log_failure(exc.status, exc.code, exc)
        return _failure(exc.status, exc.code, str(exc), request_id=request_id, details=exc.details)
    except DomainError as exc:
        if exc.code in {
            "archived_content_read_only",
            "confirmation_required",
            "port_unavailable",
            "project_locked",
            "workspace_exists",
        }:
            status = 409
        elif exc.code == "forbidden_file":
            status = 403
        else:
            status = 400
        log_failure(status, exc.code, exc)
        return _failure(status, exc.code, exc.message, request_id=request_id, details=exc.details)
    except ProjectLockError as exc:
        log_failure(409, "project_locked", exc)
        return _failure(409, "project_locked", str(exc), request_id=request_id)
    except WorkspaceExistsError as exc:
        log_failure(409, "workspace_exists", exc)
        return _failure(409, "workspace_exists", str(exc), request_id=request_id)
    except FileNotFoundError as exc:
        log_failure(404, "file_not_found", exc)
        return _failure(404, "file_not_found", str(exc), request_id=request_id)
    except PermissionError as exc:
        log_failure(403, "forbidden_file", exc)
        return _failure(403, "forbidden_file", str(exc), request_id=request_id)
    except json.JSONDecodeError as exc:
        log_failure(400, "invalid_json", exc)
        return _failure(400, "invalid_json", "request body must be valid JSON", request_id=request_id)
    except CanonError as exc:
        log_failure(400, "canon_error", exc)
        return _failure(400, "canon_error", str(exc), request_id=request_id)
    except MemoryRepairError as exc:
        log_failure(400, "memory_repair_error", exc)
        return _failure(400, "memory_repair_error", str(exc), request_id=request_id)
    except web_launcher.PortUnavailableError as exc:
        log_failure(409, "port_unavailable", exc)
        return _failure(409, "port_unavailable", str(exc), request_id=request_id)
    except SetupGuideError as exc:
        log_failure(400, "setup_guide_error", exc)
        return _failure(400, "setup_guide_error", str(exc), request_id=request_id)
    except ProviderContextLimitError as exc:
        log_failure(400, "provider_context_limit_exceeded", exc)
        return _failure(400, "provider_context_limit_exceeded", str(exc), request_id=request_id)
    except SearchError as exc:
        log_failure(400, "search_error", exc)
        return _failure(400, "search_error", str(exc), request_id=request_id)
    except CreationSessionError as exc:
        log_failure(400, "session_error", exc)
        return _failure(400, "session_error", str(exc), request_id=request_id)
    except RevisionWorkflowError as exc:
        log_failure(400, "revision_error", exc)
        return _failure(400, "revision_error", str(exc), request_id=request_id)
    except PreviewError as exc:
        log_failure(400, "preview_error", exc)
        return _failure(400, "preview_error", str(exc), request_id=request_id)
    except ValueError as exc:
        log_failure(400, "invalid_request", exc)
        return _failure(400, "invalid_request", str(exc), request_id=request_id)
    except Exception as exc:
        log_failure(400, "operation_failed", exc)
        return _failure(400, "operation_failed", str(exc), request_id=request_id)
    return _failure(404, "not_found", "not found", request_id=request_id)


def _get_routes():
    return {
        "/api/runtime": lambda query: {"runtime": _runtime_summary()},
        "/api/projects": lambda query: {"projects": _list_projects(Path(query.get("root", ".")))},
        "/api/project/status": _project_status_api,
        "/api/validate": _validate_project_api,
        "/api/canon": lambda query: {"summary": format_canon(_root_from_query(query))},
        "/api/canon/applied-proposals": _canon_applied_proposals,
        "/api/chapters": lambda query: {"chapters": _list_chapters(_root_from_query(query))},
        "/api/chapter-file": lambda query: _read_chapter_file(_root_from_query(query), query),
        "/api/file-tree": lambda query: {"files": _file_tree(_root_from_query(query))},
        "/api/read-file": lambda query: _read_workspace_file(_root_from_query(query), query.get("file") or ""),
        "/api/style-guide": lambda query: _style_guide(_root_from_query(query)),
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
        "/api/session/latest": _session_latest_api,
        "/api/session/progress": _session_progress_api,
        "/api/session/rewrite-events": _session_rewrite_events_api,
        "/api/revision-session/blocks": _revision_blocks_api,
        "/api/revision-session": _revision_show_api,
        "/api/diff": lambda query: _workspace_diff(
            _root_from_query(query),
            query.get("left") or "",
            query.get("right") or "",
        ),
    }


def _log_web_api_failure(
    path: str,
    query: dict[str, str],
    data: dict[str, object] | None,
    *,
    request_id: str,
    status: int,
    code: str,
    error: Exception,
    root_resolver: RootResolver | None = None,
) -> None:
    root = _root_for_logging(query, data, root_resolver=root_resolver)
    log_app_warning(
        root,
        "web_api_failure",
        request_id=request_id,
        endpoint=path,
        status=status,
        code=code,
        error_type=error.__class__.__name__,
        error=str(error),
        traceback=traceback.format_exc() if code == "operation_failed" else None,
    )


def _root_for_logging(
    query: dict[str, str],
    data: dict[str, object] | None,
    *,
    root_resolver: RootResolver | None = None,
) -> Path:
    if data and data.get("path") is not None:
        if root_resolver:
            try:
                return root_resolver(data)
            except Exception:
                pass
        return Path(str(data.get("path") or ".")).expanduser().resolve()
    return Path(query.get("path") or ".").expanduser().resolve()


def _post_routes() -> dict[str, PostRoute]:
    return {
        "/api/export/markdown": ("web export markdown", _export_markdown, False),
        "/api/export/docx": ("web export docx", _export_docx, False),
        "/api/preview/package": ("web preview package", _preview_package, False),
        "/api/save-chapter-file": ("web save chapter file", _save_chapter_file, False),
        "/api/style-guide": ("web style guide save", _save_style_guide, False),
        "/api/style-guide/generate": ("web style guide generate", _generate_style_guide, False),
        "/api/provider-config": ("web provider config", _save_provider_config, False),
        "/api/index/refresh": ("web index refresh", _index_refresh, False),
        "/api/init-project": ("web init project", _init_project, True, _init_project_root_from_body),
        "/api/setup/default-provider": ("web setup default provider", _setup_default_provider, False),
        "/api/setup/embedding": ("web setup embedding", _setup_embedding, False),
        "/api/setup/web-port": ("web setup web port", _setup_web_port, False),
        "/api/setup/open-web": ("web setup open web", _setup_open_web, False),
        "/api/inspire": ("web inspire", _inspire, False),
        "/api/canon/suggest": ("web canon suggest", _canon_suggest, False),
        "/api/canon/apply": ("web canon apply", _canon_apply, False),
        "/api/settings/change/suggest": ("web setting change suggest", _settings_change_suggest, False),
        "/api/settings/change/answer": ("web setting change answer", _settings_change_answer, False),
        "/api/settings/change/apply": ("web setting change apply", _settings_change_apply, False),
        "/api/chapter-memory/generate": ("web chapter memory generate", _chapter_memory_generate, False),
        "/api/chapter-memory/rebuild": ("web chapter memory rebuild", _chapter_memory_rebuild, False),
        "/api/session/start": ("web session start", _session_start, False),
        "/api/session/revise-outline": ("web session revise-outline", _session_revise_outline, False),
        "/api/session/approve-outline": ("web session approve-outline", _session_approve_outline, False),
        "/api/session/run": ("web session run", _session_run, False),
        "/api/session/cancel": ("web session cancel", _session_cancel, False),
        "/api/session/revise-content": ("web session revise-content", _session_revise_content, False),
        "/api/session/revise-audit": ("web session revise-audit", _session_revise_audit, False),
        "/api/session/retry-rewrite": ("web session retry-rewrite", _session_retry_rewrite, False),
        "/api/session/undo-rewrite": ("web session undo-rewrite", _session_undo_rewrite, False),
        "/api/session/accept": ("web session accept", _session_accept, False),
        "/api/session/archive": ("web session archive", _session_archive, False),
        "/api/revision-session/start": ("web revision-session start", _revision_start, False),
        "/api/revision-session/run": ("web revision-session run", _revision_run, False),
        "/api/revision-session/accept": ("web revision-session accept", _revision_accept, False),
    }


def _post_route_parts(route: PostRoute) -> tuple[str, WebPostHandler, bool, RootResolver]:
    if len(route) == 3:
        task, handler, locked = route
        return task, handler, locked, _root_from_body
    task, handler, locked, root_resolver = route
    return task, handler, locked, root_resolver


def _locked_write(
    data: dict[str, object],
    task: str,
    handler: WebPostHandler,
    root_resolver: RootResolver | None = None,
) -> dict[str, object]:
    resolver = root_resolver or _root_from_body
    root = resolver(data)
    with ProjectLock(root, task=task):
        usage_marker = _provider_call_log_line_count(root)
        result = handler(data)
        _attach_api_call_usage(root, result, usage_marker)
        return result


def _provider_call_log_line_count(root: Path) -> int:
    path = root / "runs" / "provider_calls.jsonl"
    if not path.exists():
        return 0
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


def _attach_api_call_usage(root: Path, data: dict[str, object], start_line: int) -> None:
    usage = _api_call_usage_since(root, start_line)
    if usage["call_count"]:
        data["api_call_usage"] = usage


def _api_call_usage_since(root: Path, start_line: int) -> dict[str, int]:
    path = root / "runs" / "provider_calls.jsonl"
    usage = {
        "call_count": 0,
        "messages_char_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "unknown_token_call_count": 0,
    }
    if not path.exists():
        return usage
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[max(start_line, 0):]
    except OSError:
        return usage
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        usage["call_count"] += 1
        usage["messages_char_count"] += _model_io_messages_char_count(root, entry.get("model_io_path"))
        prompt = _optional_int(entry.get("prompt_tokens"))
        completion = _optional_int(entry.get("completion_tokens"))
        total = _optional_int(entry.get("total_tokens"))
        if prompt is None and completion is None and total is None:
            usage["unknown_token_call_count"] += 1
            continue
        usage["prompt_tokens"] += prompt or 0
        usage["completion_tokens"] += completion or 0
        usage["total_tokens"] += total if total is not None else (prompt or 0) + (completion or 0)
    return usage


def _model_io_messages_char_count(root: Path, rel_path: object) -> int:
    if not isinstance(rel_path, str) or not rel_path.startswith("runs/model_io/") or not rel_path.endswith(".json"):
        return 0
    path = (root / rel_path).resolve()
    try:
        path.relative_to((root / "runs" / "model_io").resolve())
    except ValueError:
        return 0
    try:
        data = load_json(path)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    request_data = data.get("request")
    if not isinstance(request_data, dict):
        return 0
    payload = request_data.get("payload")
    if not isinstance(payload, dict):
        return 0
    return _messages_char_count(payload.get("messages"))


def _messages_char_count(messages: object) -> int:
    if not isinstance(messages, list):
        return 0
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
        elif content is not None:
            total += len(json.dumps(content, ensure_ascii=False, sort_keys=True))
    return total


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
