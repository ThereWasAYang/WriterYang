from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from novel.core.artifact_store import (
    AuditCandidateMismatchError,
    capture_working_chapter,
    require_working_audit_matches_candidate,
)
from novel.core.auditing import ChapterAuditOptions, audit_chapter, load_audit_provider
from novel.core.budget import consume_auto_revision_round
from novel.core.contracts import (
    ChapterNodeState,
    ChapterNodeStatus,
    SessionPhase,
    SessionProjection,
    validate_session_transition,
)
from novel.core.drafting import ChapterDraftingOptions, load_drafting_provider, write_chapter_draft
from novel.core.io import atomic_write_model_json, atomic_write_text, backup_if_exists, load_json_model, load_yaml_model
from novel.core.lifecycle import LifecycleError, commit_creation_session
from novel.core.management import record_management_event
from novel.core.orchestrator import route_audit_repair, route_revision_request
from novel.core.planning import ChapterPlanningOptions, load_planning_provider, plan_chapter
from novel.core.polishing import (
    ChapterPolishingOptions,
    load_polishing_provider,
    polish_chapter,
    read_markdown_with_front_matter,
)
from novel.core.projection import advance_projection, initialize_projection, projection_dir, projection_paths
from novel.core.revision import ChapterRevisionOptions, load_revision_provider, revise_chapter
from novel.core.runtime_config import normalize_polish_mode, project_polish_mode
from novel.core.schemas import (
    AuditReport,
    ChapterPlan,
    CreationArchiveEntry,
    CreationArchiveManifest,
    CreationOutline,
    CreationOutlineChapter,
    CreationSession,
    PolishMode,
    ProjectConfig,
    RevisionRouteDecision,
    RevisionRouteRecord,
    SessionAuditRevision,
    SessionProgress,
    SessionRewriteAction,
    SessionRewriteEvent,
    SessionRewriteEvents,
    SessionRewriteIssue,
    SessionRewriteStatus,
    StateUpdateProposal,
    VectorContextMode,
)
from novel.core.security import redact_secret_text
from novel.core.session_progress import (
    load_session_progress,
)
from novel.core.session_progress import (
    record_session_progress as _record_session_progress,
)
from novel.core.session_progress import (
    start_session_progress as _start_session_progress,
)
from novel.core.state_update import (
    StateUpdateProposeOptions,
    load_state_update_provider,
    propose_state_update,
)
from novel.core.timeutil import new_request_id, utc_now, utc_timestamp
from novel.core.transactions import TransactionError
from novel.core.workflow_runtime import bind_active_session_id

ProviderName = str


class CreationSessionError(RuntimeError):
    """Raised when a collaborative creation session cannot proceed safely."""


class _SessionCancelRequested(RuntimeError):
    """Internal signal for cooperative cancellation at safe session boundaries."""


@dataclass(frozen=True)
class SessionStartOptions:
    root: Path
    user_intent: str
    chapter_range: tuple[int, ...]
    provider_name: ProviderName = "config"
    force: bool = False
    use_search_context: bool = True
    use_vector_context: bool | VectorContextMode = "auto"
    polish_mode: PolishMode | None = None


@dataclass(frozen=True)
class SessionRunOptions:
    root: Path
    session_id: str
    provider_name: ProviderName = "config"
    force: bool = False
    max_auto_revision_rounds: int | None = None
    use_search_context: bool = True
    use_vector_context: bool | VectorContextMode = "auto"
    polish_mode: PolishMode | None = None


@dataclass(frozen=True)
class SessionInstructionOptions:
    root: Path
    session_id: str
    instruction: str | None
    provider_name: ProviderName = "config"
    force: bool = False
    from_audit: bool = False
    use_search_context: bool = True
    use_vector_context: bool | VectorContextMode = "auto"
    polish_mode: PolishMode | None = None


@dataclass(frozen=True)
class SessionActionOptions:
    root: Path
    session_id: str
    provider_name: ProviderName = "config"
    force: bool = False


@dataclass(frozen=True)
class SessionRewriteControlOptions:
    root: Path
    session_id: str
    event_id: str
    instruction: str | None = None
    provider_name: ProviderName = "config"
    force: bool = False
    use_search_context: bool = True
    use_vector_context: bool | VectorContextMode = "auto"
    polish_mode: PolishMode | None = None


@dataclass(frozen=True)
class SessionResult:
    session: CreationSession
    session_path: Path
    message: str


@dataclass(frozen=True)
class _OutlinePlanPromotion:
    chapter: CreationOutlineChapter
    source_json: Path
    source_markdown: Path
    target_json: Path
    target_markdown: Path
    already_promoted: bool


@dataclass(frozen=True)
class _TransactionalTextWrite:
    path: Path
    content: str
    backup_reason: str | None = None


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    existed: bool
    content: str | None
    missing_parent_dirs: tuple[Path, ...]


@dataclass
class _TransactionAttempt:
    snapshot: _FileSnapshot
    backup_path: Path | None = None


def _validated_session_copy(session: CreationSession, **updates: object) -> CreationSession:
    payload = session.model_dump(mode="python")
    payload.update(updates)
    return CreationSession.model_validate(payload)


def _transition_session(
    session: CreationSession,
    target: SessionPhase,
    **updates: object,
) -> CreationSession:
    validate_session_transition(session.phase, target)
    return _validated_session_copy(
        session,
        phase=target,
        updated_at=utc_now(),
        **updates,
    )


def _set_chapter_node(
    session: CreationSession,
    chapter_number: int,
    node: str,
    status: ChapterNodeStatus,
    *,
    last_completed_node: str | None = None,
) -> CreationSession:
    current = session.chapter_runs[chapter_number]
    if node not in ChapterNodeState.model_fields:
        raise CreationSessionError(f"unknown chapter node: {node}")
    run_payload = current.model_dump(mode="python")
    run_payload[node] = status
    run = ChapterNodeState.model_validate(run_payload)
    chapter_runs = dict(session.chapter_runs)
    chapter_runs[chapter_number] = run
    return _validated_session_copy(
        session,
        chapter_runs=chapter_runs,
        last_completed_node=last_completed_node or session.last_completed_node,
        failure_node=None if status is not ChapterNodeStatus.FAILED else node,
        updated_at=utc_now(),
    )


def start_session(options: SessionStartOptions) -> SessionResult:
    root = options.root.resolve()
    _validate_chapters(options.chapter_range)
    session_id = _new_session_id()
    bind_active_session_id(session_id)
    session = CreationSession(
        session_id=session_id,
        chapter_range=list(options.chapter_range),
        user_intent=options.user_intent.strip(),
        phase=SessionPhase.DRAFTING_OUTLINE,
        chapter_runs={
            chapter_number: ChapterNodeState(chapter_number=chapter_number)
            for chapter_number in options.chapter_range
        },
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    _ensure_session_mutable(root, session)
    session_dir = _session_dir(root, session_id)
    created_session_dir = not session_dir.exists()
    session_dir.mkdir(parents=True, exist_ok=True)
    try:
        session = _write_outline_proposal(
            root,
            session,
            options.provider_name,
            options.force,
            use_search_context=options.use_search_context,
            use_vector_context=options.use_vector_context,
        )
        _write_session(root, session)
    except Exception:
        if created_session_dir and not _session_path(root, session_id).exists():
            shutil.rmtree(session_dir, ignore_errors=True)
        raise
    return SessionResult(
        session=session,
        session_path=_session_path(root, session_id),
        message="Session started and outline proposal generated.",
    )


def show_session(root: Path, session_id: str) -> SessionResult:
    root = root.resolve()
    session = load_session(root, session_id)
    return SessionResult(session=session, session_path=_session_path(root, session_id), message="Session loaded.")


def revise_outline(options: SessionInstructionOptions) -> SessionResult:
    root = options.root.resolve()
    session = load_session(root, options.session_id)
    _ensure_session_mutable(root, session)
    if session.phase not in {SessionPhase.AWAITING_OUTLINE_APPROVAL, SessionPhase.READY_TO_RUN}:
        raise CreationSessionError(f"outline cannot be revised from phase {session.phase.value}")
    if session.phase is SessionPhase.READY_TO_RUN:
        session = _transition_session(
            session,
            SessionPhase.DRAFTING_OUTLINE,
            approved_outline_path=None,
            chapter_runs={
                number: ChapterNodeState(chapter_number=number)
                for number in session.chapter_range
            },
        )
    if not options.instruction or not options.instruction.strip():
        raise CreationSessionError("outline revision requires --instruction")
    merged_intent = f"{session.user_intent}\n\n用户对大纲的修改意见：{options.instruction.strip()}"
    session = _validated_session_copy(session, user_intent=merged_intent, updated_at=utc_now())
    session = _write_outline_proposal(
        root,
        session,
        options.provider_name,
        force=True,
        use_search_context=options.use_search_context,
        use_vector_context=options.use_vector_context,
    )
    _write_session(root, session)
    return SessionResult(
        session=session, session_path=_session_path(root, session.session_id), message="Outline revised."
    )


def approve_outline(options: SessionActionOptions) -> SessionResult:
    root = options.root.resolve()
    session = load_session(root, options.session_id)
    _ensure_session_mutable(root, session)
    if session.phase is not SessionPhase.AWAITING_OUTLINE_APPROVAL:
        raise CreationSessionError(f"outline cannot be approved from phase {session.phase.value}")
    proposal_json = _session_dir(root, session.session_id) / "outline_proposal.json"
    proposal_md = _session_dir(root, session.session_id) / "outline_proposal.md"
    if not proposal_json.exists() or not proposal_md.exists():
        raise CreationSessionError("outline proposal is missing; run session start or revise-outline first")
    proposal = load_json_model(proposal_json, CreationOutline)
    approved_json = _session_dir(root, session.session_id) / "approved_outline.json"
    approved_md = _session_dir(root, session.session_id) / "approved_outline.md"
    _refuse_existing(approved_json, options.force)
    _refuse_existing(approved_md, options.force)
    approved_outline, writes = _prepare_promoted_outline_plan_writes(root, proposal, force=options.force)
    approved_runs = {
        chapter_number: run.model_copy(update={"plan": ChapterNodeStatus.COMPLETED})
        for chapter_number, run in session.chapter_runs.items()
    }
    session = _transition_session(
        session,
        SessionPhase.READY_TO_RUN,
        approved_outline_path=_rel(root, approved_json),
        chapter_runs=approved_runs,
        last_completed_node="outline_approval",
    )
    approval_backup_reason = "force" if options.force else None
    writes.extend(
        [
            _TransactionalTextWrite(
                path=approved_json,
                content=approved_outline.model_dump_json(indent=2) + "\n",
                backup_reason=approval_backup_reason,
            ),
            _TransactionalTextWrite(
                path=approved_md,
                content=_render_outline_markdown(approved_outline),
                backup_reason=approval_backup_reason,
            ),
            _TransactionalTextWrite(
                path=_session_path(root, session.session_id), content=session.model_dump_json(indent=2) + "\n"
            ),
        ]
    )
    _write_text_transaction(root, writes, action="outline approval")
    return SessionResult(
        session=session, session_path=_session_path(root, session.session_id), message="Outline approved."
    )


def run_session(options: SessionRunOptions) -> SessionResult:
    root = options.root.resolve()
    session = load_session(root, options.session_id)
    _ensure_session_mutable(root, session)
    if session.phase not in {SessionPhase.READY_TO_RUN, SessionPhase.FAILED_RECOVERABLE}:
        if session.phase is SessionPhase.AWAITING_OUTLINE_APPROVAL:
            raise CreationSessionError("approve the outline before running content generation")
        if session.phase is SessionPhase.AWAITING_CONTENT_REVIEW:
            raise CreationSessionError("session content is already generated and awaiting review")
        raise CreationSessionError(f"session cannot run content generation from phase {session.phase.value}")
    if session.phase is SessionPhase.FAILED_RECOVERABLE and session.failure_node and session.failure_node.startswith(
        "revision"
    ):
        raise CreationSessionError("resume this failed revision with the matching revision command")
    if session.phase is SessionPhase.READY_TO_RUN and _session_has_generated_content(session):
        raise CreationSessionError(
            "session content is already generated; review, revise, accept, or archive it instead of running content generation"
        )
    projection = initialize_projection(root, session.session_id)
    world_state_dir = projection_dir(root, session.session_id)

    max_rounds = options.max_auto_revision_rounds
    if max_rounds is None:
        max_rounds = session.max_auto_revision_rounds
    session = _transition_session(
        session,
        SessionPhase.RUNNING,
        failure_node=None,
        failure_message=None,
        resume_count=session.resume_count + (1 if session.phase is SessionPhase.FAILED_RECOVERABLE else 0),
    )
    _write_session(root, session)
    _start_session_progress(root, session.session_id, message="Session 写作任务已开始。")

    final_outputs: list[str] = list(session.final_output_paths)
    audits: list[str] = list(session.audit_history)
    revisions: list[str] = list(session.revision_history)
    active_chapter: int | None = None
    active_node: str | None = None

    def update_node(chapter_number: int, node: str, status: ChapterNodeStatus) -> None:
        nonlocal session, active_node
        active_node = node
        completed_name = f"chapter:{chapter_number}:{node}" if status is ChapterNodeStatus.COMPLETED else None
        session = _set_chapter_node(
            session,
            chapter_number,
            node,
            status,
            last_completed_node=completed_name,
        )
        _write_session(root, session)
    try:
        _raise_if_session_cancel_requested(root, session)
        for chapter_number in session.chapter_range:
            active_chapter = chapter_number
            if any(item.chapter_number == chapter_number for item in projection.checkpoints):
                if session.chapter_runs[chapter_number].state_update is not ChapterNodeStatus.COMPLETED:
                    update_node(chapter_number, "state_update", ChapterNodeStatus.COMPLETED)
                continue
            if session.chapter_runs[chapter_number].state_update is ChapterNodeStatus.COMPLETED:
                continue

            if session.chapter_runs[chapter_number].audit is ChapterNodeStatus.COMPLETED:
                try:
                    require_working_audit_matches_candidate(root, chapter_number)
                except AuditCandidateMismatchError:
                    run = session.chapter_runs[chapter_number].model_copy(
                        update={
                            "audit": ChapterNodeStatus.STALE,
                            "state_update": ChapterNodeStatus.STALE,
                        }
                    )
                    session = _validated_session_copy(
                        session,
                        chapter_runs={**session.chapter_runs, chapter_number: run},
                    )
                    _write_session(root, session)

            def chapter_node_callback(
                node: str,
                status: ChapterNodeStatus,
                *,
                selected_chapter: int = chapter_number,
            ) -> None:
                update_node(selected_chapter, node, status)

            _record_session_progress(
                root,
                session.session_id,
                status="running",
                stage="chapter_start",
                message=f"开始处理第 {chapter_number} 章。",
                chapter_number=chapter_number,
            )
            _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number)
            _retire_state_update_proposal(root, chapter_number)
            generated_audit = _generate_chapter_content(
                root,
                chapter_number,
                session,
                options.provider_name,
                force=options.force or session.resume_count > 0,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
                polish_mode=options.polish_mode,
                world_state_dir=world_state_dir,
                chapter_run=session.chapter_runs[chapter_number],
                node_callback=chapter_node_callback,
            )
            if generated_audit is False:
                final_outputs.append(_rel(root, _chapter_dir(root, chapter_number) / "draft.md"))
                session = _transition_session(
                    session,
                    SessionPhase.AWAITING_CONTENT_REVIEW,
                    final_output_paths=list(dict.fromkeys(final_outputs)),
                    audit_history=list(dict.fromkeys(audits)),
                    revision_history=list(dict.fromkeys(revisions)),
                )
                _write_session(root, session)
                _record_session_progress(
                    root,
                    session.session_id,
                    status="completed",
                    stage="review_gate",
                    message="Session stopped at review gate.",
                    chapter_number=chapter_number,
                )
                return SessionResult(
                    session=session,
                    session_path=_session_path(root, session.session_id),
                    message="Session stopped at review gate.",
                )
            _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number)
            audit_report = _load_audit(root, chapter_number)
            round_number = 0
            while _has_hard_issues(audit_report) and round_number < max_rounds:
                round_number += 1
                consume_auto_revision_round()
                after_output_path: Path | None = None
                _record_session_progress(
                    root,
                    session.session_id,
                    status="running",
                    stage="auto_repair_route",
                    message=f"第 {chapter_number} 章审核未通过，正在选择第 {round_number} 轮自动修复方式。",
                    chapter_number=chapter_number,
                    round_number=round_number,
                )
                _raise_if_session_cancel_requested(
                    root, session, chapter_number=chapter_number, round_number=round_number
                )
                repair_route = route_audit_repair(
                    root,
                    audit_report,
                    provider_name=options.provider_name,
                )
                if repair_route.route == "manual_review":
                    _record_session_progress(
                        root,
                        session.session_id,
                        status="running",
                        stage="manual_review",
                        message=f"第 {chapter_number} 章需要人工处理，自动修复停止。",
                        chapter_number=chapter_number,
                        round_number=round_number,
                    )
                    break
                if repair_route.route == "plot_replan":
                    rewrite_event = _start_rewrite_event(
                        root,
                        session,
                        chapter_number,
                        round_number,
                        "plot_replan",
                        audit_report,
                    )
                    try:
                        _record_session_progress(
                            root,
                            session.session_id,
                            status="running",
                            stage="auto_replan",
                            message=f"正在重写第 {chapter_number} 章大纲并重新生成正文。",
                            chapter_number=chapter_number,
                            round_number=round_number,
                        )
                        _auto_replan_chapter(
                            root,
                            chapter_number,
                            session,
                            audit_report,
                            options.provider_name,
                            round_number,
                            use_search_context=options.use_search_context,
                            use_vector_context=options.use_vector_context,
                        )
                        _raise_if_session_cancel_requested(
                            root, session, chapter_number=chapter_number, round_number=round_number
                        )
                        _generate_chapter_content(
                            root,
                            chapter_number,
                            session,
                            options.provider_name,
                            force=True,
                            use_search_context=options.use_search_context,
                            use_vector_context=options.use_vector_context,
                            polish_mode=options.polish_mode,
                            world_state_dir=world_state_dir,
                        )
                        after_output_path = _chapter_dir(root, chapter_number) / "polished.md"
                    except _SessionCancelRequested:
                        raise
                    except Exception:
                        _update_rewrite_event(root, session.session_id, rewrite_event.event_id, status="failed")
                        raise
                else:
                    rewrite_event = _start_rewrite_event(
                        root,
                        session,
                        chapter_number,
                        round_number,
                        "revision_rewrite",
                        audit_report,
                    )
                    try:
                        _record_session_progress(
                            root,
                            session.session_id,
                            status="running",
                            stage="auto_repair",
                            message=f"正在按审核意见修订第 {chapter_number} 章。",
                            chapter_number=chapter_number,
                            round_number=round_number,
                        )
                        revision_path = _auto_repair_chapter(
                            root,
                            chapter_number,
                            session,
                            audit_report,
                            options.provider_name,
                            round_number,
                            use_search_context=options.use_search_context,
                            use_vector_context=options.use_vector_context,
                            world_state_dir=world_state_dir,
                        )
                        _raise_if_session_cancel_requested(
                            root, session, chapter_number=chapter_number, round_number=round_number
                        )
                        revisions.append(_rel(root, revision_path))
                        after_output_path = _promote_revision_to_polished(root, chapter_number, revision_path)
                        _retire_state_update_proposal(root, chapter_number)
                        _record_session_progress(
                            root,
                            session.session_id,
                            status="running",
                            stage="reaudit",
                            message=f"正在重新审核第 {chapter_number} 章。",
                            chapter_number=chapter_number,
                            round_number=round_number,
                        )
                        _audit_chapter_content(
                            root,
                            chapter_number,
                            session,
                            options.provider_name,
                            force=True,
                            use_search_context=options.use_search_context,
                            use_vector_context=options.use_vector_context,
                            world_state_dir=world_state_dir,
                        )
                    except _SessionCancelRequested:
                        raise
                    except Exception:
                        _update_rewrite_event(root, session.session_id, rewrite_event.event_id, status="failed")
                        raise
                _raise_if_session_cancel_requested(
                    root, session, chapter_number=chapter_number, round_number=round_number
                )
                audit_report = _load_audit(root, chapter_number)
                rewrite_status: SessionRewriteStatus = "unresolved" if _has_hard_issues(audit_report) else "completed"
                _update_rewrite_event(
                    root,
                    session.session_id,
                    rewrite_event.event_id,
                    status=rewrite_status,
                    after_output_path=after_output_path,
                )
            audit_path = _chapter_dir(root, chapter_number) / "audit.json"
            audits.append(_rel(root, audit_path))
            final_outputs.append(_rel(root, _chapter_dir(root, chapter_number) / "polished.md"))
            if _has_hard_issues(audit_report):
                session = _transition_session(
                    session,
                    SessionPhase.AWAITING_CONTENT_REVIEW,
                    final_output_paths=list(dict.fromkeys(final_outputs)),
                    audit_history=list(dict.fromkeys(audits)),
                    revision_history=list(dict.fromkeys(revisions)),
                )
                _write_session(root, session)
                _record_session_progress(
                    root,
                    session.session_id,
                    status="completed",
                    stage="needs_revision",
                    message=f"第 {chapter_number} 章仍有未解决审核问题，Session 停止等待修订。",
                    chapter_number=chapter_number,
                )
                return SessionResult(
                    session=session,
                    session_path=_session_path(root, session.session_id),
                    message=f"Session stopped after unresolved audit issues in chapter {chapter_number}.",
                )
            _record_session_progress(
                root,
                session.session_id,
                status="running",
                stage="state_update",
                message=f"正在生成第 {chapter_number} 章状态更新建议。",
                chapter_number=chapter_number,
            )
            _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number)
            update_node(chapter_number, "state_update", ChapterNodeStatus.RUNNING)
            _propose_state(
                root,
                chapter_number,
                session,
                options.provider_name,
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
                world_state_dir=world_state_dir,
            )
            try:
                projection = _capture_and_advance_projection(root, session, chapter_number, projection)
            except AuditCandidateMismatchError:
                session = _validated_session_copy(
                    session,
                    chapter_runs={
                        **session.chapter_runs,
                        chapter_number: session.chapter_runs[chapter_number].model_copy(
                            update={
                                "audit": ChapterNodeStatus.STALE,
                                "state_update": ChapterNodeStatus.STALE,
                            }
                        ),
                    },
                )
                _write_session(root, session)
                active_node = None
                raise
            update_node(chapter_number, "state_update", ChapterNodeStatus.COMPLETED)
            _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number)

        session = _transition_session(
            session,
            SessionPhase.AWAITING_CONTENT_REVIEW,
            final_output_paths=list(dict.fromkeys(final_outputs)),
            audit_history=list(dict.fromkeys(audits)),
            revision_history=list(dict.fromkeys(revisions)),
        )
        _write_session(root, session)
        _record_session_progress(
            root,
            session.session_id,
            status="completed",
            stage="completed",
            message="Session 内容已生成，等待用户审阅。",
        )
        return SessionResult(
            session=session,
            session_path=_session_path(root, session.session_id),
            message="Session content is ready for user review.",
        )
    except _SessionCancelRequested as exc:
        return _cancelled_session_result(
            root,
            session,
            final_outputs=final_outputs,
            audits=audits,
            revisions=revisions,
            message=str(exc) or "Session 任务已取消。",
        )
    except Exception as exc:
        if active_chapter is not None and active_node in ChapterNodeState.model_fields:
            session = _set_chapter_node(
                session,
                active_chapter,
                active_node,
                ChapterNodeStatus.FAILED,
            )
        session = _transition_session(
            session,
            SessionPhase.FAILED_RECOVERABLE,
            failure_node=(f"chapter:{active_chapter}:{active_node}" if active_chapter and active_node else "session.run"),
            failure_message=redact_secret_text(str(exc)),
            final_output_paths=list(dict.fromkeys(final_outputs)),
            audit_history=list(dict.fromkeys(audits)),
            revision_history=list(dict.fromkeys(revisions)),
        )
        _write_session(root, session)
        _record_session_progress(
            root,
            session.session_id,
            status="failed",
            stage="failed",
            message="Session 任务失败。",
            error=str(exc),
        )
        raise


def _revise_content_impl(options: SessionInstructionOptions) -> SessionResult:
    root = options.root.resolve()
    session = load_session(root, options.session_id)
    _ensure_session_mutable(root, session)
    recoverable_revision = (
        session.phase is SessionPhase.FAILED_RECOVERABLE
        and bool(session.failure_node and session.failure_node.startswith("revision"))
    )
    if session.phase is not SessionPhase.AWAITING_CONTENT_REVIEW and not recoverable_revision:
        raise CreationSessionError("content can be revised only after generation has produced reviewable content")
    if not options.from_audit and not (options.instruction and options.instruction.strip()):
        raise CreationSessionError("content revision requires --instruction or --from-audit")
    route_record = _resolve_content_revision_route(root, session, options)
    route = route_record.decision
    if route.route == "manual_review":
        raise CreationSessionError(
            "revision routing confidence is insufficient; clarify whether this is a plot replan, full rewrite, or scoped patch"
        )
    authorized_chapters = sorted(set(route.chapter_numbers))
    if not authorized_chapters:
        raise CreationSessionError("revision route must authorize at least one chapter")
    unauthorized = sorted(set(authorized_chapters) - set(session.chapter_range))
    if unauthorized:
        raise CreationSessionError(f"revision route escaped session chapter range: {unauthorized}")
    session = _transition_session(session, SessionPhase.REVISING)
    for chapter_number in authorized_chapters:
        run = session.chapter_runs[chapter_number].model_copy(
            update={
                "write": ChapterNodeStatus.STALE,
                "polish": ChapterNodeStatus.STALE,
                "audit": ChapterNodeStatus.STALE,
                "state_update": ChapterNodeStatus.STALE,
            }
        )
        session = _validated_session_copy(
            session,
            chapter_runs={**session.chapter_runs, chapter_number: run},
        )
    _write_session(root, session)
    revisions: list[str] = []
    audits: list[str] = list(session.audit_history)
    final_outputs: list[str] = list(session.final_output_paths)
    protected_hashes = {
        chapter_number: _sha256(_chapter_dir(root, chapter_number) / "polished.md")
        for chapter_number in session.chapter_range
        if chapter_number not in authorized_chapters
    }
    projection = initialize_projection(root, session.session_id, force=True)
    world_state_dir = projection_dir(root, session.session_id)
    for chapter_number in session.chapter_range:
        if chapter_number not in authorized_chapters:
            if _sha256(_chapter_dir(root, chapter_number) / "polished.md") != protected_hashes[chapter_number]:
                raise CreationSessionError(
                    f"revision changed chapter outside authorized scope: {chapter_number}"
                )
            projection = _capture_and_advance_projection(root, session, chapter_number, projection)
            continue
        if route.route == "plot_replan":
            output_path = _replan_and_rewrite_chapter(
                root,
                chapter_number,
                session,
                options.provider_name,
                route.instruction_for_plot or options.instruction or "",
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
                world_state_dir=world_state_dir,
            )
        elif route.route == "writer_rewrite":
            output_path = _rewrite_chapter_with_writer(
                root,
                chapter_number,
                options.provider_name,
                route.instruction_for_writer or options.instruction or "",
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
                world_state_dir=world_state_dir,
            )
        else:
            provider = load_revision_provider(
                root,
                options.provider_name,
                target="polished",
                chapter_number=chapter_number,
            )
            result = revise_chapter(
                ChapterRevisionOptions(
                    root=root,
                    chapter_number=chapter_number,
                    instruction=route.instruction_for_revision or options.instruction,
                    from_audit=options.from_audit,
                    target="polished",
                    force=options.force,
                    use_search_context=options.use_search_context,
                    use_vector_context=options.use_vector_context,
                    world_state_dir=world_state_dir,
                ),
                provider,
                provider_name=options.provider_name,
            )
            revisions.append(_rel(root, result.output_path))
            output_path = _promote_revision_to_polished(root, chapter_number, result.output_path)
        final_outputs = _with_replaced_output(final_outputs, root, output_path)
        _retire_state_update_proposal(root, chapter_number)
        _audit_chapter_content(
            root,
            chapter_number,
            session,
            options.provider_name,
            force=True,
            use_search_context=options.use_search_context,
            use_vector_context=options.use_vector_context,
            world_state_dir=world_state_dir,
        )
        audit_path = _chapter_dir(root, chapter_number) / "audit.json"
        audits.append(_rel(root, audit_path))
        run = session.chapter_runs[chapter_number].model_copy(
            update={
                "write": ChapterNodeStatus.COMPLETED,
                "polish": ChapterNodeStatus.COMPLETED,
                "audit": ChapterNodeStatus.COMPLETED,
            }
        )
        session = _validated_session_copy(
            session,
            chapter_runs={**session.chapter_runs, chapter_number: run},
            last_completed_node=f"chapter:{chapter_number}:audit",
        )
        _write_session(root, session)
        if _has_hard_issues(_load_audit(root, chapter_number)):
            changed_outside_scope = [
                protected_chapter
                for protected_chapter, expected_hash in protected_hashes.items()
                if _sha256(_chapter_dir(root, protected_chapter) / "polished.md") != expected_hash
            ]
            if changed_outside_scope:
                raise CreationSessionError(
                    f"revision changed chapters outside authorized scope: {changed_outside_scope}"
                )
            session = _transition_session(
                session,
                SessionPhase.AWAITING_CONTENT_REVIEW,
                revision_history=list(dict.fromkeys([*session.revision_history, *revisions])),
                revision_route_history=[*session.revision_route_history, route_record],
                audit_history=list(dict.fromkeys(audits)),
                final_output_paths=list(dict.fromkeys(final_outputs)),
            )
            _write_session(root, session)
            return SessionResult(
                session=session,
                session_path=_session_path(root, session.session_id),
                message=f"Content revised, but chapter {chapter_number} still needs revision after audit.",
            )

        _propose_state(
            root,
            chapter_number,
            session,
            options.provider_name,
            force=True,
            use_search_context=options.use_search_context,
            use_vector_context=options.use_vector_context,
            world_state_dir=world_state_dir,
        )
        session = _set_chapter_node(
            session,
            chapter_number,
            "state_update",
            ChapterNodeStatus.COMPLETED,
            last_completed_node=f"chapter:{chapter_number}:state_update",
        )
        try:
            projection = _capture_and_advance_projection(root, session, chapter_number, projection)
        except AuditCandidateMismatchError:
            session = _validated_session_copy(
                session,
                chapter_runs={
                    **session.chapter_runs,
                    chapter_number: session.chapter_runs[chapter_number].model_copy(
                        update={
                            "audit": ChapterNodeStatus.STALE,
                            "state_update": ChapterNodeStatus.STALE,
                        }
                    ),
                },
            )
            _write_session(root, session)
            raise

    changed_outside_scope = [
        chapter_number
        for chapter_number, expected_hash in protected_hashes.items()
        if _sha256(_chapter_dir(root, chapter_number) / "polished.md") != expected_hash
    ]
    if changed_outside_scope:
        raise CreationSessionError(f"revision changed chapters outside authorized scope: {changed_outside_scope}")

    session = _transition_session(
        session,
        SessionPhase.AWAITING_CONTENT_REVIEW,
        revision_history=list(dict.fromkeys([*session.revision_history, *revisions])),
        revision_route_history=[*session.revision_route_history, route_record],
        audit_history=list(dict.fromkeys(audits)),
        final_output_paths=list(dict.fromkeys(final_outputs)),
    )
    _write_session(root, session)
    return SessionResult(
        session=session,
        session_path=_session_path(root, session.session_id),
        message=f"Content revised, audited, and ready for user review. Route: {route.route}.",
    )


def revise_content(options: SessionInstructionOptions) -> SessionResult:
    try:
        return _revise_content_impl(options)
    except Exception as exc:
        _mark_revising_session_failed(options.root, options.session_id, "revision.content", exc)
        raise


def _resolve_content_revision_route(
    root: Path,
    session: CreationSession,
    options: SessionInstructionOptions,
) -> RevisionRouteRecord:
    if options.from_audit:
        decision = _audit_driven_revision_route(root, session, options.instruction)
    else:
        decision = route_revision_request(
            root,
            options.instruction or "",
            provider_name=options.provider_name,
            chapter_numbers=session.chapter_range,
            session_summary=_session_route_summary(root, session),
        )
    return RevisionRouteRecord(
        created_at=utc_now(),
        user_instruction=options.instruction or ("from current audit issues" if options.from_audit else ""),
        from_audit=options.from_audit,
        decision=decision,
    )


def _audit_driven_revision_route(
    root: Path,
    session: CreationSession,
    instruction: str | None,
) -> RevisionRouteDecision:
    reports = [_load_audit(root, chapter_number) for chapter_number in session.chapter_range]
    issue_summary = "\n".join(_blocking_issue_summary(report) for report in reports).strip() or "当前 audit issues"
    user_note = f"\n用户补充意见：{instruction.strip()}" if instruction and instruction.strip() else ""
    routes = [route_audit_repair(root, report, provider_name="mock") for report in reports]
    if any(route.route == "plot_replan" for route in routes):
        return RevisionRouteDecision(
            route="plot_replan",
            reason="audit issues point to plan/outline-level conflicts",
            chapter_numbers=session.chapter_range,
            instruction_for_plot=f"根据 audit 修订大纲或章节计划，解决以下问题：\n{issue_summary}{user_note}",
            risk_level="high",
        )
    return RevisionRouteDecision(
        route="revision_patch",
        reason="audit-driven revision can be handled as a focused content patch",
        chapter_numbers=session.chapter_range,
        instruction_for_revision=f"根据 audit 修订当前正文，解决以下问题：\n{issue_summary}{user_note}",
        risk_level="medium",
    )


def _session_route_summary(root: Path, session: CreationSession) -> str:
    final_outputs = "\n".join(f"- {path}" for path in session.final_output_paths) or "无"
    latest_audits = "\n".join(f"- {path}" for path in session.audit_history[-5:]) or "无"
    return (
        f"session_id: {session.session_id}\n"
        f"chapter_range: {session.chapter_range}\n"
        f"phase: {session.phase.value}\n"
        f"user_intent: {session.user_intent}\n"
        f"final_outputs:\n{final_outputs}\n"
        f"recent_audits:\n{latest_audits}\n"
        f"workspace: {_rel(root, root)}"
    )


def _replan_and_rewrite_chapter(
    root: Path,
    chapter_number: int,
    session: CreationSession,
    provider_name: str,
    instruction: str,
    *,
    use_search_context: bool,
    use_vector_context: bool | VectorContextMode,
    world_state_dir: Path,
) -> Path:
    planning_provider = load_planning_provider(root, provider_name, chapter_number=chapter_number)
    plan_chapter(
        ChapterPlanningOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=f"{_session_instruction(session)}\n\n用户剧情级修改意见：{instruction}",
            force=True,
            use_search_context=use_search_context,
            use_vector_context=use_vector_context,
            world_state_dir=world_state_dir,
        ),
        planning_provider,
    )
    _refresh_session_outline_from_plans(root, session)
    return _rewrite_chapter_with_writer(
        root,
        chapter_number,
        provider_name,
        f"{_session_instruction(session)}\n\n基于重写后的 ChapterPlan 写作。用户剧情级修改意见：{instruction}",
        use_search_context=use_search_context,
        use_vector_context=use_vector_context,
        world_state_dir=world_state_dir,
    )


def _rewrite_chapter_with_writer(
    root: Path,
    chapter_number: int,
    provider_name: str,
    instruction: str,
    *,
    use_search_context: bool,
    use_vector_context: bool | VectorContextMode,
    world_state_dir: Path,
) -> Path:
    draft_provider = load_drafting_provider(root, provider_name, chapter_number=chapter_number)
    write_chapter_draft(
        ChapterDraftingOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=instruction,
            force=True,
            use_search_context=use_search_context,
            use_vector_context=use_vector_context,
            world_state_dir=world_state_dir,
        ),
        draft_provider,
    )
    polish_provider = load_polishing_provider(root, provider_name, chapter_number=chapter_number)
    polish_chapter(
        ChapterPolishingOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=instruction,
            force=True,
            use_search_context=use_search_context,
            use_vector_context=use_vector_context,
            world_state_dir=world_state_dir,
        ),
        polish_provider,
    )
    return _chapter_dir(root, chapter_number) / "polished.md"


def _refresh_session_outline_from_plans(root: Path, session: CreationSession) -> None:
    chapters: list[CreationOutlineChapter] = []
    for chapter_number in session.chapter_range:
        plan_path = _chapter_dir(root, chapter_number) / "plan.json"
        plan = load_json_model(plan_path, ChapterPlan)
        chapters.append(
            CreationOutlineChapter(
                chapter_number=chapter_number,
                title=plan.title,
                plan_path=_rel(root, plan_path),
                summary=plan.summary,
                reveal_authorizations=plan.reveal_authorizations,
            )
        )
    outline = CreationOutline(
        session_id=session.session_id,
        user_intent=session.user_intent,
        chapters=chapters,
        created_at=utc_now(),
    )
    session_dir = _session_dir(root, session.session_id)
    targets = [
        session_dir / "outline_proposal.json",
        session_dir / "outline_proposal.md",
        session_dir / "approved_outline.json",
        session_dir / "approved_outline.md",
    ]
    for path in targets:
        backup_if_exists(path, reason="revision_route_replan")
    atomic_write_model_json(session_dir / "outline_proposal.json", outline)
    atomic_write_text(session_dir / "outline_proposal.md", _render_outline_markdown(outline))
    atomic_write_model_json(session_dir / "approved_outline.json", outline)
    atomic_write_text(session_dir / "approved_outline.md", _render_outline_markdown(outline))


def accept_session(options: SessionActionOptions) -> SessionResult:
    root = options.root.resolve()
    session = load_session(root, options.session_id)
    _ensure_session_mutable(root, session)
    if session.phase not in {SessionPhase.AWAITING_CONTENT_REVIEW, SessionPhase.READY_TO_COMMIT}:
        raise CreationSessionError("session content must be ready for user review before acceptance")
    if session.phase is SessionPhase.AWAITING_CONTENT_REVIEW:
        session = _transition_session(session, SessionPhase.READY_TO_COMMIT)
    session = _transition_session(session, SessionPhase.COMMITTING)
    _write_session(root, session)
    try:
        session = commit_creation_session(root, session, _session_path(root, session.session_id))
    except (LifecycleError, TransactionError) as exc:
        recovering = _transition_session(session, SessionPhase.RECOVERING, failure_node="acceptance", failure_message=str(exc))
        ready = _transition_session(recovering, SessionPhase.READY_TO_COMMIT)
        _write_session(root, ready)
        raise CreationSessionError(str(exc)) from exc
    for chapter_number in session.chapter_range:
        record_management_event(
            root,
            "chapter_accepted",
            f"Session {session.session_id} 已认可第 {chapter_number} 章，并确认状态/时间线更新。",
            source=session.session_id,
            target_files=[
                f"memory/chapters/{chapter_number:03d}/metadata.json",
                f"memory/chapters/{chapter_number:03d}/acceptance.json",
                f"memory/chapters/{chapter_number:03d}/chapter_memory.json",
            ],
            status="success",
        )
    return SessionResult(
        session=session, session_path=_session_path(root, session.session_id), message="Session accepted."
    )


def archive_session(options: SessionActionOptions) -> SessionResult:
    root = options.root.resolve()
    session = load_session(root, options.session_id)
    if session.phase not in {SessionPhase.COMMITTED, SessionPhase.ARCHIVED}:
        raise CreationSessionError("accept the session before archiving it")
    archive_dir = root / "memory" / "archive" / session.session_id
    if archive_dir.exists() and session.phase is SessionPhase.ARCHIVED and not options.force:
        raise CreationSessionError("session is already archived")
    archive_dir.mkdir(parents=True, exist_ok=True)
    entries: list[CreationArchiveEntry] = []
    source_paths = _archive_sources(root, session)
    for source in source_paths:
        if not source.exists():
            continue
        target = archive_dir / _safe_archive_name(root, source)
        _refuse_existing(target, options.force)
        if options.force:
            backup_if_exists(target, reason="archive")
        shutil.copy2(source, target)
        entries.append(
            CreationArchiveEntry(
                source_path=_rel(root, source),
                archive_path=_rel(root, target),
                sha256=_sha256(target),
                created_at=utc_now(),
            )
        )
    manifest = CreationArchiveManifest(session_id=session.session_id, created_at=utc_now(), entries=entries)
    manifest_path = archive_dir / "manifest.json"
    if options.force:
        backup_if_exists(manifest_path, reason="archive")
    atomic_write_model_json(manifest_path, manifest)
    if session.phase is SessionPhase.COMMITTED:
        session = _transition_session(
            session,
            SessionPhase.ARCHIVED,
            archive_paths=[_rel(root, archive_dir), _rel(root, manifest_path)],
        )
    else:
        session = _validated_session_copy(
            session,
            archive_paths=[_rel(root, archive_dir), _rel(root, manifest_path)],
            updated_at=utc_now(),
        )
    _write_session(root, session)
    return SessionResult(
        session=session, session_path=_session_path(root, session.session_id), message="Session archived."
    )


def _revise_audit_impl(options: SessionRewriteControlOptions) -> SessionResult:
    root, session, event = _load_rewrite_control_context(options)
    if not options.instruction or not options.instruction.strip():
        raise CreationSessionError("audit revision requires --instruction")
    session = _prepare_rewrite_control(session, event.chapter_number)
    _write_session(root, session)
    snapshot_path = _event_snapshot_path(root, event)
    chapter_number = event.chapter_number
    polished_path = _chapter_dir(root, chapter_number) / "polished.md"
    backup_if_exists(polished_path, reason="audit_revision")
    atomic_write_text(polished_path, snapshot_path.read_text(encoding="utf-8"))
    audit_path = _chapter_dir(root, chapter_number) / "audit.json"
    previous_audit_path = event.trigger_audit_path
    instruction = (
        f"用户纠正了上一轮 Audit 的理解：{options.instruction.strip()}\n"
        "请基于当前 polished.md 重新审核；当前 polished.md 是被打回的原文快照。"
    )
    _audit_chapter_content_with_instruction(
        root,
        chapter_number,
        session,
        options.provider_name,
        instruction=instruction,
        force=True,
        use_search_context=options.use_search_context,
        use_vector_context=options.use_vector_context,
    )
    revision = SessionAuditRevision(
        instruction=options.instruction.strip(),
        previous_audit_path=previous_audit_path,
        new_audit_path=_rel(root, audit_path),
        created_at=utc_now(),
    )
    updated_event = event.model_copy(
        update={
            "audit_revision_history": [*event.audit_revision_history, revision],
            "trigger_audit_path": _rel(root, audit_path),
            "status": "unresolved" if _has_hard_issues(_load_audit(root, chapter_number)) else "completed",
            "updated_at": utc_now(),
        }
    )
    _replace_rewrite_event(root, session.session_id, updated_event)
    audit_report = _load_audit(root, chapter_number)
    status_update = _session_status_after_manual_rewrite(
        root,
        session,
        chapter_number,
        audit_report,
        options.provider_name,
        force=True,
        final_output_path=polished_path,
        use_search_context=options.use_search_context,
        use_vector_context=options.use_vector_context,
    )
    session = _finish_rewrite_control(session, chapter_number, audit_report)
    session = _transition_session(
        session,
        SessionPhase.AWAITING_CONTENT_REVIEW,
        **status_update,
        audit_history=[*session.audit_history, _rel(root, audit_path)],
    )
    _write_session(root, session)
    return SessionResult(
        session=session, session_path=_session_path(root, session.session_id), message="Audit revised."
    )


def _undo_rewrite_impl(options: SessionRewriteControlOptions) -> SessionResult:
    root, session, event = _load_rewrite_control_context(options)
    session = _prepare_rewrite_control(session, event.chapter_number)
    _write_session(root, session)
    snapshot_path = _event_snapshot_path(root, event)
    chapter_number = event.chapter_number
    polished_path = _chapter_dir(root, chapter_number) / "polished.md"
    backup_if_exists(polished_path, reason="undo_rewrite")
    atomic_write_text(polished_path, snapshot_path.read_text(encoding="utf-8"))
    _audit_chapter_content(
        root,
        chapter_number,
        session,
        options.provider_name,
        force=True,
        use_search_context=options.use_search_context,
        use_vector_context=options.use_vector_context,
    )
    audit_path = _chapter_dir(root, chapter_number) / "audit.json"
    updated_event = event.model_copy(
        update={
            "undo_status": "restored",
            "restored_from_snapshot_path": event.rejected_text_snapshot_path,
            "status": "unresolved" if _has_hard_issues(_load_audit(root, chapter_number)) else "completed",
            "trigger_audit_path": _rel(root, audit_path),
            "updated_at": utc_now(),
        }
    )
    _replace_rewrite_event(root, session.session_id, updated_event)
    audit_report = _load_audit(root, chapter_number)
    status_update = _session_status_after_manual_rewrite(
        root,
        session,
        chapter_number,
        audit_report,
        options.provider_name,
        force=True,
        final_output_path=polished_path,
        use_search_context=options.use_search_context,
        use_vector_context=options.use_vector_context,
    )
    session = _finish_rewrite_control(session, chapter_number, audit_report)
    session = _transition_session(
        session,
        SessionPhase.AWAITING_CONTENT_REVIEW,
        **status_update,
        audit_history=[*session.audit_history, _rel(root, audit_path)],
    )
    _write_session(root, session)
    return SessionResult(
        session=session,
        session_path=_session_path(root, session.session_id),
        message="Rewrite restored from rejected text snapshot.",
    )


def _retry_rewrite_impl(options: SessionRewriteControlOptions) -> SessionResult:
    root, session, event = _load_rewrite_control_context(options)
    chapter_number = event.chapter_number
    audit_report = _load_audit(root, chapter_number)
    if not _has_hard_issues(audit_report):
        raise CreationSessionError("latest audit has no medium/high/critical issue to retry rewrite")
    session = _prepare_rewrite_control(session, event.chapter_number)
    _write_session(root, session)
    new_revisions: list[str] = []
    if event.action == "plot_replan":
        _retire_state_update_proposal(root, chapter_number)
        _auto_replan_chapter(
            root,
            chapter_number,
            session,
            audit_report,
            options.provider_name,
            event.round_number + 1,
            use_search_context=options.use_search_context,
            use_vector_context=options.use_vector_context,
        )
        _generate_chapter_content(
            root,
            chapter_number,
            session,
            options.provider_name,
            force=True,
            use_search_context=options.use_search_context,
            use_vector_context=options.use_vector_context,
            polish_mode=options.polish_mode,
        )
        after_output_path = _chapter_dir(root, chapter_number) / "polished.md"
    else:
        revision_path = _auto_repair_chapter_with_instruction(
            root,
            chapter_number,
            session,
            audit_report,
            options.provider_name,
            event.round_number + 1,
            options.instruction,
            use_search_context=options.use_search_context,
            use_vector_context=options.use_vector_context,
        )
        new_revisions.append(_rel(root, revision_path))
        after_output_path = _promote_revision_to_polished(root, chapter_number, revision_path)
        _retire_state_update_proposal(root, chapter_number)
        _audit_chapter_content(
            root,
            chapter_number,
            session,
            options.provider_name,
            force=True,
            use_search_context=options.use_search_context,
            use_vector_context=options.use_vector_context,
        )
    audit_report = _load_audit(root, chapter_number)
    updated_event = event.model_copy(
        update={
            "status": "unresolved" if _has_hard_issues(audit_report) else "completed",
            "after_output_path": _rel(root, after_output_path),
            "updated_at": utc_now(),
        }
    )
    _replace_rewrite_event(root, session.session_id, updated_event)
    status_update = _session_status_after_manual_rewrite(
        root,
        session,
        chapter_number,
        audit_report,
        options.provider_name,
        force=True,
        final_output_path=after_output_path,
        use_search_context=options.use_search_context,
        use_vector_context=options.use_vector_context,
    )
    session = _finish_rewrite_control(session, chapter_number, audit_report)
    session = _transition_session(
        session,
        SessionPhase.AWAITING_CONTENT_REVIEW,
        **status_update,
        audit_history=[*session.audit_history, _rel(root, _chapter_dir(root, chapter_number) / "audit.json")],
        revision_history=[*session.revision_history, *new_revisions],
    )
    _write_session(root, session)
    return SessionResult(
        session=session,
        session_path=_session_path(root, session.session_id),
        message="Rewrite retried from latest audit.",
    )


def revise_audit(options: SessionRewriteControlOptions) -> SessionResult:
    return _run_rewrite_control(options, "revision.audit", _revise_audit_impl)


def undo_rewrite(options: SessionRewriteControlOptions) -> SessionResult:
    return _run_rewrite_control(options, "revision.undo", _undo_rewrite_impl)


def retry_rewrite(options: SessionRewriteControlOptions) -> SessionResult:
    return _run_rewrite_control(options, "revision.retry", _retry_rewrite_impl)


def _run_rewrite_control(
    options: SessionRewriteControlOptions,
    failure_node: str,
    operation: Callable[[SessionRewriteControlOptions], SessionResult],
) -> SessionResult:
    try:
        return operation(options)
    except Exception as exc:
        _mark_revising_session_failed(options.root, options.session_id, failure_node, exc)
        raise


def _prepare_rewrite_control(session: CreationSession, chapter_number: int) -> CreationSession:
    session = _transition_session(session, SessionPhase.REVISING)
    run = session.chapter_runs[chapter_number].model_copy(
        update={
            "polish": ChapterNodeStatus.STALE,
            "audit": ChapterNodeStatus.STALE,
            "state_update": ChapterNodeStatus.STALE,
        }
    )
    return _validated_session_copy(session, chapter_runs={**session.chapter_runs, chapter_number: run})


def _finish_rewrite_control(
    session: CreationSession,
    chapter_number: int,
    audit_report: AuditReport,
) -> CreationSession:
    run = session.chapter_runs[chapter_number].model_copy(
        update={
            "polish": ChapterNodeStatus.COMPLETED,
            "audit": ChapterNodeStatus.COMPLETED,
            "state_update": (
                ChapterNodeStatus.STALE if _has_hard_issues(audit_report) else ChapterNodeStatus.COMPLETED
            ),
        }
    )
    return _validated_session_copy(
        session,
        chapter_runs={**session.chapter_runs, chapter_number: run},
        last_completed_node=f"chapter:{chapter_number}:audit",
    )


def _mark_revising_session_failed(root: Path, session_id: str, failure_node: str, exc: Exception) -> None:
    try:
        session = load_session(root.resolve(), session_id)
        if session.phase is not SessionPhase.REVISING:
            return
        session = _transition_session(
            session,
            SessionPhase.FAILED_RECOVERABLE,
            failure_node=failure_node,
            failure_message=redact_secret_text(str(exc)),
        )
        _write_session(root.resolve(), session)
    except Exception:
        return


def load_session(root: Path, session_id: str) -> CreationSession:
    root = root.resolve()
    session_id = _validate_session_id(session_id)
    path = _session_path(root, session_id)
    if not path.exists():
        raise CreationSessionError(f"session not found: {session_id}")
    return load_json_model(path, CreationSession)


def find_latest_active_session(root: Path, prefer_generated: bool = True) -> SessionResult | None:
    root = root.resolve()
    sessions_dir = root / "memory" / "sessions"
    if not sessions_dir.exists():
        return None
    sessions: list[CreationSession] = []
    for path in sessions_dir.glob("session_*/session.json"):
        try:
            session = load_json_model(path, CreationSession)
        except Exception:
            continue
        if session.phase is SessionPhase.ARCHIVED:
            continue
        sessions.append(session)
    if not sessions:
        return None
    pool = [session for session in sessions if _session_has_generated_content(session)] if prefer_generated else []
    selected = max(pool or sessions, key=lambda session: (session.updated_at, session.session_id))
    return SessionResult(
        session=selected,
        session_path=_session_path(root, selected.session_id),
        message="Latest active session loaded.",
    )


def _session_has_generated_content(session: CreationSession) -> bool:
    return bool(session.final_output_paths) or session.phase in {
        SessionPhase.AWAITING_CONTENT_REVIEW,
        SessionPhase.REVISING,
        SessionPhase.READY_TO_COMMIT,
        SessionPhase.COMMITTING,
        SessionPhase.COMMITTED,
        SessionPhase.FAILED_RECOVERABLE,
    }


def load_rewrite_events(root: Path, session_id: str) -> list[SessionRewriteEvent]:
    path = _rewrite_events_path(root.resolve(), session_id)
    if not path.exists():
        return []
    return load_json_model(path, SessionRewriteEvents).events


def _load_rewrite_control_context(
    options: SessionRewriteControlOptions,
) -> tuple[Path, CreationSession, SessionRewriteEvent]:
    root = options.root.resolve()
    session = load_session(root, options.session_id)
    _ensure_session_mutable(root, session)
    recoverable_revision = (
        session.phase is SessionPhase.FAILED_RECOVERABLE
        and bool(session.failure_node and session.failure_node.startswith("revision"))
    )
    if session.phase is not SessionPhase.AWAITING_CONTENT_REVIEW and not recoverable_revision:
        raise CreationSessionError(f"rewrite control is not allowed from phase {session.phase.value}")
    event = _find_rewrite_event(root, session.session_id, options.event_id)
    return root, session, event


def _session_status_after_manual_rewrite(
    root: Path,
    session: CreationSession,
    chapter_number: int,
    audit_report: AuditReport,
    provider_name: str,
    *,
    force: bool,
    final_output_path: Path,
    use_search_context: bool,
    use_vector_context: bool | VectorContextMode,
) -> dict[str, object]:
    if _has_hard_issues(audit_report):
        return {
            "final_output_paths": _with_replaced_output(session.final_output_paths, root, final_output_path),
        }
    _retire_state_update_proposal(root, chapter_number)
    _propose_state(
        root,
        chapter_number,
        session,
        provider_name,
        force=force,
        use_search_context=use_search_context,
        use_vector_context=use_vector_context,
    )
    return {
        "final_output_paths": _with_replaced_output(session.final_output_paths, root, final_output_path),
    }


def _with_replaced_output(existing: list[str], root: Path, output_path: Path) -> list[str]:
    rel_path = _rel(root, output_path)
    chapter_prefix = "/".join(Path(rel_path).parts[:3])
    retained = [path for path in existing if "/".join(Path(path).parts[:3]) != chapter_prefix and path != rel_path]
    return [*retained, rel_path]


def _write_outline_proposal(
    root: Path,
    session: CreationSession,
    provider_name: str,
    force: bool,
    *,
    use_search_context: bool,
    use_vector_context: bool | VectorContextMode,
) -> CreationSession:
    chapters: list[CreationOutlineChapter] = []
    for chapter_number in session.chapter_range:
        provider = load_planning_provider(root, provider_name, chapter_number=chapter_number)
        result = plan_chapter(
            ChapterPlanningOptions(
                root=root,
                chapter_number=chapter_number,
                instruction=session.user_intent,
                force=force,
                use_search_context=use_search_context,
                use_vector_context=use_vector_context,
                output_dir=_session_plan_dir(root, session.session_id, chapter_number),
            ),
            provider,
        )
        chapters.append(
            CreationOutlineChapter(
                chapter_number=chapter_number,
                title=result.plan.title,
                plan_path=_rel(root, result.plan_json_path),
                summary=result.plan.summary,
                reveal_authorizations=result.plan.reveal_authorizations,
            )
        )
    outline = CreationOutline(
        session_id=session.session_id,
        user_intent=session.user_intent,
        chapters=chapters,
        created_at=utc_now(),
    )
    session_dir = _session_dir(root, session.session_id)
    atomic_write_model_json(session_dir / "outline_proposal.json", outline)
    atomic_write_text(session_dir / "outline_proposal.md", _render_outline_markdown(outline))
    if session.phase is SessionPhase.DRAFTING_OUTLINE:
        return _transition_session(session, SessionPhase.AWAITING_OUTLINE_APPROVAL)
    if session.phase is not SessionPhase.AWAITING_OUTLINE_APPROVAL:
        raise CreationSessionError(f"outline proposal cannot be written from phase {session.phase.value}")
    return _validated_session_copy(session, updated_at=utc_now())


def _prepare_promoted_outline_plan_writes(
    root: Path,
    outline: CreationOutline,
    *,
    force: bool,
) -> tuple[CreationOutline, list[_TransactionalTextWrite]]:
    promotions = _outline_plan_promotions(root, outline, force=force)
    writes: list[_TransactionalTextWrite] = []
    for promotion in promotions:
        if promotion.already_promoted:
            continue
        try:
            plan_json_text = promotion.source_json.read_text(encoding="utf-8")
            plan_markdown_text = promotion.source_markdown.read_text(encoding="utf-8")
        except OSError as exc:
            raise CreationSessionError(f"failed to read outline plan before approval: {exc}") from exc
        backup_reason = "session_outline" if force else None
        writes.extend(
            [
                _TransactionalTextWrite(
                    path=promotion.target_json,
                    content=plan_json_text,
                    backup_reason=backup_reason,
                ),
                _TransactionalTextWrite(
                    path=promotion.target_markdown,
                    content=plan_markdown_text,
                    backup_reason=backup_reason,
                ),
            ]
        )
    chapters = [
        promotion.chapter.model_copy(update={"plan_path": _rel(root, promotion.target_json)})
        for promotion in promotions
    ]
    return outline.model_copy(update={"chapters": chapters}), writes


def _write_text_transaction(root: Path, writes: list[_TransactionalTextWrite], *, action: str) -> None:
    attempts: list[_TransactionAttempt] = []
    try:
        for write in writes:
            attempt = _TransactionAttempt(snapshot=_file_snapshot(root, write.path))
            attempts.append(attempt)
            if write.backup_reason:
                attempt.backup_path = backup_if_exists(write.path, reason=write.backup_reason)
            atomic_write_text(write.path, write.content)
    except Exception as exc:
        rollback_errors = _rollback_text_transaction(attempts)
        backup_cleanup_errors: list[str] = []
        if not rollback_errors:
            backup_cleanup_errors = _cleanup_transaction_backups(attempts)
        message = f"{action} failed; rolled back file changes: {exc}"
        if rollback_errors:
            message += "; rollback also failed and transaction backups were retained: " + "; ".join(rollback_errors)
        elif backup_cleanup_errors:
            message += "; backup cleanup failed after rollback, so some backups were retained: " + "; ".join(
                backup_cleanup_errors
            )
        raise CreationSessionError(message) from exc


def _file_snapshot(root: Path, path: Path) -> _FileSnapshot:
    return _FileSnapshot(
        path=path,
        existed=path.exists(),
        content=path.read_text(encoding="utf-8") if path.exists() else None,
        missing_parent_dirs=_missing_parent_dirs(root, path),
    )


def _missing_parent_dirs(root: Path, path: Path) -> tuple[Path, ...]:
    missing: list[Path] = []
    root = root.resolve()
    current = path.parent
    while current.resolve() != root and not current.exists():
        missing.append(current)
        current = current.parent
    return tuple(missing)


def _rollback_text_transaction(attempts: list[_TransactionAttempt]) -> list[str]:
    errors: list[str] = []
    for attempt in reversed(attempts):
        snapshot = attempt.snapshot
        try:
            if snapshot.existed:
                atomic_write_text(snapshot.path, snapshot.content or "")
            else:
                snapshot.path.unlink(missing_ok=True)
            _remove_empty_parent_dirs(snapshot.missing_parent_dirs)
        except Exception as exc:
            errors.append(f"{snapshot.path}: {exc}")
    return errors


def _remove_empty_parent_dirs(directories: tuple[Path, ...]) -> None:
    for directory in directories:
        with contextlib.suppress(OSError):
            directory.rmdir()


def _cleanup_transaction_backups(attempts: list[_TransactionAttempt]) -> list[str]:
    errors: list[str] = []
    for attempt in attempts:
        backup_path = attempt.backup_path
        if backup_path is None:
            continue
        try:
            backup_path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{backup_path}: {exc}")
    return errors


def _outline_plan_promotions(
    root: Path,
    outline: CreationOutline,
    *,
    force: bool,
) -> list[_OutlinePlanPromotion]:
    promotions: list[_OutlinePlanPromotion] = []
    for chapter in outline.chapters:
        source_json = _workspace_path(root, chapter.plan_path)
        source_markdown = source_json.with_name("plan.md")
        target_json, target_markdown = _chapter_plan_paths(root, chapter.chapter_number)
        if not source_json.exists():
            raise CreationSessionError(f"outline plan is missing: {chapter.plan_path}")
        if not source_markdown.exists():
            raise CreationSessionError(f"outline plan markdown is missing: {_rel(root, source_markdown)}")
        already_promoted = _same_path(source_json, target_json)
        if not already_promoted:
            _refuse_existing_chapter_plan(target_json, chapter.chapter_number, force)
            _refuse_existing_chapter_plan(target_markdown, chapter.chapter_number, force)
        promotions.append(
            _OutlinePlanPromotion(
                chapter=chapter,
                source_json=source_json,
                source_markdown=source_markdown,
                target_json=target_json,
                target_markdown=target_markdown,
                already_promoted=already_promoted,
            )
        )
    return promotions


def _refuse_existing_chapter_plan(path: Path, chapter_number: int, force: bool) -> None:
    if path.exists() and not force:
        raise CreationSessionError(
            f"第 {chapter_number} 章计划已存在：{path}；如需用 Session 大纲覆盖，请勾选“允许覆盖已有产物”。"
        )


def _chapter_plan_paths(root: Path, chapter_number: int) -> tuple[Path, Path]:
    chapter_dir = _chapter_dir(root, chapter_number)
    return chapter_dir / "plan.json", chapter_dir / "plan.md"


def _workspace_path(root: Path, path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else root / path


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def _generate_chapter_content(
    root: Path,
    chapter_number: int,
    session: CreationSession,
    provider_name: str,
    *,
    force: bool,
    use_search_context: bool,
    use_vector_context: bool | VectorContextMode,
    polish_mode: PolishMode | None = None,
    world_state_dir: Path | None = None,
    chapter_run: ChapterNodeState | None = None,
    node_callback: Callable[[str, ChapterNodeStatus], None] | None = None,
) -> bool:
    mode = _effective_session_polish_mode(root, polish_mode)
    instruction = _session_instruction(session)
    run = chapter_run or ChapterNodeState(chapter_number=chapter_number)
    notify = node_callback or (lambda _node, _status: None)
    if run.write is not ChapterNodeStatus.COMPLETED:
        notify("write", ChapterNodeStatus.RUNNING)
        draft_provider = load_drafting_provider(root, provider_name, chapter_number=chapter_number)
        _record_session_progress(
            root,
            session.session_id,
            status="running",
            stage="draft",
            message=f"正在生成第 {chapter_number} 章草稿。",
            chapter_number=chapter_number,
        )
        write_chapter_draft(
            ChapterDraftingOptions(
                root=root,
                chapter_number=chapter_number,
                instruction=instruction,
                force=force,
                use_search_context=use_search_context,
                use_vector_context=use_vector_context,
                world_state_dir=world_state_dir,
            ),
            draft_provider,
        )
        notify("write", ChapterNodeStatus.COMPLETED)
    _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number)
    if mode == "review_gate":
        _record_session_progress(
            root,
            session.session_id,
            status="running",
            stage="review_gate",
            message=f"第 {chapter_number} 章草稿已生成，等待人工复核。",
            chapter_number=chapter_number,
        )
        return False
    if run.polish is not ChapterNodeStatus.COMPLETED and mode == "auto":
        notify("polish", ChapterNodeStatus.RUNNING)
        polish_provider = load_polishing_provider(root, provider_name, chapter_number=chapter_number)
        _record_session_progress(
            root,
            session.session_id,
            status="running",
            stage="polish",
            message=f"正在润色第 {chapter_number} 章。",
            chapter_number=chapter_number,
        )
        polish_chapter(
            ChapterPolishingOptions(
                root=root,
                chapter_number=chapter_number,
                instruction=instruction,
                force=force,
                use_search_context=use_search_context,
                use_vector_context=use_vector_context,
                world_state_dir=world_state_dir,
            ),
            polish_provider,
        )
        notify("polish", ChapterNodeStatus.COMPLETED)
    elif run.polish is not ChapterNodeStatus.COMPLETED:
        notify("polish", ChapterNodeStatus.RUNNING)
        _record_session_progress(
            root,
            session.session_id,
            status="running",
            stage="single_pass_final",
            message=f"正在将第 {chapter_number} 章草稿标记为最终稿。",
            chapter_number=chapter_number,
        )
        _promote_draft_to_polished(root, chapter_number, force=force)
        notify("polish", ChapterNodeStatus.COMPLETED)
    _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number)
    if run.audit is not ChapterNodeStatus.COMPLETED:
        notify("audit", ChapterNodeStatus.RUNNING)
        audit_provider = load_audit_provider(root, provider_name, chapter_number=chapter_number)
        _record_session_progress(
            root,
            session.session_id,
            status="running",
            stage="audit",
            message=f"正在审核第 {chapter_number} 章。",
            chapter_number=chapter_number,
        )
        audit_chapter(
            ChapterAuditOptions(
                root=root,
                chapter_number=chapter_number,
                instruction=instruction,
                force=force,
                use_search_context=use_search_context,
                use_vector_context=use_vector_context,
                world_state_dir=world_state_dir,
            ),
            audit_provider,
        )
        notify("audit", ChapterNodeStatus.COMPLETED)
    _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number)
    return True


def _effective_session_polish_mode(root: Path, polish_mode: PolishMode | None) -> PolishMode:
    if polish_mode:
        return normalize_polish_mode(polish_mode)
    project = load_yaml_model(root / "project.yaml", ProjectConfig)
    return project_polish_mode(project)


def _promote_draft_to_polished(root: Path, chapter_number: int, *, force: bool) -> Path:
    chapter_dir = _chapter_dir(root, chapter_number)
    draft_path = chapter_dir / "draft.md"
    polished_path = chapter_dir / "polished.md"
    if polished_path.exists() and not force:
        raise CreationSessionError(f"{polished_path} already exists; use force to overwrite it")
    draft = read_markdown_with_front_matter(draft_path)
    title = str(draft.metadata.get("title") or f"Chapter {chapter_number}")
    if force:
        backup_if_exists(polished_path, reason="force")
    atomic_write_text(
        polished_path,
        "---\n"
        f"chapter_number: {chapter_number}\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        "status: polished\n"
        "created_by: writer_agent\n"
        "based_on: draft.md\n"
        "polish_skipped: true\n"
        f"created_at: {utc_now()}\n"
        "---\n\n"
        f"{draft.body.strip()}\n",
    )
    return polished_path


def _audit_chapter_content(
    root: Path,
    chapter_number: int,
    session: CreationSession,
    provider_name: str,
    *,
    force: bool,
    use_search_context: bool,
    use_vector_context: bool | VectorContextMode,
    world_state_dir: Path | None = None,
) -> None:
    audit_provider = load_audit_provider(root, provider_name, chapter_number=chapter_number)
    audit_chapter(
        ChapterAuditOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=_session_instruction(session),
            force=force,
            use_search_context=use_search_context,
            use_vector_context=use_vector_context,
            world_state_dir=world_state_dir,
        ),
        audit_provider,
    )


def _audit_chapter_content_with_instruction(
    root: Path,
    chapter_number: int,
    session: CreationSession,
    provider_name: str,
    *,
    instruction: str,
    force: bool,
    use_search_context: bool,
    use_vector_context: bool | VectorContextMode,
) -> None:
    audit_provider = load_audit_provider(root, provider_name, chapter_number=chapter_number)
    audit_chapter(
        ChapterAuditOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=f"{_session_instruction(session)}\n\n{instruction}",
            force=force,
            use_search_context=use_search_context,
            use_vector_context=use_vector_context,
        ),
        audit_provider,
    )


def _auto_repair_chapter(
    root: Path,
    chapter_number: int,
    session: CreationSession,
    audit_report: AuditReport,
    provider_name: str,
    round_number: int,
    *,
    use_search_context: bool,
    use_vector_context: bool | VectorContextMode,
    world_state_dir: Path | None = None,
) -> Path:
    issue_summary = "; ".join(
        f"{issue.severity}/{issue.type}: {issue.description}"
        for issue in audit_report.issues
        if issue.severity in {"medium", "high", "critical"}
    )
    provider = load_revision_provider(
        root,
        provider_name,
        target="polished",
        chapter_number=chapter_number,
    )
    result = revise_chapter(
        ChapterRevisionOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=(
                f"自动修复第 {round_number} 轮。必须解决以下 audit medium/high/critical issues，"
                f"不得改变已批准大纲的核心剧情：{issue_summary}"
            ),
            from_audit=True,
            target="polished",
            force=True,
            use_search_context=use_search_context,
            use_vector_context=use_vector_context,
            world_state_dir=world_state_dir,
        ),
        provider,
        provider_name=provider_name,
    )
    return result.output_path


def _auto_repair_chapter_with_instruction(
    root: Path,
    chapter_number: int,
    session: CreationSession,
    audit_report: AuditReport,
    provider_name: str,
    round_number: int,
    instruction: str | None,
    *,
    use_search_context: bool,
    use_vector_context: bool | VectorContextMode,
) -> Path:
    issue_summary = "; ".join(
        f"{issue.severity}/{issue.type}: {issue.description}"
        for issue in audit_report.issues
        if issue.severity in {"medium", "high", "critical"}
    )
    extra = f"\n用户补充提示：{instruction.strip()}" if instruction and instruction.strip() else ""
    provider = load_revision_provider(
        root,
        provider_name,
        target="polished",
        chapter_number=chapter_number,
    )
    result = revise_chapter(
        ChapterRevisionOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=(
                f"根据最新 audit 重新打回重写第 {round_number} 轮。必须解决以下 issues，"
                f"不得改变已批准大纲的核心剧情：{issue_summary}{extra}"
            ),
            from_audit=True,
            target="polished",
            force=True,
            use_search_context=use_search_context,
            use_vector_context=use_vector_context,
        ),
        provider,
        provider_name=provider_name,
    )
    return result.output_path


def _auto_replan_chapter(
    root: Path,
    chapter_number: int,
    session: CreationSession,
    audit_report: AuditReport,
    provider_name: str,
    round_number: int,
    *,
    use_search_context: bool,
    use_vector_context: bool | VectorContextMode,
) -> None:
    issue_summary = _blocking_issue_summary(audit_report)
    provider = load_planning_provider(root, provider_name, chapter_number=chapter_number)
    plan_chapter(
        ChapterPlanningOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=(
                f"{_session_instruction(session)}\n\n"
                f"自动修复第 {round_number} 轮：上一轮 audit 显示问题来自章节计划或信息暴露边界。"
                "请重写本章 ChapterPlan，降低伏笔直白程度，避免角色知道尚未获得的信息，"
                f"并解决这些问题：{issue_summary}"
            ),
            force=True,
            use_search_context=use_search_context,
            use_vector_context=use_vector_context,
        ),
        provider,
    )


def _promote_revision_to_polished(root: Path, chapter_number: int, revision_path: Path) -> Path:
    chapter_dir = _chapter_dir(root, chapter_number)
    polished_path = chapter_dir / "polished.md"
    document = read_markdown_with_front_matter(revision_path)
    metadata = dict(document.metadata)
    revision_id = metadata.get("revision_id")
    source_file = metadata.get("based_on")
    metadata["status"] = "polished"
    metadata["created_by"] = "revision_agent"
    metadata["based_on"] = revision_path.name
    if revision_id:
        metadata["revision_id"] = revision_id
    if source_file:
        metadata["revision_source_file"] = source_file
    metadata["promoted_at"] = utc_now()
    metadata_text = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
    backup_if_exists(polished_path, reason="auto_repair")
    atomic_write_text(polished_path, f"---\n{metadata_text}\n---\n\n{document.body.strip()}\n")
    return polished_path


def _retire_state_update_proposal(root: Path, chapter_number: int) -> None:
    proposal_path = _chapter_dir(root, chapter_number) / "state_update_proposal.json"
    if not proposal_path.exists():
        return
    backup_if_exists(proposal_path, reason="content_revision")
    proposal_path.unlink()


def _should_replan_chapter(report: AuditReport, round_number: int) -> bool:
    if round_number <= 1:
        return False
    for issue in report.issues:
        if issue.severity not in {"medium", "high", "critical"}:
            continue
        if issue.source_layer == "plan":
            return True
        if any(Path(item.source).name == "plan.json" for item in issue.evidence):
            return True
    return False


def _blocking_issue_summary(report: AuditReport) -> str:
    return "; ".join(
        f"{issue.severity}/{issue.type}: {issue.description} suggested_fix={issue.suggested_fix}"
        for issue in report.issues
        if issue.severity in {"medium", "high", "critical"}
    )


def _propose_state(
    root: Path,
    chapter_number: int,
    session: CreationSession,
    provider_name: str,
    *,
    force: bool,
    use_search_context: bool,
    use_vector_context: bool | VectorContextMode,
    world_state_dir: Path | None = None,
) -> None:
    provider = load_state_update_provider(root, provider_name, chapter_number=chapter_number)
    propose_state_update(
        StateUpdateProposeOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=_session_instruction(session),
            force=force,
            use_search_context=use_search_context,
            use_vector_context=use_vector_context,
            world_state_dir=world_state_dir,
        ),
        provider,
    )


def _capture_and_advance_projection(
    root: Path,
    session: CreationSession,
    chapter_number: int,
    projection: SessionProjection,
) -> SessionProjection:
    proposal = load_json_model(
        _chapter_dir(root, chapter_number) / "state_update_proposal.json",
        StateUpdateProposal,
    )
    base_state_path, base_timeline_path = projection_paths(root, projection)
    lifecycle = capture_working_chapter(
        root,
        chapter_number,
        base_state_sha256=projection.current_state_sha256,
        base_timeline_sha256=projection.current_timeline_sha256,
        base_state_path=base_state_path,
        base_timeline_path=base_timeline_path,
    )
    proposal_ref = lifecycle.active_state_proposal.proposal if lifecycle.active_state_proposal else None
    return advance_projection(
        root,
        session.session_id,
        proposal,
        proposal_artifact_id=proposal_ref.artifact_id if proposal_ref else None,
    )


def _archive_sources(root: Path, session: CreationSession) -> list[Path]:
    paths: list[Path] = [
        _session_path(root, session.session_id),
        _session_dir(root, session.session_id) / "approved_outline.json",
        _session_dir(root, session.session_id) / "approved_outline.md",
        _rewrite_events_path(root, session.session_id),
        projection_dir(root, session.session_id) / "projection.json",
        projection_dir(root, session.session_id) / "current_state.json",
        projection_dir(root, session.session_id) / "timeline.json",
    ]
    rejection_dir = _session_dir(root, session.session_id) / "rejections"
    if rejection_dir.exists():
        paths.extend(sorted(rejection_dir.glob("*.md")))
    for chapter_number in session.chapter_range:
        chapter_dir = _chapter_dir(root, chapter_number)
        paths.extend(
            [
                chapter_dir / "plan.json",
                chapter_dir / "plan.md",
                chapter_dir / "draft.md",
                chapter_dir / "polished.md",
                chapter_dir / "audit.json",
                chapter_dir / "state_update_proposal.json",
                chapter_dir / "state_update_apply_log.json",
                chapter_dir / "chapter_memory.json",
                chapter_dir / "metadata.json",
                chapter_dir / "lifecycle.json",
                chapter_dir / "accepted.md",
                chapter_dir / "acceptance.json",
            ]
        )
        for directory_name in (
            "plans",
            "candidates",
            "audits",
            "state_proposals",
            "chapter_memories",
            "acceptances",
        ):
            directory = chapter_dir / directory_name
            if directory.exists():
                paths.extend(sorted(path for path in directory.iterdir() if path.is_file()))
    return paths


def _write_session(root: Path, session: CreationSession) -> None:
    atomic_write_model_json(_session_path(root, session.session_id), session)


def request_session_cancel(root: Path, session_id: str) -> SessionProgress:
    root = root.resolve()
    load_session(root, session_id)
    progress = load_session_progress(root, session_id)
    if progress.status not in {"running", "cancel_requested"}:
        return progress
    return _record_session_progress(
        root,
        session_id,
        status="cancel_requested",
        stage=progress.current_stage or "cancel_requested",
        message="取消已请求，将在当前阶段结束后生效。",
        chapter_number=progress.current_chapter,
        round_number=progress.current_round,
    )


def _raise_if_session_cancel_requested(
    root: Path,
    session: CreationSession,
    *,
    chapter_number: int | None = None,
    round_number: int | None = None,
) -> None:
    progress = load_session_progress(root, session.session_id)
    if progress.status != "cancel_requested":
        return
    _record_session_progress(
        root,
        session.session_id,
        status="cancel_requested",
        stage="cancel_boundary",
        message="取消请求已到达安全边界，正在停止当前 Session 任务。",
        chapter_number=chapter_number,
        round_number=round_number,
    )
    raise _SessionCancelRequested("Session 任务已取消。")


def _cancelled_session_result(
    root: Path,
    session: CreationSession,
    *,
    final_outputs: list[str],
    audits: list[str],
    revisions: list[str],
    message: str,
) -> SessionResult:
    updates: dict[str, object] = {
        "final_output_paths": _merge_relative_paths(session.final_output_paths, final_outputs),
        "audit_history": [*session.audit_history, *audits],
        "revision_history": [*session.revision_history, *revisions],
    }
    updated = _transition_session(session, SessionPhase.CANCELLED, **updates)
    _write_session(root, updated)
    _record_session_progress(
        root,
        updated.session_id,
        status="cancelled",
        stage="cancelled",
        message=message,
    )
    return SessionResult(session=updated, session_path=_session_path(root, updated.session_id), message=message)


def _session_has_partial_outputs(root: Path, session: CreationSession) -> bool:
    for chapter_number in session.chapter_range:
        chapter_dir = _chapter_dir(root, chapter_number)
        if any(
            (chapter_dir / name).exists()
            for name in ("draft.md", "polished.md", "audit.json", "state_update_proposal.json")
        ):
            return True
    return False


def _merge_relative_paths(existing: list[str], incoming: list[str]) -> list[str]:
    merged = list(existing)
    seen = set(merged)
    for path in incoming:
        if path not in seen:
            merged.append(path)
            seen.add(path)
    return merged


def _start_rewrite_event(
    root: Path,
    session: CreationSession,
    chapter_number: int,
    round_number: int,
    action: SessionRewriteAction,
    audit_report: AuditReport,
) -> SessionRewriteEvent:
    chapter_dir = _chapter_dir(root, chapter_number)
    before_output = chapter_dir / "polished.md"
    snapshot_path: Path | None = None
    if before_output.exists():
        snapshot_path = (
            _session_dir(root, session.session_id)
            / "rejections"
            / f"chapter_{chapter_number:03d}_round_{round_number}_before.md"
        )
        atomic_write_text(snapshot_path, before_output.read_text(encoding="utf-8"))
    event = SessionRewriteEvent(
        event_id=_new_rewrite_event_id(chapter_number, round_number, action),
        session_id=session.session_id,
        chapter_number=chapter_number,
        round_number=round_number,
        action=action,
        status="started",
        trigger_audit_path=_rel(root, chapter_dir / "audit.json"),
        blocking_issues=_rewrite_issues(audit_report),
        rejected_text_snapshot_path=_rel(root, snapshot_path) if snapshot_path else None,
        before_output_path=_rel(root, before_output) if before_output.exists() else None,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    events = [*load_rewrite_events(root, session.session_id), event]
    _write_rewrite_events(root, session.session_id, events)
    return event


def _update_rewrite_event(
    root: Path,
    session_id: str,
    event_id: str,
    *,
    status: SessionRewriteStatus,
    after_output_path: Path | None = None,
) -> None:
    events = load_rewrite_events(root, session_id)
    updated_events: list[SessionRewriteEvent] = []
    for event in events:
        if event.event_id != event_id:
            updated_events.append(event)
            continue
        updates: dict[str, object] = {"status": status, "updated_at": utc_now()}
        if after_output_path:
            updates["after_output_path"] = _rel(root, after_output_path)
        updated_events.append(event.model_copy(update=updates))
    _write_rewrite_events(root, session_id, updated_events)


def _write_rewrite_events(root: Path, session_id: str, events: list[SessionRewriteEvent]) -> None:
    atomic_write_model_json(_rewrite_events_path(root, session_id), SessionRewriteEvents(events=events))


def _find_rewrite_event(root: Path, session_id: str, event_id: str) -> SessionRewriteEvent:
    for event in load_rewrite_events(root, session_id):
        if event.event_id == event_id:
            return event
    raise CreationSessionError(f"rewrite event not found: {event_id}")


def _replace_rewrite_event(root: Path, session_id: str, updated_event: SessionRewriteEvent) -> None:
    events = [
        updated_event if event.event_id == updated_event.event_id else event
        for event in load_rewrite_events(root, session_id)
    ]
    _write_rewrite_events(root, session_id, events)


def _event_snapshot_path(root: Path, event: SessionRewriteEvent) -> Path:
    if not event.rejected_text_snapshot_path:
        raise CreationSessionError(f"rewrite event has no rejected text snapshot: {event.event_id}")
    path = root / event.rejected_text_snapshot_path
    if not path.exists():
        raise CreationSessionError(f"rejected text snapshot is missing: {event.rejected_text_snapshot_path}")
    return path


def _rewrite_issues(audit_report: AuditReport) -> list[SessionRewriteIssue]:
    return [
        SessionRewriteIssue.model_validate(issue.model_dump(mode="json"))
        for issue in audit_report.issues
        if issue.severity in {"medium", "high", "critical"}
    ]


def _render_outline_markdown(outline: CreationOutline) -> str:
    lines = [
        f"# Creation Session {outline.session_id}",
        "",
        "## User Intent",
        "",
        outline.user_intent,
        "",
        "## Chapters",
        "",
    ]
    for chapter in outline.chapters:
        lines.extend(
            [
                f"### Chapter {chapter.chapter_number:03d}: {chapter.title}",
                "",
                f"- Plan: {chapter.plan_path}",
                f"- Summary: {chapter.summary}",
                f"- Reveal Authorizations: {len(chapter.reveal_authorizations)}",
                *(
                    f"  - {item.hidden_truth_id}: {item.method}（{item.reason}）"
                    for item in chapter.reveal_authorizations
                ),
                "",
            ]
        )
    return "\n".join(lines)


def _has_hard_issues(report: AuditReport) -> bool:
    return any(issue.severity in {"medium", "high", "critical"} for issue in report.issues)


def _load_audit(root: Path, chapter_number: int) -> AuditReport:
    return load_json_model(_chapter_dir(root, chapter_number) / "audit.json", AuditReport)


def _ensure_session_mutable(root: Path, session: CreationSession) -> None:
    archive_dir = root / "memory" / "archive" / session.session_id
    if session.phase is SessionPhase.ARCHIVED or archive_dir.exists():
        raise CreationSessionError("archived sessions are immutable; create a new revision session instead")


def _session_instruction(session: CreationSession) -> str:
    return (
        f"本次创作 Session: {session.session_id}\n"
        f"用户意图：{session.user_intent}\n"
        "必须遵守 approved_outline，不要自行改变核心剧情安排。"
    )


def _validate_chapters(chapters: tuple[int, ...]) -> None:
    if not chapters or any(chapter < 1 for chapter in chapters):
        raise CreationSessionError("chapter range must contain positive integers")
    if tuple(sorted(set(chapters))) != chapters:
        raise CreationSessionError("chapter range must be sorted and unique")


def _validate_session_id(session_id: str) -> str:
    text = str(session_id or "").strip()
    if not text:
        raise CreationSessionError("session_id is required")
    return text


def _refuse_existing(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise CreationSessionError(f"{path} already exists; use --force to overwrite it")


def parse_range(value: str) -> tuple[int, ...]:
    text = value.strip()
    if "-" in text:
        start_text, end_text = text.split("-", 1)
        start = int(start_text)
        end = int(end_text)
        if end < start:
            raise CreationSessionError("range end must be greater than or equal to start")
        return tuple(range(start, end + 1))
    return tuple(int(part.strip()) for part in text.split(",") if part.strip())


def _session_dir(root: Path, session_id: str) -> Path:
    return root / "memory" / "sessions" / session_id


def _session_path(root: Path, session_id: str) -> Path:
    return _session_dir(root, session_id) / "session.json"


def _session_plan_dir(root: Path, session_id: str, chapter_number: int) -> Path:
    return _session_dir(root, session_id) / "plans" / f"{chapter_number:03d}"


def _rewrite_events_path(root: Path, session_id: str) -> Path:
    return _session_dir(root, session_id) / "rewrite_events.json"


def _chapter_dir(root: Path, chapter_number: int) -> Path:
    return root / "memory" / "chapters" / f"{chapter_number:03d}"


def _rel(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _safe_archive_name(root: Path, path: Path) -> str:
    return _rel(root, path).replace("/", "__")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _new_session_id() -> str:
    return new_request_id("session")


def _new_rewrite_event_id(chapter_number: int, round_number: int, action: SessionRewriteAction) -> str:
    return f"rewrite_ch{chapter_number:03d}_round{round_number}_{action}_{utc_timestamp()}"
