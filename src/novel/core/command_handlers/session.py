from __future__ import annotations

from pathlib import Path

from novel.core.command_bus import DomainError, _handler, _result
from novel.core.contracts import (
    CommandEnvelope,
    CommandResult,
    RevisionBlocksCommand,
    RevisionCommand,
    RevisionStartCommand,
    SessionCommand,
    SessionStartCommand,
)
from novel.core.revision_workflow import (
    RevisionActionOptions,
    RevisionRunOptions,
    RevisionSessionResult,
    RevisionStartOptions,
    accept_revision_session,
    cancel_revision_session,
    list_revision_blocks,
    run_revision_session,
    show_revision_session,
    start_revision_session,
)
from novel.core.schemas import CreationSession
from novel.core.session import (
    SessionActionOptions,
    SessionInstructionOptions,
    SessionResult,
    SessionRewriteControlOptions,
    SessionRunOptions,
    SessionStartOptions,
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
