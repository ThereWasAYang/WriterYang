from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
import uuid

from pydantic import ValidationError

from novel.core.artifact_store import resolve_project_path
from novel.core.budget import WorkflowBudgetExceeded, active_budget_tracker, workflow_budget_scope
from novel.core.io import atomic_write_model_json, load_json
from novel.core.contracts import (
    BudgetUsage,
    CommandEnvelope,
    CommandResult,
    MemoryRepairApplyCommand,
    MemoryRepairSuggestCommand,
    PreviewPackageCommand,
    ProductionExportCommand,
    PublicCommand,
    ProjectShowCommand,
    ProjectInitCommand,
    ProjectStatusCommand,
    ProjectValidateCommand,
    RevisionBlocksCommand,
    RevisionCommand,
    RevisionSession,
    RevisionStartCommand,
    SessionCommand,
    SessionStartCommand,
    SearchCommand,
    SettingChangeAnswerCommand,
    SettingChangeApplyCommand,
    SettingChangeSuggestCommand,
    Surface,
    WorkflowBudget,
    default_workflow_budget,
)
from novel.core.exporting import (
    DocxExportOptions,
    ExportError,
    MarkdownExportOptions,
    MarkdownExportResult,
    DocxExportResult,
    export_docx,
    export_markdown,
)
from novel.core.locking import ProjectLock, ProjectLockError
from novel.core.inspection import (
    ProjectReadError,
    format_canon,
    format_characters,
    format_state,
    format_timeline,
    get_project_status,
)
from novel.core.memory_repair import (
    MemoryRepairError,
    SettingChangeSuggestionResult,
    answer_setting_change_clarification,
    apply_memory_repair,
    suggest_memory_repair,
    suggest_setting_change_interactive,
)
from novel.core.previewing import PreviewError, PreviewPackageOptions, build_preview_package
from novel.core.search import SearchError, search_project
from novel.core.revision_workflow import (
    RevisionActionOptions,
    RevisionRunOptions,
    RevisionStartOptions,
    RevisionWorkflowError,
    RevisionSessionResult,
    accept_revision_session,
    list_revision_blocks,
    run_revision_session,
    show_revision_session,
    start_revision_session,
)
from novel.core.session import (
    CreationSessionError,
    SessionActionOptions,
    SessionInstructionOptions,
    SessionRewriteControlOptions,
    SessionRunOptions,
    SessionStartOptions,
    SessionResult,
    accept_session,
    approve_outline,
    archive_session,
    request_session_cancel,
    retry_rewrite,
    revise_audit,
    revise_content,
    revise_outline,
    run_session,
    show_session,
    start_session,
    undo_rewrite,
)
from novel.core.setting_change_followup import (
    SettingChangeFollowupOptions,
    sync_setting_change_session,
)
from novel.core.timeutil import utc_now
from novel.core.schemas import CreationSession
from novel.core.validation import validate_project
from novel.core.workflow_runtime import workflow_runtime_scope
from novel.core.workspace import InitOptions, WorkspaceExistsError, init_workspace


class DomainError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        recoverable: bool = False,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable
        self.details = details or {}


CommandHandler = Callable[[CommandEnvelope, Path], CommandResult]
COMMAND_HANDLERS: dict[str, CommandHandler] = {}
READ_ONLY_COMMANDS = {
    "project.status",
    "project.validate",
    "project.show",
    "search",
    "session.show",
    "revision.blocks",
    "revision.show",
}
UNLOCKED_WRITE_COMMANDS = {"project.init", "session.cancel"}
CONFIRMATION_REQUIRED = {
    "session.accept",
    "session.archive",
    "revision.accept",
    "memory_repair.apply",
    "setting_change.apply",
    "export.markdown",
    "export.docx",
}


def new_command_envelope(
    *,
    surface: Surface,
    project_root: Path,
    command: PublicCommand,
    confirmed: bool = False,
    workflow_run_id: str | None = None,
    budget: WorkflowBudget | None = None,
    initial_budget_usage: BudgetUsage | None = None,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=f"cmd_{uuid.uuid4().hex}",
        workflow_run_id=workflow_run_id or f"run_{uuid.uuid4().hex}",
        surface=surface,
        project_root=str(project_root.expanduser().resolve()),
        command=command,
        confirmed=confirmed,
        budget=budget or default_workflow_budget(),
        initial_budget_usage=initial_budget_usage or BudgetUsage(),
        issued_at=utc_now(),
    )


def dispatch_command(envelope: CommandEnvelope) -> CommandResult:
    root = Path(envelope.project_root).expanduser().resolve()
    budget_tracker = None
    effective_budget = envelope.budget
    try:
        envelope = _resume_session_workflow(root, envelope)
        command_type = envelope.command.type
        if command_type in CONFIRMATION_REQUIRED and not envelope.confirmed:
            raise DomainError(
                "confirmation_required",
                f"command requires explicit confirmation: {command_type}",
                recoverable=True,
                details={"command_type": command_type},
            )
        handler = COMMAND_HANDLERS.get(command_type)
        if not handler:
            raise DomainError("unknown_command", f"no handler registered for {command_type}")
        budget, initial_usage = _effective_command_budget(root, envelope)
        effective_budget = budget
        with workflow_budget_scope(
            budget,
            initial_usage=initial_usage,
        ) as budget_tracker:

            def execute_handler() -> CommandResult:
                _reserve_command_scope(envelope.command)
                if command_type in READ_ONLY_COMMANDS or command_type in UNLOCKED_WRITE_COMMANDS:
                    return handler(envelope, root)
                with ProjectLock(root, task=command_type):
                    return handler(envelope, root)

            if (root / "project.yaml").exists():
                with workflow_runtime_scope(
                    root=root,
                    workflow_run_id=envelope.workflow_run_id,
                    command_id=envelope.command_id,
                    surface=envelope.surface,
                    budget=budget,
                ) as runtime:
                    result = runtime.execute_node(
                        name=f"command:{command_type}",
                        node_type="command",
                        function=execute_handler,
                        input_paths=["project.yaml"],
                        output_details=lambda value: (
                            value.changed_artifacts,
                            value.changed_paths,
                        ),
                    )
            else:
                result = execute_handler()
            usage = budget_tracker.snapshot()
            result = _persist_session_budget(result, envelope.command, budget, usage)
            return result.model_copy(update={"budget_usage": usage})
    except DomainError:
        raise
    except ProjectLockError as exc:
        raise DomainError("project_locked", str(exc), recoverable=True) from exc
    except WorkspaceExistsError as exc:
        raise DomainError("workspace_exists", str(exc), recoverable=True) from exc
    except CreationSessionError as exc:
        raise DomainError("session_error", str(exc), recoverable=True) from exc
    except RevisionWorkflowError as exc:
        raise DomainError("revision_error", str(exc), recoverable=True) from exc
    except MemoryRepairError as exc:
        raise DomainError("memory_repair_error", str(exc), recoverable=True) from exc
    except ExportError as exc:
        raise DomainError("export_error", str(exc), recoverable=True) from exc
    except PreviewError as exc:
        raise DomainError("preview_error", str(exc), recoverable=True) from exc
    except SearchError as exc:
        raise DomainError("search_error", str(exc), recoverable=True) from exc
    except ProjectReadError as exc:
        raise DomainError("project_read_error", str(exc), recoverable=True) from exc
    except WorkflowBudgetExceeded as exc:
        if budget_tracker is not None:
            _persist_failed_session_budget(
                root,
                envelope.command,
                effective_budget,
                budget_tracker.snapshot(),
            )
        raise DomainError(
            "budget_exceeded",
            str(exc),
            recoverable=True,
            details={"dimension": exc.dimension, "used": exc.used, "limit": exc.limit},
        ) from exc
    except (ValidationError, ValueError) as exc:
        raise DomainError("invalid_command", str(exc), recoverable=True) from exc


def command_result_payload(value: CommandResult) -> dict[str, object]:
    return {
        **value.result,
        "command_id": value.command_id,
        "workflow_run_id": value.workflow_run_id,
        "command_type": value.command_type,
        "next_allowed_commands": value.next_allowed_commands,
        "warnings": value.warnings,
        "changed_artifacts": [ref.model_dump(mode="json") for ref in value.changed_artifacts],
        "changed_paths": value.changed_paths,
        "budget_usage": value.budget_usage.model_dump(mode="json"),
    }


def _reserve_command_scope(command: PublicCommand) -> None:
    tracker = active_budget_tracker()
    if not tracker:
        return
    if isinstance(command, SessionStartCommand):
        tracker.consume_chapters(len(command.chapter_range))
    elif isinstance(command, RevisionStartCommand):
        tracker.consume_chapters(1)
    elif isinstance(command, (ProductionExportCommand, PreviewPackageCommand)) and command.chapters:
        tracker.consume_chapters(len(set(command.chapters)))


def _effective_command_budget(
    root: Path,
    envelope: CommandEnvelope,
) -> tuple[WorkflowBudget, BudgetUsage]:
    command = envelope.command
    if isinstance(command, SessionCommand):
        creation_session = show_session(root, command.session_id).session
        if creation_session.workflow_budget is not None:
            return creation_session.workflow_budget, creation_session.budget_usage
    if isinstance(command, RevisionCommand):
        revision_session = show_revision_session(root, command.revision_session_id).session
        if revision_session.workflow_budget is not None:
            return revision_session.workflow_budget, revision_session.budget_usage
    return envelope.budget, envelope.initial_budget_usage


def _resume_session_workflow(root: Path, envelope: CommandEnvelope) -> CommandEnvelope:
    command = envelope.command
    if isinstance(command, SessionCommand):
        try:
            session = show_session(root, command.session_id).session
        except CreationSessionError:
            return envelope
        if session.workflow_run_id:
            return envelope.model_copy(update={"workflow_run_id": session.workflow_run_id})
    if isinstance(command, RevisionCommand):
        try:
            revision_session = show_revision_session(root, command.revision_session_id).session
        except RevisionWorkflowError:
            return envelope
        if revision_session.workflow_run_id:
            return envelope.model_copy(update={"workflow_run_id": revision_session.workflow_run_id})
    return envelope


def _persist_session_budget(
    result: CommandResult,
    command: PublicCommand,
    budget: WorkflowBudget,
    usage: BudgetUsage,
) -> CommandResult:
    if isinstance(command, (RevisionStartCommand, RevisionCommand)):
        if isinstance(command, RevisionCommand) and command.type == "revision.show":
            return result
        revision_data = result.result.get("revision_session")
        revision_path_value = result.result.get("session_path")
        if not isinstance(revision_data, dict) or not isinstance(revision_path_value, str):
            return result
        revision = RevisionSession.model_validate(revision_data).model_copy(
            update={
                "workflow_run_id": result.workflow_run_id,
                "workflow_budget": budget,
                "budget_usage": usage,
            }
        )
        atomic_write_model_json(Path(revision_path_value), revision)
        payload = dict(result.result)
        payload["revision_session"] = revision.model_dump(mode="json")
        return result.model_copy(update={"result": payload})
    if not isinstance(command, (SessionStartCommand, SessionCommand)):
        return result
    if isinstance(command, SessionCommand) and command.type in {"session.show", "session.cancel"}:
        return result
    session_data = result.result.get("session")
    session_path_value = result.result.get("session_path")
    if not isinstance(session_data, dict) or not isinstance(session_path_value, str):
        return result
    session = CreationSession.model_validate(session_data).model_copy(
        update={
            "workflow_run_id": result.workflow_run_id,
            "workflow_budget": budget,
            "budget_usage": usage,
        }
    )
    atomic_write_model_json(Path(session_path_value), session)
    payload = dict(result.result)
    payload["session"] = session.model_dump(mode="json")
    return result.model_copy(update={"result": payload})


def _persist_failed_session_budget(
    root: Path,
    command: PublicCommand,
    budget: WorkflowBudget,
    usage: BudgetUsage,
) -> None:
    if isinstance(command, RevisionCommand):
        try:
            revision_value = show_revision_session(root, command.revision_session_id)
            revision_session = revision_value.session.model_copy(
                update={
                    "workflow_budget": budget,
                    "budget_usage": usage,
                }
            )
            atomic_write_model_json(revision_value.session_path, revision_session)
        except Exception:
            return
        return
    if not isinstance(command, SessionCommand):
        return
    try:
        session_value = show_session(root, command.session_id)
        creation_session = session_value.session.model_copy(
            update={
                "workflow_budget": budget,
                "budget_usage": usage,
            }
        )
        atomic_write_model_json(session_value.session_path, creation_session)
    except Exception:
        return


def _handler(*command_types: str) -> Callable[[CommandHandler], CommandHandler]:
    def register(function: CommandHandler) -> CommandHandler:
        for command_type in command_types:
            if command_type in COMMAND_HANDLERS:
                raise RuntimeError(f"duplicate command handler: {command_type}")
            COMMAND_HANDLERS[command_type] = function
        return function

    return register


def _result(
    envelope: CommandEnvelope,
    *,
    result: dict[str, object],
    next_allowed_commands: list[str] | None = None,
    warnings: list[str] | None = None,
    changed_paths: list[str] | None = None,
) -> CommandResult:
    return CommandResult(
        command_id=envelope.command_id,
        workflow_run_id=envelope.workflow_run_id,
        command_type=envelope.command.type,
        result=result,
        next_allowed_commands=next_allowed_commands or [],
        warnings=warnings or [],
        changed_paths=changed_paths or [],
    )


@_handler("project.status")
def _handle_project_status(envelope: CommandEnvelope, root: Path) -> CommandResult:
    if not isinstance(envelope.command, ProjectStatusCommand):
        raise DomainError("invalid_command", "project.status payload type mismatch")
    status = get_project_status(root)
    payload = asdict(status)
    payload["latest_run_log"] = str(status.latest_run_log) if status.latest_run_log else None
    return _result(envelope, result={"status": payload})


@_handler("project.init")
def _handle_project_init(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, ProjectInitCommand):
        raise DomainError("invalid_command", "project.init payload type mismatch")
    value = init_workspace(
        InitOptions(
            title=command.title,
            root=root,
            project_id=command.project_id,
            language=command.language,
            genre=command.genre,
        )
    )
    return _result(
        envelope,
        result={
            "root": str(value.root),
            "created_files": [str(path) for path in value.created_files],
            "created_dirs": [str(path) for path in value.created_dirs],
        },
        changed_paths=[path.relative_to(value.root).as_posix() for path in value.created_files],
    )


@_handler("project.validate")
def _handle_project_validate(envelope: CommandEnvelope, root: Path) -> CommandResult:
    if not isinstance(envelope.command, ProjectValidateCommand):
        raise DomainError("invalid_command", "project.validate payload type mismatch")
    report = validate_project(root)
    return _result(
        envelope,
        result={
            "root": str(report.root),
            "valid": report.ok,
            "error_count": len(report.errors),
            "warning_count": len(report.warnings),
            "messages": [
                {
                    "level": message.level,
                    "path": (
                        message.path.relative_to(report.root).as_posix()
                        if message.path.is_relative_to(report.root)
                        else str(message.path)
                    ),
                    "message": message.message,
                }
                for message in report.messages
            ],
        },
    )


@_handler("project.show")
def _handle_project_show(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, ProjectShowCommand):
        raise DomainError("invalid_command", "project.show payload type mismatch")
    paths = {
        "characters": root / "memory" / "canon" / "characters.json",
        "timeline": root / "memory" / "state" / "timeline.json",
        "state": root / "memory" / "state" / "current_state.json",
        "canon": root / "memory" / "canon" / "characters.json",
    }
    path = paths[command.target]
    output = {
        "characters": format_characters,
        "timeline": format_timeline,
        "state": format_state,
        "canon": format_canon,
    }[command.target](root)
    return _result(
        envelope,
        result={
            "target": command.target,
            "path": str(path),
            "data": load_json(path),
            "output": output,
        },
    )


@_handler("search")
def _handle_search(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, SearchCommand):
        raise DomainError("invalid_command", "search payload type mismatch")
    results = search_project(
        root,
        command.query,
        search_type=command.search_type,
        limit=command.limit,
        chapter_number=command.chapter_number,
        highlight=command.highlight,
        use_vector=command.use_vector,
        embedding_provider_name=command.embedding_provider_name,
        embedding_config_path=(Path(command.embedding_config_path) if command.embedding_config_path else None),
    )
    return _result(
        envelope,
        result={
            "query": command.query,
            "results": [
                {
                    "id": item.id,
                    "type": item.type,
                    "path": item.path,
                    "title": item.title,
                    "score": item.score,
                    "matched_terms": list(item.matched_terms),
                    "excerpt": item.excerpt,
                    "highlighted_excerpt": item.highlighted_excerpt,
                    "metadata": item.metadata,
                }
                for item in results
            ],
        },
    )


@_handler("session.start")
def _handle_session_start(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, SessionStartCommand):
        raise DomainError("invalid_command", "session.start payload type mismatch")
    result = start_session(
        SessionStartOptions(
            root=root,
            user_intent=command.user_intent,
            chapter_range=tuple(command.chapter_range),
            provider_name=command.provider_name,
            force=command.force,
            use_search_context=command.use_search_context,
            use_vector_context=command.use_vector_context,
            polish_mode=command.polish_mode,
        )
    )
    return _session_command_result(envelope, result)


@_handler(
    "session.show",
    "session.revise_outline",
    "session.approve_outline",
    "session.run",
    "session.revise_content",
    "session.revise_audit",
    "session.retry_rewrite",
    "session.undo_rewrite",
    "session.accept",
    "session.archive",
    "session.cancel",
)
def _handle_session_command(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, SessionCommand):
        raise DomainError("invalid_command", "session payload type mismatch")
    action = command.type
    if action == "session.show":
        result = show_session(root, command.session_id)
    elif action == "session.revise_outline":
        result = revise_outline(_session_instruction_options(root, command))
    elif action == "session.approve_outline":
        result = approve_outline(_session_action_options(root, command))
    elif action == "session.run":
        result = run_session(
            SessionRunOptions(
                root=root,
                session_id=command.session_id,
                provider_name=command.provider_name,
                force=command.force,
                max_auto_revision_rounds=command.max_auto_revision_rounds,
                use_search_context=command.use_search_context,
                use_vector_context=command.use_vector_context,
                polish_mode=command.polish_mode,
            )
        )
    elif action == "session.revise_content":
        result = revise_content(_session_instruction_options(root, command))
    elif action in {"session.revise_audit", "session.retry_rewrite", "session.undo_rewrite"}:
        if not command.event_id:
            raise DomainError("invalid_command", f"{action} requires event_id", recoverable=True)
        options = SessionRewriteControlOptions(
            root=root,
            session_id=command.session_id,
            event_id=command.event_id,
            instruction=command.instruction,
            provider_name=command.provider_name,
            force=command.force,
            use_search_context=command.use_search_context,
            use_vector_context=command.use_vector_context,
            polish_mode=command.polish_mode,
        )
        if action == "session.revise_audit":
            result = revise_audit(options)
        elif action == "session.retry_rewrite":
            result = retry_rewrite(options)
        else:
            result = undo_rewrite(options)
    elif action == "session.accept":
        result = accept_session(_session_action_options(root, command))
    elif action == "session.archive":
        result = archive_session(_session_action_options(root, command))
    elif action == "session.cancel":
        progress = request_session_cancel(root, command.session_id)
        return _result(
            envelope,
            result={
                "session_id": command.session_id,
                "cancel_requested": True,
                "progress": progress.model_dump(mode="json"),
            },
            next_allowed_commands=["session.show"],
            changed_paths=[
                (root / "memory" / "sessions" / command.session_id / "progress.json").relative_to(root).as_posix()
            ],
        )
    else:
        raise DomainError("unknown_command", action)
    return _session_command_result(envelope, result)


def _session_instruction_options(root: Path, command: SessionCommand) -> SessionInstructionOptions:
    return SessionInstructionOptions(
        root=root,
        session_id=command.session_id,
        instruction=command.instruction,
        provider_name=command.provider_name,
        force=command.force,
        from_audit=command.from_audit,
        use_search_context=command.use_search_context,
        use_vector_context=command.use_vector_context,
        polish_mode=command.polish_mode,
    )


def _session_action_options(root: Path, command: SessionCommand) -> SessionActionOptions:
    return SessionActionOptions(
        root=root,
        session_id=command.session_id,
        provider_name=command.provider_name,
        force=command.force,
    )


def _session_command_result(envelope: CommandEnvelope, value: SessionResult) -> CommandResult:
    session = value.session
    changed = [value.session_path.relative_to(Path(envelope.project_root)).as_posix()]
    changed.extend(session.final_output_paths)
    return _result(
        envelope,
        result={
            "session": session.model_dump(mode="json"),
            "session_path": str(value.session_path),
            "message": value.message,
        },
        next_allowed_commands=allowed_session_commands(session),
        changed_paths=list(dict.fromkeys(changed)),
    )


def allowed_session_commands(session: CreationSession) -> list[str]:
    if session.status == "outline_proposed":
        return ["session.revise_outline", "session.approve_outline"]
    if session.status == "outline_approved":
        return ["session.run"]
    if session.status in {"needs_revision", "needs_user_review"}:
        return [
            "session.revise_content",
            "session.revise_audit",
            "session.retry_rewrite",
            "session.undo_rewrite",
            "session.accept",
        ]
    if session.status == "accepted":
        return ["session.archive"]
    if session.status in {"generating"}:
        return ["session.cancel"]
    return []


@_handler("revision.blocks")
def _handle_revision_blocks(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, RevisionBlocksCommand):
        raise DomainError("invalid_command", "revision.blocks payload type mismatch")
    blocks = list_revision_blocks(root, command.chapter_number)
    return _result(
        envelope,
        result={"chapter_number": command.chapter_number, "blocks": blocks},
        next_allowed_commands=["revision.start"],
    )


@_handler("revision.start")
def _handle_revision_start(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, RevisionStartCommand):
        raise DomainError("invalid_command", "revision.start payload type mismatch")
    value = start_revision_session(
        RevisionStartOptions(
            root=root,
            chapter_number=command.chapter_number,
            start_block=command.start_block,
            end_block=command.end_block,
            instruction=command.instruction,
        )
    )
    return _revision_command_result(envelope, value)


@_handler("revision.show", "revision.run", "revision.accept")
def _handle_revision_command(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, RevisionCommand):
        raise DomainError("invalid_command", "revision payload type mismatch")
    if command.type == "revision.show":
        value = show_revision_session(root, command.revision_session_id)
    elif command.type == "revision.run":
        value = run_revision_session(
            RevisionRunOptions(
                root=root,
                revision_session_id=command.revision_session_id,
                provider_name=command.provider_name,
                use_search_context=command.use_search_context,
                use_vector_context=command.use_vector_context,
            )
        )
    else:
        value = accept_revision_session(
            RevisionActionOptions(root=root, revision_session_id=command.revision_session_id)
        )
    return _revision_command_result(envelope, value)


def _revision_command_result(envelope: CommandEnvelope, value: RevisionSessionResult) -> CommandResult:
    session = value.session
    allowed = {
        "awaiting_patch": ["revision.run"],
        "failed_recoverable": ["revision.run"],
        "awaiting_review": ["revision.accept"],
    }.get(session.phase.value, [])
    return _result(
        envelope,
        result={
            "revision_session": session.model_dump(mode="json"),
            "session_path": str(value.session_path),
            "message": value.message,
        },
        next_allowed_commands=allowed,
        changed_paths=[value.session_path.relative_to(Path(envelope.project_root)).as_posix()],
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
