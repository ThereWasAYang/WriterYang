from __future__ import annotations

import argparse
from pathlib import Path

from novel.core.session import (
    CreationSessionError,
    SessionActionOptions,
    SessionInstructionOptions,
    SessionResult,
    SessionRunOptions,
    SessionStartOptions,
    SessionRewriteControlOptions,
    accept_session,
    approve_outline,
    archive_session,
    load_rewrite_events,
    parse_range,
    retry_rewrite,
    revise_audit,
    revise_content,
    revise_outline,
    run_session,
    show_session,
    start_session,
    undo_rewrite,
)
from novel.core.io import load_json_model
from novel.core.locking import ProjectLockError
from novel.core.schemas import (
    AuditReport,
    CreationSession,
)
from novel.cli_shared import (
    _vector_context_mode_from_args,
    _polish_mode_from_arg,
    _management_event_payload,
    _management_event_lines,
    _success,
    _failure,
    _command_lock,
)

def _run_session_command(args: argparse.Namespace, root: Path) -> SessionResult:
    command = args.session_command
    if command == "start":
        chapters = _resolve_session_chapters(args)
        segments = parse_range(args.segments) if getattr(args, "segments", None) else None
        return start_session(
            SessionStartOptions(
                root=root,
                user_intent=args.intent,
                chapter_range=chapters,
                segment_range=segments,
                provider_name=args.provider,
                force=args.force,
                use_search_context=getattr(args, "use_search_context", True),
                use_vector_context=_vector_context_mode_from_args(args),
            )
        )
    if command == "show":
        return show_session(root, args.session_id)
    if command == "revise-outline":
        return revise_outline(
            SessionInstructionOptions(
                root=root,
                session_id=args.session_id,
                instruction=args.instruction,
                provider_name=args.provider,
                force=args.force,
                use_search_context=getattr(args, "use_search_context", True),
                use_vector_context=_vector_context_mode_from_args(args),
            )
        )
    if command == "approve-outline":
        return approve_outline(SessionActionOptions(root=root, session_id=args.session_id, force=args.force))
    if command == "run":
        return run_session(
            SessionRunOptions(
                root=root,
                session_id=args.session_id,
                provider_name=args.provider,
                force=args.force,
                max_auto_revision_rounds=args.max_auto_revision_rounds,
                use_search_context=getattr(args, "use_search_context", True),
                use_vector_context=_vector_context_mode_from_args(args),
                polish_mode=_polish_mode_from_arg(getattr(args, "polish_mode", None)),
            )
        )
    if command == "revise-content":
        return revise_content(
            SessionInstructionOptions(
                root=root,
                session_id=args.session_id,
                instruction=args.instruction,
                provider_name=args.provider,
                force=args.force,
                from_audit=args.from_audit,
                use_search_context=getattr(args, "use_search_context", True),
                use_vector_context=_vector_context_mode_from_args(args),
            )
        )
    if command == "revise-audit":
        return revise_audit(
            SessionRewriteControlOptions(
                root=root,
                session_id=args.session_id,
                event_id=args.event_id,
                instruction=args.instruction,
                provider_name=args.provider,
                force=args.force,
                use_search_context=getattr(args, "use_search_context", True),
                use_vector_context=_vector_context_mode_from_args(args),
                polish_mode=_polish_mode_from_arg(getattr(args, "polish_mode", None)),
            )
        )
    if command == "retry-rewrite":
        return retry_rewrite(
            SessionRewriteControlOptions(
                root=root,
                session_id=args.session_id,
                event_id=args.event_id,
                instruction=args.instruction,
                provider_name=args.provider,
                force=args.force,
                use_search_context=getattr(args, "use_search_context", True),
                use_vector_context=_vector_context_mode_from_args(args),
            )
        )
    if command == "undo-rewrite":
        return undo_rewrite(
            SessionRewriteControlOptions(
                root=root,
                session_id=args.session_id,
                event_id=args.event_id,
                provider_name=args.provider,
                use_search_context=getattr(args, "use_search_context", True),
                use_vector_context=_vector_context_mode_from_args(args),
            )
        )
    if command == "accept":
        return accept_session(
            SessionActionOptions(root=root, session_id=args.session_id, provider_name=args.provider, force=args.force)
        )
    if command == "archive":
        return archive_session(SessionActionOptions(root=root, session_id=args.session_id, force=args.force))
    raise CreationSessionError(f"unknown session command: {command}")

def _resolve_session_chapters(args: argparse.Namespace) -> tuple[int, ...]:
    chapters = getattr(args, "chapters", None)
    chapter = getattr(args, "chapter", None)
    if chapters and chapter:
        raise CreationSessionError("provide either --chapters or --chapter, not both")
    if chapters:
        return parse_range(chapters)
    if chapter:
        return (int(chapter),)
    raise CreationSessionError("provide --chapters or --chapter")

def _session_payload(command: str, result: SessionResult, root: Path) -> dict[str, object]:
    return {
        "command": f"session {command}",
        "session_id": result.session.session_id,
        "status": result.session.status,
        "outline_status": result.session.outline_status,
        "content_status": result.session.content_status,
        "chapter_range": result.session.chapter_range,
        "segment_range": result.session.segment_range,
        "approved_outline_path": result.session.approved_outline_path,
        "final_output_paths": result.session.final_output_paths,
        "archive_paths": result.session.archive_paths,
        "session_path": str(result.session_path),
        "message": result.message,
        "rewrite_events": _session_rewrite_payload(root, result.session),
        "revision_route": _session_latest_revision_route_payload(result.session),
        "management_events": _management_event_payload(root),
    }

def _session_low_issue_lines(root: Path, audit_history: list[str]) -> list[str]:
    low_issues: list[str] = []
    for audit_path_text in audit_history:
        audit_path = root / audit_path_text
        if not audit_path.exists():
            continue
        try:
            report = load_json_model(audit_path, AuditReport)
        except Exception:
            continue
        for issue in report.issues:
            if issue.severity != "low":
                continue
            low_issues.append(
                f"- [{issue.severity}/{issue.type}] {issue.id}: {issue.description}"
                + (f" suggested_fix: {issue.suggested_fix}" if issue.suggested_fix else "")
            )
    if not low_issues:
        return []
    return [
        "Low audit issues for user review:",
        *low_issues,
        "Choose: accept as-is, or run session revise-content <session_id> --from-audit to create a revised version.",
    ]

def _session_rewrite_payload(root: Path, session: CreationSession) -> list[dict[str, object]]:
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
            "blocking_issues": [issue.model_dump(mode="json") for issue in event.blocking_issues],
        }
        for event in load_rewrite_events(root, session.session_id)
    ]

def _session_rewrite_lines(root: Path, session: CreationSession) -> list[str]:
    events = load_rewrite_events(root, session.session_id)
    if not events:
        return []
    lines = ["Automatic audit rewrite events:"]
    for event in events:
        action_label = "修正文" if event.action == "revision_rewrite" else "重写大纲"
        lines.append(
            f"- Chapter {event.chapter_number}, round {event.round_number}: "
            f"{action_label}, status={event.status}"
        )
        if event.rejected_text_snapshot_path:
            lines.append(f"  rejected_text: {event.rejected_text_snapshot_path}")
        if event.undo_status != "not_requested":
            lines.append(f"  undo_status: {event.undo_status}")
        if event.audit_revision_history:
            lines.append(f"  audit_revisions: {len(event.audit_revision_history)}")
        for issue in event.blocking_issues:
            lines.append(f"  reason [{issue.severity}/{issue.type}] {issue.id}: {issue.description}")
            if issue.suggested_fix:
                lines.append(f"  suggested_fix: {issue.suggested_fix}")
    return lines

def _session_latest_revision_route_payload(session: CreationSession) -> dict[str, object] | None:
    if not session.revision_route_history:
        return None
    return session.revision_route_history[-1].model_dump(mode="json")

def _session_revision_route_lines(session: CreationSession) -> list[str]:
    record = _session_latest_revision_route_payload(session)
    if not record:
        return []
    decision = record.get("decision", {}) if isinstance(record, dict) else {}
    if not isinstance(decision, dict) or not decision.get("route"):
        return []
    return [
        "Revision route:",
        f"- route: {decision.get('route')}",
        f"- reason: {decision.get('reason')}",
        f"- risk: {decision.get('risk_level')}",
    ]

def _cmd_session(args: argparse.Namespace) -> int:
    try:
        root = Path(args.path)
        with _command_lock(args, root, f"session {args.session_command}"):
            result = _run_session_command(args, root)
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except CreationSessionError as exc:
        return _failure(args, str(exc), error_type="session_error")
    payload = _session_payload(args.session_command, result, root)
    lines = [
        f"Session: {result.session.session_id}",
        result.message,
        f"Status: {result.session.status}",
        f"Session file: {result.session_path}",
        *_session_revision_route_lines(result.session),
        *_session_rewrite_lines(root, result.session),
        *_management_event_lines(root),
        *_session_low_issue_lines(root, result.session.audit_history),
    ]
    return _success(args, payload, lines)
