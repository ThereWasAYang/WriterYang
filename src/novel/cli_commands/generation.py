from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Literal

from novel.cli_shared import (
    _audit_issue_lines,
    _command_lock,
    _dispatch_cli_command,
    _failure,
    _print_dry_run_provider,
    _print_json,
    _quiet,
    _success,
    _validation_payload,
    _vector_context_mode_from_args,
    _wants_json,
)
from novel.core.auditing import (
    AuditError,
    ChapterAuditOptions,
    audit_chapter,
    load_audit_provider,
    read_audit_instruction,
)
from novel.core.canon import (
    format_canon_validation_report,
)
from novel.core.command_bus import DomainError
from novel.core.contracts import (
    CanonApplyCommand,
    CanonSuggestCommand,
    InspirationGenerateCommand,
    ProductionExportCommand,
)
from novel.core.drafting import (
    ChapterDraftingOptions,
    DraftingError,
    load_drafting_provider,
    read_drafting_instruction,
    write_chapter_draft,
)
from novel.core.exporting import (
    parse_chapter_selector,
)
from novel.core.inspection import (
    ProjectReadError,
    format_canon,
)
from novel.core.inspiration import (
    InspirationError,
    read_inspiration_input,
)
from novel.core.locking import ProjectLockError
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
from novel.core.validation import validate_canon


def _cmd_inspire(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        if args.dry_run_provider:
            _print_dry_run_provider(
                root,
                args.agent_config,
                args.provider,
                args.model,
                ("inspiration",),
            )
            return 0
        source_text, source_type = read_inspiration_input(args.text, args.input)
        payload = _dispatch_cli_command(
            args,
            root,
            InspirationGenerateCommand(
                source_text=source_text,
                source_type=source_type,
                write_json=args.json,
                overwrite=args.overwrite,
                provider_name=args.provider,
                agent_config_path=str(args.agent_config) if args.agent_config else None,
                model_name=args.model,
                use_search_context=args.use_search_context,
                use_vector_context=_vector_context_mode_from_args(args),
            ),
        )
    except (DomainError, InspirationError) as exc:
        error_type = exc.code if isinstance(exc, DomainError) else "inspiration_error"
        return _failure(args, str(exc), error_type=error_type)

    lines = [f"Wrote inspiration markdown: {payload.get('markdown_path')}"]
    if payload.get("json_path"):
        lines.append(f"Wrote inspiration JSON: {payload.get('json_path')}")
    return _success(
        args,
        {
            **payload,
            "command": "inspire",
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
                    ("canon",),
                )
                return 0
            payload = _dispatch_cli_command(
                args,
                root,
                CanonSuggestCommand(
                    output_path=str(args.output) if args.output else None,
                    provider_name=args.provider,
                    agent_config_path=str(args.agent_config) if args.agent_config else None,
                    model_name=args.model,
                    use_search_context=args.use_search_context,
                    use_vector_context=_vector_context_mode_from_args(args),
                ),
            )
        except DomainError as exc:
            return _failure(args, exc.message, error_type=exc.code)

        if _wants_json(args):
            _print_json(
                {
                    "ok": True,
                    "command": "canon suggest",
                    **payload,
                }
            )
            return 0
        if _quiet(args):
            return 0
        if payload.get("output_path"):
            print(f"Wrote canon proposal: {payload.get('output_path')}")
        else:
            print(str(payload.get("proposal_json") or ""), end="")
        return 0

    if args.canon_command == "apply":
        try:
            payload = _dispatch_cli_command(
                args,
                root,
                CanonApplyCommand(proposal_path=str(args.proposal_file)),
                confirmed=True,
            )
        except DomainError as exc:
            return _failure(args, exc.message, error_type=exc.code)
        validation_ok = bool(payload.get("validation_ok"))
        if _wants_json(args):
            _print_json(
                {
                    "ok": validation_ok,
                    "command": "canon apply",
                    **payload,
                }
            )
            return 0 if validation_ok else 1
        if not _quiet(args):
            errors_value = payload.get("errors")
            errors = errors_value if isinstance(errors_value, list) else []
            warnings_value = payload.get("warnings")
            warnings = warnings_value if isinstance(warnings_value, list) else []
            for message in errors:
                print(f"error: {message}")
            for message in warnings:
                print(f"warning: {message}")
            if validation_ok:
                print("Canon validation passed")
            print(f"Canon apply log: {payload.get('apply_log_path')}")
            print(f"Canon proposal snapshot: {payload.get('proposal_snapshot_path')}")
        return 0 if validation_ok else 1

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
                ("plot",),
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
                ("writer",),
            )
            return 0
        instruction = read_drafting_instruction(args.instruction, args.input)
        provider = load_drafting_provider(
            root,
            args.provider,
            chapter_number=args.chapter_number,
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
                ("polish",),
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
            chapter_number=args.chapter_number,
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
                ("audit",),
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
        + (f" (highest: {result.deterministic_highest_severity})" if result.deterministic_highest_severity else ""),
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


def _cmd_propose_state_update(args: argparse.Namespace) -> int:
    root = Path(args.path)
    try:
        if args.dry_run_provider:
            _print_dry_run_provider(
                root,
                args.agent_config,
                args.provider,
                args.model,
                ("state_update",),
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
            result = apply_state_update(StateUpdateApplyOptions(root=root, chapter_number=args.chapter_number))
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
                ("state_update", "chapter_memory"),
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
            "proposal_path": str(result.proposal_result.proposal_path) if result.proposal_result else None,
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


def _cmd_export(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    if args.export_command not in {"markdown", "docx"}:
        return _failure(args, f"unknown export command: {args.export_command}", code=2)
    command_type: Literal["export.markdown", "export.docx"] = (
        "export.markdown" if args.export_command == "markdown" else "export.docx"
    )
    try:
        payload = _dispatch_cli_command(
            args,
            root,
            ProductionExportCommand(
                type=command_type,
                chapters=list(parse_chapter_selector(args.chapters)),
                from_chapter=args.from_chapter,
                to_chapter=args.to_chapter,
                output_path=str(args.output) if args.output else None,
                title=args.title,
                include_toc=bool(getattr(args, "toc", False)),
                volume_title=getattr(args, "volume_title", None),
                chapter_number_style=getattr(args, "chapter_number_style", "chinese"),
                force=args.force,
            ),
            confirmed=True,
        )
    except DomainError as exc:
        return _failure(args, exc.message, error_type=exc.code)
    label = "Markdown" if args.export_command == "markdown" else "DOCX"
    warnings_value = payload.get("warnings")
    warnings = warnings_value if isinstance(warnings_value, list) else []
    chapters_value = payload.get("chapters")
    chapters = chapters_value if isinstance(chapters_value, list) else []
    return _success(
        args,
        payload,
        [
            *(f"warning: {warning}" for warning in warnings),
            f"Wrote {label} export: {payload['output_path']}",
            f"Updated export manifest: {payload['manifest_path']}",
            f"Chapters: {', '.join(str(number) for number in chapters)}",
        ],
    )
