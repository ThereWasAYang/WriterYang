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

def _cmd_memory_repair(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        with _command_lock(args, root, f"memory-repair {args.memory_repair_command}"):
            if args.memory_repair_command == "suggest":
                repair_result = suggest_memory_repair(root, args.request, provider_name=args.provider)
                repair_payload: dict[str, object] = {
                    "command": "memory-repair suggest",
                    "repair_id": repair_result.proposal.repair_id,
                    "proposal_path": str(repair_result.proposal_path),
                    "markdown_path": str(repair_result.markdown_path),
                    "target_files": repair_result.proposal.target_files,
                    "operation_count": len(repair_result.proposal.operations),
                    "confidence": repair_result.proposal.confidence,
                    "management_events": _management_event_payload(root),
                }
                return _success(
                    args,
                    repair_payload,
                    [
                        f"Memory repair proposal: {repair_result.proposal_path}",
                        f"Targets: {', '.join(repair_result.proposal.target_files) or 'none'}",
                        f"Operations: {len(repair_result.proposal.operations)}",
                        *_management_event_lines(root),
                    ],
                )
            apply_result = apply_memory_repair(root, _resolve_memory_repair_proposal_arg(args.proposal))
            apply_payload: dict[str, object] = {
                "command": "memory-repair apply",
                "repair_id": apply_result.proposal.repair_id,
                "apply_log_path": str(apply_result.apply_log_path),
                "status": apply_result.apply_log.status,
                "management_events": _management_event_payload(root),
            }
            return _success(
                args,
                apply_payload,
                [
                    f"Applied memory repair: {apply_result.proposal.repair_id}",
                    f"Apply log: {apply_result.apply_log_path}",
                    *_management_event_lines(root),
                ],
            )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except MemoryRepairError as exc:
        return _failure(args, str(exc), error_type="memory_repair_error")

def _setting_change_suggestion_success(
    args: argparse.Namespace,
    root: Path,
    result: SettingChangeSuggestionResult,
    *,
    command: str,
) -> int:
    if result.status == "needs_clarification":
        clarification = result.clarification
        if clarification is None:
            return _failure(args, "missing setting change clarification result", error_type="memory_repair_error")
        payload: dict[str, object] = {
            "command": command,
            "status": "needs_clarification",
            "clarification_id": clarification.clarification_id,
            "questions": clarification.questions,
            "conversation_turns": [turn.model_dump(mode="json") for turn in clarification.conversation_turns],
            "clarification_path": str((root / "memory" / "repairs" / "clarifications" / clarification.clarification_id / "session.json").resolve()),
            "management_events": _management_event_payload(root),
        }
        lines = [
            f"Setting change needs clarification: {clarification.clarification_id}",
            *[f"Question: {question}" for question in clarification.questions],
            f"Continue: novel setting-change answer {clarification.clarification_id} --path {root} --answer <your-answer>",
        ]
        return _success(args, payload, lines)
    proposal_result = result.proposal_result
    if proposal_result is None:
        return _failure(args, "missing setting change proposal result", error_type="memory_repair_error")
    proposal = proposal_result.proposal
    impact = proposal.impact
    payload = {
        "command": command,
        "status": "proposal_ready",
        "repair_id": proposal.repair_id,
        "proposal_path": str(proposal_result.proposal_path),
        "markdown_path": str(proposal_result.markdown_path),
        "target_files": proposal.target_files,
        "domains": proposal.domains,
        "operation_count": len(proposal.operations),
        "confidence": proposal.confidence,
        "impact": impact.model_dump(mode="json") if impact else None,
        "followup_actions": [
            action.model_dump(mode="json") for action in proposal.followup_actions
        ],
        "management_events": _management_event_payload(root),
    }
    affected = ", ".join(str(number) for number in impact.affected_chapters) if impact else ""
    return _success(
        args,
        payload,
        [
            f"Setting change proposal: {proposal_result.proposal_path}",
            f"Targets: {', '.join(proposal.target_files) or 'none'}",
            f"Domains: {', '.join(proposal.domains) or 'none'}",
            f"Operations: {len(proposal.operations)}",
            f"Affected chapters: {affected or 'none'}",
            *_management_event_lines(root),
        ],
    )

def _cmd_setting_change(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        with _command_lock(args, root, f"setting-change {args.setting_change_command}"):
            if args.setting_change_command == "suggest":
                result = suggest_setting_change_interactive(
                    root,
                    args.request,
                    provider_name=args.provider,
                    stage=cast(MemoryChangeStage, args.stage),
                    session_id=args.session_id,
                    chapter_number=args.chapter,
                    audit_issue_ids=list(args.audit_issue_id or []),
                )
                return _setting_change_suggestion_success(args, root, result, command="setting-change suggest")
            if args.setting_change_command == "answer":
                result = answer_setting_change_clarification(
                    root,
                    args.clarification_id,
                    args.answer,
                    provider_name=args.provider,
                )
                return _setting_change_suggestion_success(args, root, result, command="setting-change answer")
            apply_result = apply_memory_repair(root, _resolve_memory_repair_proposal_arg(args.proposal))
            payload: dict[str, object] = {
                "command": "setting-change apply",
                "repair_id": apply_result.proposal.repair_id,
                "apply_log_path": str(apply_result.apply_log_path),
                "status": apply_result.apply_log.status,
                "management_events": _management_event_payload(root),
            }
            return _success(
                args,
                payload,
                [
                    f"Applied setting change: {apply_result.proposal.repair_id}",
                    f"Apply log: {apply_result.apply_log_path}",
                    *_management_event_lines(root),
                ],
            )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except MemoryRepairError as exc:
        return _failure(args, str(exc), error_type="memory_repair_error")

def _cmd_chapter_memory(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        if args.chapter_memory_command == "show":
            path = chapter_memory_path(root, args.chapter_number)
            if not path.exists():
                raise ChapterMemoryError(f"{path} is missing")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if _wants_json(args):
                _print_json({"ok": True, "command": "chapter-memory show", "path": str(path), "memory": payload})
                return 0
            if not _quiet(args):
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if args.chapter_memory_command == "generate":
            if args.dry_run_provider:
                _print_dry_run_provider(
                    root,
                    args.agent_config,
                    args.provider,
                    args.model,
                    (("chapter_memory", ("state_update", "audit")),),
                )
                return 0
            with _command_lock(args, root, "chapter-memory generate"):
                provider_warnings: list[str] = []
                provider = None
                try:
                    provider = load_chapter_memory_provider(
                        root,
                        args.provider,
                        chapter_number=args.chapter_number,
                        agent_config_path=args.agent_config,
                        model_name=args.model,
                    )
                except Exception as exc:
                    provider_warnings.append(f"chapter memory provider unavailable; using deterministic fallback: {exc}")
                result = generate_chapter_memory(
                    ChapterMemoryOptions(root=root, chapter_number=args.chapter_number, force=args.force),
                    provider,
                    initial_warnings=tuple(provider_warnings),
                )
            return _success(
                args,
                {
                    "command": "chapter-memory generate",
                    "chapter_number": args.chapter_number,
                    "memory_path": str(result.memory_path),
                    "warnings": list(result.warnings),
                },
                [
                    *(f"warning: {warning}" for warning in result.warnings),
                    f"Wrote chapter memory: {result.memory_path}",
                ],
            )

        if args.chapter_memory_command == "rebuild":
            if args.dry_run_provider:
                _print_dry_run_provider(
                    root,
                    args.agent_config,
                    args.provider,
                    args.model,
                    (("chapter_memory", ("state_update", "audit")),),
                )
                return 0
            written: list[str] = []
            warnings: list[str] = []
            with _command_lock(args, root, "chapter-memory rebuild"):
                for chapter_number in _accepted_chapter_numbers(root):
                    path = chapter_memory_path(root, chapter_number)
                    if args.missing_only and path.exists():
                        continue
                    try:
                        provider_warnings = []
                        provider = None
                        try:
                            provider = load_chapter_memory_provider(
                                root,
                                args.provider,
                                chapter_number=chapter_number,
                                agent_config_path=args.agent_config,
                                model_name=args.model,
                            )
                        except Exception as exc:
                            provider_warnings.append(
                                f"chapter {chapter_number}: chapter memory provider unavailable; "
                                f"using deterministic fallback: {exc}"
                            )
                        result = generate_chapter_memory(
                            ChapterMemoryOptions(root=root, chapter_number=chapter_number, force=True),
                            provider,
                            initial_warnings=tuple(provider_warnings),
                        )
                        written.append(str(result.memory_path))
                        warnings.extend(result.warnings)
                    except Exception as exc:
                        warnings.append(f"chapter {chapter_number}: {exc}")
            return _success(
                args,
                {
                    "command": "chapter-memory rebuild",
                    "written": written,
                    "warnings": warnings,
                },
                [
                    *(f"warning: {warning}" for warning in warnings),
                    f"Rebuilt chapter memories: {len(written)}",
                    *(f"Wrote: {path}" for path in written),
                ],
            )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except ChapterMemoryError as exc:
        return _failure(args, str(exc), error_type="chapter_memory_error")
    except Exception as exc:
        return _failure(args, f"chapter memory operation failed: {exc}", error_type="chapter_memory_error")
    return _failure(args, f"unknown chapter-memory command: {args.chapter_memory_command}", code=2)

def _accepted_chapter_numbers(root: Path) -> list[int]:
    return accepted_chapter_numbers(root)
