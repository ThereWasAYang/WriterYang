from __future__ import annotations

from pathlib import Path

from novel.core.artifact_store import resolve_project_path
from novel.core.command_bus import DomainError, _handler, _result
from novel.core.contracts import (
    CommandEnvelope,
    CommandResult,
    MemoryRepairApplyCommand,
    MemoryRepairSuggestCommand,
    PreviewPackageCommand,
    ProductionExportCommand,
    SettingChangeAnswerCommand,
    SettingChangeApplyCommand,
    SettingChangeSuggestCommand,
)
from novel.core.exporting import (
    DocxExportOptions,
    DocxExportResult,
    MarkdownExportOptions,
    MarkdownExportResult,
    export_docx,
    export_markdown,
)
from novel.core.io import load_json
from novel.core.memory_repair import (
    MemoryRepairError,
    SettingChangeSuggestionResult,
    answer_setting_change_clarification,
    apply_memory_repair,
    suggest_memory_repair,
    suggest_setting_change_interactive,
)
from novel.core.previewing import PreviewPackageOptions, build_preview_package
from novel.core.setting_change_followup import (
    SettingChangeFollowupOptions,
    sync_setting_change_session,
)


@_handler("export.markdown", "export.docx")
def _handle_export(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, ProductionExportCommand):
        raise DomainError("invalid_command", "export payload type mismatch")
    output = Path(command.output_path) if command.output_path else None
    value: MarkdownExportResult | DocxExportResult
    if command.type == "export.markdown":
        value = export_markdown(
            MarkdownExportOptions(
                root=root,
                chapters=tuple(command.chapters),
                from_chapter=command.from_chapter,
                to_chapter=command.to_chapter,
                output_path=output,
                title=command.title,
                include_toc=command.include_toc,
                volume_title=command.volume_title,
                chapter_number_style=command.chapter_number_style,
                force=command.force,
            )
        )
    else:
        value = export_docx(
            DocxExportOptions(
                root=root,
                chapters=tuple(command.chapters),
                from_chapter=command.from_chapter,
                to_chapter=command.to_chapter,
                output_path=output,
                title=command.title,
                force=command.force,
            )
        )
    return _result(
        envelope,
        result={
            "output_path": str(value.output_path),
            "manifest_path": str(value.manifest_path),
            "chapters": list(value.exported_chapters),
        },
        warnings=list(value.warnings),
        changed_paths=[
            value.output_path.relative_to(root).as_posix(),
            value.manifest_path.relative_to(root).as_posix(),
        ],
    )


@_handler("preview.package")
def _handle_preview(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, PreviewPackageCommand):
        raise DomainError("invalid_command", "preview payload type mismatch")
    value = build_preview_package(
        PreviewPackageOptions(
            root=root,
            chapters=tuple(command.chapters),
            from_chapter=command.from_chapter,
            to_chapter=command.to_chapter,
            source_kind=command.source_kind,
            title=command.title,
        )
    )
    return _result(
        envelope,
        result={
            "preview_id": value.manifest.preview_id,
            "package_dir": str(value.package_dir),
            "content_path": str(value.content_path),
            "manifest_path": str(value.manifest_path),
            "chapters": list(value.chapters),
            "production_eligible": value.manifest.production_eligible,
        },
        changed_paths=[
            value.content_path.relative_to(root).as_posix(),
            value.manifest_path.relative_to(root).as_posix(),
        ],
    )


@_handler("memory_repair.suggest")
def _handle_memory_repair_suggest(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, MemoryRepairSuggestCommand):
        raise DomainError("invalid_command", "memory repair suggest payload type mismatch")
    value = suggest_memory_repair(root, command.request, provider_name=command.provider_name)
    return _result(
        envelope,
        result={
            "repair_id": value.proposal.repair_id,
            "proposal": value.proposal.model_dump(mode="json"),
            "proposal_path": str(value.proposal_path),
            "markdown_path": str(value.markdown_path),
        },
        next_allowed_commands=["memory_repair.apply"],
        changed_paths=[
            value.proposal_path.relative_to(root).as_posix(),
            value.markdown_path.relative_to(root).as_posix(),
        ],
    )


@_handler("memory_repair.apply")
def _handle_memory_repair_apply(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, MemoryRepairApplyCommand):
        raise DomainError("invalid_command", "memory repair apply payload type mismatch")
    proposal_path = resolve_project_path(root, command.proposal_path)
    try:
        value = apply_memory_repair(root, proposal_path)
    except MemoryRepairError as exc:
        raise DomainError(
            "memory_repair_error",
            str(exc),
            recoverable=True,
            details=_memory_repair_apply_error_details(root, proposal_path),
        ) from exc
    return _result(
        envelope,
        result={
            "repair_id": value.proposal.repair_id,
            "proposal": value.proposal.model_dump(mode="json"),
            "apply_log": value.apply_log.model_dump(mode="json"),
            "apply_log_path": str(value.apply_log_path),
        },
        changed_paths=[value.apply_log_path.relative_to(root).as_posix(), *value.proposal.target_files],
    )


@_handler("setting_change.suggest")
def _handle_setting_change_suggest(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, SettingChangeSuggestCommand):
        raise DomainError("invalid_command", "setting change suggest payload type mismatch")
    value = suggest_setting_change_interactive(
        root,
        command.request,
        provider_name=command.provider_name,
        stage=command.stage,
        session_id=command.session_id,
        chapter_number=command.chapter_number,
        audit_issue_ids=command.audit_issue_ids,
    )
    return _setting_change_result(envelope, root, value)


@_handler("setting_change.answer")
def _handle_setting_change_answer(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, SettingChangeAnswerCommand):
        raise DomainError("invalid_command", "setting change answer payload type mismatch")
    value = answer_setting_change_clarification(
        root,
        command.clarification_id,
        command.answer,
        provider_name=command.provider_name,
    )
    return _setting_change_result(envelope, root, value)


def _setting_change_result(
    envelope: CommandEnvelope,
    root: Path,
    value: SettingChangeSuggestionResult,
) -> CommandResult:
    if value.status == "needs_clarification":
        if not value.clarification:
            raise DomainError("internal_error", "missing clarification result")
        clarification = value.clarification
        path = root / "memory" / "repairs" / "clarifications" / clarification.clarification_id / "session.json"
        return _result(
            envelope,
            result={
                "status": "needs_clarification",
                "clarification": clarification.model_dump(mode="json"),
                "clarification_id": clarification.clarification_id,
                "questions": clarification.questions,
            },
            next_allowed_commands=["setting_change.answer"],
            changed_paths=[path.relative_to(root).as_posix()],
        )
    if not value.proposal_result:
        raise DomainError("internal_error", "missing setting change proposal result")
    proposal = value.proposal_result.proposal
    return _result(
        envelope,
        result={
            "status": "proposal_ready",
            "proposal": proposal.model_dump(mode="json"),
            "proposal_path": str(value.proposal_result.proposal_path),
            "markdown_path": str(value.proposal_result.markdown_path),
        },
        next_allowed_commands=["setting_change.apply"],
        changed_paths=[
            value.proposal_result.proposal_path.relative_to(root).as_posix(),
            value.proposal_result.markdown_path.relative_to(root).as_posix(),
        ],
    )


@_handler("setting_change.apply")
def _handle_setting_change_apply(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, SettingChangeApplyCommand):
        raise DomainError("invalid_command", "setting change apply payload type mismatch")
    proposal_path = resolve_project_path(root, command.proposal_path)
    try:
        value = apply_memory_repair(root, proposal_path)
    except MemoryRepairError as exc:
        raise DomainError(
            "memory_repair_error",
            str(exc),
            recoverable=True,
            details=_memory_repair_apply_error_details(root, proposal_path),
        ) from exc
    sync_result: dict[str, object] = {"status": "skipped", "reason": "sync_session is false"}
    if command.sync_session:
        sync_result = sync_setting_change_session(
            SettingChangeFollowupOptions(
                root=root,
                proposal=value.proposal,
                session_id=command.session_id,
                provider_name=command.provider_name,
                use_search_context=command.use_search_context,
                use_vector_context=command.use_vector_context,
                polish_mode=command.polish_mode,
            )
        )
    return _result(
        envelope,
        result={
            "proposal": value.proposal.model_dump(mode="json"),
            "apply_log": value.apply_log.model_dump(mode="json"),
            "apply_log_path": str(value.apply_log_path),
            "sync_result": sync_result,
        },
        next_allowed_commands=(
            ["session.revise_outline", "session.revise_content"]
            if sync_result.get("status") in {"manual_review", "failed_recoverable"}
            else []
        ),
        changed_paths=[value.apply_log_path.relative_to(root).as_posix(), *value.proposal.target_files],
    )


def _memory_repair_apply_error_details(root: Path, proposal_path: Path) -> dict[str, object]:
    details: dict[str, object] = {}
    try:
        proposal = load_json(proposal_path)
    except Exception:
        return details
    if not isinstance(proposal, dict):
        return details
    repair_id = proposal.get("repair_id")
    if not isinstance(repair_id, str) or not repair_id:
        return details
    details["repair_id"] = repair_id
    apply_log_path = root / "memory" / "repairs" / repair_id / "apply_log.json"
    if not apply_log_path.exists():
        return details
    details["apply_log_path"] = str(apply_log_path)
    details["apply_log_relative_path"] = apply_log_path.relative_to(root).as_posix()
    try:
        apply_log = load_json(apply_log_path)
    except Exception:
        return details
    if isinstance(apply_log, dict):
        status = apply_log.get("status")
        errors = apply_log.get("errors")
        if isinstance(status, str):
            details["apply_log_status"] = status
        if isinstance(errors, list):
            details["apply_log_error_count"] = len(errors)
    return details
