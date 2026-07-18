from __future__ import annotations

from pathlib import Path

from novel.core.contracts import (
    BudgetUsage,
    CommandEnvelope,
    CommandResult,
    PublicCommand,
    RevisionCommand,
    RevisionSession,
    RevisionStartCommand,
    SessionCommand,
    SessionStartCommand,
    WorkflowBudget,
)
from novel.core.io import atomic_write_model_json
from novel.core.revision_workflow import RevisionWorkflowError, show_revision_session
from novel.core.schemas import CreationSession
from novel.core.session import CreationSessionError, show_session


def effective_command_budget(root: Path, envelope: CommandEnvelope) -> tuple[WorkflowBudget, BudgetUsage]:
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


def resume_session_workflow(root: Path, envelope: CommandEnvelope) -> CommandEnvelope:
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


def persist_session_budget(
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


def persist_failed_session_budget(
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


__all__ = [
    "effective_command_budget",
    "persist_failed_session_budget",
    "persist_session_budget",
    "resume_session_workflow",
]
