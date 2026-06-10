from __future__ import annotations

import argparse
from pathlib import Path

from novel.core.memory_repair import (
    MemoryRepairError,
    apply_memory_repair,
    suggest_memory_repair,
)
from novel.core.session import (
    CreationSessionError,
    SessionStartOptions,
    start_session,
)
from novel.core.exporting import (
    MarkdownExportOptions,
    export_markdown,
)
from novel.core.inspection import (
    format_status,
    get_project_status,
)
from novel.core.locking import ProjectLockError
from novel.core.orchestrator import (
    OrchestratorError,
    OrchestratorOptions,
    decide_ask_intent,
    format_orchestrator_plan,
    handoff_rules_text,
    orchestrate,
)
from novel.cli_shared import (
    _vector_context_mode_from_args,
    _management_event_payload,
    _management_event_lines,
    _extract_chapter_from_text,
    _wants_json,
    _quiet,
    _success,
    _failure,
    _print_json,
    _command_lock,
    _status_payload,
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
