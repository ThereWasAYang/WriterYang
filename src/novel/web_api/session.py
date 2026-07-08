from __future__ import annotations

from .deps import (
    asdict,
    Path,
    localize_audit_issue_for_author,
    localize_session_rewrite_issue_for_author,
    get_project_status,
    load_json_model,
    search_project,
    AuditReport,
    CreationSession,
    SessionProgress,
    SessionActionOptions,
    SessionInstructionOptions,
    SessionRunOptions,
    SessionStartOptions,
    SessionRewriteControlOptions,
    accept_session,
    approve_outline,
    archive_session,
    find_latest_active_session,
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
    ValidationMessage,
    validate_project,
)

from .common import (
    WebAPIError,
    _require_workspace,
    _root_from_query,
    _root_from_body,
    _optional_string,
    _vector_context_mode,
    _polish_mode,
    _optional_int,
    _truthy,
    _required_string,
    _relative,
    _safe_error,
)

from .inspection import _management_event_summary

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
    root = _root_from_body(data)
    session_id = _required_string(data.get("session_id"), "session_id")
    result = revise_outline(
        SessionInstructionOptions(
            root=root,
            session_id=session_id,
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
    root = _root_from_body(data)
    session_id = _required_string(data.get("session_id"), "session_id")
    result = approve_outline(
        SessionActionOptions(
            root=root,
            session_id=session_id,
            force=bool(data.get("force")),
        )
    )
    return _session_result_payload(result)


def _session_run(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    session_id = _required_string(data.get("session_id"), "session_id")
    result = run_session(
        SessionRunOptions(
            root=root,
            session_id=session_id,
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
    root = _root_from_body(data)
    session_id = _required_string(data.get("session_id"), "session_id")
    result = revise_content(
        SessionInstructionOptions(
            root=root,
            session_id=session_id,
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
    root = _root_from_body(data)
    session_id = _required_string(data.get("session_id"), "session_id")
    event_id = _required_string(data.get("event_id"), "event_id")
    result = revise_audit(
        SessionRewriteControlOptions(
            root=root,
            session_id=session_id,
            event_id=event_id,
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
    root = _root_from_body(data)
    session_id = _required_string(data.get("session_id"), "session_id")
    event_id = _required_string(data.get("event_id"), "event_id")
    result = retry_rewrite(
        SessionRewriteControlOptions(
            root=root,
            session_id=session_id,
            event_id=event_id,
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
    root = _root_from_body(data)
    session_id = _required_string(data.get("session_id"), "session_id")
    event_id = _required_string(data.get("event_id"), "event_id")
    result = undo_rewrite(
        SessionRewriteControlOptions(
            root=root,
            session_id=session_id,
            event_id=event_id,
            provider_name=str(data.get("provider") or "config"),
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
            polish_mode=_polish_mode(data),
        )
    )
    return _session_result_payload(result)


def _session_accept(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    session_id = _required_string(data.get("session_id"), "session_id")
    result = accept_session(
        SessionActionOptions(
            root=root,
            session_id=session_id,
            provider_name=str(data.get("provider") or "config"),
            force=bool(data.get("force")),
        )
    )
    return _session_result_payload(result)


def _session_archive(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    session_id = _required_string(data.get("session_id"), "session_id")
    result = archive_session(
        SessionActionOptions(
            root=root,
            session_id=session_id,
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


def _session_api(query: dict[str, str]) -> dict[str, object]:
    root = _root_from_query(query)
    session_id = _required_string(query.get("session_id"), "session_id")
    session = load_session(root, session_id)
    return {
        "session": session.model_dump(mode="json"),
        "progress": _session_progress_payload(load_session_progress(root, session.session_id)),
        "audit_summary": _session_audit_summary(root, session),
        "rewrite_events": _session_rewrite_event_summary(root, session),
        "management_events": _management_event_summary(root),
    }


def _session_latest_api(query: dict[str, str]) -> dict[str, object]:
    root = _root_from_query(query)
    prefer_generated = query.get("prefer_generated") is None or _truthy(query.get("prefer_generated"))
    result = find_latest_active_session(root, prefer_generated=prefer_generated)
    if result is None:
        return {
            "session": None,
            "progress": None,
            "audit_summary": [],
            "rewrite_events": [],
            "management_events": _management_event_summary(root),
            "message": "No active session found.",
        }
    return _session_result_payload(result)


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
    session_id = _required_string(query.get("session_id"), "session_id")
    session = load_session(root, session_id)
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
        issues = []
        for issue in report.issues:
            localized = localize_audit_issue_for_author(issue)
            issues.append(
                {
                    "id": localized.id,
                    "severity": localized.severity,
                    "type": localized.type,
                    "description": localized.description,
                    "suggested_fix": localized.suggested_fix,
                }
            )
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
                    "id": localized.id,
                    "severity": localized.severity,
                    "type": localized.type,
                    "description": localized.description,
                    "evidence": [evidence.model_dump(mode="json") for evidence in localized.evidence],
                    "suggested_fix": localized.suggested_fix,
                }
                for localized in (localize_session_rewrite_issue_for_author(issue) for issue in event.blocking_issues)
            ],
        }
        for event in events
    ]
