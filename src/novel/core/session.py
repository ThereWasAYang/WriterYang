from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import yaml

from novel.core.auditing import ChapterAuditOptions, audit_chapter, load_audit_provider
from novel.core.drafting import ChapterDraftingOptions, load_drafting_provider, write_chapter_draft
from novel.core.io import atomic_write_model_json, atomic_write_text, backup_if_exists, load_json_model, load_yaml_model
from novel.core.management import record_management_event
from novel.core.orchestrator import route_audit_repair, route_revision_request
from novel.core.planning import ChapterPlanningOptions, load_planning_provider, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, load_polishing_provider, polish_chapter
from novel.core.polishing import read_markdown_with_front_matter
from novel.core.revision import ChapterRevisionOptions, load_revision_provider, revise_chapter
from novel.core.runtime_config import normalize_polish_mode, project_polish_mode
from novel.core.security import redact_secret_text
from novel.core.schemas import (
    AuditReport,
    ChapterPlan,
    CreationArchiveEntry,
    CreationArchiveManifest,
    CreationOutline,
    CreationOutlineChapter,
    CreationScopeType,
    CreationSession,
    SessionRewriteAction,
    SessionAuditRevision,
    RevisionRouteDecision,
    RevisionRouteRecord,
    SessionRewriteEvent,
    SessionRewriteEvents,
    SessionRewriteIssue,
    SessionRewriteStatus,
    SessionProgress,
    SessionProgressEvent,
    SessionProgressStatus,
    PolishMode,
    ProjectConfig,
    VectorContextMode,
)
from novel.core.state_update import (
    AcceptChapterOptions,
    StateUpdateProposeOptions,
    accept_chapter,
    load_state_update_provider,
    propose_state_update,
)


ProviderName = str
_SESSION_PROGRESS_EVENT_LIMIT = 50


class CreationSessionError(RuntimeError):
    """Raised when a collaborative creation session cannot proceed safely."""


class _SessionCancelRequested(RuntimeError):
    """Internal signal for cooperative cancellation at safe session boundaries."""


@dataclass(frozen=True)
class SessionStartOptions:
    root: Path
    user_intent: str
    chapter_range: tuple[int, ...]
    segment_range: tuple[int, ...] | None = None
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


def start_session(options: SessionStartOptions) -> SessionResult:
    root = options.root.resolve()
    _validate_chapters(options.chapter_range)
    session_id = _new_session_id()
    scope_type: CreationScopeType = "segments" if options.segment_range else "chapters"
    session = CreationSession(
        session_id=session_id,
        scope_type=scope_type,
        chapter_range=list(options.chapter_range),
        segment_range=list(options.segment_range) if options.segment_range else None,
        user_intent=options.user_intent.strip(),
        status="drafting_intent",
        outline_status="draft",
        content_status="not_started",
        created_at=_utc_now(),
        updated_at=_utc_now(),
    )
    _ensure_session_mutable(root, session)
    session_dir = _session_dir(root, session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    session = _write_outline_proposal(
        root,
        session,
        options.provider_name,
        options.force,
        use_search_context=options.use_search_context,
        use_vector_context=options.use_vector_context,
    )
    _write_session(root, session)
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
    if session.status == "archived":
        raise CreationSessionError("archived sessions cannot be revised")
    if not options.instruction or not options.instruction.strip():
        raise CreationSessionError("outline revision requires --instruction")
    merged_intent = f"{session.user_intent}\n\n用户对大纲的修改意见：{options.instruction.strip()}"
    session = session.model_copy(update={"user_intent": merged_intent, "updated_at": _utc_now()})
    session = _write_outline_proposal(
        root,
        session,
        options.provider_name,
        force=True,
        use_search_context=options.use_search_context,
        use_vector_context=options.use_vector_context,
    )
    _write_session(root, session)
    return SessionResult(session=session, session_path=_session_path(root, session.session_id), message="Outline revised.")


def approve_outline(options: SessionActionOptions) -> SessionResult:
    root = options.root.resolve()
    session = load_session(root, options.session_id)
    _ensure_session_mutable(root, session)
    proposal_json = _session_dir(root, session.session_id) / "outline_proposal.json"
    proposal_md = _session_dir(root, session.session_id) / "outline_proposal.md"
    if not proposal_json.exists() or not proposal_md.exists():
        raise CreationSessionError("outline proposal is missing; run session start or revise-outline first")
    approved_json = _session_dir(root, session.session_id) / "approved_outline.json"
    approved_md = _session_dir(root, session.session_id) / "approved_outline.md"
    _refuse_existing(approved_json, options.force)
    _refuse_existing(approved_md, options.force)
    if options.force:
        backup_if_exists(approved_json, reason="force")
        backup_if_exists(approved_md, reason="force")
    shutil.copy2(proposal_json, approved_json)
    shutil.copy2(proposal_md, approved_md)
    session = session.model_copy(
        update={
            "status": "outline_approved",
            "outline_status": "approved",
            "approved_outline_path": _rel(root, approved_json),
            "updated_at": _utc_now(),
        }
    )
    _write_session(root, session)
    return SessionResult(session=session, session_path=_session_path(root, session.session_id), message="Outline approved.")


def run_session(options: SessionRunOptions) -> SessionResult:
    root = options.root.resolve()
    session = load_session(root, options.session_id)
    _ensure_session_mutable(root, session)
    if session.status not in {"outline_approved", "generating", "needs_revision"} or session.outline_status != "approved":
        raise CreationSessionError("approve the outline before running content generation")
    if session.scope_type == "segments":
        return _run_segment_session(root, session, options)

    max_rounds = options.max_auto_revision_rounds
    if max_rounds is None:
        max_rounds = session.max_auto_revision_rounds
    session = session.model_copy(update={"status": "generating", "content_status": "generating", "updated_at": _utc_now()})
    _write_session(root, session)
    _start_session_progress(root, session.session_id, message="Session 写作任务已开始。")

    final_outputs: list[str] = []
    audits: list[str] = []
    revisions: list[str] = []
    try:
        _raise_if_session_cancel_requested(root, session)
        for chapter_number in session.chapter_range:
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
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
                polish_mode=options.polish_mode,
            )
            if generated_audit is False:
                final_outputs.append(_rel(root, _chapter_dir(root, chapter_number) / "draft.md"))
                session = session.model_copy(
                    update={
                        "status": "needs_revision",
                        "content_status": "needs_user_review",
                        "final_output_paths": final_outputs,
                        "audit_history": [*session.audit_history, *audits],
                        "revision_history": [*session.revision_history, *revisions],
                        "updated_at": _utc_now(),
                    }
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
                return SessionResult(session=session, session_path=_session_path(root, session.session_id), message="Session stopped at review gate.")
            _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number)
            audit_report = _load_audit(root, chapter_number)
            round_number = 0
            while _has_hard_issues(audit_report) and round_number < max_rounds:
                round_number += 1
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
                _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number, round_number=round_number)
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
                        _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number, round_number=round_number)
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
                        )
                        _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number, round_number=round_number)
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
                        )
                    except _SessionCancelRequested:
                        raise
                    except Exception:
                        _update_rewrite_event(root, session.session_id, rewrite_event.event_id, status="failed")
                        raise
                _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number, round_number=round_number)
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
                session = session.model_copy(
                    update={
                        "status": "needs_revision",
                        "content_status": "needs_revision",
                        "final_output_paths": final_outputs,
                        "audit_history": [*session.audit_history, *audits],
                        "revision_history": [*session.revision_history, *revisions],
                        "updated_at": _utc_now(),
                    }
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
            _propose_state(
                root,
                chapter_number,
                session,
                options.provider_name,
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            )
            _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number)

        session = session.model_copy(
            update={
                "status": "needs_user_review",
                "content_status": "needs_user_review",
                "final_output_paths": final_outputs,
                "audit_history": [*session.audit_history, *audits],
                "revision_history": [*session.revision_history, *revisions],
                "updated_at": _utc_now(),
            }
        )
        _write_session(root, session)
        _record_session_progress(
            root,
            session.session_id,
            status="completed",
            stage="completed",
            message="Session 内容已生成，等待用户审阅。",
        )
        return SessionResult(session=session, session_path=_session_path(root, session.session_id), message="Session content is ready for user review.")
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
        _record_session_progress(
            root,
            session.session_id,
            status="failed",
            stage="failed",
            message="Session 任务失败。",
            error=str(exc),
        )
        raise


def revise_content(options: SessionInstructionOptions) -> SessionResult:
    root = options.root.resolve()
    session = load_session(root, options.session_id)
    _ensure_session_mutable(root, session)
    if session.content_status not in {"needs_user_review", "needs_revision"}:
        raise CreationSessionError("content can be revised only after generation has produced reviewable content")
    if not options.from_audit and not (options.instruction and options.instruction.strip()):
        raise CreationSessionError("content revision requires --instruction or --from-audit")
    route_record = _resolve_content_revision_route(root, session, options)
    route = route_record.decision
    revisions: list[str] = []
    audits: list[str] = []
    final_outputs: list[str] = []
    hard_issue_chapters: list[int] = []
    for chapter_number in session.chapter_range:
        if route.route == "plot_replan":
            output_path = _replan_and_rewrite_chapter(
                root,
                chapter_number,
                session,
                options.provider_name,
                route.instruction_for_plot or options.instruction or "",
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            )
        elif route.route == "writer_rewrite":
            output_path = _rewrite_chapter_with_writer(
                root,
                chapter_number,
                options.provider_name,
                route.instruction_for_writer or options.instruction or "",
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            )
        else:
            provider = load_revision_provider(root, options.provider_name, target="polished")
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
                ),
                provider,
                provider_name=options.provider_name,
            )
            revisions.append(_rel(root, result.output_path))
            output_path = _promote_revision_to_polished(root, chapter_number, result.output_path)
        final_outputs.append(_rel(root, output_path))
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
        audit_path = _chapter_dir(root, chapter_number) / "audit.json"
        audits.append(_rel(root, audit_path))
        if _has_hard_issues(_load_audit(root, chapter_number)):
            hard_issue_chapters.append(chapter_number)

    if hard_issue_chapters:
        session = session.model_copy(
            update={
                "status": "needs_revision",
                "content_status": "needs_revision",
                "revision_history": [*session.revision_history, *revisions],
                "revision_route_history": [*session.revision_route_history, route_record],
                "audit_history": [*session.audit_history, *audits],
                "final_output_paths": final_outputs,
                "updated_at": _utc_now(),
            }
        )
        _write_session(root, session)
        chapters = ", ".join(str(number) for number in hard_issue_chapters)
        return SessionResult(
            session=session,
            session_path=_session_path(root, session.session_id),
            message=f"Content revised, but chapter(s) {chapters} still need revision after audit.",
        )

    for chapter_number in session.chapter_range:
        _propose_state(
            root,
            chapter_number,
            session,
            options.provider_name,
            force=True,
            use_search_context=options.use_search_context,
            use_vector_context=options.use_vector_context,
        )

    session = session.model_copy(
        update={
            "status": "needs_user_review",
            "content_status": "needs_user_review",
            "revision_history": [*session.revision_history, *revisions],
            "revision_route_history": [*session.revision_route_history, route_record],
            "audit_history": [*session.audit_history, *audits],
            "final_output_paths": final_outputs,
            "updated_at": _utc_now(),
        }
    )
    _write_session(root, session)
    return SessionResult(
        session=session,
        session_path=_session_path(root, session.session_id),
        message=f"Content revised, audited, and ready for user review. Route: {route.route}.",
    )


def _resolve_content_revision_route(
    root: Path,
    session: CreationSession,
    options: SessionInstructionOptions,
) -> RevisionRouteRecord:
    if session.scope_type == "segments":
        decision = RevisionRouteDecision(
            route="revision_patch",
            reason="segment sessions are constrained to local text patches",
            chapter_numbers=session.chapter_range,
            instruction_for_revision=options.instruction or "按当前 session 的段落范围进行局部修订。",
            risk_level="low",
        )
    elif options.from_audit:
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
        created_at=_utc_now(),
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
        f"scope_type: {session.scope_type}\n"
        f"chapter_range: {session.chapter_range}\n"
        f"status: {session.status}/{session.content_status}\n"
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
    )


def _rewrite_chapter_with_writer(
    root: Path,
    chapter_number: int,
    provider_name: str,
    instruction: str,
    *,
    use_search_context: bool,
    use_vector_context: bool | VectorContextMode,
) -> Path:
    draft_provider = load_drafting_provider(root, provider_name)
    write_chapter_draft(
        ChapterDraftingOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=instruction,
            force=True,
            use_search_context=use_search_context,
            use_vector_context=use_vector_context,
        ),
        draft_provider,
    )
    polish_provider = load_polishing_provider(root, provider_name)
    polish_chapter(
        ChapterPolishingOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=instruction,
            force=True,
            use_search_context=use_search_context,
            use_vector_context=use_vector_context,
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
            )
        )
    outline = CreationOutline(
        session_id=session.session_id,
        user_intent=session.user_intent,
        chapters=chapters,
        created_at=_utc_now(),
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
    if session.content_status != "needs_user_review":
        raise CreationSessionError("session content must be ready for user review before acceptance")
    for chapter_number in session.chapter_range:
        provider = load_state_update_provider(root, options.provider_name, chapter_number=chapter_number)
        accept_chapter(
            AcceptChapterOptions(
                root=root,
                chapter_number=chapter_number,
                allow_issues=False,
                propose=True,
                force_proposal=options.force,
                canon_provider_name=options.provider_name,
            ),
            provider,
        )
        record_management_event(
            root,
            "chapter_accepted",
            f"Session {session.session_id} 已认可第 {chapter_number} 章，并确认状态/时间线更新。",
            source=session.session_id,
            target_files=[
                f"memory/chapters/{chapter_number:03d}/metadata.json",
                f"memory/chapters/{chapter_number:03d}/state_update_apply_log.json",
            ],
            status="success",
        )
    session = session.model_copy(
        update={"status": "accepted", "content_status": "accepted", "updated_at": _utc_now()}
    )
    _write_session(root, session)
    return SessionResult(session=session, session_path=_session_path(root, session.session_id), message="Session accepted.")


def archive_session(options: SessionActionOptions) -> SessionResult:
    root = options.root.resolve()
    session = load_session(root, options.session_id)
    if session.status not in {"accepted", "archived"}:
        raise CreationSessionError("accept the session before archiving it")
    archive_dir = root / "memory" / "archive" / session.session_id
    if archive_dir.exists() and session.status == "archived" and not options.force:
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
                created_at=_utc_now(),
            )
        )
    manifest = CreationArchiveManifest(session_id=session.session_id, created_at=_utc_now(), entries=entries)
    manifest_path = archive_dir / "manifest.json"
    if options.force:
        backup_if_exists(manifest_path, reason="archive")
    atomic_write_model_json(manifest_path, manifest)
    session = session.model_copy(
        update={
            "status": "archived",
            "content_status": "archived",
            "archive_paths": [_rel(root, archive_dir), _rel(root, manifest_path)],
            "updated_at": _utc_now(),
        }
    )
    _write_session(root, session)
    return SessionResult(session=session, session_path=_session_path(root, session.session_id), message="Session archived.")


def revise_audit(options: SessionRewriteControlOptions) -> SessionResult:
    root, session, event = _load_rewrite_control_context(options)
    if not options.instruction or not options.instruction.strip():
        raise CreationSessionError("audit revision requires --instruction")
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
        created_at=_utc_now(),
    )
    updated_event = event.model_copy(
        update={
            "audit_revision_history": [*event.audit_revision_history, revision],
            "trigger_audit_path": _rel(root, audit_path),
            "status": "unresolved" if _has_hard_issues(_load_audit(root, chapter_number)) else "completed",
            "updated_at": _utc_now(),
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
    session = session.model_copy(
        update={
            **status_update,
            "audit_history": [*session.audit_history, _rel(root, audit_path)],
            "updated_at": _utc_now(),
        }
    )
    _write_session(root, session)
    return SessionResult(session=session, session_path=_session_path(root, session.session_id), message="Audit revised.")


def undo_rewrite(options: SessionRewriteControlOptions) -> SessionResult:
    root, session, event = _load_rewrite_control_context(options)
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
            "updated_at": _utc_now(),
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
    session = session.model_copy(
        update={
            **status_update,
            "audit_history": [*session.audit_history, _rel(root, audit_path)],
            "updated_at": _utc_now(),
        }
    )
    _write_session(root, session)
    return SessionResult(session=session, session_path=_session_path(root, session.session_id), message="Rewrite restored from rejected text snapshot.")


def retry_rewrite(options: SessionRewriteControlOptions) -> SessionResult:
    root, session, event = _load_rewrite_control_context(options)
    chapter_number = event.chapter_number
    audit_report = _load_audit(root, chapter_number)
    if not _has_hard_issues(audit_report):
        raise CreationSessionError("latest audit has no medium/high/critical issue to retry rewrite")
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
            "updated_at": _utc_now(),
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
    session = session.model_copy(
        update={
            **status_update,
            "audit_history": [*session.audit_history, _rel(root, _chapter_dir(root, chapter_number) / "audit.json")],
            "revision_history": [*session.revision_history, *new_revisions],
            "updated_at": _utc_now(),
        }
    )
    _write_session(root, session)
    return SessionResult(session=session, session_path=_session_path(root, session.session_id), message="Rewrite retried from latest audit.")


def load_session(root: Path, session_id: str) -> CreationSession:
    return load_json_model(_session_path(root.resolve(), session_id), CreationSession)


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
            "status": "needs_revision",
            "content_status": "needs_revision",
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
        "status": "needs_user_review",
        "content_status": "needs_user_review",
        "final_output_paths": _with_replaced_output(session.final_output_paths, root, final_output_path),
    }


def _with_replaced_output(existing: list[str], root: Path, output_path: Path) -> list[str]:
    rel_path = _rel(root, output_path)
    chapter_prefix = "/".join(Path(rel_path).parts[:3])
    retained = [
        path for path in existing
        if "/".join(Path(path).parts[:3]) != chapter_prefix and path != rel_path
    ]
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
            ),
            provider,
        )
        chapters.append(
            CreationOutlineChapter(
                chapter_number=chapter_number,
                title=result.plan.title,
                plan_path=_rel(root, result.plan_json_path),
                summary=result.plan.summary,
            )
        )
    outline = CreationOutline(
        session_id=session.session_id,
        user_intent=session.user_intent,
        chapters=chapters,
        created_at=_utc_now(),
    )
    session_dir = _session_dir(root, session.session_id)
    atomic_write_model_json(session_dir / "outline_proposal.json", outline)
    atomic_write_text(session_dir / "outline_proposal.md", _render_outline_markdown(outline))
    return session.model_copy(
        update={
            "status": "outline_proposed",
            "outline_status": "proposed",
            "updated_at": _utc_now(),
        }
    )


def _run_segment_session(root: Path, session: CreationSession, options: SessionRunOptions) -> SessionResult:
    revisions: list[str] = []
    for chapter_number in session.chapter_range:
        provider = load_revision_provider(root, options.provider_name, target="polished")
        result = revise_chapter(
            ChapterRevisionOptions(
                root=root,
                chapter_number=chapter_number,
                instruction=session.user_intent,
                target="polished",
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            provider,
            provider_name=options.provider_name,
        )
        revisions.append(_rel(root, result.output_path))
    session = session.model_copy(
        update={
            "status": "needs_user_review",
            "content_status": "needs_user_review",
            "final_output_paths": revisions,
            "revision_history": [*session.revision_history, *revisions],
            "updated_at": _utc_now(),
        }
    )
    _write_session(root, session)
    return SessionResult(session=session, session_path=_session_path(root, session.session_id), message="Segment revision is ready for user review.")


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
) -> bool:
    mode = _effective_session_polish_mode(root, polish_mode)
    instruction = _session_instruction(session)
    draft_provider = load_drafting_provider(root, provider_name)
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
        ),
        draft_provider,
    )
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
    if mode == "auto":
        polish_provider = load_polishing_provider(root, provider_name)
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
            ),
            polish_provider,
        )
    else:
        _record_session_progress(
            root,
            session.session_id,
            status="running",
            stage="single_pass_final",
            message=f"正在将第 {chapter_number} 章草稿标记为最终稿。",
            chapter_number=chapter_number,
        )
        _promote_draft_to_polished(root, chapter_number, force=force)
    _raise_if_session_cancel_requested(root, session, chapter_number=chapter_number)
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
        ),
        audit_provider,
    )
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
        f"created_at: {_utc_now()}\n"
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
) -> Path:
    issue_summary = "; ".join(
        f"{issue.severity}/{issue.type}: {issue.description}"
        for issue in audit_report.issues
        if issue.severity in {"medium", "high", "critical"}
    )
    provider = load_revision_provider(root, provider_name, target="polished")
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
    provider = load_revision_provider(root, provider_name, target="polished")
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
    metadata["promoted_at"] = _utc_now()
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
        ),
        provider,
    )


def _archive_sources(root: Path, session: CreationSession) -> list[Path]:
    paths: list[Path] = [
        _session_path(root, session.session_id),
        _session_dir(root, session.session_id) / "approved_outline.json",
        _session_dir(root, session.session_id) / "approved_outline.md",
        _rewrite_events_path(root, session.session_id),
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
                chapter_dir / "metadata.json",
            ]
        )
    return paths


def _write_session(root: Path, session: CreationSession) -> None:
    atomic_write_model_json(_session_path(root, session.session_id), session)


def load_session_progress(root: Path, session_id: str) -> SessionProgress:
    path = _session_progress_path(root.resolve(), session_id)
    if not path.exists():
        return SessionProgress(session_id=session_id, status="idle")
    return load_json_model(path, SessionProgress)


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


def _start_session_progress(root: Path, session_id: str, *, message: str) -> SessionProgress:
    now = _utc_now()
    event = SessionProgressEvent(stage="session_start", message=message, created_at=now)
    progress = SessionProgress(
        session_id=session_id,
        status="running",
        current_stage="session_start",
        current_message=message,
        events=[event],
        started_at=now,
        updated_at=now,
    )
    _write_session_progress(root, progress)
    return progress


def _record_session_progress(
    root: Path,
    session_id: str,
    *,
    status: SessionProgressStatus,
    stage: str,
    message: str,
    chapter_number: int | None = None,
    round_number: int | None = None,
    error: str | None = None,
) -> SessionProgress:
    now = _utc_now()
    existing = load_session_progress(root, session_id)
    next_status = status
    if existing.status == "cancel_requested" and status == "running":
        next_status = "cancel_requested"
    event = SessionProgressEvent(
        stage=stage,
        message=message,
        chapter_number=chapter_number,
        round_number=round_number,
        created_at=now,
    )
    events = [*existing.events, event][-_SESSION_PROGRESS_EVENT_LIMIT:]
    progress = SessionProgress(
        session_id=session_id,
        status=next_status,
        current_stage=stage,
        current_message=message,
        current_chapter=chapter_number,
        current_round=round_number,
        events=events,
        started_at=existing.started_at or now,
        updated_at=now,
        completed_at=now if next_status in {"cancelled", "completed", "failed"} else existing.completed_at,
        cancel_requested_at=now if next_status == "cancel_requested" and not existing.cancel_requested_at else existing.cancel_requested_at,
        error=_safe_progress_error(error) if error else None,
    )
    _write_session_progress(root, progress)
    return progress


def _write_session_progress(root: Path, progress: SessionProgress) -> None:
    atomic_write_model_json(_session_progress_path(root, progress.session_id), progress)


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
    partial_outputs = _session_has_partial_outputs(root, session)
    updates: dict[str, object] = {
        "updated_at": _utc_now(),
        "final_output_paths": _merge_relative_paths(session.final_output_paths, final_outputs),
        "audit_history": [*session.audit_history, *audits],
        "revision_history": [*session.revision_history, *revisions],
    }
    if partial_outputs or final_outputs or audits or revisions:
        updates.update({"status": "needs_revision", "content_status": "needs_revision"})
    else:
        updates.update({"status": "outline_approved", "content_status": "not_started"})
    updated = session.model_copy(update=updates)
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
        if any((chapter_dir / name).exists() for name in ("draft.md", "polished.md", "audit.json", "state_update_proposal.json")):
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


def _safe_progress_error(value: str) -> str:
    text = redact_secret_text(value)
    return text if len(text) <= 500 else text[:497] + "..."


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
        created_at=_utc_now(),
        updated_at=_utc_now(),
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
        updates: dict[str, object] = {"status": status, "updated_at": _utc_now()}
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
    lines = [f"# Creation Session {outline.session_id}", "", "## User Intent", "", outline.user_intent, "", "## Chapters", ""]
    for chapter in outline.chapters:
        lines.extend(
            [
                f"### Chapter {chapter.chapter_number:03d}: {chapter.title}",
                "",
                f"- Plan: {chapter.plan_path}",
                f"- Summary: {chapter.summary}",
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
    if session.status == "archived" or archive_dir.exists():
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


def _rewrite_events_path(root: Path, session_id: str) -> Path:
    return _session_dir(root, session_id) / "rewrite_events.json"


def _session_progress_path(root: Path, session_id: str) -> Path:
    return _session_dir(root, session_id) / "progress.json"


def _chapter_dir(root: Path, chapter_number: int) -> Path:
    return root / "memory" / "chapters" / f"{chapter_number:03d}"


def _rel(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _safe_archive_name(root: Path, path: Path) -> str:
    return _rel(root, path).replace("/", "__")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _new_session_id() -> str:
    return "session_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _new_rewrite_event_id(chapter_number: int, round_number: int, action: SessionRewriteAction) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return f"rewrite_ch{chapter_number:03d}_round{round_number}_{action}_{stamp}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
