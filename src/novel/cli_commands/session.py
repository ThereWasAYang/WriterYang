from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from novel.core.audit_localization import (
    localize_audit_issue_for_author,
    localize_session_rewrite_issue_for_author,
)
from novel.core.session import (
    load_rewrite_events,
    parse_range,
)
from novel.core.command_bus import DomainError
from novel.core.contracts import SessionCommand, SessionStartCommand
from novel.core.contracts.commands import SessionCommandType
from novel.core.io import load_json_model
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
    _dispatch_cli_command,
)

def _run_session_command(args: argparse.Namespace, root: Path) -> dict[str, object]:
    command = args.session_command
    if command == "start":
        return _dispatch_cli_command(
            args,
            root,
            SessionStartCommand(
                user_intent=args.intent,
                chapter_range=list(parse_range(args.chapters)),
                provider_name=args.provider,
                force=args.force,
                use_search_context=getattr(args, "use_search_context", True),
                use_vector_context=_vector_context_mode_from_args(args),
            ),
        )
    command_type = cast(SessionCommandType, f"session.{command.replace('-', '_')}")
    return _dispatch_cli_command(
        args,
        root,
        SessionCommand(
            type=command_type,
            session_id=args.session_id,
            instruction=getattr(args, "instruction", None),
            event_id=getattr(args, "event_id", None),
            provider_name=getattr(args, "provider", "config"),
            force=bool(getattr(args, "force", False)),
            from_audit=bool(getattr(args, "from_audit", False)),
            max_auto_revision_rounds=getattr(args, "max_auto_revision_rounds", None),
            use_search_context=getattr(args, "use_search_context", True),
            use_vector_context=_vector_context_mode_from_args(args),
            polish_mode=_polish_mode_from_arg(getattr(args, "polish_mode", None)),
        ),
        confirmed=command in {"accept", "archive"},
    )

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
            localized = localize_audit_issue_for_author(issue)
            low_issues.append(
                f"- [{localized.severity}/{localized.type}] {localized.id}: {localized.description}"
                + (f" 建议修复：{localized.suggested_fix}" if localized.suggested_fix else "")
            )
    if not low_issues:
        return []
    return [
        "低级别 Audit 问题供用户确认：",
        *low_issues,
        "可选择直接接受，或运行 session revise-content <session_id> --from-audit 生成修订版。",
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
            localized = localize_session_rewrite_issue_for_author(issue)
            lines.append(f"  reason [{localized.severity}/{localized.type}] {localized.id}: {localized.description}")
            if localized.suggested_fix:
                lines.append(f"  建议修复：{localized.suggested_fix}")
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
        root = Path(args.path).expanduser().resolve()
        payload = _run_session_command(args, root)
        session_data = payload.get("session")
        if not isinstance(session_data, dict):
            raise DomainError("internal_error", "command result is missing session")
        session = CreationSession.model_validate(session_data)
    except DomainError as exc:
        return _failure(args, exc.message, error_type=exc.code)
    payload.update(
        {
            "command": f"session {args.session_command}",
            "session_id": session.session_id,
            "status": session.status,
            "outline_status": session.outline_status,
            "content_status": session.content_status,
            "chapter_range": session.chapter_range,
            "approved_outline_path": session.approved_outline_path,
            "final_output_paths": session.final_output_paths,
            "archive_paths": session.archive_paths,
            "rewrite_events": _session_rewrite_payload(root, session),
            "revision_route": _session_latest_revision_route_payload(session),
            "management_events": _management_event_payload(root),
        }
    )
    lines = [
        f"Session: {session.session_id}",
        str(payload.get("message") or ""),
        f"Status: {session.status}",
        f"Session file: {payload['session_path']}",
        *_session_revision_route_lines(session),
        *_session_rewrite_lines(root, session),
        *_management_event_lines(root),
        *_session_low_issue_lines(root, session.audit_history),
    ]
    return _success(args, payload, lines)
