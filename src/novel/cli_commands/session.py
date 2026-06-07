from __future__ import annotations

# ruff: noqa: F401

import argparse
import getpass
import importlib.util
import json
import os
import re
import sys
import webbrowser
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import cast

from novel.core.auditing import (
    AuditError,
    ChapterAuditOptions,
    audit_chapter,
    load_audit_provider,
    read_audit_instruction,
)
from novel.core.canon import (
    CanonError,
    CanonSuggestOptions,
    apply_canon_proposal,
    format_canon_validation_report,
    load_canon_provider,
    suggest_canon,
)
from novel.core.chapter_memory import (
    ChapterMemoryError,
    ChapterMemoryOptions,
    accepted_chapter_numbers,
    chapter_memory_path,
    generate_chapter_memory,
    load_chapter_memory_provider,
)
from novel.core.env import load_project_env
from novel.core.drafting import (
    ChapterDraftingOptions,
    DraftingError,
    load_drafting_provider,
    read_drafting_instruction,
    write_chapter_draft,
)
from novel.core.inspiration import (
    InspirationError,
    InspirationOptions,
    load_inspiration_provider,
    read_inspiration_input,
    run_inspiration_agent,
)
from novel.core.planning import (
    ChapterPlanningOptions,
    PlanningError,
    load_planning_provider,
    plan_chapter,
    read_planning_instruction,
)
from novel.core.polishing import (
    ChapterPolishingOptions,
    PolishingError,
    load_polishing_provider,
    polish_chapter,
    read_polishing_instruction,
    resolve_edit_mode,
)
from novel.core.revision import (
    ChapterRevisionOptions,
    RevisionError,
    RevisionLoopOptions,
    load_revision_provider,
    read_revision_instruction,
    revise_chapter,
    revise_chapter_loop,
)
from novel.core.management import load_management_events
from novel.core.memory_repair import (
    MemoryRepairError,
    SettingChangeSuggestionResult,
    answer_setting_change_clarification,
    apply_memory_repair,
    suggest_memory_repair,
    suggest_setting_change_interactive,
)
from novel.core.search import SearchError, rebuild_search_index, refresh_search_index, search_index_status, search_project
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
from novel.core.state_update import (
    AcceptChapterOptions,
    StateUpdateApplyOptions,
    StateUpdateError,
    StateUpdateProposeOptions,
    accept_chapter,
    apply_state_update,
    load_state_update_provider,
    propose_state_update,
    read_state_update_instruction,
)
from novel.core.exporting import (
    DocxExportOptions,
    ExportError,
    MarkdownExportOptions,
    export_docx,
    export_markdown,
    parse_chapter_selector,
)
from novel.core.inspection import (
    ProjectReadError,
    format_characters,
    format_canon,
    format_state,
    format_status,
    format_timeline,
    get_project_status,
)
from novel.core.json_schema import export_json_schemas
from novel.core.io import load_json_model, load_yaml, load_yaml_model
from novel.core.locking import ProjectLock, ProjectLockError
from novel.core.migration import MigrationError, migrate_project
from novel.core.orchestrator import (
    OrchestratorError,
    OrchestratorOptions,
    decide_ask_intent,
    format_orchestrator_plan,
    handoff_rules_text,
    orchestrate,
)
from novel.core.provider_config import ProviderOverrides, describe_agent_provider, default_agent_config_path
from novel.core.security import scan_security
from novel.core.setup_guide import (
    SetupGuideError,
    configure_default_provider,
    configure_embedding_provider,
    configure_web_port,
    find_available_port,
    is_port_available,
)
from novel.core.schemas import (
    AgentsConfig,
    AuditReport,
    CreationSession,
    MemoryChangeStage,
    PolishMode,
    ProjectConfig,
    VectorContextMode,
)
from novel.core.usage import UsageError, summarize_provider_usage
from novel.core.workspace import InitOptions, WorkspaceExistsError, init_workspace
from novel.core.validation import validate_canon, validate_project
from novel.core.workflow import (
    GenerateChapterOptions,
    WorkflowError,
    generate_chapter,
    read_workflow_instruction,
)
from novel import __version__
from novel.cli_shared import (
    _add_agent_runtime_args,
    _add_search_context_args,
    _add_polish_mode_arg,
    _vector_context_mode_from_args,
    _polish_mode_from_arg,
    _audit_issue_lines,
    _management_event_payload,
    _management_event_lines,
    _severity_rank,
    _extract_chapter_from_text,
    _extract_repair_id,
    _resolve_memory_repair_proposal_arg,
    _print_dry_run_provider,
    _add_integration_args,
    _add_integration_args_recursive,
    _apply_project_alias,
    _wants_json,
    _quiet,
    _success,
    _failure,
    _print_json,
    _safe_message,
    _command_lock,
    _validation_payload,
    _status_payload,
    _format_usage_summary,
    _resolve_web_port,
    _should_run_init_guide,
    _run_init_setup_guide,
    _prompt_text,
    _prompt_yes_no,
    completion_script,
    run_doctor,
    format_doctor_result,
    _doctor_check,
    _doctor_env_checks,
    _doctor_agent_config_checks,
    _collect_env_names,
    _repo_root,
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
