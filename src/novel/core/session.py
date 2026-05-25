from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil

from novel.core.auditing import ChapterAuditOptions, audit_chapter, load_audit_provider
from novel.core.drafting import ChapterDraftingOptions, load_drafting_provider, write_chapter_draft
from novel.core.io import atomic_write_model_json, atomic_write_text, backup_if_exists, load_json_model
from novel.core.planning import ChapterPlanningOptions, load_planning_provider, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, load_polishing_provider, polish_chapter
from novel.core.revision import ChapterRevisionOptions, load_revision_provider, revise_chapter
from novel.core.schemas import (
    AuditReport,
    CreationArchiveEntry,
    CreationArchiveManifest,
    CreationOutline,
    CreationOutlineChapter,
    CreationScopeType,
    CreationSession,
)
from novel.core.state_update import (
    AcceptChapterOptions,
    StateUpdateProposeOptions,
    accept_chapter,
    load_state_update_provider,
    propose_state_update,
)


ProviderName = str


class CreationSessionError(RuntimeError):
    """Raised when a collaborative creation session cannot proceed safely."""


@dataclass(frozen=True)
class SessionStartOptions:
    root: Path
    user_intent: str
    chapter_range: tuple[int, ...]
    segment_range: tuple[int, ...] | None = None
    provider_name: ProviderName = "config"
    force: bool = False


@dataclass(frozen=True)
class SessionRunOptions:
    root: Path
    session_id: str
    provider_name: ProviderName = "config"
    force: bool = False
    max_auto_revision_rounds: int | None = None


@dataclass(frozen=True)
class SessionInstructionOptions:
    root: Path
    session_id: str
    instruction: str
    provider_name: ProviderName = "config"
    force: bool = False


@dataclass(frozen=True)
class SessionActionOptions:
    root: Path
    session_id: str
    provider_name: ProviderName = "config"
    force: bool = False


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
    session = _write_outline_proposal(root, session, options.provider_name, options.force)
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
    merged_intent = f"{session.user_intent}\n\n用户对大纲的修改意见：{options.instruction.strip()}"
    session = session.model_copy(update={"user_intent": merged_intent, "updated_at": _utc_now()})
    session = _write_outline_proposal(root, session, options.provider_name, force=True)
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
    if session.status not in {"outline_approved", "generating"} or session.outline_status != "approved":
        raise CreationSessionError("approve the outline before running content generation")
    if session.scope_type == "segments":
        return _run_segment_session(root, session, options)

    max_rounds = options.max_auto_revision_rounds
    if max_rounds is None:
        max_rounds = session.max_auto_revision_rounds
    session = session.model_copy(update={"status": "generating", "content_status": "generating", "updated_at": _utc_now()})
    _write_session(root, session)

    final_outputs: list[str] = []
    audits: list[str] = []
    revisions: list[str] = []
    for chapter_number in session.chapter_range:
        _generate_chapter_content(root, chapter_number, session, options.provider_name, force=options.force)
        audit_report = _load_audit(root, chapter_number)
        round_number = 0
        while _has_hard_issues(audit_report) and round_number < max_rounds:
            round_number += 1
            revision_path = _auto_repair_chapter(
                root,
                chapter_number,
                session,
                audit_report,
                options.provider_name,
                round_number,
            )
            revisions.append(_rel(root, revision_path))
            _generate_chapter_content(root, chapter_number, session, options.provider_name, force=True)
            audit_report = _load_audit(root, chapter_number)
        audit_path = _chapter_dir(root, chapter_number) / "audit.json"
        audits.append(_rel(root, audit_path))
        final_outputs.append(_rel(root, _chapter_dir(root, chapter_number) / "polished.md"))
        if _has_hard_issues(audit_report):
            session = session.model_copy(
                update={
                    "status": "generating",
                    "content_status": "needs_revision",
                    "final_output_paths": final_outputs,
                    "audit_history": [*session.audit_history, *audits],
                    "revision_history": [*session.revision_history, *revisions],
                    "updated_at": _utc_now(),
                }
            )
            _write_session(root, session)
            return SessionResult(
                session=session,
                session_path=_session_path(root, session.session_id),
                message=f"Session stopped after unresolved audit issues in chapter {chapter_number}.",
            )
        _propose_state(root, chapter_number, session, options.provider_name, force=options.force)

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
    return SessionResult(session=session, session_path=_session_path(root, session.session_id), message="Session content is ready for user review.")


def revise_content(options: SessionInstructionOptions) -> SessionResult:
    root = options.root.resolve()
    session = load_session(root, options.session_id)
    _ensure_session_mutable(root, session)
    if session.content_status not in {"needs_user_review", "needs_revision"}:
        raise CreationSessionError("content can be revised only after generation has produced reviewable content")
    revisions: list[str] = []
    for chapter_number in session.chapter_range:
        provider = load_revision_provider(root, options.provider_name, target="polished")
        result = revise_chapter(
            ChapterRevisionOptions(
                root=root,
                chapter_number=chapter_number,
                instruction=options.instruction,
                target="polished",
                force=options.force,
            ),
            provider,
            provider_name=options.provider_name,
        )
        revisions.append(_rel(root, result.output_path))
    session = session.model_copy(
        update={
            "content_status": "needs_user_review",
            "revision_history": [*session.revision_history, *revisions],
            "final_output_paths": revisions,
            "updated_at": _utc_now(),
        }
    )
    _write_session(root, session)
    return SessionResult(session=session, session_path=_session_path(root, session.session_id), message="Content revised for user review.")


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
            ),
            provider,
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


def load_session(root: Path, session_id: str) -> CreationSession:
    return load_json_model(_session_path(root.resolve(), session_id), CreationSession)


def _write_outline_proposal(
    root: Path,
    session: CreationSession,
    provider_name: str,
    force: bool,
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
) -> None:
    instruction = _session_instruction(session)
    draft_provider = load_drafting_provider(root, provider_name)
    write_chapter_draft(
        ChapterDraftingOptions(root=root, chapter_number=chapter_number, instruction=instruction, force=force),
        draft_provider,
    )
    polish_provider = load_polishing_provider(root, provider_name)
    polish_chapter(
        ChapterPolishingOptions(root=root, chapter_number=chapter_number, instruction=instruction, force=force),
        polish_provider,
    )
    audit_provider = load_audit_provider(root, provider_name, chapter_number=chapter_number)
    audit_chapter(
        ChapterAuditOptions(root=root, chapter_number=chapter_number, instruction=instruction, force=force),
        audit_provider,
    )


def _auto_repair_chapter(
    root: Path,
    chapter_number: int,
    session: CreationSession,
    audit_report: AuditReport,
    provider_name: str,
    round_number: int,
) -> Path:
    issue_summary = "; ".join(
        f"{issue.severity}/{issue.type}: {issue.description}" for issue in audit_report.issues if issue.severity in {"high", "critical"}
    )
    provider = load_revision_provider(root, provider_name, target="polished")
    result = revise_chapter(
        ChapterRevisionOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=(
                f"自动修复第 {round_number} 轮。必须解决以下 audit hard issues，"
                f"不得改变已批准大纲的核心剧情：{issue_summary}"
            ),
            from_audit=True,
            target="polished",
            force=True,
        ),
        provider,
        provider_name=provider_name,
    )
    return result.output_path


def _propose_state(root: Path, chapter_number: int, session: CreationSession, provider_name: str, *, force: bool) -> None:
    provider = load_state_update_provider(root, provider_name, chapter_number=chapter_number)
    propose_state_update(
        StateUpdateProposeOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=_session_instruction(session),
            force=force,
        ),
        provider,
    )


def _archive_sources(root: Path, session: CreationSession) -> list[Path]:
    paths: list[Path] = [
        _session_path(root, session.session_id),
        _session_dir(root, session.session_id) / "approved_outline.json",
        _session_dir(root, session.session_id) / "approved_outline.md",
    ]
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
    return any(issue.severity in {"high", "critical"} for issue in report.issues)


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
