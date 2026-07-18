from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from novel.core.budget import WorkflowBudgetExceeded, active_budget_tracker, workflow_budget_scope
from novel.core.command_handlers import map_domain_error, register_builtin_handlers
from novel.core.command_registry import COMMAND_SPECS, command_spec
from novel.core.command_workflow_state import (
    effective_command_budget,
    persist_failed_session_budget,
    persist_session_budget,
    resume_session_workflow,
)
from novel.core.contracts import (
    BudgetUsage,
    CommandEnvelope,
    CommandResult,
    PreviewPackageCommand,
    ProductionExportCommand,
    PublicCommand,
    RevisionCommand,
    RevisionStartCommand,
    SessionCommand,
    SessionStartCommand,
    Surface,
    WorkflowBudget,
    default_workflow_budget,
)
from novel.core.locking import ProjectLock, ProjectLockError
from novel.core.timeutil import utc_now
from novel.core.workflow_runtime import workflow_runtime_scope

if TYPE_CHECKING:
    from novel.core.schemas import CreationSession


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
        envelope = resume_session_workflow(root, envelope)
        command_type = envelope.command.type
        spec = command_spec(command_type)
        if spec.confirmation_required and not envelope.confirmed:
            raise DomainError(
                "confirmation_required",
                f"command requires explicit confirmation: {command_type}",
                recoverable=True,
                details={"command_type": command_type},
            )
        handler = COMMAND_HANDLERS.get(command_type)
        if not handler:
            raise DomainError("unknown_command", f"no handler registered for {command_type}")
        budget, initial_usage = effective_command_budget(root, envelope)
        effective_budget = budget
        with workflow_budget_scope(
            budget,
            initial_usage=initial_usage,
        ) as budget_tracker:

            def execute_handler() -> CommandResult:
                _reserve_command_scope(envelope.command)
                if not spec.lock_required:
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
            result = persist_session_budget(result, envelope.command, budget, usage)
            return result.model_copy(update={"budget_usage": usage})
    except DomainError:
        raise
    except ProjectLockError as exc:
        raise DomainError("project_locked", str(exc), recoverable=True) from exc
    except WorkflowBudgetExceeded as exc:
        if budget_tracker is not None:
            persist_failed_session_budget(
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
    except Exception as exc:
        mapped = map_domain_error(exc)
        if mapped is not None:
            raise mapped from exc
        raise


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


def _handler(*command_types: str) -> Callable[[CommandHandler], CommandHandler]:
    def register(function: CommandHandler) -> CommandHandler:
        for command_type in command_types:
            if command_type not in COMMAND_SPECS:
                raise RuntimeError(f"handler has no CommandSpec: {command_type}")
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


def allowed_session_commands(session: CreationSession) -> list[str]:
    from novel.core.command_handlers.session import allowed_session_commands as resolve_allowed_commands

    return resolve_allowed_commands(session)

register_builtin_handlers()
