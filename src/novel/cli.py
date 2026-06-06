from __future__ import annotations

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
from novel.core.memory_repair import MemoryRepairError, apply_memory_repair, suggest_memory_repair, suggest_setting_change
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


ERROR_CODES = {
    "audit_error": "Audit generation or validation failed.",
    "canon_error": "Canon operation failed.",
    "drafting_error": "Chapter drafting failed.",
    "export_error": "Export operation failed.",
    "inspiration_error": "Inspiration generation failed.",
    "migration_error": "Schema migration failed.",
    "orchestrator_error": "Orchestrator request failed.",
    "memory_repair_error": "Memory repair proposal or apply failed.",
    "chapter_memory_error": "Chapter memory generation or loading failed.",
    "planning_error": "Chapter planning failed.",
    "polishing_error": "Chapter polishing failed.",
    "project_read_error": "Project data could not be read.",
    "revision_error": "Chapter revision failed.",
    "search_error": "Search index or query failed.",
    "session_error": "Creation session operation failed.",
    "setup_guide_error": "Project initial setup guide failed.",
    "state_update_error": "State/timeline update failed.",
    "usage_error": "Provider usage statistics could not be read.",
    "validation_failed": "Project validation failed.",
    "web_error": "Web UI could not start.",
    "workflow_error": "Chapter workflow failed.",
    "workspace_exists": "Workspace initialization would overwrite data.",
    "doctor_failed": "Doctor checks found blocking errors.",
    "secret_detected": "Secret scanner found a raw secret-looking value.",
    "invalid_env_example": ".env.example contains a non-empty or invalid value.",
    "unsafe_config_secret": "Config contains a likely literal secret instead of an env var name.",
    "project_locked": "Project workspace is locked by another writer process.",
    "atomic_write_failed": "Atomic file write failed.",
    "backup_failed": "File backup failed.",
    "error": "Generic command error.",
}


def _add_agent_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent-config",
        type=Path,
        default=None,
        help="Agent model config file. Defaults to config/agents.yaml in the workspace.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Temporarily override the configured model name.",
    )
    parser.add_argument(
        "--dry-run-provider",
        action="store_true",
        help="Show the provider configuration that would be used without calling the provider.",
    )


def _add_search_context_args(parser: argparse.ArgumentParser, *, default_enabled: bool = False) -> None:
    if default_enabled:
        parser.add_argument(
            "--no-search-context",
            dest="use_search_context",
            action="store_false",
            default=True,
            help="Disable automatic FTS memory context for this workflow.",
        )
    else:
        parser.add_argument(
            "--use-search-context",
            action="store_true",
            help="Add explainable FTS memory context to the agent prompt.",
        )
    parser.add_argument(
        "--vector-context",
        choices=("auto", "on", "off"),
        default="auto",
        help="Embedding semantic context mode for agent memory retrieval. Defaults to auto.",
    )
    parser.add_argument(
        "--use-vector-context",
        action="store_true",
        help="Compatibility alias for --vector-context on.",
    )


def _add_polish_mode_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--polish-mode",
        choices=("single-pass", "auto", "review-gate"),
        default=None,
        help="Finalization mode. Defaults to project polish.mode or single-pass.",
    )


def _vector_context_mode_from_args(args: argparse.Namespace) -> VectorContextMode:
    if getattr(args, "use_vector_context", False):
        return "on"
    value = str(getattr(args, "vector_context", "auto") or "auto")
    if value in {"auto", "on", "off"}:
        return cast(VectorContextMode, value)
    return "auto"


def _polish_mode_from_arg(value: str | None) -> PolishMode | None:
    if not value:
        return None
    normalized = value.replace("-", "_")
    if normalized in {"single_pass", "auto", "review_gate"}:
        return cast(PolishMode, normalized)
    return None


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


def _audit_issue_lines(report: AuditReport) -> list[str]:
    if not report.issues:
        return []
    lines = ["Audit issues:"]
    for issue in sorted(report.issues, key=lambda item: _severity_rank(item.severity), reverse=True):
        lines.append(f"- [{issue.severity}/{issue.type}] {issue.id}: {issue.description}")
        if issue.suggested_fix:
            lines.append(f"  suggested_fix: {issue.suggested_fix}")
    if all(issue.severity == "low" for issue in report.issues):
        lines.append("Low issues are not auto-fixed; choose whether to revise with revise-chapter --from-audit.")
    return lines


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


def _management_event_payload(root: Path) -> list[dict[str, object]]:
    return [event.model_dump(mode="json") for event in load_management_events(root, limit=5)]


def _management_event_lines(root: Path) -> list[str]:
    events = load_management_events(root, limit=5)
    if not events:
        return []
    lines = ["Recent background management events:"]
    for event in events:
        targets = ", ".join(event.target_files) if event.target_files else "none"
        lines.append(f"- [{event.status}/{event.event_type}] {event.message} targets={targets}")
    return lines


def _severity_rank(severity: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(severity, 0)


def _extract_chapter_from_text(text: str) -> int | None:
    match = re.search(r"第\s*(\d+)\s*章", text)
    if match:
        return int(match.group(1))
    match = re.search(r"chapter\s*(\d+)", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _extract_repair_id(text: str) -> str | None:
    match = re.search(r"\brepair_[0-9]{8}_[0-9]{6}_[0-9]{6}\b", text)
    return match.group(0) if match else None


def _resolve_memory_repair_proposal_arg(value: str) -> Path:
    repair_id = _extract_repair_id(value)
    if repair_id and value.strip() == repair_id:
        return Path("memory") / "repairs" / repair_id / "proposal.json"
    return Path(value)


def _print_dry_run_provider(
    root: Path,
    agent_config: Path | None,
    provider_name: str,
    model_name: str | None,
    agents: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    path = agent_config or default_agent_config_path(root)
    overrides = ProviderOverrides(provider_name=provider_name, model_name=model_name)
    for index, (agent_name, fallback_agents) in enumerate(agents):
        if index:
            print("")
        print(
            describe_agent_provider(
                path,
                agent_name,
                fallback_agents=fallback_agents,
                overrides=overrides,
            ).format()
        )


def _add_integration_args(parser: argparse.ArgumentParser) -> None:
    option_strings = {
        option
        for action in parser._actions
        for option in getattr(action, "option_strings", ())
    }
    if "--project" not in option_strings:
        parser.add_argument(
            "--project",
            default=None,
            help="Stable alias for --path, intended for external agent integrations.",
        )
    if "--quiet" not in option_strings:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress human-readable success output.",
        )
    if "--json" not in option_strings:
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output machine-readable JSON.",
        )


def _add_integration_args_recursive(parser: argparse.ArgumentParser) -> None:
    _add_integration_args(parser)
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for subparser in choices.values():
                _add_integration_args_recursive(subparser)


def _apply_project_alias(args: argparse.Namespace) -> None:
    project = getattr(args, "project", None)
    if project is not None and hasattr(args, "path"):
        args.path = project


def _wants_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _quiet(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "quiet", False))


def _success(args: argparse.Namespace, payload: dict[str, object], lines: list[str] | None = None) -> int:
    if _wants_json(args):
        response = {"ok": True, **payload}
        if lines:
            response["messages"] = lines
        _print_json(response)
    elif not _quiet(args):
        for line in lines or []:
            print(line)
    return 0


def _failure(args: argparse.Namespace, message: str, *, code: int = 1, error_type: str = "error") -> int:
    safe = _safe_message(message)
    if _wants_json(args):
        _print_json(
            {
                "ok": False,
                "error": {
                    "type": error_type,
                    "code": error_type,
                    "message": safe,
                    "exit_code": code,
                },
            }
        )
    else:
        print(f"error: {safe}", file=sys.stderr)
    return code


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _safe_message(message: str) -> str:
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[redacted-api-key]", message)
    for key, value in os.environ.items():
        if value and ("KEY" in key or "TOKEN" in key or "SECRET" in key):
            redacted = redacted.replace(value, "[redacted]")
    return redacted


def _command_lock(args: argparse.Namespace, root: Path, task: str, *, enabled: bool = True):
    if not enabled:
        return nullcontext()
    return ProjectLock(root, task=task)


def _validation_payload(report) -> dict[str, object]:
    return {
        "root": str(report.root),
        "ok": report.ok,
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "messages": [
            {
                "level": message.level,
                "path": str(message.path),
                "message": message.message,
            }
            for message in report.messages
        ],
    }


def _status_payload(status) -> dict[str, object]:
    return {
        "title": status.title,
        "latest_chapter": status.latest_chapter,
        "inspiration_exists": status.inspiration_exists,
        "character_count": status.character_count,
        "location_count": status.location_count,
        "item_count": status.item_count,
        "timeline_event_count": status.timeline_event_count,
        "latest_run_log": str(status.latest_run_log) if status.latest_run_log else None,
        "latest_run_summary": status.latest_run_summary,
        "accepted_chapter_count": status.accepted_chapter_count,
    }


def _format_usage_summary(summary: dict[str, object]) -> list[str]:
    total = summary.get("total")
    total = total if isinstance(total, dict) else {}
    last_call = summary.get("last_call")
    lines = [
        "Provider usage:",
        f"Calls: {total.get('call_count', 0)} "
        f"(success: {total.get('success_count', 0)}, failed: {total.get('failed_count', 0)})",
        f"Tokens: total={total.get('total_tokens', 0)}, "
        f"prompt={total.get('prompt_tokens', 0)}, completion={total.get('completion_tokens', 0)}",
        f"Unknown token calls: {total.get('unknown_token_call_count', 0)}",
    ]
    if isinstance(last_call, dict):
        lines.append(
            "Last call: "
            f"{last_call.get('provider', 'unknown')} / {last_call.get('model', 'unknown')} / "
            f"{last_call.get('status', 'unknown')}"
        )
    return lines


def _resolve_web_port(path: str, explicit_port: int | None) -> int:
    if explicit_port is not None:
        return explicit_port
    project_path = Path(path) / "project.yaml"
    if not project_path.exists():
        return 8765
    project = load_yaml_model(project_path, ProjectConfig)
    if project.web:
        return project.web.default_port
    return 8765


def _should_run_init_guide(args: argparse.Namespace) -> bool:
    if getattr(args, "no_guide", False):
        return False
    if getattr(args, "guide", False):
        return True
    if _wants_json(args) or _quiet(args):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def _run_init_setup_guide(root: Path) -> tuple[list[str], bool, int]:
    lines = [
        "",
        "项目初始引导",
        "默认 API 需要使用 OpenAI-compatible /chat/completions 格式。",
        "API Key 会写入项目根目录 .env；config/agents.yaml 只保存环境变量名。",
    ]
    print("\n".join(lines))
    output_lines: list[str] = []

    base_url = _prompt_text("默认 API base URL", "https://api.openai.com/v1")
    api_key = getpass.getpass("默认 API Key（输入时不会显示，留空跳过默认 API 配置）: ").strip()
    model = ""
    if api_key:
        while not model:
            model = _prompt_text("默认模型名", "")
            if not model:
                print("模型名必填；没有模型名无法进行连通性测试。")
        try:
            result = configure_default_provider(
                root,
                base_url=base_url,
                api_key=api_key,
                model=model,
                provider="openai_compatible",
                ping=True,
            )
        except SetupGuideError as exc:
            raise SetupGuideError(
                f"默认 API 配置未保存，连通性测试失败：{exc}"
            ) from exc
        output_lines.append(f"默认 API 连通性测试通过：{result.provider} / {result.model}")
        output_lines.append(
            "这组 API 配置已作为所有未单独配置 Agent 的默认配置；"
            "后续可编辑 config/agents.yaml 为单个 Agent 覆盖模型、思考模式、温度等参数。"
        )
    else:
        output_lines.append("已跳过默认 API 配置；运行真实 Agent 前需要先配置 config/agents.yaml 和 .env。")

    if _prompt_yes_no("是否配置 embedding API？", default=False):
        embedding_base_url = _prompt_text("Embedding API base URL（OpenAI-compatible /embeddings 格式）", base_url)
        embedding_api_key = getpass.getpass("Embedding API Key（输入时不会显示）: ").strip()
        embedding_model = ""
        while not embedding_model:
            embedding_model = _prompt_text("Embedding 模型名", "")
            if not embedding_model:
                print("Embedding 模型名必填；如暂不配置，请按 Ctrl+C 中止后重新 init --no-guide。")
        if not embedding_api_key:
            raise SetupGuideError("embedding API Key must not be empty")
        try:
            embedding_result = configure_embedding_provider(
                root,
                base_url=embedding_base_url,
                api_key=embedding_api_key,
                model=embedding_model,
                provider="openai_compatible",
                provider_name="configured",
                ping=True,
            )
        except SetupGuideError as exc:
            raise SetupGuideError(f"Embedding 配置未保存，连通性测试失败：{exc}") from exc
        output_lines.append(
            f"Embedding 连通性测试通过：{embedding_result.provider} / {embedding_result.model}"
        )
    else:
        output_lines.append("已跳过 embedding API 配置；关键词/FTS 检索仍可用。")

    recommended_port = find_available_port(8765)
    while True:
        port_text = _prompt_text("Web UI 端口", str(recommended_port))
        try:
            requested_port = int(port_text)
        except ValueError:
            print("端口号必须是 1-65535 之间的整数。")
            continue
        if not is_port_available(requested_port):
            replacement = find_available_port(requested_port + 1 if requested_port < 65535 else 8765)
            print(f"端口 {requested_port} 已被占用，将改用 {replacement}。")
            requested_port = replacement
        port_result = configure_web_port(root, requested_port=requested_port)
        output_lines.append(f"Web UI 默认端口已写入 project.yaml：{port_result.selected_port}")
        open_web = _prompt_yes_no("是否现在打开 Web UI？", default=True)
        return output_lines, open_web, port_result.selected_port


def _prompt_text(label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def _prompt_yes_no(label: str, *, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{label} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "是", "好", "打开"}


def completion_script(shell: str) -> str:
    commands = (
        "init validate migrate schema index search ask memory-repair setting-change chapter-memory session status usage show inspire canon plan-chapter "
        "write-chapter polish-chapter audit-chapter revise-chapter propose-state-update "
        "apply-state-update accept-chapter generate-chapter export web doctor completion"
    )
    common_options = "--help --json --quiet --project --path"
    if shell == "bash":
        return (
            "_novel_completion() {\n"
            "  local cur prev\n"
            "  COMPREPLY=()\n"
            "  cur=\"${COMP_WORDS[COMP_CWORD]}\"\n"
            f"  local commands=\"{commands}\"\n"
            f"  local options=\"{common_options} --version\"\n"
            "  if [[ ${COMP_CWORD} -eq 1 ]]; then\n"
            "    COMPREPLY=( $(compgen -W \"$commands $options\" -- \"$cur\") )\n"
            "  else\n"
            "    COMPREPLY=( $(compgen -W \"$commands $options\" -- \"$cur\") )\n"
            "  fi\n"
            "}\n"
            "complete -F _novel_completion novel\n"
        )
    if shell == "zsh":
        return (
            "#compdef novel\n"
            "_novel() {\n"
            "  local -a commands options\n"
            f"  commands=({commands})\n"
            f"  options=({common_options} --version)\n"
            "  _describe 'command' commands || _describe 'option' options\n"
            "}\n"
            "compdef _novel novel\n"
        )
    if shell == "fish":
        lines = ["complete -c novel -f"]
        for command in commands.split():
            lines.append(f"complete -c novel -n '__fish_use_subcommand' -a {command}")
        for option in (common_options + " --version").split():
            lines.append(f"complete -c novel -l {option.removeprefix('--')}")
        return "\n".join(lines) + "\n"
    raise ValueError(f"unsupported shell: {shell}")


def run_doctor(root: Path) -> dict[str, object]:
    root = root.expanduser().resolve()
    checks: list[dict[str, object]] = []
    _doctor_check(checks, "python", "ok", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    for module_name, label, required in (
        ("pydantic", "dependency:pydantic", True),
        ("yaml", "dependency:PyYAML", True),
        ("docx", "dependency:python-docx", True),
        ("playwright", "dependency:playwright", False),
    ):
        exists = importlib.util.find_spec(module_name) is not None
        status = "ok" if exists else ("error" if required else "warning")
        message = "installed" if exists else ("missing required dependency" if required else "missing optional dependency")
        _doctor_check(checks, label, status, message)

    if (root / "project.yaml").exists():
        _doctor_check(checks, "project", "ok", str(root))
        for rel_path in (
            "project.yaml",
            "config/agents.yaml",
            "config/embeddings.yaml",
            "memory/canon/characters.json",
            "memory/state/current_state.json",
            "memory/state/timeline.json",
        ):
            path = root / rel_path
            _doctor_check(
                checks,
                f"file:{rel_path}",
                "ok" if path.exists() else "error",
                "present" if path.exists() else "missing",
            )
        report = validate_project(root)
        _doctor_check(
            checks,
            "validation",
            "ok" if report.ok else "error",
            f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)",
        )
        for config_rel in ("config/agents.yaml", "config/embeddings.yaml"):
            checks.extend(_doctor_env_checks(root / config_rel))
        checks.extend(_doctor_agent_config_checks(root / "config" / "agents.yaml"))
    else:
        _doctor_check(checks, "project", "warning", f"{root} does not look like a novel workspace")

    security_root = _repo_root()
    if security_root:
        security = scan_security(security_root)
        if security.ok:
            _doctor_check(checks, "security", "ok", "no tracked secrets detected")
        else:
            _doctor_check(
                checks,
                "security",
                "error",
                f"{len(security.findings)} security finding(s); run tests for details",
            )

    error_count = sum(1 for check in checks if check["status"] == "error")
    warning_count = sum(1 for check in checks if check["status"] == "warning")
    return {
        "root": str(root),
        "ok": error_count == 0,
        "error_count": error_count,
        "warning_count": warning_count,
        "checks": checks,
        "error_codes": ERROR_CODES,
    }


def format_doctor_result(result: dict[str, object]) -> list[str]:
    lines = [
        f"Doctor: {'passed' if result['ok'] else 'failed'}",
        f"Root: {result['root']}",
        f"Errors: {result['error_count']}; warnings: {result['warning_count']}",
    ]
    checks = result.get("checks", [])
    if not isinstance(checks, list):
        checks = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        lines.append(f"{check['status']}: {check['name']}: {check['message']}")
    return lines


def _doctor_check(checks: list[dict[str, object]], name: str, status: str, message: str) -> None:
    checks.append({"name": name, "status": status, "message": message})


def _doctor_env_checks(path: Path) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    if not path.exists():
        return checks
    try:
        config = load_yaml(path)
    except Exception as exc:
        _doctor_check(checks, f"env:{path.name}", "error", f"could not read config: {exc}")
        return checks
    env = load_project_env(path.parent.parent)
    for env_name in sorted(_collect_env_names(config)):
        _doctor_check(
            checks,
            f"env:{env_name}",
            "ok" if env.get(env_name) else "warning",
            "set" if env.get(env_name) else "not set",
        )
    return checks


def _doctor_agent_config_checks(path: Path) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    if not path.exists():
        return checks
    try:
        config = load_yaml_model(path, AgentsConfig)
    except Exception as exc:
        _doctor_check(checks, "agent-config", "error", f"could not read config/agents.yaml: {exc}")
        return checks
    if config.default is None:
        _doctor_check(
            checks,
            "agent-config:default",
            "warning",
            "default API config is missing; unconfigured agents cannot use provider config",
        )
    else:
        status = "warning" if config.default.provider.lower() == "mock" else "ok"
        message = (
            "default provider uses mock; mock is intended for tests only"
            if status == "warning"
            else f"default provider is {config.default.provider}"
        )
        _doctor_check(checks, "agent-config:default", status, message)
    for name, config_item in sorted(config.agents.items()):
        provider = config_item.provider
        if provider and provider.lower() == "mock":
            _doctor_check(
                checks,
                f"agent-config:{name}",
                "warning",
                "agent uses mock provider; mock is intended for tests only",
            )
    return checks


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


def _repo_root() -> Path | None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / ".git").exists():
            return parent
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel", description="Novel workspace CLI")
    parser.add_argument(
        "--version",
        action="version",
        version=f"novel {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a new novel project workspace")
    init_parser.add_argument("title", help="Novel title")
    init_parser.add_argument(
        "--path",
        default="novel-project",
        help="Workspace directory to create. Defaults to ./novel-project",
    )
    init_parser.add_argument(
        "--project-id",
        default=None,
        help="Stable project id. Defaults to a generated id based on the title.",
    )
    init_parser.add_argument("--language", default="zh-CN", help="Project language")
    init_parser.add_argument(
        "--genre",
        action="append",
        default=[],
        help="Genre label. Can be provided multiple times.",
    )
    guide_group = init_parser.add_mutually_exclusive_group()
    guide_group.add_argument(
        "--guide",
        action="store_true",
        help="Run the interactive initial setup guide after creating the workspace.",
    )
    guide_group.add_argument(
        "--no-guide",
        action="store_true",
        help="Skip the interactive initial setup guide.",
    )

    validate_parser = subparsers.add_parser("validate", help="Validate a novel project workspace")
    validate_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to validate. Defaults to the current directory.",
    )

    migrate_parser = subparsers.add_parser("migrate", help="Migrate a novel project workspace schema")
    migrate_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    migrate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show migration actions without writing files.",
    )

    schema_parser = subparsers.add_parser("schema", help="Export JSON Schema files")
    schema_subparsers = schema_parser.add_subparsers(dest="schema_command", required=True)
    schema_export_parser = schema_subparsers.add_parser("export", help="Export JSON Schema files")
    schema_export_parser.add_argument(
        "--output",
        type=Path,
        default=Path("schemas"),
        help="Output directory. Defaults to ./schemas.",
    )

    completion_parser = subparsers.add_parser("completion", help="Print shell completion script")
    completion_parser.add_argument(
        "shell",
        choices=("bash", "zsh", "fish"),
        help="Shell to generate completion for.",
    )

    doctor_parser = subparsers.add_parser("doctor", help="Check local environment and project health")
    doctor_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to check. Defaults to the current directory.",
    )

    index_parser = subparsers.add_parser("index", help="Manage the local search index")
    index_subparsers = index_parser.add_subparsers(dest="index_command", required=True)
    index_rebuild = index_subparsers.add_parser("rebuild", help="Rebuild the local search index")
    index_rebuild.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    index_rebuild.add_argument(
        "--embedding-config",
        type=Path,
        default=None,
        help="Embedding config file. Defaults to config/embeddings.yaml in the workspace.",
    )
    index_rebuild.add_argument(
        "--embedding-provider",
        default="config",
        choices=("config", "local_hash", "dashscope", "zhipu", "openai", "openai_compatible"),
        help="Embedding provider to use for vector indexing. Defaults to config active_provider.",
    )
    index_rebuild.add_argument(
        "--with-embeddings",
        action="store_true",
        help="Also build real embedding vectors. This may call an external embedding API.",
    )
    index_refresh = index_subparsers.add_parser("refresh", help="Refresh stale local search index documents")
    index_refresh.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    index_refresh.add_argument(
        "--embedding-config",
        type=Path,
        default=None,
        help="Embedding config file. Defaults to config/embeddings.yaml in the workspace.",
    )
    index_refresh.add_argument(
        "--embedding-provider",
        default="config",
        choices=("config", "local_hash", "dashscope", "zhipu", "openai", "openai_compatible"),
        help="Embedding provider to use when --with-embeddings is set.",
    )
    index_refresh.add_argument(
        "--with-embeddings",
        action="store_true",
        help="Refresh real embedding vectors for changed documents. This may call an external embedding API.",
    )
    index_status = index_subparsers.add_parser("status", help="Show local search index status")
    index_status.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    index_status.add_argument(
        "--embedding-config",
        type=Path,
        default=None,
        help="Embedding config file. Defaults to config/embeddings.yaml in the workspace.",
    )
    index_status.add_argument(
        "--embedding-provider",
        default="config",
        choices=("config", "local_hash", "dashscope", "zhipu", "openai", "openai_compatible"),
        help="Embedding provider to inspect. Defaults to config active_provider.",
    )

    search_parser = subparsers.add_parser("search", help="Search project memory")
    search_parser.add_argument("query", help="Keyword query")
    search_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    search_parser.add_argument(
        "--type",
        default="all",
        choices=("character", "location", "item", "event", "chapter", "chapter_memory", "all"),
        help="Result type to search. Defaults to all.",
    )
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of results. Defaults to 10.",
    )
    search_parser.add_argument(
        "--chapter",
        type=int,
        default=None,
        help="Only return results associated with this chapter number.",
    )
    search_parser.add_argument(
        "--highlight",
        action="store_true",
        help="Include highlighted excerpts with <mark>...</mark> tags.",
    )
    search_parser.add_argument(
        "--use-vector",
        action="store_true",
        help="Use stored embedding vectors to boost lexical search results.",
    )
    search_parser.add_argument(
        "--embedding-config",
        type=Path,
        default=None,
        help="Embedding config file. Defaults to config/embeddings.yaml in the workspace.",
    )
    search_parser.add_argument(
        "--embedding-provider",
        default="config",
        choices=("config", "local_hash", "dashscope", "zhipu", "openai", "openai_compatible"),
        help="Embedding provider for query embedding when --use-vector is enabled.",
    )
    search_parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON.",
    )

    ask_parser = subparsers.add_parser("ask", help="Ask the controlled orchestrator to run a task")
    ask_parser.add_argument("request", help="Natural language task request")
    ask_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    ask_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for selected agents.",
    )
    ask_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the execution plan without calling agents or writing files.",
    )
    ask_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow selected services to overwrite their normal target files.",
    )
    ask_parser.add_argument(
        "--max-steps",
        type=int,
        default=8,
        help="Maximum handoff steps. Defaults to 8.",
    )
    ask_parser.add_argument(
        "--max-retries",
        type=int,
        default=0,
        help="Maximum retries per task. Defaults to 0.",
    )
    ask_parser.add_argument(
        "--max-agent-calls",
        type=int,
        default=8,
        help="Maximum agent calls. Defaults to 8.",
    )
    ask_parser.add_argument(
        "--show-handoff-rules",
        action="store_true",
        help="Print allowed handoff rules before the plan.",
    )
    _add_search_context_args(ask_parser, default_enabled=True)

    memory_repair_parser = subparsers.add_parser("memory-repair", help="Suggest or apply project memory repair proposals")
    memory_repair_subparsers = memory_repair_parser.add_subparsers(dest="memory_repair_command", required=True)
    memory_repair_suggest = memory_repair_subparsers.add_parser("suggest", help="Create a memory repair proposal")
    memory_repair_suggest.add_argument("request", help="Natural language description of the memory problem")
    memory_repair_suggest.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    memory_repair_suggest.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for structured repair proposal generation.",
    )
    memory_repair_suggest.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    memory_repair_suggest.add_argument("--quiet", action="store_true", help="Suppress normal output.")
    memory_repair_apply = memory_repair_subparsers.add_parser("apply", help="Apply a memory repair proposal explicitly")
    memory_repair_apply.add_argument("proposal", help="repair_id or path to memory/repairs/{repair_id}/proposal.json")
    memory_repair_apply.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    memory_repair_apply.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    memory_repair_apply.add_argument("--quiet", action="store_true", help="Suppress normal output.")

    setting_change_parser = subparsers.add_parser(
        "setting-change",
        help="Suggest or apply natural-language character/background setting changes",
    )
    setting_change_subparsers = setting_change_parser.add_subparsers(dest="setting_change_command", required=True)
    setting_change_suggest = setting_change_subparsers.add_parser("suggest", help="Create a setting change proposal")
    setting_change_suggest.add_argument("request", help="Natural language setting change request")
    setting_change_suggest.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    setting_change_suggest.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for structured setting change proposal generation.",
    )
    setting_change_suggest.add_argument(
        "--stage",
        default="unknown",
        choices=("pre_creation", "outline_discussion", "content_review", "post_chapter", "unknown"),
        help="Current creative stage for impact/follow-up analysis.",
    )
    setting_change_suggest.add_argument("--session-id", help="Active session id, if any.")
    setting_change_suggest.add_argument("--chapter", type=int, help="Current chapter number, if any.")
    setting_change_suggest.add_argument(
        "--audit-issue-id",
        action="append",
        default=[],
        help="Audit issue id that triggered this setting change. Can be repeated.",
    )
    setting_change_suggest.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    setting_change_suggest.add_argument("--quiet", action="store_true", help="Suppress normal output.")
    setting_change_apply = setting_change_subparsers.add_parser("apply", help="Apply a setting change proposal explicitly")
    setting_change_apply.add_argument("proposal", help="repair_id or path to memory/repairs/{repair_id}/proposal.json")
    setting_change_apply.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    setting_change_apply.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    setting_change_apply.add_argument("--quiet", action="store_true", help="Suppress normal output.")

    chapter_memory_parser = subparsers.add_parser("chapter-memory", help="Manage accepted chapter memory")
    chapter_memory_subparsers = chapter_memory_parser.add_subparsers(dest="chapter_memory_command", required=True)
    chapter_memory_show = chapter_memory_subparsers.add_parser("show", help="Show a chapter_memory.json file")
    chapter_memory_show.add_argument("chapter_number", type=int, help="Chapter number")
    chapter_memory_show.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    chapter_memory_generate = chapter_memory_subparsers.add_parser("generate", help="Generate chapter_memory.json")
    chapter_memory_generate.add_argument("chapter_number", type=int, help="Chapter number")
    chapter_memory_generate.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    chapter_memory_generate.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for structured ChapterMemory generation.",
    )
    chapter_memory_generate.add_argument("--force", action="store_true", help="Overwrite existing chapter_memory.json.")
    _add_agent_runtime_args(chapter_memory_generate)
    chapter_memory_rebuild = chapter_memory_subparsers.add_parser("rebuild", help="Rebuild chapter memories")
    chapter_memory_rebuild.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    chapter_memory_rebuild.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for structured ChapterMemory generation.",
    )
    chapter_memory_rebuild.add_argument("--force", action="store_true", help="Overwrite existing chapter_memory.json files.")
    chapter_memory_rebuild.add_argument(
        "--missing-only",
        action="store_true",
        help="Only generate ChapterMemory for accepted chapters missing chapter_memory.json.",
    )
    _add_agent_runtime_args(chapter_memory_rebuild)

    session_parser = subparsers.add_parser("session", help="Manage collaborative creation sessions")
    session_subparsers = session_parser.add_subparsers(dest="session_command", required=True)
    session_start = session_subparsers.add_parser("start", help="Start a collaborative creation session")
    session_start.add_argument("intent", help="User intent for this creation session")
    session_start.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_start.add_argument("--chapters", default=None, help="Chapter range, for example 3 or 3-4.")
    session_start.add_argument("--chapter", type=int, default=None, help="Single chapter for segment sessions.")
    session_start.add_argument("--segments", default=None, help="Segment range for a chapter, for example 8-10.")
    session_start.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for outline generation.",
    )
    session_start.add_argument("--force", action="store_true", help="Overwrite outline artifacts if needed.")
    _add_search_context_args(session_start, default_enabled=True)

    session_show = session_subparsers.add_parser("show", help="Show a creation session")
    session_show.add_argument("session_id", help="Session id")
    session_show.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")

    session_revise_outline = session_subparsers.add_parser("revise-outline", help="Revise a session outline proposal")
    session_revise_outline.add_argument("session_id", help="Session id")
    session_revise_outline.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_revise_outline.add_argument("--instruction", required=True, help="Outline revision instruction.")
    session_revise_outline.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for outline revision.",
    )
    session_revise_outline.add_argument("--force", action="store_true", help="Overwrite outline artifacts if needed.")
    _add_search_context_args(session_revise_outline, default_enabled=True)

    session_approve = session_subparsers.add_parser("approve-outline", help="Approve a session outline")
    session_approve.add_argument("session_id", help="Session id")
    session_approve.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_approve.add_argument("--force", action="store_true", help="Overwrite approved outline artifacts.")

    session_run = session_subparsers.add_parser("run", help="Run a session after outline approval")
    session_run.add_argument("session_id", help="Session id")
    session_run.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_run.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for session generation.",
    )
    session_run.add_argument("--force", action="store_true", help="Overwrite generated artifacts.")
    session_run.add_argument(
        "--max-auto-revision-rounds",
        type=int,
        default=None,
        help="Maximum automatic repair rounds. Defaults to session setting.",
    )
    _add_polish_mode_arg(session_run)
    _add_search_context_args(session_run, default_enabled=True)

    session_revise_content = session_subparsers.add_parser("revise-content", help="Revise generated session content")
    session_revise_content.add_argument("session_id", help="Session id")
    session_revise_content.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_revise_content.add_argument("--instruction", default=None, help="User feedback for content revision.")
    session_revise_content.add_argument(
        "--from-audit",
        action="store_true",
        help="Use current audit.json issues as the revision target. Useful when choosing to fix low issues.",
    )
    session_revise_content.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for content revision.",
    )
    session_revise_content.add_argument("--force", action="store_true", help="Overwrite selected revision artifacts.")
    _add_search_context_args(session_revise_content, default_enabled=True)

    session_revise_audit = session_subparsers.add_parser("revise-audit", help="Correct Audit understanding and rerun audit for a rewrite event")
    session_revise_audit.add_argument("session_id", help="Session id")
    session_revise_audit.add_argument("event_id", help="Rewrite event id")
    session_revise_audit.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_revise_audit.add_argument("--instruction", required=True, help="Correction instruction for Audit Agent.")
    session_revise_audit.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for audit revision.",
    )
    session_revise_audit.add_argument("--force", action="store_true", help="Overwrite audit artifacts if needed.")
    _add_search_context_args(session_revise_audit, default_enabled=True)

    session_retry_rewrite = session_subparsers.add_parser("retry-rewrite", help="Retry a rewrite event from the latest audit")
    session_retry_rewrite.add_argument("session_id", help="Session id")
    session_retry_rewrite.add_argument("event_id", help="Rewrite event id")
    session_retry_rewrite.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_retry_rewrite.add_argument("--instruction", default=None, help="Optional extra rewrite instruction.")
    session_retry_rewrite.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for rewrite retry.",
    )
    session_retry_rewrite.add_argument("--force", action="store_true", help="Overwrite generated artifacts if needed.")
    _add_polish_mode_arg(session_retry_rewrite)
    _add_search_context_args(session_retry_rewrite, default_enabled=True)

    session_undo_rewrite = session_subparsers.add_parser("undo-rewrite", help="Restore rejected text snapshot for a rewrite event")
    session_undo_rewrite.add_argument("session_id", help="Session id")
    session_undo_rewrite.add_argument("event_id", help="Rewrite event id")
    session_undo_rewrite.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_undo_rewrite.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for post-restore audit.",
    )
    _add_search_context_args(session_undo_rewrite, default_enabled=True)

    session_accept = session_subparsers.add_parser("accept", help="Accept generated session content")
    session_accept.add_argument("session_id", help="Session id")
    session_accept.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_accept.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for missing state proposals.",
    )
    session_accept.add_argument("--force", action="store_true", help="Overwrite missing state proposals if needed.")

    session_archive = session_subparsers.add_parser("archive", help="Archive accepted session content")
    session_archive.add_argument("session_id", help="Session id")
    session_archive.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_archive.add_argument("--force", action="store_true", help="Overwrite archive files.")

    status_parser = subparsers.add_parser("status", help="Show project status")
    status_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to inspect. Defaults to the current directory.",
    )

    usage_parser = subparsers.add_parser("usage", help="Show provider token usage statistics")
    usage_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to inspect. Defaults to the current directory.",
    )

    show_parser = subparsers.add_parser("show", help="Show project data")
    show_parser.add_argument(
        "target",
        choices=("characters", "timeline", "state", "canon"),
        help="Project data to display.",
    )
    show_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to inspect. Defaults to the current directory.",
    )

    inspire_parser = subparsers.add_parser("inspire", help="Generate an inspiration weak outline")
    inspire_parser.add_argument(
        "text",
        nargs="?",
        help="Raw inspiration text. Use --input to read from a file instead.",
    )
    inspire_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read raw inspiration text from a file.",
    )
    inspire_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    inspire_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the inspiration agent config.",
    )
    _add_agent_runtime_args(inspire_parser)
    inspire_parser.add_argument(
        "--json",
        action="store_true",
        help="Also write memory/inspiration.json.",
    )
    inspire_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing inspiration files.",
    )
    _add_search_context_args(inspire_parser)

    canon_parser = subparsers.add_parser("canon", help="Manage canon data")
    canon_subparsers = canon_parser.add_subparsers(dest="canon_command", required=True)

    canon_suggest = canon_subparsers.add_parser("suggest", help="Generate a canon proposal")
    canon_suggest.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    canon_suggest.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to config/agents.yaml.",
    )
    _add_agent_runtime_args(canon_suggest)
    canon_suggest.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Save proposal JSON to this file. Refuses to overwrite.",
    )
    _add_search_context_args(canon_suggest)

    canon_apply = canon_subparsers.add_parser("apply", help="Apply a canon proposal")
    canon_apply.add_argument("proposal_file", type=Path, help="Canon proposal JSON file")
    canon_apply.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )

    canon_validate = canon_subparsers.add_parser("validate", help="Validate canon files only")
    canon_validate.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    canon_show = canon_subparsers.add_parser("show", help="Show canon summary")
    canon_show.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )

    plan_parser = subparsers.add_parser("plan-chapter", help="Generate a chapter plan")
    plan_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    plan_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    plan_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra planning instruction for this chapter.",
    )
    plan_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra planning instruction from a file.",
    )
    plan_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the plot agent config.",
    )
    _add_agent_runtime_args(plan_parser)
    plan_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing plan files.",
    )
    plan_parser.add_argument(
        "--use-search-context",
        action="store_true",
        help="Add explainable search results to the planning prompt.",
    )
    plan_parser.add_argument(
        "--vector-context",
        choices=("auto", "on", "off"),
        default="auto",
        help="Embedding semantic context mode for agent memory retrieval. Defaults to auto.",
    )
    plan_parser.add_argument(
        "--use-vector-context",
        action="store_true",
        help="Compatibility alias for --vector-context on.",
    )

    write_parser = subparsers.add_parser("write-chapter", help="Generate a chapter draft")
    write_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    write_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    write_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra writing instruction for this chapter.",
    )
    write_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra writing instruction from a file.",
    )
    write_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the writer agent config.",
    )
    _add_agent_runtime_args(write_parser)
    write_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing draft.md.",
    )
    write_parser.add_argument(
        "--target-words",
        type=int,
        default=None,
        help="Optional target word count.",
    )
    write_parser.add_argument(
        "--style-note",
        default=None,
        help="Temporary style guidance for this draft.",
    )
    write_parser.add_argument(
        "--use-search-context",
        action="store_true",
        help="Add explainable search results to the writing prompt.",
    )
    write_parser.add_argument(
        "--vector-context",
        choices=("auto", "on", "off"),
        default="auto",
        help="Embedding semantic context mode for agent memory retrieval. Defaults to auto.",
    )
    write_parser.add_argument(
        "--use-vector-context",
        action="store_true",
        help="Compatibility alias for --vector-context on.",
    )

    polish_parser = subparsers.add_parser("polish-chapter", help="Polish a chapter draft")
    polish_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    polish_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    polish_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra polishing instruction for this chapter.",
    )
    polish_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra polishing instruction from a file.",
    )
    polish_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the polish agent config.",
    )
    _add_agent_runtime_args(polish_parser)
    polish_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing polished.md.",
    )
    polish_parser.add_argument(
        "--style-note",
        default=None,
        help="Temporary style guidance for this polish pass.",
    )
    polish_parser.add_argument(
        "--keep-length",
        action="store_true",
        help="Try to keep the original length and paragraph scale.",
    )
    polish_parser.add_argument(
        "--light-edit",
        action="store_true",
        help="Light edit: language cleanup only.",
    )
    polish_parser.add_argument(
        "--deep-edit",
        action="store_true",
        help="Deep edit: improve rhythm, dialogue, and description without changing facts.",
    )
    _add_search_context_args(polish_parser)

    audit_parser = subparsers.add_parser("audit-chapter", help="Audit a chapter for consistency")
    audit_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    audit_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    audit_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra audit instruction for this chapter.",
    )
    audit_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra audit instruction from a file.",
    )
    audit_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the audit agent config.",
    )
    _add_agent_runtime_args(audit_parser)
    audit_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing audit.json.",
    )
    audit_parser.add_argument(
        "--strict",
        action="store_true",
        help="Use stricter audit criteria.",
    )
    audit_parser.add_argument(
        "--focus",
        action="append",
        default=[],
        choices=("canon", "state", "timeline", "style", "plot", "character_voice", "premature_reveal"),
        help="Audit focus area. Can be provided multiple times.",
    )
    audit_parser.add_argument(
        "--audited-file",
        default="polished.md",
        choices=("draft.md", "polished.md"),
        help="Chapter file to audit. Defaults to polished.md.",
    )
    audit_parser.add_argument(
        "--use-search-context",
        action="store_true",
        help="Add explainable search results to the audit prompt.",
    )
    audit_parser.add_argument(
        "--vector-context",
        choices=("auto", "on", "off"),
        default="auto",
        help="Embedding semantic context mode for agent memory retrieval. Defaults to auto.",
    )
    audit_parser.add_argument(
        "--use-vector-context",
        action="store_true",
        help="Compatibility alias for --vector-context on.",
    )
    audit_parser.add_argument(
        "--no-audit-recall",
        action="store_true",
        help="Disable bounded audit context recall for this run.",
    )

    revise_parser = subparsers.add_parser("revise-chapter", help="Revise a chapter from instructions or audit")
    revise_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    revise_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    revise_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra revision instruction for this chapter.",
    )
    revise_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra revision instruction from a file.",
    )
    revise_parser.add_argument(
        "--from-audit",
        action="store_true",
        help="Use audit.json issues as the main revision target.",
    )
    revise_parser.add_argument(
        "--target",
        default="polished",
        choices=("draft", "polished"),
        help="Source and output version family to revise. Defaults to polished.",
    )
    revise_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to writer/polish agent config based on target.",
    )
    _add_agent_runtime_args(revise_parser)
    revise_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the selected revision version file if it already exists.",
    )
    revise_parser.add_argument(
        "--save-as-version",
        action="store_true",
        default=True,
        help="Save as draft.vN.md or polished.vN.md. This is the default.",
    )
    revise_parser.add_argument(
        "--max-rounds",
        type=int,
        default=1,
        help="Maximum revision loop rounds. Defaults to 1.",
    )
    revise_parser.add_argument(
        "--confirm-loop",
        action="store_true",
        help="Explicitly allow more than one revision round.",
    )
    _add_search_context_args(revise_parser)

    propose_state_parser = subparsers.add_parser(
        "propose-state-update",
        help="Generate a state and timeline update proposal",
    )
    propose_state_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    propose_state_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    propose_state_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra state update instruction for this chapter.",
    )
    propose_state_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra state update instruction from a file.",
    )
    propose_state_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the state update agent config.",
    )
    _add_agent_runtime_args(propose_state_parser)
    propose_state_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing state_update_proposal.json.",
    )
    propose_state_parser.add_argument(
        "--allow-unresolved-audit",
        action="store_true",
        help="Allow proposal generation when audit has medium, high, or critical issues.",
    )
    _add_search_context_args(propose_state_parser)

    apply_state_parser = subparsers.add_parser(
        "apply-state-update",
        help="Apply a chapter state update proposal",
    )
    apply_state_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    apply_state_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )

    accept_parser = subparsers.add_parser(
        "accept-chapter",
        help="Accept a chapter and apply state/timeline updates",
    )
    accept_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    accept_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    accept_parser.add_argument(
        "--allow-issues",
        action="store_true",
        help="Allow acceptance when audit has medium, high, or critical issues.",
    )
    accept_parser.add_argument(
        "--propose",
        action="store_true",
        help="Generate state_update_proposal.json first if it is missing.",
    )
    accept_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra state update instruction when used with --propose.",
    )
    accept_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra state update instruction from a file when used with --propose.",
    )
    accept_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for --propose and canon drift proposal checks.",
    )
    _add_agent_runtime_args(accept_parser)
    accept_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing state_update_proposal.json when used with --propose.",
    )
    _add_search_context_args(accept_parser, default_enabled=True)

    generate_parser = subparsers.add_parser(
        "generate-chapter",
        help="Run the chapter generation pipeline",
    )
    generate_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    generate_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    generate_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra instruction shared by planning, writing, polishing, and audit.",
    )
    generate_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra instruction from a file.",
    )
    generate_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for each pipeline step.",
    )
    _add_agent_runtime_args(generate_parser)
    generate_parser.add_argument(
        "--target-words",
        type=int,
        default=None,
        help="Optional target word count for writing.",
    )
    generate_parser.add_argument(
        "--style-note",
        default=None,
        help="Temporary style guidance for writing and polishing.",
    )
    generate_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite files generated by pipeline steps.",
    )
    generate_parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse already generated step outputs and continue the pipeline.",
    )
    generate_parser.add_argument(
        "--polish-mode",
        choices=("single-pass", "auto", "review-gate"),
        default=None,
        help="Finalization mode. Defaults to project polish.mode or single-pass.",
    )
    generate_parser.add_argument(
        "--skip-polish",
        action="store_true",
        help="Compatibility alias for --polish-mode single-pass.",
    )
    generate_parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Generate through polished.md but skip audit.json.",
    )
    generate_parser.add_argument(
        "--stop-after",
        choices=("plan", "write", "polish", "audit"),
        default=None,
        help="Stop after the selected pipeline step.",
    )
    _add_search_context_args(generate_parser, default_enabled=True)

    export_parser = subparsers.add_parser("export", help="Export project content")
    export_subparsers = export_parser.add_subparsers(dest="export_command", required=True)
    export_markdown_parser = export_subparsers.add_parser("markdown", help="Export chapters as Markdown")
    export_markdown_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    export_markdown_parser.add_argument(
        "--chapters",
        default=None,
        help="Comma-separated chapter numbers, for example 1,2,3.",
    )
    export_markdown_parser.add_argument(
        "--from",
        dest="from_chapter",
        type=int,
        default=None,
        help="First chapter number to export.",
    )
    export_markdown_parser.add_argument(
        "--to",
        dest="to_chapter",
        type=int,
        default=None,
        help="Last chapter number to export.",
    )
    export_markdown_parser.add_argument(
        "--include-unaccepted",
        action="store_true",
        help="Include chapters whose polished.md is not marked accepted.",
    )
    export_markdown_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output Markdown path. Defaults to exports/novel.md.",
    )
    export_markdown_parser.add_argument(
        "--title",
        default=None,
        help="Override exported work title.",
    )
    export_markdown_parser.add_argument(
        "--toc",
        action="store_true",
        help="Include a Markdown table of contents.",
    )
    export_markdown_parser.add_argument(
        "--volume-title",
        default=None,
        help="Optional volume title inserted before chapters and inside the table of contents.",
    )
    export_markdown_parser.add_argument(
        "--chapter-number-style",
        choices=("chinese", "arabic", "chapter", "plain"),
        default="chinese",
        help="Chapter heading style. Defaults to chinese, for example 第一章.",
    )
    export_markdown_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing output Markdown.",
    )
    export_docx_parser = export_subparsers.add_parser("docx", help="Export chapters as Word DOCX")
    export_docx_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    export_docx_parser.add_argument(
        "--chapters",
        default=None,
        help="Comma-separated chapter numbers, for example 1,2,3.",
    )
    export_docx_parser.add_argument(
        "--from",
        dest="from_chapter",
        type=int,
        default=None,
        help="First chapter number to export.",
    )
    export_docx_parser.add_argument(
        "--to",
        dest="to_chapter",
        type=int,
        default=None,
        help="Last chapter number to export.",
    )
    export_docx_parser.add_argument(
        "--include-unaccepted",
        action="store_true",
        help="Include chapters whose polished.md is not marked accepted.",
    )
    export_docx_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output DOCX path. Defaults to exports/novel.docx.",
    )
    export_docx_parser.add_argument(
        "--title",
        default=None,
        help="Override exported work title.",
    )
    export_docx_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing output DOCX.",
    )

    web_parser = subparsers.add_parser("web", help="Run the local Web UI")
    web_parser.add_argument(
        "--path",
        default=".",
        help="Novel project directory whose project.yaml may define web.default_port. Defaults to current directory.",
    )
    web_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind. Defaults to 127.0.0.1.",
    )
    web_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind. Overrides project.yaml web.default_port. Defaults to 8765.",
    )
    web_open_group = web_parser.add_mutually_exclusive_group()
    web_open_group.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the Web UI URL in the default browser after the server starts.",
    )
    web_open_group.add_argument(
        "--no-open",
        dest="open_browser",
        action="store_false",
        help="Do not open a browser automatically. This is the default.",
    )
    web_parser.set_defaults(open_browser=False)

    _add_integration_args_recursive(parser)
    return parser


def _cmd_init(args: argparse.Namespace) -> int:
    options = InitOptions(
        title=args.title,
        root=Path(args.path),
        project_id=args.project_id,
        language=args.language,
        genre=args.genre,
    )
    try:
        result = init_workspace(options)
    except WorkspaceExistsError as exc:
        return _failure(args, str(exc), error_type="workspace_exists")
    setup_lines: list[str] = []
    open_web = False
    web_port: int | None = None
    if _should_run_init_guide(args):
        try:
            setup_lines, open_web, web_port = _run_init_setup_guide(result.root)
        except SetupGuideError as exc:
            return _failure(
                args,
                f"Workspace created at {result.root}, but initial setup failed: {exc}",
                error_type="setup_guide_error",
            )
    elif not getattr(args, "no_guide", False) and not _wants_json(args) and not _quiet(args):
        setup_lines.append("Skipped initial setup guide because this command is not running in an interactive terminal.")

    if open_web and web_port is not None:
        from novel.web_server import WebServerError, run_web_server

        url = f"http://127.0.0.1:{web_port}"
        print(f"Created novel workspace: {result.root}")
        for line in setup_lines:
            print(line)
        print(f"Web UI: {url}")
        webbrowser.open(url)
        try:
            run_web_server(host="127.0.0.1", port=web_port)
        except WebServerError as exc:
            return _failure(args, str(exc), error_type="web_error")
        return 0

    return _success(
        args,
        {
            "command": "init",
            "root": str(result.root),
            "project_file": str(result.root / "project.yaml"),
            "setup_guide_ran": bool(setup_lines) and not setup_lines[0].startswith("Skipped"),
            "setup_messages": setup_lines,
            "web_port": web_port,
        },
        [
            f"Created novel workspace: {result.root}",
            f"Project file: {result.root / 'project.yaml'}",
            *setup_lines,
        ],
    )


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate_project(Path(args.path))
    payload = _validation_payload(report)
    if _wants_json(args):
        _print_json({"ok": report.ok, "command": "validate", "validation": payload})
        return 0 if report.ok else 1
    if _quiet(args):
        return 0 if report.ok else 1
    for message in report.messages:
        path = message.path
        try:
            path = path.relative_to(report.root)
        except ValueError:
            pass
        print(f"{message.level}: {path}: {message.message}")

    if report.ok:
        print(f"Validation passed: {len(report.warnings)} warning(s)")
        return 0

    print(
        f"Validation failed: {len(report.errors)} error(s), "
        f"{len(report.warnings)} warning(s)",
        file=sys.stderr,
    )
    return 1


def _cmd_migrate(args: argparse.Namespace) -> int:
    try:
        with _command_lock(args, Path(args.path), "migrate", enabled=not args.dry_run):
            result = migrate_project(Path(args.path), dry_run=args.dry_run)
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except MigrationError as exc:
        return _failure(args, str(exc), error_type="migration_error")
    payload = {
        "command": "migrate",
        "root": str(result.root),
        "changed": result.changed,
        "from_version": result.from_version,
        "to_version": result.to_version,
        "updated_files": [str(path) for path in result.updated_files],
        "dry_run": args.dry_run,
    }
    lines = [
        f"Schema version: {result.from_version or 'missing'} -> {result.to_version}",
        "Migration required." if result.changed else "Already up to date.",
    ]
    if result.changed:
        action = "Would update" if args.dry_run else "Updated"
        lines.extend(f"{action}: {path}" for path in result.updated_files)
    return _success(args, payload, lines)


def _cmd_schema(args: argparse.Namespace) -> int:
    if args.schema_command == "export":
        paths = export_json_schemas(args.output)
        return _success(
            args,
            {
                "command": "schema export",
                "output": str(args.output),
                "schema_count": len(paths),
                "files": [str(path) for path in paths],
            },
            [f"Wrote {len(paths)} JSON Schema file(s) to {args.output}"],
        )
    return _failure(args, f"unknown schema command: {args.schema_command}", code=2)


def _cmd_completion(args: argparse.Namespace) -> int:
    script = completion_script(args.shell)
    if _wants_json(args):
        _print_json({"ok": True, "command": "completion", "shell": args.shell, "script": script})
    elif not _quiet(args):
        print(script, end="" if script.endswith("\n") else "\n")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    result = run_doctor(Path(args.path))
    payload = {"command": "doctor", **result}
    lines = format_doctor_result(result)
    if result["error_count"]:
        if _wants_json(args):
            _print_json({"ok": False, **payload})
            return 1
        if not _quiet(args):
            for line in lines:
                print(line)
        return 1
    return _success(args, payload, lines)


def _cmd_index(args: argparse.Namespace) -> int:
    if args.index_command == "rebuild":
        try:
            with _command_lock(args, Path(args.path), "index rebuild"):
                result = rebuild_search_index(
                    Path(args.path),
                    embedding_provider_name=args.embedding_provider,
                    embedding_config_path=args.embedding_config,
                    with_embeddings=args.with_embeddings,
                )
        except ProjectLockError as exc:
            return _failure(args, str(exc), error_type="project_locked")
        except SearchError as exc:
            return _failure(args, str(exc), error_type="search_error")
        return _success(
            args,
            {
                "command": "index rebuild",
                "index_path": str(result.index_path),
                "sqlite_path": str(result.sqlite_path),
                "manifest_path": str(result.manifest_path),
                "document_count": result.document_count,
                "embedding_document_count": result.embedding_document_count,
                "with_embeddings": result.with_embeddings,
            },
            [
                f"Rebuilt search index: {result.index_path}",
                f"Documents: {result.document_count}",
                f"Embedding vectors: {result.embedding_document_count}",
            ],
        )
    if args.index_command == "refresh":
        try:
            with _command_lock(args, Path(args.path), "index refresh"):
                result = refresh_search_index(
                    Path(args.path),
                    embedding_provider_name=args.embedding_provider,
                    embedding_config_path=args.embedding_config,
                    with_embeddings=args.with_embeddings,
                )
        except ProjectLockError as exc:
            return _failure(args, str(exc), error_type="project_locked")
        except SearchError as exc:
            return _failure(args, str(exc), error_type="search_error")
        return _success(
            args,
            {
                "command": "index refresh",
                "index_path": str(result.index_path),
                "sqlite_path": str(result.sqlite_path),
                "manifest_path": str(result.manifest_path),
                "document_count": result.document_count,
                "refreshed_count": result.refreshed_count,
                "deleted_count": result.deleted_count,
                "embedding_document_count": result.embedding_document_count,
                "with_embeddings": result.with_embeddings,
            },
            [
                f"Refreshed search index: {result.index_path}",
                f"Documents: {result.document_count}",
                f"Changed: {result.refreshed_count}; deleted: {result.deleted_count}",
                f"Embedding vectors: {result.embedding_document_count}",
            ],
        )
    if args.index_command == "status":
        status = search_index_status(
            Path(args.path),
            embedding_provider_name=args.embedding_provider,
            embedding_config_path=args.embedding_config,
        )
        return _success(
            args,
            {"command": "index status", **status.as_dict()},
            [
                f"FTS: {status.fts_status}",
                f"Embedding: {status.embedding_status}",
                status.message,
            ],
        )
    return _failure(args, f"unknown index command: {args.index_command}", code=2)


def _cmd_search(args: argparse.Namespace) -> int:
    try:
        results = search_project(
            Path(args.path),
            args.query,
            search_type=args.type,
            limit=args.limit,
            chapter_number=args.chapter,
            highlight=args.highlight,
            use_vector=args.use_vector,
            embedding_provider_name=args.embedding_provider,
            embedding_config_path=args.embedding_config,
        )
    except SearchError as exc:
        return _failure(args, str(exc), error_type="search_error")
    if args.json:
        print(
            json.dumps(
                [
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
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not results:
        print("No results.")
        return 0
    for index, result in enumerate(results, start=1):
        terms = ", ".join(result.matched_terms) if result.matched_terms else "none"
        print(f"{index}. [{result.type}] {result.title}")
        print(f"   path: {result.path}")
        print(f"   score: {result.score}; matched_terms: {terms}")
        print(f"   excerpt: {result.highlighted_excerpt if args.highlight else result.excerpt}")
    return 0


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


def _cmd_setting_change(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        with _command_lock(args, root, f"setting-change {args.setting_change_command}"):
            if args.setting_change_command == "suggest":
                result = suggest_setting_change(
                    root,
                    args.request,
                    provider_name=args.provider,
                    stage=cast(MemoryChangeStage, args.stage),
                    session_id=args.session_id,
                    chapter_number=args.chapter,
                    audit_issue_ids=list(args.audit_issue_id or []),
                )
                impact = result.proposal.impact
                payload: dict[str, object] = {
                    "command": "setting-change suggest",
                    "repair_id": result.proposal.repair_id,
                    "proposal_path": str(result.proposal_path),
                    "markdown_path": str(result.markdown_path),
                    "target_files": result.proposal.target_files,
                    "domains": result.proposal.domains,
                    "operation_count": len(result.proposal.operations),
                    "confidence": result.proposal.confidence,
                    "impact": impact.model_dump(mode="json") if impact else None,
                    "followup_actions": [
                        action.model_dump(mode="json") for action in result.proposal.followup_actions
                    ],
                    "management_events": _management_event_payload(root),
                }
                affected = ", ".join(str(number) for number in impact.affected_chapters) if impact else ""
                return _success(
                    args,
                    payload,
                    [
                        f"Setting change proposal: {result.proposal_path}",
                        f"Targets: {', '.join(result.proposal.target_files) or 'none'}",
                        f"Domains: {', '.join(result.proposal.domains) or 'none'}",
                        f"Operations: {len(result.proposal.operations)}",
                        f"Affected chapters: {affected or 'none'}",
                        *_management_event_lines(root),
                    ],
                )
            apply_result = apply_memory_repair(root, _resolve_memory_repair_proposal_arg(args.proposal))
            payload = {
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


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        status = get_project_status(Path(args.path))
    except ProjectReadError as exc:
        return _failure(args, str(exc), error_type="project_read_error")
    return _success(
        args,
        {"command": "status", "status": _status_payload(status)},
        [format_status(status, Path(args.path))],
    )


def _cmd_usage(args: argparse.Namespace) -> int:
    try:
        summary = summarize_provider_usage(Path(args.path))
    except UsageError as exc:
        return _failure(args, str(exc), error_type="usage_error")
    payload: dict[str, object] = {"command": "usage", "usage": summary.as_dict()}
    lines = _format_usage_summary(summary.as_dict())
    return _success(args, payload, lines)


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        if args.target == "characters":
            output = format_characters(Path(args.path))
        elif args.target == "timeline":
            output = format_timeline(Path(args.path))
        elif args.target == "canon":
            output = format_canon(Path(args.path))
        else:
            output = format_state(Path(args.path))
    except ProjectReadError as exc:
        return _failure(args, str(exc), error_type="project_read_error")
    return _success(
        args,
        {"command": "show", "target": args.target, "output": output},
        [output],
    )


def _cmd_inspire(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        if args.dry_run_provider:
            _print_dry_run_provider(
                root,
                args.agent_config,
                args.provider,
                args.model,
                (("inspiration", ()),),
            )
            return 0
        source_text, source_type = read_inspiration_input(args.text, args.input)
        provider = load_inspiration_provider(
            root,
            args.provider,
            agent_config_path=args.agent_config,
            model_name=args.model,
        )
        with _command_lock(args, root, "inspire"):
            result = run_inspiration_agent(
                InspirationOptions(
                    root=root,
                    source_text=source_text,
                    source_type=source_type,
                    write_json=args.json,
                    overwrite=args.overwrite,
                    use_search_context=args.use_search_context,
                    use_vector_context=_vector_context_mode_from_args(args),
                ),
                provider,
            )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except InspirationError as exc:
        return _failure(args, str(exc), error_type="inspiration_error")
    except Exception as exc:
        return _failure(args, f"inspiration generation failed: {exc}", error_type="inspiration_error")

    lines = [f"Wrote inspiration markdown: {result.markdown_path}"]
    if result.json_path:
        lines.append(f"Wrote inspiration JSON: {result.json_path}")
    return _success(
        args,
        {
            "command": "inspire",
            "markdown_path": str(result.markdown_path),
            "json_path": str(result.json_path) if result.json_path else None,
        },
        lines,
    )


def _cmd_canon(args: argparse.Namespace) -> int:
    root = Path(args.path)
    if args.canon_command == "suggest":
        try:
            if args.dry_run_provider:
                _print_dry_run_provider(
                    root,
                    args.agent_config,
                    args.provider,
                    args.model,
                    (("canon", ("inspiration",)),),
                )
                return 0
            provider = load_canon_provider(
                root,
                args.provider,
                agent_config_path=args.agent_config,
                model_name=args.model,
            )
            with _command_lock(args, root, "canon suggest", enabled=args.output is not None):
                suggest_result = suggest_canon(
                    CanonSuggestOptions(
                        root=root,
                        output_path=args.output,
                        use_search_context=args.use_search_context,
                        use_vector_context=_vector_context_mode_from_args(args),
                    ),
                    provider,
                )
        except ProjectLockError as exc:
            return _failure(args, str(exc), error_type="project_locked")
        except CanonError as exc:
            return _failure(args, str(exc), error_type="canon_error")
        except Exception as exc:
            return _failure(args, f"canon suggestion failed: {exc}", error_type="canon_error")

        if _wants_json(args):
            _print_json(
                {
                    "ok": True,
                    "command": "canon suggest",
                    "output_path": str(suggest_result.output_path) if suggest_result.output_path else None,
                    "proposal": json.loads(suggest_result.proposal_json),
                }
            )
            return 0
        if _quiet(args):
            return 0
        if suggest_result.output_path:
            print(f"Wrote canon proposal: {suggest_result.output_path}")
        else:
            print(suggest_result.proposal_json, end="")
        return 0

    if args.canon_command == "apply":
        try:
            with _command_lock(args, root, "canon apply"):
                apply_result = apply_canon_proposal(root, args.proposal_file)
        except ProjectLockError as exc:
            return _failure(args, str(exc), error_type="project_locked")
        except CanonError as exc:
            return _failure(args, str(exc), error_type="canon_error")
        if _wants_json(args):
            _print_json(
                {
                    "ok": apply_result.validation_report.ok,
                    "command": "canon apply",
                    "validation": _validation_payload(apply_result.validation_report),
                    "apply_log_path": str(apply_result.apply_log_path),
                    "proposal_snapshot_path": str(apply_result.proposal_snapshot_path),
                }
            )
            return 0 if apply_result.validation_report.ok else 1
        if not _quiet(args):
            print(format_canon_validation_report(apply_result.validation_report))
            print(f"Canon apply log: {apply_result.apply_log_path}")
            print(f"Canon proposal snapshot: {apply_result.proposal_snapshot_path}")
        return 0 if apply_result.validation_report.ok else 1

    if args.canon_command == "validate":
        report = validate_canon(root)
        if _wants_json(args):
            _print_json({"ok": report.ok, "command": "canon validate", "validation": _validation_payload(report)})
            return 0 if report.ok else 1
        if not _quiet(args):
            print(format_canon_validation_report(report))
        return 0 if report.ok else 1

    if args.canon_command == "show":
        try:
            output = format_canon(root)
        except ProjectReadError as exc:
            return _failure(args, str(exc), error_type="project_read_error")
        return _success(args, {"command": "canon show", "output": output}, [output])
    return _failure(args, f"unknown canon command: {args.canon_command}", code=2)


def _cmd_plan_chapter(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        if args.dry_run_provider:
            _print_dry_run_provider(
                root,
                args.agent_config,
                args.provider,
                args.model,
                (("plot", ()),),
            )
            return 0
        instruction = read_planning_instruction(args.instruction, args.input)
        provider = load_planning_provider(
            root,
            args.provider,
            chapter_number=args.chapter_number,
            agent_config_path=args.agent_config,
            model_name=args.model,
        )
        with _command_lock(args, root, "plan-chapter"):
            result = plan_chapter(
                ChapterPlanningOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    instruction=instruction,
                    force=args.force,
                    use_search_context=args.use_search_context,
                    use_vector_context=_vector_context_mode_from_args(args),
                ),
                provider,
            )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except PlanningError as exc:
        return _failure(args, str(exc), error_type="planning_error")
    except Exception as exc:
        return _failure(args, f"chapter planning failed: {exc}", error_type="planning_error")

    payload = {
        "command": "plan-chapter",
        "chapter_number": result.plan.chapter_number,
        "plan_json_path": str(result.plan_json_path),
        "plan_markdown_path": str(result.plan_markdown_path),
        "validation": _validation_payload(result.validation_report),
    }
    if _wants_json(args):
        _print_json({"ok": result.validation_report.ok, **payload})
        return 0 if result.validation_report.ok else 1
    if not _quiet(args):
        print(f"Wrote chapter plan JSON: {result.plan_json_path}")
        print(f"Wrote chapter plan Markdown: {result.plan_markdown_path}")
    if not result.validation_report.ok:
        if not _quiet(args):
            print(
                f"Validation failed after planning: {len(result.validation_report.errors)} error(s), "
                f"{len(result.validation_report.warnings)} warning(s)",
                file=sys.stderr,
            )
        return 1
    if not _quiet(args):
        print(f"Validation passed: {len(result.validation_report.warnings)} warning(s)")
    return 0


def _cmd_write_chapter(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        if args.dry_run_provider:
            _print_dry_run_provider(
                root,
                args.agent_config,
                args.provider,
                args.model,
                (("writer", ()),),
            )
            return 0
        instruction = read_drafting_instruction(args.instruction, args.input)
        provider = load_drafting_provider(
            root,
            args.provider,
            agent_config_path=args.agent_config,
            model_name=args.model,
        )
        with _command_lock(args, root, "write-chapter"):
            result = write_chapter_draft(
                ChapterDraftingOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    instruction=instruction,
                    force=args.force,
                    target_words=args.target_words,
                    style_note=args.style_note,
                    use_search_context=args.use_search_context,
                    use_vector_context=_vector_context_mode_from_args(args),
                ),
                provider,
            )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except DraftingError as exc:
        return _failure(args, str(exc), error_type="drafting_error")
    except Exception as exc:
        return _failure(args, f"chapter drafting failed: {exc}", error_type="drafting_error")

    lines = [*(f"warning: {warning}" for warning in result.warnings), f"Wrote chapter draft: {result.draft_path}"]
    return _success(
        args,
        {
            "command": "write-chapter",
            "draft_path": str(result.draft_path),
            "warnings": list(result.warnings),
        },
        lines,
    )


def _cmd_polish_chapter(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        if args.dry_run_provider:
            _print_dry_run_provider(
                root,
                args.agent_config,
                args.provider,
                args.model,
                (("polish", ()),),
            )
            return 0
        instruction = read_polishing_instruction(args.instruction, args.input)
        edit_mode = resolve_edit_mode(
            light_edit=args.light_edit,
            deep_edit=args.deep_edit,
        )
        provider = load_polishing_provider(
            root,
            args.provider,
            agent_config_path=args.agent_config,
            model_name=args.model,
        )
        with _command_lock(args, root, "polish-chapter"):
            result = polish_chapter(
                ChapterPolishingOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    instruction=instruction,
                    force=args.force,
                    style_note=args.style_note,
                    keep_length=args.keep_length,
                    edit_mode=edit_mode,
                    use_search_context=args.use_search_context,
                    use_vector_context=_vector_context_mode_from_args(args),
                ),
                provider,
            )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except PolishingError as exc:
        return _failure(args, str(exc), error_type="polishing_error")
    except Exception as exc:
        return _failure(args, f"chapter polishing failed: {exc}", error_type="polishing_error")

    lines = [*(f"warning: {warning}" for warning in result.warnings), f"Wrote polished chapter: {result.polished_path}"]
    return _success(
        args,
        {
            "command": "polish-chapter",
            "polished_path": str(result.polished_path),
            "warnings": list(result.warnings),
        },
        lines,
    )


def _cmd_audit_chapter(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        if args.dry_run_provider:
            _print_dry_run_provider(
                root,
                args.agent_config,
                args.provider,
                args.model,
                (("audit", ()),),
            )
            return 0
        instruction = read_audit_instruction(args.instruction, args.input)
        provider = load_audit_provider(
            root,
            args.provider,
            chapter_number=args.chapter_number,
            audited_file=args.audited_file,
            agent_config_path=args.agent_config,
            model_name=args.model,
        )
        with _command_lock(args, root, "audit-chapter"):
            result = audit_chapter(
                ChapterAuditOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    instruction=instruction,
                    force=args.force,
                    strict=args.strict,
                    focus=tuple(args.focus),
                    audited_file=args.audited_file,
                    use_search_context=args.use_search_context,
                    use_vector_context=_vector_context_mode_from_args(args),
                    max_recall_rounds=0 if args.no_audit_recall else None,
                ),
                provider,
            )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except AuditError as exc:
        return _failure(args, str(exc), error_type="audit_error")
    except Exception as exc:
        return _failure(args, f"chapter audit failed: {exc}", error_type="audit_error")

    lines = [
        *(f"warning: {warning}" for warning in result.warnings),
        f"Wrote chapter audit: {result.audit_path}",
        f"Audit status: {result.report.overall_status}",
        f"Issues: {len(result.report.issues)}",
        f"Deterministic issues: {len(result.deterministic_findings)}"
        + (
            f" (highest: {result.deterministic_highest_severity})"
            if result.deterministic_highest_severity
            else ""
        ),
        *_audit_issue_lines(result.report),
    ]
    return _success(
        args,
        {
            "command": "audit-chapter",
            "audit_path": str(result.audit_path),
            "overall_status": result.report.overall_status,
            "issue_count": len(result.report.issues),
            "issues": [issue.model_dump(mode="json") for issue in result.report.issues],
            "deterministic_issue_count": len(result.deterministic_findings),
            "deterministic_highest_severity": result.deterministic_highest_severity,
            "warnings": list(result.warnings),
        },
        lines,
    )


def _cmd_revise_chapter(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        if args.dry_run_provider:
            agent = "writer" if args.target == "draft" else "polish"
            _print_dry_run_provider(
                root,
                args.agent_config,
                args.provider,
                args.model,
                ((agent, ()),),
            )
            return 0
        instruction = read_revision_instruction(args.instruction, args.input)
        provider = load_revision_provider(
            root,
            args.provider,
            target=args.target,
            agent_config_path=args.agent_config,
            model_name=args.model,
        )
        base_options = ChapterRevisionOptions(
            root=root,
            chapter_number=args.chapter_number,
            instruction=instruction,
            from_audit=args.from_audit,
            target=args.target,
            force=args.force,
            save_as_version=args.save_as_version,
            use_search_context=args.use_search_context,
            use_vector_context=_vector_context_mode_from_args(args),
        )
        if args.max_rounds > 1:
            with _command_lock(args, root, "revise-chapter"):
                loop_result = revise_chapter_loop(
                    RevisionLoopOptions(
                        base_options=base_options,
                        max_rounds=args.max_rounds,
                        confirm_loop=args.confirm_loop,
                    ),
                    provider,
                    provider_name=args.provider,
                )
                result = loop_result.results[-1]
                revision_loop_log_path = loop_result.run_log_path
        else:
            with _command_lock(args, root, "revise-chapter"):
                result = revise_chapter(
                    base_options,
                    provider,
                    provider_name=args.provider,
                )
                revision_loop_log_path = None
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except RevisionError as exc:
        return _failure(args, str(exc), error_type="revision_error")
    except Exception as exc:
        return _failure(args, f"chapter revision failed: {exc}", error_type="revision_error")

    lines = [
        *(f"warning: {warning}" for warning in result.warnings),
        f"Wrote chapter revision: {result.output_path}",
        f"Updated revision log: {result.revision_log_path}",
        *( [f"Wrote revision loop log: {revision_loop_log_path}"] if revision_loop_log_path else [] ),
    ]
    return _success(
        args,
        {
            "command": "revise-chapter",
            "output_path": str(result.output_path),
            "revision_log_path": str(result.revision_log_path),
            "revision_loop_log_path": str(revision_loop_log_path) if revision_loop_log_path else None,
            "revision_id": result.record.id,
            "warnings": list(result.warnings),
        },
        lines,
    )


def _cmd_propose_state_update(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        if args.dry_run_provider:
            _print_dry_run_provider(
                root,
                args.agent_config,
                args.provider,
                args.model,
                (("state_update", ("audit",)),),
            )
            return 0
        instruction = read_state_update_instruction(args.instruction, args.input)
        provider = load_state_update_provider(
            root,
            args.provider,
            chapter_number=args.chapter_number,
            agent_config_path=args.agent_config,
            model_name=args.model,
        )
        with _command_lock(args, root, "propose-state-update"):
            result = propose_state_update(
                StateUpdateProposeOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    instruction=instruction,
                    force=args.force,
                    allow_unresolved_audit=args.allow_unresolved_audit,
                    use_search_context=args.use_search_context,
                    use_vector_context=_vector_context_mode_from_args(args),
                ),
                provider,
            )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except StateUpdateError as exc:
        return _failure(args, str(exc), error_type="state_update_error")
    except Exception as exc:
        return _failure(args, f"state update proposal failed: {exc}", error_type="state_update_error")

    lines = [
        *(f"warning: {warning}" for warning in result.warnings),
        f"Wrote state update proposal: {result.proposal_path}",
        f"State changes: {len(result.proposal.state_changes)}",
        f"Timeline events: {len(result.proposal.timeline_events)}",
    ]
    return _success(
        args,
        {
            "command": "propose-state-update",
            "proposal_path": str(result.proposal_path),
            "state_change_count": len(result.proposal.state_changes),
            "timeline_event_count": len(result.proposal.timeline_events),
            "warnings": list(result.warnings),
        },
        lines,
    )


def _cmd_apply_state_update(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        with _command_lock(args, root, "apply-state-update"):
            result = apply_state_update(
                StateUpdateApplyOptions(root=root, chapter_number=args.chapter_number)
            )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except StateUpdateError as exc:
        return _failure(args, str(exc), error_type="state_update_error")
    except Exception as exc:
        return _failure(args, f"state update application failed: {exc}", error_type="state_update_error")

    return _success(
        args,
        {
            "command": "apply-state-update",
            "state_backup_path": str(result.state_backup_path),
            "timeline_backup_path": str(result.timeline_backup_path),
            "apply_log_path": str(result.apply_log_path),
            "state_path": str(result.state_path),
            "timeline_path": str(result.timeline_path),
        },
        [
            f"Backed up current state: {result.state_backup_path}",
            f"Backed up timeline: {result.timeline_backup_path}",
            f"Updated current state: {result.state_path}",
            f"Updated timeline: {result.timeline_path}",
            f"Wrote apply log: {result.apply_log_path}",
        ],
    )


def _cmd_accept_chapter(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        if args.dry_run_provider:
            _print_dry_run_provider(
                root,
                args.agent_config,
                args.provider,
                args.model,
                (("state_update", ("audit",)), ("chapter_memory", ("state_update", "audit"))),
            )
            return 0
        instruction = read_state_update_instruction(args.instruction, args.input)
        provider = (
            load_state_update_provider(
                root,
                args.provider,
                chapter_number=args.chapter_number,
                agent_config_path=args.agent_config,
                model_name=args.model,
            )
            if args.propose
            else None
        )
        with _command_lock(args, root, "accept-chapter"):
            result = accept_chapter(
                AcceptChapterOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    allow_issues=args.allow_issues,
                    propose=args.propose,
                    instruction=instruction,
                    force_proposal=args.force,
                    use_search_context=args.use_search_context,
                    use_vector_context=_vector_context_mode_from_args(args),
                    canon_provider_name=args.provider,
                    chapter_memory_provider_name=args.provider,
                    agent_config_path=args.agent_config,
                    model_name=args.model,
                ),
                provider,
            )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except StateUpdateError as exc:
        return _failure(args, str(exc), error_type="state_update_error")
    except Exception as exc:
        return _failure(args, f"chapter acceptance failed: {exc}", error_type="state_update_error")

    lines = []
    if result.proposal_result:
        lines.append(f"Wrote state update proposal: {result.proposal_result.proposal_path}")
    if result.canon_drift_proposal_path:
        lines.append(f"Wrote canon drift proposal: {result.canon_drift_proposal_path}")
    if result.chapter_memory_result:
        lines.append(f"Wrote chapter memory: {result.chapter_memory_result.memory_path}")
    lines.extend(f"warning: {warning}" for warning in result.warnings)
    lines.extend(
        [
            f"Accepted chapter: {result.accepted_path}",
            f"Updated chapter metadata: {result.metadata_path}",
            f"Updated current state: {result.apply_result.state_path}",
            f"Updated timeline: {result.apply_result.timeline_path}",
        ]
    )
    return _success(
        args,
        {
            "command": "accept-chapter",
            "accepted_path": str(result.accepted_path),
            "metadata_path": str(result.metadata_path),
            "state_path": str(result.apply_result.state_path),
            "timeline_path": str(result.apply_result.timeline_path),
            "proposal_path": str(result.proposal_result.proposal_path)
            if result.proposal_result
            else None,
            "canon_drift_proposal_path": str(result.canon_drift_proposal_path)
            if result.canon_drift_proposal_path
            else None,
            "chapter_memory_path": str(result.chapter_memory_result.memory_path)
            if result.chapter_memory_result
            else None,
            "warnings": list(result.warnings),
        },
        lines,
    )


def _cmd_generate_chapter(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        if args.dry_run_provider:
            _print_dry_run_provider(
                root,
                args.agent_config,
                args.provider,
                args.model,
                (
                    ("plot", ()),
                    ("writer", ()),
                    ("polish", ()),
                    ("audit", ()),
                ),
            )
            return 0
        instruction = read_workflow_instruction(args.instruction, args.input)
        with _command_lock(args, root, "generate-chapter"):
            result = generate_chapter(
                GenerateChapterOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    instruction=instruction,
                    force=args.force,
                    resume=args.resume,
                    provider_name=args.provider,
                    agent_config_path=args.agent_config,
                    model_name=args.model,
                    target_words=args.target_words,
            style_note=args.style_note,
            polish_mode=_polish_mode_from_arg(args.polish_mode),
            skip_polish=args.skip_polish,
            skip_audit=args.skip_audit,
            stop_after=args.stop_after,
            use_search_context=args.use_search_context,
            use_vector_context=_vector_context_mode_from_args(args),
        )
    )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except WorkflowError as exc:
        return _failure(args, str(exc), error_type="workflow_error")
    except Exception as exc:
        return _failure(args, f"chapter generation failed: {exc}", error_type="workflow_error")

    lines = [result.message, f"Run log: {result.run_log_path}"]
    lines.extend(f"{step.step_id} {step.agent}: {step.status}" for step in result.run_log.steps)
    return _success(
        args,
        {
            "command": "generate-chapter",
            "message": result.message,
            "run_log_path": str(result.run_log_path),
            "status": result.run_log.status,
            "steps": [
                {
                    "step_id": step.step_id,
                    "agent": step.agent,
                    "status": step.status,
                    "output_files": step.output_files,
                    "error": step.error,
                }
                for step in result.run_log.steps
            ],
        },
        lines,
    )


def _cmd_export(args: argparse.Namespace) -> int:
    root = Path(args.path)
    if args.export_command == "markdown":
        try:
            chapters = parse_chapter_selector(args.chapters)
            with _command_lock(args, root, "export markdown"):
                markdown_result = export_markdown(
                    MarkdownExportOptions(
                        root=root,
                        chapters=chapters,
                        from_chapter=args.from_chapter,
                        to_chapter=args.to_chapter,
                        include_unaccepted=args.include_unaccepted,
                        output_path=args.output,
                        title=args.title,
                        include_toc=args.toc,
                        volume_title=args.volume_title,
                        chapter_number_style=args.chapter_number_style,
                        force=args.force,
                    )
                )
        except ProjectLockError as exc:
            return _failure(args, str(exc), error_type="project_locked")
        except ExportError as exc:
            return _failure(args, str(exc), error_type="export_error")
        except Exception as exc:
            return _failure(args, f"markdown export failed: {exc}", error_type="export_error")

        lines = [
            *(f"warning: {warning}" for warning in markdown_result.warnings),
            f"Wrote Markdown export: {markdown_result.output_path}",
            f"Updated export manifest: {markdown_result.manifest_path}",
            f"Chapters: {', '.join(str(number) for number in markdown_result.exported_chapters)}",
        ]
        return _success(
            args,
            {
                "command": "export markdown",
                "output_path": str(markdown_result.output_path),
                "manifest_path": str(markdown_result.manifest_path),
                "chapters": list(markdown_result.exported_chapters),
                "warnings": list(markdown_result.warnings),
            },
            lines,
        )
    if args.export_command == "docx":
        try:
            chapters = parse_chapter_selector(args.chapters)
            with _command_lock(args, root, "export docx"):
                docx_result = export_docx(
                    DocxExportOptions(
                        root=root,
                        chapters=chapters,
                        from_chapter=args.from_chapter,
                        to_chapter=args.to_chapter,
                        include_unaccepted=args.include_unaccepted,
                        output_path=args.output,
                        title=args.title,
                        force=args.force,
                    )
                )
        except ProjectLockError as exc:
            return _failure(args, str(exc), error_type="project_locked")
        except ExportError as exc:
            return _failure(args, str(exc), error_type="export_error")
        except Exception as exc:
            return _failure(args, f"docx export failed: {exc}", error_type="export_error")

        lines = [
            *(f"warning: {warning}" for warning in docx_result.warnings),
            f"Wrote DOCX export: {docx_result.output_path}",
            f"Updated export manifest: {docx_result.manifest_path}",
            f"Chapters: {', '.join(str(number) for number in docx_result.exported_chapters)}",
        ]
        return _success(
            args,
            {
                "command": "export docx",
                "output_path": str(docx_result.output_path),
                "manifest_path": str(docx_result.manifest_path),
                "chapters": list(docx_result.exported_chapters),
                "warnings": list(docx_result.warnings),
            },
            lines,
        )
    return _failure(args, f"unknown export command: {args.export_command}", code=2)


def _cmd_web(args: argparse.Namespace) -> int:
    from novel.web_server import WebServerError, run_web_server

    try:
        port = _resolve_web_port(args.path, args.port)
        if args.open_browser:
            webbrowser.open(f"http://{args.host}:{port}")
        run_web_server(host=args.host, port=port)
    except Exception as exc:
        error_type = "web_error"
        if isinstance(exc, WebServerError):
            return _failure(args, str(exc), error_type=error_type)
        return _failure(args, f"Web UI 启动失败：{exc}", error_type=error_type)
    return 0


_COMMAND_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "init": _cmd_init,
    "validate": _cmd_validate,
    "migrate": _cmd_migrate,
    "schema": _cmd_schema,
    "completion": _cmd_completion,
    "doctor": _cmd_doctor,
    "index": _cmd_index,
    "search": _cmd_search,
    "memory-repair": _cmd_memory_repair,
    "setting-change": _cmd_setting_change,
    "chapter-memory": _cmd_chapter_memory,
    "ask": _cmd_ask,
    "session": _cmd_session,
    "status": _cmd_status,
    "usage": _cmd_usage,
    "show": _cmd_show,
    "inspire": _cmd_inspire,
    "canon": _cmd_canon,
    "plan-chapter": _cmd_plan_chapter,
    "write-chapter": _cmd_write_chapter,
    "polish-chapter": _cmd_polish_chapter,
    "audit-chapter": _cmd_audit_chapter,
    "revise-chapter": _cmd_revise_chapter,
    "propose-state-update": _cmd_propose_state_update,
    "apply-state-update": _cmd_apply_state_update,
    "accept-chapter": _cmd_accept_chapter,
    "generate-chapter": _cmd_generate_chapter,
    "export": _cmd_export,
    "web": _cmd_web,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_project_alias(args)

    handler = _COMMAND_HANDLERS.get(args.command)
    if handler is not None:
        return handler(args)

    parser.error(f"unknown command: {args.command}")
    return 2
