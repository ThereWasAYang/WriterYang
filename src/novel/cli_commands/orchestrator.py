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

def _cmd_ask(args: argparse.Namespace) -> int:
    try:
        with _command_lock(args, Path(args.path), "ask", enabled=not args.dry_run):
            if args.max_steps < 1:
                raise OrchestratorError("max_steps must be at least 1")
            if args.max_agent_calls < 1:
                raise OrchestratorError("max_agent_calls must be at least 1")
            if args.dry_run:
                result = orchestrate(
                    OrchestratorOptions(
                        root=Path(args.path),
                        request=args.request,
                        provider_name=args.provider,
                        dry_run=True,
                        force=args.force,
                        max_steps=args.max_steps,
                        max_retries=args.max_retries,
                        max_agent_calls=args.max_agent_calls,
                        use_search_context=args.use_search_context,
                        use_vector_context=_vector_context_mode_from_args(args),
                    )
                )
                dry_run_payload: dict[str, object] = {
                    "command": "ask",
                    "task": result.plan.task,
                    "chapter_number": result.plan.chapter_number,
                    "message": result.message,
                    "run_log_path": str(result.run_log_path) if result.run_log_path else None,
                    "handoff_trace": [entry.as_dict() for entry in result.plan.handoff_trace],
                    "revision_route": result.plan.revision_route.model_dump(mode="json")
                    if result.plan.revision_route
                    else None,
                }
                if _wants_json(args):
                    _print_json({"ok": True, **dry_run_payload})
                    return 0
                if not _quiet(args):
                    if args.show_handoff_rules:
                        print(handoff_rules_text())
                        print("")
                    print(format_orchestrator_plan(result.plan))
                    print(result.message)
                return 0
            intent = decide_ask_intent(Path(args.path), args.request, provider_name=args.provider)
            if intent.task == "memory_repair_apply":
                if intent.source != "model" or not intent.repair_id:
                    raise OrchestratorError("memory repair apply requires a structured model decision; use novel memory-repair apply <repair_id>")
                apply_result = apply_memory_repair(
                    Path(args.path),
                    Path("memory") / "repairs" / intent.repair_id / "proposal.json",
                )
                apply_payload: dict[str, object] = {
                    "command": "ask",
                    "task": "memory_repair_apply",
                    "ask_intent": intent.model_dump(mode="json"),
                    "repair_id": apply_result.proposal.repair_id,
                    "apply_log_path": str(apply_result.apply_log_path),
                    "status": apply_result.apply_log.status,
                    "management_events": _management_event_payload(Path(args.path)),
                }
                return _success(
                    args,
                    apply_payload,
                    [
                        f"Applied memory repair: {apply_result.proposal.repair_id}",
                        f"Apply log: {apply_result.apply_log_path}",
                        *_management_event_lines(Path(args.path)),
                    ],
                )
            if intent.task == "memory_repair_suggest":
                repair_result = suggest_memory_repair(Path(args.path), args.request, provider_name=args.provider)
                repair_payload: dict[str, object] = {
                    "command": "ask",
                    "task": "memory_repair_suggest",
                    "ask_intent": intent.model_dump(mode="json"),
                    "repair_id": repair_result.proposal.repair_id,
                    "proposal_path": str(repair_result.proposal_path),
                    "markdown_path": str(repair_result.markdown_path),
                    "target_files": repair_result.proposal.target_files,
                    "operation_count": len(repair_result.proposal.operations),
                    "management_events": _management_event_payload(Path(args.path)),
                }
                return _success(
                    args,
                    repair_payload,
                    [
                        f"Memory repair proposal: {repair_result.proposal_path}",
                        f"Targets: {', '.join(repair_result.proposal.target_files)}",
                        *_management_event_lines(Path(args.path)),
                    ],
                )
            if intent.task == "export":
                export_result = export_markdown(MarkdownExportOptions(root=Path(args.path), force=args.force))
                export_payload: dict[str, object] = {
                    "command": "ask",
                    "task": "export",
                    "ask_intent": intent.model_dump(mode="json"),
                    "output_path": str(export_result.output_path),
                    "manifest_path": str(export_result.manifest_path),
                }
                return _success(args, export_payload, [f"Exported Markdown: {export_result.output_path}"])
            if intent.task in {"status", "show"}:
                status = get_project_status(Path(args.path))
                status_payload: dict[str, object] = {
                    "command": "ask",
                    "task": intent.task,
                    "ask_intent": intent.model_dump(mode="json"),
                    "status": _status_payload(status),
                }
                return _success(args, status_payload, format_status(status, Path(args.path)).splitlines())
            if intent.task == "unknown":
                raise OrchestratorError(intent.user_message or intent.reason)
            fallback_chapter = _extract_chapter_from_text(args.request)
            chapter_numbers = tuple(intent.chapter_range or ([fallback_chapter] if fallback_chapter else []) or [1])
            session_result = start_session(
                SessionStartOptions(
                    root=Path(args.path),
                    user_intent=args.request,
                    chapter_range=chapter_numbers,
                    provider_name=args.provider,
                    force=args.force,
                    use_search_context=args.use_search_context,
                    use_vector_context=_vector_context_mode_from_args(args),
                )
            )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except MemoryRepairError as exc:
        return _failure(args, str(exc), error_type="memory_repair_error")
    except (OrchestratorError, CreationSessionError) as exc:
        return _failure(args, str(exc), error_type="orchestrator_error")
    session_payload: dict[str, object] = {
        "command": "ask",
        "task": "creation_session",
        "ask_intent": intent.model_dump(mode="json"),
        "chapter_number": session_result.session.chapter_range[0],
        "chapter_range": session_result.session.chapter_range,
        "session_id": session_result.session.session_id,
        "message": session_result.message,
        "session_path": str(session_result.session_path),
        "status": session_result.session.status,
    }
    if _wants_json(args):
        _print_json({"ok": True, **session_payload})
        return 0
    if _quiet(args):
        return 0
    print(f"Session: {session_result.session.session_id}")
    print(session_result.message)
    print(f"Session file: {session_result.session_path}")
    print("Next: review outline_proposal.md, then run novel session approve-outline <session_id>")
    return 0
