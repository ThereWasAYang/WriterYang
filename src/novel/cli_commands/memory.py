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
from novel.core.command_bus import DomainError
from novel.core.contracts import (
    MemoryRepairApplyCommand,
    MemoryRepairSuggestCommand,
    SettingChangeAnswerCommand,
    SettingChangeApplyCommand,
    SettingChangeSuggestCommand,
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
    _dispatch_cli_command,
)

def _cmd_memory_repair(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    try:
        if args.memory_repair_command == "suggest":
            payload = _dispatch_cli_command(
                args,
                root,
                MemoryRepairSuggestCommand(request=args.request, provider_name=args.provider),
            )
            proposal = payload.get("proposal")
            if not isinstance(proposal, dict):
                raise DomainError("internal_error", "command result is missing proposal")
            payload.update({"command": "memory-repair suggest", "management_events": _management_event_payload(root)})
            return _success(
                args,
                payload,
                [
                    f"Memory repair proposal: {payload.get('proposal_path')}",
                    f"Targets: {', '.join(str(item) for item in proposal.get('target_files', [])) or 'none'}",
                    f"Operations: {len(proposal.get('operations', []))}",
                    *_management_event_lines(root),
                ],
            )
        proposal_path = _resolve_memory_repair_proposal_arg(args.proposal).as_posix()
        payload = _dispatch_cli_command(
            args,
            root,
            MemoryRepairApplyCommand(proposal_path=proposal_path),
            confirmed=True,
        )
        payload.update({"command": "memory-repair apply", "management_events": _management_event_payload(root)})
        return _success(
            args,
            payload,
            [
                f"Applied memory repair: {payload.get('repair_id')}",
                f"Apply log: {payload.get('apply_log_path')}",
                *_management_event_lines(root),
            ],
        )
    except DomainError as exc:
        return _failure(args, exc.message, error_type=exc.code)

def _setting_change_suggestion_success(
    args: argparse.Namespace,
    root: Path,
    result: dict[str, object],
    *,
    command: str,
) -> int:
    if result.get("status") == "needs_clarification":
        clarification = result.get("clarification")
        if not isinstance(clarification, dict):
            return _failure(args, "missing setting change clarification result", error_type="memory_repair_error")
        clarification_id = str(clarification.get("clarification_id") or "")
        payload: dict[str, object] = {
            **result,
            "command": command,
            "status": "needs_clarification",
            "clarification_id": clarification_id,
            "questions": clarification.get("questions", []),
            "conversation_turns": clarification.get("conversation_turns", []),
            "clarification_path": str((root / "memory" / "repairs" / "clarifications" / clarification_id / "session.json").resolve()),
            "management_events": _management_event_payload(root),
        }
        questions = clarification.get("questions", [])
        lines = [
            f"Setting change needs clarification: {clarification_id}",
            *[f"Question: {question}" for question in questions if isinstance(question, str)],
            f"Continue: novel setting-change answer {clarification_id} --path {root} --answer <your-answer>",
        ]
        return _success(args, payload, lines)
    proposal = result.get("proposal")
    if not isinstance(proposal, dict):
        return _failure(args, "missing setting change proposal result", error_type="memory_repair_error")
    impact = proposal.get("impact")
    payload = {
        **result,
        "command": command,
        "status": "proposal_ready",
        "repair_id": proposal.get("repair_id"),
        "target_files": proposal.get("target_files", []),
        "domains": proposal.get("domains", []),
        "operation_count": len(proposal.get("operations", [])),
        "confidence": proposal.get("confidence"),
        "impact": impact,
        "followup_actions": proposal.get("followup_actions", []),
        "management_events": _management_event_payload(root),
    }
    affected_chapters = impact.get("affected_chapters", []) if isinstance(impact, dict) else []
    affected = ", ".join(str(number) for number in affected_chapters)
    return _success(
        args,
        payload,
        [
            f"Setting change proposal: {result.get('proposal_path')}",
            f"Targets: {', '.join(str(item) for item in proposal.get('target_files', [])) or 'none'}",
            f"Domains: {', '.join(str(item) for item in proposal.get('domains', [])) or 'none'}",
            f"Operations: {len(proposal.get('operations', []))}",
            f"Affected chapters: {affected or 'none'}",
            *_management_event_lines(root),
        ],
    )

def _cmd_setting_change(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    try:
        if args.setting_change_command == "suggest":
            result = _dispatch_cli_command(
                args,
                root,
                SettingChangeSuggestCommand(
                    request=args.request,
                    provider_name=args.provider,
                    stage=cast(MemoryChangeStage, args.stage),
                    session_id=args.session_id,
                    chapter_number=args.chapter,
                    audit_issue_ids=list(args.audit_issue_id or []),
                ),
            )
            return _setting_change_suggestion_success(args, root, result, command="setting-change suggest")
        if args.setting_change_command == "answer":
            result = _dispatch_cli_command(
                args,
                root,
                SettingChangeAnswerCommand(
                    clarification_id=args.clarification_id,
                    answer=args.answer,
                    provider_name=args.provider,
                ),
            )
            return _setting_change_suggestion_success(args, root, result, command="setting-change answer")
        result = _dispatch_cli_command(
            args,
            root,
            SettingChangeApplyCommand(
                proposal_path=_resolve_memory_repair_proposal_arg(args.proposal).as_posix(),
            ),
            confirmed=True,
        )
        result.update({"command": "setting-change apply", "management_events": _management_event_payload(root)})
        return _success(
            args,
            result,
            [
                f"Applied setting change: {result.get('repair_id') or result.get('proposal', {})}",
                f"Apply log: {result.get('apply_log_path')}",
                *_management_event_lines(root),
            ],
        )
    except DomainError as exc:
        return _failure(args, exc.message, error_type=exc.code)

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
