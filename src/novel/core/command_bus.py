from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
import uuid

from pydantic import ValidationError

from novel.core.artifact_store import resolve_project_path
from novel.core.budget import WorkflowBudgetExceeded, active_budget_tracker, workflow_budget_scope
from novel.core.io import atomic_write_model_json, load_json, load_json_model
from novel.core.contracts import (
    AgentConfigUpdateCommand,
    BudgetUsage,
    CanonApplyCommand,
    CanonSuggestCommand,
    ChapterCandidateSaveCommand,
    ChapterMemoryGenerateCommand,
    ChapterMemoryRebuildCommand,
    DefaultProviderSetupCommand,
    EmbeddingProviderSetupCommand,
    CommandEnvelope,
    CommandResult,
    MemoryRepairApplyCommand,
    MemoryRepairSuggestCommand,
    IndexUpdateCommand,
    InspirationGenerateCommand,
    PreviewPackageCommand,
    ProductionExportCommand,
    PublicCommand,
    ProjectShowCommand,
    ProjectInitCommand,
    ProjectStatusCommand,
    ProjectValidateCommand,
    ProjectWebPortSetupCommand,
    RevisionBlocksCommand,
    RevisionCommand,
    RevisionSession,
    RevisionStartCommand,
    SessionCommand,
    SessionStartCommand,
    SearchCommand,
    SchemaExportCommand,
    SettingChangeAnswerCommand,
    SettingChangeApplyCommand,
    SettingChangeSuggestCommand,
    StyleGuideGenerateCommand,
    StyleGuideSaveCommand,
    WebLauncherConfigCommand,
    Surface,
    WorkflowBudget,
    default_workflow_budget,
)
from novel.core.config_mutations import ConfigMutationError, update_agent_config
from novel.core.canon import (
    CanonError,
    CanonSuggestOptions,
    apply_canon_proposal,
    load_canon_provider,
    suggest_canon,
)
from novel.core.chapter_memory import (
    ChapterMemoryError,
    ChapterMemoryOptions,
    accepted_chapter_numbers,
    chapter_memory_freshness_warnings,
    chapter_memory_path,
    generate_chapter_memory,
    load_chapter_memory_provider,
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
from novel.core.inspiration import (
    InspirationError,
    InspirationOptions,
    load_inspiration_provider,
    run_inspiration_agent,
)
from novel.core.inspection import (
    ProjectReadError,
    format_canon,
    format_characters,
    format_state,
    format_timeline,
    get_project_status,
)
from novel.core.json_schema import export_json_schemas
from novel.core.memory_repair import (
    MemoryRepairError,
    SettingChangeSuggestionResult,
    answer_setting_change_clarification,
    apply_memory_repair,
    suggest_memory_repair,
    suggest_setting_change_interactive,
)
from novel.core.previewing import PreviewError, PreviewPackageOptions, build_preview_package
from novel.core.providers import ModelProvider
from novel.core.search import SearchError, search_project
from novel.core.search import rebuild_search_index, refresh_search_index, search_index_status
from novel.core.security import redact_secret_text
from novel.core.style_guide import (
    StyleGuideGenerationError,
    StyleGuideGenerationOptions,
    generate_style_guide,
    load_style_guide_provider,
)
from novel.core.setup_guide import (
    SetupGuideError,
    configure_default_provider,
    configure_embedding_provider,
    configure_web_port,
)
from novel.core.revision_workflow import (
    RevisionActionOptions,
    RevisionRunOptions,
    RevisionStartOptions,
    RevisionWorkflowError,
    RevisionSessionResult,
    accept_revision_session,
    cancel_revision_session,
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
from novel.core.schemas import ChapterMemory, CreationSession
from novel.core.validation import validate_project
from novel.core.workflow_runtime import workflow_runtime_scope
from novel.core import web_launcher
from novel.core.workspace_mutations import (
    STYLE_GUIDE_RELATIVE_PATH,
    WorkspaceMutationError,
    save_chapter_candidate,
    save_style_guide,
)
from novel.core.workspace import (
    InitOptions,
    WorkspaceExistsError,
    init_workspace,
    is_default_inspiration_placeholder,
)


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
UNLOCKED_WRITE_COMMANDS = {"project.init", "schema.export", "session.cancel"}
CONFIRMATION_REQUIRED = {
    "canon.apply",
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
    request_id: str | None = None,
    parent_request_id: str | None = None,
    budget: WorkflowBudget | None = None,
    initial_budget_usage: BudgetUsage | None = None,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=f"cmd_{uuid.uuid4().hex}",
        request_id=request_id or f"req_{uuid.uuid4().hex}",
        parent_request_id=parent_request_id,
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
                with ProjectLock(
                    root,
                    task=command_type,
                    workflow_run_id=envelope.workflow_run_id,
                    command_id=envelope.command_id,
                ):
                    return handler(envelope, root)

            if (root / "project.yaml").exists():
                with workflow_runtime_scope(
                    root=root,
                    workflow_run_id=envelope.workflow_run_id,
                    command_id=envelope.command_id,
                    surface=envelope.surface,
                    budget=budget,
                    request_id=envelope.request_id,
                    parent_request_id=envelope.parent_request_id,
                    session_id=_command_session_id(envelope.command),
                    workflow_type=(
                        "creation_session"
                        if command_type.startswith("session.")
                        else "revision_session"
                        if command_type.startswith("revision.")
                        else None
                    ),
                ) as runtime:
                    result = runtime.execute_node(
                        name=f"command:{command_type}",
                        node_type="command",
                        function=execute_handler,
                        request_id=envelope.request_id,
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
    except InspirationError as exc:
        raise DomainError("inspiration_error", str(exc), recoverable=True) from exc
    except CanonError as exc:
        raise DomainError("canon_error", str(exc), recoverable=True) from exc
    except ChapterMemoryError as exc:
        raise DomainError("chapter_memory_error", str(exc), recoverable=True) from exc
    except StyleGuideGenerationError as exc:
        raise DomainError("style_guide_error", str(exc), recoverable=True) from exc
    except WorkspaceMutationError as exc:
        raise DomainError(exc.code, exc.message, recoverable=True) from exc
    except ConfigMutationError as exc:
        raise DomainError(exc.code, exc.message, recoverable=True) from exc
    except SetupGuideError as exc:
        raise DomainError("setup_guide_error", str(exc), recoverable=True) from exc
    except web_launcher.PortUnavailableError as exc:
        raise DomainError("port_unavailable", str(exc), recoverable=True) from exc
    except web_launcher.WebLauncherError as exc:
        raise DomainError("web_launcher_error", str(exc), recoverable=True) from exc
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
        "request_id": value.request_id,
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
        request_id=envelope.request_id,
        workflow_run_id=envelope.workflow_run_id,
        command_type=envelope.command.type,
        result=result,
        next_allowed_commands=next_allowed_commands or [],
        warnings=warnings or [],
        changed_paths=changed_paths or [],
    )


def _command_session_id(command: PublicCommand) -> str | None:
    if isinstance(command, SessionCommand):
        return command.session_id
    if isinstance(command, RevisionCommand):
        return command.revision_session_id
    return None


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


@_handler("schema.export")
def _handle_schema_export(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, SchemaExportCommand):
        raise DomainError("invalid_command", "schema.export payload type mismatch")
    output = Path(command.output_path).expanduser().resolve()
    paths = export_json_schemas(output)
    return _result(
        envelope,
        result={
            "output": str(output),
            "schema_count": len(paths),
            "files": [str(path) for path in paths],
        },
        changed_paths=[str(path) for path in paths],
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


@_handler("inspiration.generate")
def _handle_inspiration_generate(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, InspirationGenerateCommand):
        raise DomainError("invalid_command", "inspiration.generate payload type mismatch")
    provider = load_inspiration_provider(
        root,
        command.provider_name,
        agent_config_path=Path(command.agent_config_path) if command.agent_config_path else None,
        model_name=command.model_name,
    )
    value = run_inspiration_agent(
        InspirationOptions(
            root=root,
            source_text=command.source_text,
            source_type=command.source_type,
            write_json=command.write_json,
            overwrite=command.overwrite
            or (
                command.allow_default_placeholder
                and is_default_inspiration_placeholder(root / "memory" / "inspiration.md")
            ),
            use_search_context=command.use_search_context,
            use_vector_context=command.use_vector_context,
        ),
        provider,
    )
    changed_paths = [value.markdown_path.relative_to(root).as_posix()]
    if value.json_path:
        changed_paths.append(value.json_path.relative_to(root).as_posix())
    return _result(
        envelope,
        result={
            "markdown_path": str(value.markdown_path),
            "json_path": str(value.json_path) if value.json_path else None,
            "context_report_path": str(value.context_report_path) if value.context_report_path else None,
        },
        changed_paths=changed_paths,
    )


@_handler("canon.suggest")
def _handle_canon_suggest(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, CanonSuggestCommand):
        raise DomainError("invalid_command", "canon.suggest payload type mismatch")
    output_path = _resolve_command_file(envelope, root, command.output_path) if command.output_path else None
    provider = load_canon_provider(
        root,
        command.provider_name,
        agent_config_path=Path(command.agent_config_path) if command.agent_config_path else None,
        model_name=command.model_name,
    )
    value = suggest_canon(
        CanonSuggestOptions(
            root=root,
            output_path=output_path,
            use_search_context=command.use_search_context,
            use_vector_context=command.use_vector_context,
        ),
        provider,
    )
    return _result(
        envelope,
        result={
            "output_path": str(value.output_path) if value.output_path else None,
            "relative_path": _changed_path(root, value.output_path) if value.output_path else None,
            "proposal": value.proposal.model_dump(mode="json"),
            "proposal_json": value.proposal_json,
            "context_report_path": str(value.context_report_path) if value.context_report_path else None,
        },
        next_allowed_commands=["canon.apply"] if value.output_path else [],
        changed_paths=[_changed_path(root, value.output_path)] if value.output_path else [],
    )


@_handler("canon.apply")
def _handle_canon_apply(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, CanonApplyCommand):
        raise DomainError("invalid_command", "canon.apply payload type mismatch")
    proposal_path = _resolve_command_file(envelope, root, command.proposal_path)
    value = apply_canon_proposal(root, proposal_path)
    changed_paths = [
        value.apply_log_path.relative_to(root).as_posix(),
        value.proposal_snapshot_path.relative_to(root).as_posix(),
        *value.apply_log.target_files,
    ]
    return _result(
        envelope,
        result={
            "proposal_path": str(proposal_path),
            "apply_log": value.apply_log.model_dump(mode="json"),
            "apply_log_path": str(value.apply_log_path),
            "apply_log_relative_path": value.apply_log_path.relative_to(root).as_posix(),
            "proposal_snapshot_path": str(value.proposal_snapshot_path),
            "proposal_snapshot_relative_path": value.proposal_snapshot_path.relative_to(root).as_posix(),
            "validation_ok": value.validation_report.ok,
            "errors": [message.message for message in value.validation_report.errors],
            "warnings": [message.message for message in value.validation_report.warnings],
        },
        warnings=[message.message for message in value.validation_report.warnings],
        changed_paths=list(dict.fromkeys(changed_paths)),
    )


def _resolve_command_file(envelope: CommandEnvelope, root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        resolved = path.resolve()
        if envelope.surface != Surface.CLI and not resolved.is_relative_to(root):
            raise DomainError("forbidden_file", "command path escapes project root", recoverable=True)
        return resolved
    return resolve_project_path(root, value)


def _changed_path(root: Path, path: Path) -> str:
    resolved = path.resolve()
    return resolved.relative_to(root).as_posix() if resolved.is_relative_to(root) else str(resolved)


@_handler("chapter_memory.generate")
def _handle_chapter_memory_generate(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, ChapterMemoryGenerateCommand):
        raise DomainError("invalid_command", "chapter_memory.generate payload type mismatch")
    provider, provider_warnings = _chapter_memory_provider(root, command, command.chapter_number)
    value = generate_chapter_memory(
        ChapterMemoryOptions(
            root=root,
            chapter_number=command.chapter_number,
            force=command.force,
        ),
        provider,
        initial_warnings=tuple(provider_warnings),
    )
    return _result(
        envelope,
        result={
            "chapter_number": value.memory.chapter_number,
            "memory_path": str(value.memory_path),
            "relative_path": value.memory_path.relative_to(root).as_posix(),
            "generation_status": value.memory.generation_status,
            "warnings": list(value.warnings),
        },
        warnings=list(value.warnings),
        changed_paths=[value.memory_path.relative_to(root).as_posix()],
    )


@_handler("chapter_memory.rebuild")
def _handle_chapter_memory_rebuild(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, ChapterMemoryRebuildCommand):
        raise DomainError("invalid_command", "chapter_memory.rebuild payload type mismatch")
    written: list[dict[str, object]] = []
    skipped: list[int] = []
    warnings: list[str] = []
    changed_paths: list[str] = []
    for chapter_number in accepted_chapter_numbers(root):
        path = chapter_memory_path(root, chapter_number)
        should_generate = command.mode == "all" or not path.exists()
        if not should_generate and command.mode == "missing_or_stale":
            try:
                should_generate = bool(chapter_memory_freshness_warnings(root, load_json_model(path, ChapterMemory)))
            except Exception:
                should_generate = True
        if not should_generate:
            skipped.append(chapter_number)
            continue
        try:
            provider, provider_warnings = _chapter_memory_provider(root, command, chapter_number)
            value = generate_chapter_memory(
                ChapterMemoryOptions(root=root, chapter_number=chapter_number, force=True),
                provider,
                initial_warnings=tuple(provider_warnings),
            )
            relative_path = value.memory_path.relative_to(root).as_posix()
            written.append(
                {
                    "chapter_number": value.memory.chapter_number,
                    "memory_path": str(value.memory_path),
                    "relative_path": relative_path,
                    "generation_status": value.memory.generation_status,
                    "warnings": list(value.warnings),
                }
            )
            changed_paths.append(relative_path)
            warnings.extend(f"chapter {chapter_number}: {warning}" for warning in value.warnings)
        except Exception as exc:
            warnings.append(f"chapter {chapter_number}: {redact_secret_text(str(exc))}")
    return _result(
        envelope,
        result={"mode": command.mode, "written": written, "skipped": skipped, "warnings": warnings},
        warnings=warnings,
        changed_paths=changed_paths,
    )


def _chapter_memory_provider(
    root: Path,
    command: ChapterMemoryGenerateCommand | ChapterMemoryRebuildCommand,
    chapter_number: int,
) -> tuple[ModelProvider | None, list[str]]:
    try:
        return (
            load_chapter_memory_provider(
                root,
                command.provider_name,
                chapter_number=chapter_number,
                agent_config_path=Path(command.agent_config_path) if command.agent_config_path else None,
                model_name=command.model_name,
            ),
            [],
        )
    except Exception as exc:
        return None, [
            "chapter memory provider unavailable; using deterministic fallback: "
            f"{redact_secret_text(str(exc))}"
        ]


@_handler("index.rebuild", "index.refresh")
def _handle_index_update(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, IndexUpdateCommand):
        raise DomainError("invalid_command", "index update payload type mismatch")
    embedding_config_path = Path(command.embedding_config_path) if command.embedding_config_path else None
    if command.type == "index.rebuild":
        value = rebuild_search_index(
            root,
            embedding_provider_name=command.embedding_provider_name,
            embedding_config_path=embedding_config_path,
            with_embeddings=command.with_embeddings,
        )
        counts: dict[str, object] = {}
    else:
        value = refresh_search_index(
            root,
            embedding_provider_name=command.embedding_provider_name,
            embedding_config_path=embedding_config_path,
            with_embeddings=command.with_embeddings,
        )
        counts = {
            "refreshed_count": value.refreshed_count,
            "deleted_count": value.deleted_count,
        }
    changed_paths = [
        value.index_path.relative_to(root).as_posix(),
        value.sqlite_path.relative_to(root).as_posix(),
        value.manifest_path.relative_to(root).as_posix(),
    ]
    return _result(
        envelope,
        result={
            "index_path": str(value.index_path),
            "sqlite_path": str(value.sqlite_path),
            "manifest_path": str(value.manifest_path),
            "document_count": value.document_count,
            "embedding_document_count": value.embedding_document_count,
            "with_embeddings": value.with_embeddings,
            **counts,
            "search": search_index_status(root).as_dict(),
        },
        changed_paths=changed_paths,
    )


@_handler("style_guide.save")
def _handle_style_guide_save(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, StyleGuideSaveCommand):
        raise DomainError("invalid_command", "style_guide.save payload type mismatch")
    value = save_style_guide(root, command.content)
    return _result(
        envelope,
        result={
            "path": STYLE_GUIDE_RELATIVE_PATH,
            "backup_path": value.backup_path.relative_to(root).as_posix() if value.backup_path else None,
            "content": value.content,
        },
        changed_paths=[STYLE_GUIDE_RELATIVE_PATH],
    )


@_handler("style_guide.generate")
def _handle_style_guide_generate(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, StyleGuideGenerateCommand):
        raise DomainError("invalid_command", "style_guide.generate payload type mismatch")
    provider = load_style_guide_provider(
        root,
        command.provider_name,
        agent_config_path=Path(command.agent_config_path) if command.agent_config_path else None,
        model_name=command.model_name,
    )
    value = generate_style_guide(
        StyleGuideGenerationOptions(
            root=root,
            instruction=command.instruction,
            include_project_context=command.include_project_context,
            include_existing_style=command.include_existing_style,
        ),
        provider,
    )
    return _result(
        envelope,
        result={
            "path": STYLE_GUIDE_RELATIVE_PATH,
            "content": value.content,
            "warnings": list(value.warnings),
        },
        warnings=list(value.warnings),
    )


@_handler("chapter_candidate.save")
def _handle_chapter_candidate_save(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, ChapterCandidateSaveCommand):
        raise DomainError("invalid_command", "chapter_candidate.save payload type mismatch")
    value = save_chapter_candidate(
        root,
        chapter_number=command.chapter_number,
        target=command.target,
        source_file=command.source_file,
        content=command.content,
        instruction=command.instruction,
    )
    output_path = value.output_path.relative_to(root).as_posix()
    log_path = value.revision_log_path.relative_to(root).as_posix()
    return _result(
        envelope,
        result={
            "output_path": str(value.output_path),
            "relative_path": output_path,
            "revision_log_path": str(value.revision_log_path),
            "record": value.record.model_dump(mode="json"),
        },
        changed_paths=[output_path, log_path],
    )


@_handler("agent_config.update")
def _handle_agent_config_update(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, AgentConfigUpdateCommand):
        raise DomainError("invalid_command", "agent_config.update payload type mismatch")
    value = update_agent_config(
        root,
        default_update=command.default,
        profiles_update=command.profiles,
        tasks_update=command.tasks,
        clear_profiles=command.clear_profiles,
        clear_tasks=command.clear_tasks,
    )
    config_path = value.path.relative_to(root).as_posix()
    return _result(
        envelope,
        result={
            "path": str(value.path),
            "backup_path": str(value.backup_path) if value.backup_path else None,
            "cleared_profiles": list(value.cleared_profiles),
            "cleared_tasks": list(value.cleared_tasks),
            "config_data": value.config.model_dump(mode="json", exclude_none=True),
        },
        changed_paths=[config_path],
    )


@_handler("setup.default_provider")
def _handle_default_provider_setup(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, DefaultProviderSetupCommand):
        raise DomainError("invalid_command", "setup.default_provider payload type mismatch")
    value = configure_default_provider(
        root,
        provider=command.provider,
        base_url=command.base_url,
        api_key=command.api_key,
        model=command.model,
        max_context_tokens=command.max_context_tokens,
        max_tokens=command.max_tokens,
        timeout_seconds=command.timeout_seconds,
        max_retries=command.max_retries,
        ping=command.ping,
    )
    return _result(
        envelope,
        result={
            "config_path": str(value.config_path),
            "env_path": str(value.env_path),
            "provider": value.provider,
            "model": value.model,
            "api_key_env": value.api_key_env,
            "base_url_env": value.base_url_env,
            "ping_ok": value.ping_ok,
            "ping_message": value.ping_message,
        },
        changed_paths=[
            value.config_path.relative_to(root).as_posix(),
            value.env_path.relative_to(root).as_posix(),
        ],
    )


@_handler("setup.embedding_provider")
def _handle_embedding_provider_setup(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, EmbeddingProviderSetupCommand):
        raise DomainError("invalid_command", "setup.embedding_provider payload type mismatch")
    if command.skip:
        return _result(
            envelope,
            result={"skipped": True, "message": "已跳过 embedding API 配置；关键词/FTS 检索仍可用。"},
        )
    value = configure_embedding_provider(
        root,
        provider=command.provider,
        provider_name=command.provider_name,
        base_url=command.base_url,
        api_key=command.api_key,
        model=command.model,
        dimensions=command.dimensions,
        batch_size=command.batch_size,
        timeout_seconds=command.timeout_seconds,
        max_retries=command.max_retries,
        ping=command.ping,
    )
    return _result(
        envelope,
        result={
            "config_path": str(value.config_path),
            "env_path": str(value.env_path),
            "active_provider": value.active_provider,
            "provider": value.provider,
            "model": value.model,
            "dimensions": value.dimensions,
            "batch_size": value.batch_size,
            "api_key_env": value.api_key_env,
            "base_url_env": value.base_url_env,
            "ping_ok": value.ping_ok,
            "ping_message": value.ping_message,
        },
        changed_paths=[
            value.config_path.relative_to(root).as_posix(),
            value.env_path.relative_to(root).as_posix(),
        ],
    )


@_handler("setup.project_web_port")
def _handle_project_web_port_setup(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, ProjectWebPortSetupCommand):
        raise DomainError("invalid_command", "setup.project_web_port payload type mismatch")
    value = configure_web_port(root, requested_port=command.requested_port, host=command.host)
    return _result(
        envelope,
        result={
            "project_path": str(value.project_path),
            "host": value.host,
            "requested_port": value.requested_port,
            "selected_port": value.selected_port,
            "url": f"http://{value.host}:{value.selected_port}",
        },
        changed_paths=[value.project_path.relative_to(root).as_posix()],
    )


@_handler("setup.web_launcher")
def _handle_web_launcher_setup(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, WebLauncherConfigCommand):
        raise DomainError("invalid_command", "setup.web_launcher payload type mismatch")
    config_path = web_launcher.launcher_config_path_from_env()
    value = web_launcher.save_web_launcher_port_config(
        config_path,
        host=command.host,
        requested_port=command.requested_port,
        current_host=command.current_host,
        current_port=command.current_port,
    )
    launcher_path: Path | None = web_launcher.launcher_path_from_env()
    try:
        web_launcher.write_web_launcher_command(
            launcher_path,
            config_path=value.config_path,
            cwd=Path.cwd(),
        )
    except OSError:
        launcher_path = None
    return _result(
        envelope,
        result={
            "launcher_config_path": str(value.config_path),
            "launcher_path": str(launcher_path) if launcher_path else "",
            "host": value.host,
            "requested_port": value.requested_port,
            "selected_port": value.selected_port,
            "available": value.requested_port == value.selected_port,
            "url": value.url,
        },
        changed_paths=[str(value.config_path), *([str(launcher_path)] if launcher_path else [])],
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
    if session.phase.value == "awaiting_outline_approval":
        return ["session.revise_outline", "session.approve_outline"]
    if session.phase.value == "ready_to_run":
        return ["session.run"]
    if session.phase.value == "awaiting_content_review":
        return [
            "session.revise_content",
            "session.revise_audit",
            "session.retry_rewrite",
            "session.undo_rewrite",
            "session.accept",
        ]
    if session.phase.value == "ready_to_commit":
        return ["session.accept", "session.revise_content"]
    if session.phase.value == "committed":
        return ["session.archive"]
    if session.phase.value in {"running", "revising"}:
        return ["session.cancel"]
    if session.phase.value == "failed_recoverable":
        if session.failure_node == "revision.content":
            return ["session.revise_content", "session.show", "session.cancel"]
        if session.failure_node == "revision.audit":
            return ["session.revise_audit", "session.show", "session.cancel"]
        if session.failure_node == "revision.retry":
            return ["session.retry_rewrite", "session.show", "session.cancel"]
        if session.failure_node == "revision.undo":
            return ["session.undo_rewrite", "session.show", "session.cancel"]
        return ["session.run", "session.show", "session.cancel"]
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


@_handler("revision.show", "revision.run", "revision.accept", "revision.cancel")
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
    elif command.type == "revision.accept":
        value = accept_revision_session(
            RevisionActionOptions(root=root, revision_session_id=command.revision_session_id)
        )
    else:
        value = cancel_revision_session(
            RevisionActionOptions(root=root, revision_session_id=command.revision_session_id)
        )
    return _revision_command_result(envelope, value)


def _revision_command_result(envelope: CommandEnvelope, value: RevisionSessionResult) -> CommandResult:
    session = value.session
    allowed = {
        "awaiting_patch": ["revision.run", "revision.cancel"],
        "failed_recoverable": ["revision.run", "revision.cancel"],
        "awaiting_review": ["revision.accept", "revision.cancel"],
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
