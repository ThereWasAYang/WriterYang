from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from novel.core.chapter_memory import (
    ChapterMemoryError,
    ChapterMemoryOptions,
    accepted_chapter_numbers,
    chapter_memory_path,
    generate_chapter_memory,
    load_chapter_memory_provider,
)
from novel.core.memory_repair import (
    MemoryRepairError,
    SettingChangeSuggestionResult,
    answer_setting_change_clarification,
    apply_memory_repair,
    suggest_memory_repair,
    suggest_setting_change_interactive,
)
from novel.core.locking import ProjectLockError
from novel.core.schemas import (
    MemoryChangeStage,
)
from novel.cli_shared import (
    _management_event_payload,
    _management_event_lines,
    _resolve_memory_repair_proposal_arg,
    _print_dry_run_provider,
    _wants_json,
    _quiet,
    _success,
    _failure,
    _print_json,
    _command_lock,
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
                    ("chapter_memory",),
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
                    ("chapter_memory",),
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
