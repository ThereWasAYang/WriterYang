from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import uuid

from pydantic import ValidationError

from novel.core.agent_output import AgentInvocationContext, AgentOutputContract
from novel.core.artifact_store import (
    ArtifactStore,
    combined_sha256,
    load_lifecycle,
    resolve_project_path,
    sha256_file,
)
from novel.core.auditing import ChapterAuditOptions, audit_chapter, load_audit_provider
from novel.core.chapter_memory import (
    ChapterMemoryContext,
    ChapterMemoryDocument,
    build_deterministic_chapter_memory,
)
from novel.core.contracts import (
    AcceptanceCommit,
    ArtifactKind,
    AuditBinding,
    ProjectionCheckpoint,
    RevisionSession,
    RevisionSessionPhase,
    SegmentPatch,
    SessionProjection,
    StateProposalBinding,
    ensure_revision_phase_transition,
)
from novel.core.io import (
    atomic_write_model_json,
    atomic_write_text,
    backup_if_exists,
    load_json_model,
    load_yaml_model,
)
from novel.core.json_extract import JsonExtractionError, extract_json_object
from novel.core.lifecycle import LifecycleError, accepted_chapter_commit
from novel.core.markdown_blocks import (
    MarkdownBlockError,
    apply_segment_patch,
    create_segment_selection,
    parse_markdown_blocks,
    render_block_preview,
)
from novel.core.polishing import read_markdown_with_front_matter
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.prompts import load_prompt_template, prompt_template_version
from novel.core.schemas import (
    AuditReport,
    ChapterMemorySource,
    ChapterMetadata,
    ChapterPlan,
    EntityState,
    ProjectConfig,
    StateUpdateProposal,
    TimelineFile,
    VectorContextMode,
)
from novel.core.state_update import (
    StateUpdateProposeOptions,
    _validate_applied_state,
    _validate_applied_timeline,
    _validate_state_change_old_values,
    apply_state_changes_to_state,
    load_revision_state_update_provider,
    propose_state_update,
)
from novel.core.structured_generation import JsonRepairExhaustedError, generate_json_with_repair
from novel.core.timeutil import utc_now, utc_timestamp
from novel.core.transactions import (
    FileMutation,
    TransactionError,
    commit_transaction,
    prepare_transaction,
    recover_incomplete_transactions,
)
from novel.core.workflow_runtime import bind_active_session_id


class RevisionWorkflowError(RuntimeError):
    """Raised when a scoped revision workflow cannot proceed safely."""


_REVISION_SESSION_ID = re.compile(r"^revision_session_[0-9]{8}_[0-9]{6}_[0-9]{6}$")


@dataclass(frozen=True)
class RevisionStartOptions:
    root: Path
    chapter_number: int
    start_block: int
    end_block: int
    instruction: str


@dataclass(frozen=True)
class RevisionRunOptions:
    root: Path
    revision_session_id: str
    provider_name: str = "config"
    use_search_context: bool = True
    use_vector_context: bool | VectorContextMode = "auto"


@dataclass(frozen=True)
class RevisionActionOptions:
    root: Path
    revision_session_id: str


@dataclass(frozen=True)
class RevisionSessionResult:
    session: RevisionSession
    session_path: Path
    message: str


def list_revision_blocks(root: Path, chapter_number: int) -> list[dict[str, object]]:
    root = root.resolve()
    commit = accepted_chapter_commit(root, chapter_number)
    markdown = ArtifactStore(root).read_bytes(commit.candidate).decode("utf-8")
    return render_block_preview(parse_markdown_blocks(markdown))


def start_revision_session(options: RevisionStartOptions) -> RevisionSessionResult:
    root = options.root.resolve()
    instruction = options.instruction.strip()
    if not instruction:
        raise RevisionWorkflowError("revision instruction cannot be blank")
    try:
        commit = accepted_chapter_commit(root, options.chapter_number)
    except LifecycleError as exc:
        raise RevisionWorkflowError(str(exc)) from exc
    state = load_json_model(root / "memory" / "state" / "current_state.json", EntityState)
    if state.story_position.latest_chapter != options.chapter_number:
        raise RevisionWorkflowError(
            "segment revision currently requires the latest accepted chapter; "
            "revise later dependent chapters first or start a rebase workflow"
        )
    source = ArtifactStore(root).read_bytes(commit.candidate).decode("utf-8")
    try:
        selection = create_segment_selection(
            chapter_number=options.chapter_number,
            source_candidate=commit.candidate,
            markdown=source,
            start_block=options.start_block,
            end_block=options.end_block,
        )
    except MarkdownBlockError as exc:
        raise RevisionWorkflowError(str(exc)) from exc
    now = utc_now()
    revision_session_id = _new_revision_session_id()
    bind_active_session_id(revision_session_id)
    session = RevisionSession(
        revision_session_id=revision_session_id,
        chapter_number=options.chapter_number,
        base_acceptance_commit_id=commit.commit_id,
        instruction=instruction,
        phase=RevisionSessionPhase.AWAITING_PATCH,
        selection=selection,
        created_at=now,
        updated_at=now,
    )
    path = revision_session_path(root, revision_session_id)
    atomic_write_model_json(path, session)
    return RevisionSessionResult(session=session, session_path=path, message="Revision selection is ready.")


def run_revision_session(options: RevisionRunOptions) -> RevisionSessionResult:
    root = options.root.resolve()
    session = load_revision_session(root, options.revision_session_id)
    if session.phase not in {RevisionSessionPhase.AWAITING_PATCH, RevisionSessionPhase.FAILED_RECOVERABLE}:
        raise RevisionWorkflowError(f"revision session cannot run from phase {session.phase.value}")
    _require_revision_base_is_current(root, session)
    store = ArtifactStore(root)
    source = store.read_bytes(session.selection.source_candidate).decode("utf-8")
    selected = _selected_text(source, session.selection.start_block, session.selection.end_block)
    running = _transition_revision_session(session, RevisionSessionPhase.RUNNING)
    _write_revision_session(root, running)
    try:
        provider = load_segment_revision_provider(root, options.provider_name, running, selected)
        patch = generate_segment_patch(root, running, selected, provider)
        applied = apply_segment_patch(source, running.selection, patch)
        patch_ref = store.create(
            chapter_number=running.chapter_number,
            kind=ArtifactKind.SEGMENT_PATCH,
            content=(patch.model_dump_json(indent=2) + "\n").encode("utf-8"),
            suffix=".json",
        )
        candidate_ref = store.create(
            chapter_number=running.chapter_number,
            kind=ArtifactKind.CANDIDATE,
            content=applied.markdown.encode("utf-8"),
            suffix=".md",
        )
        chapter_dir = _chapter_dir(root, running.chapter_number)
        backup_if_exists(chapter_dir / "polished.md", reason="segment_revision_working")
        atomic_write_text(chapter_dir / "polished.md", applied.markdown)
        old_proposal_path = chapter_dir / "state_update_proposal.json"
        backup_if_exists(old_proposal_path, reason="segment_revision_working")
        old_proposal_path.unlink(missing_ok=True)
        audit_provider = load_audit_provider(root, options.provider_name, chapter_number=running.chapter_number)
        audit_result = audit_chapter(
            ChapterAuditOptions(
                root=root,
                chapter_number=running.chapter_number,
                instruction=(
                    f"局部修订范围：Markdown block {running.selection.start_block}-"
                    f"{running.selection.end_block}。用户要求：{running.instruction}"
                ),
                force=True,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            audit_provider,
        )
        audit_ref = store.create_from_file(
            chapter_number=running.chapter_number,
            kind=ArtifactKind.AUDIT,
            source=audit_result.audit_path,
        )
        audit_binding = AuditBinding(
            audit=audit_ref,
            candidate=candidate_ref,
            context_snapshot_hash=_canonical_context_hash(root),
            policy_version="audit-policy-v3",
        )
        if audit_result.report.overall_status != "passed":
            failed = _transition_revision_session(
                running,
                RevisionSessionPhase.FAILED_RECOVERABLE,
                update={
                    "patch": patch_ref,
                    "candidate": candidate_ref,
                    "audit": audit_binding,
                }
            )
            _write_revision_session(root, failed)
            return RevisionSessionResult(
                session=failed,
                session_path=revision_session_path(root, failed.revision_session_id),
                message="Revised candidate did not pass audit; rerun or inspect the audit.",
            )
        state_provider = load_revision_state_update_provider(
            root,
            options.provider_name,
            chapter_number=running.chapter_number,
        )
        proposal_result = propose_state_update(
            StateUpdateProposeOptions(
                root=root,
                chapter_number=running.chapter_number,
                instruction=(
                    "这是 accepted chapter 的局部修订。只输出相对当前 canonical state 的净变化，"
                    "并输出修订后本章的完整 timeline event 集合。"
                ),
                force=True,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
                timeline_mode="replace_chapter",
            ),
            state_provider,
        )
        proposal_ref = store.create_from_file(
            chapter_number=running.chapter_number,
            kind=ArtifactKind.STATE_PROPOSAL,
            source=proposal_result.proposal_path,
        )
        state_hash = sha256_file(root / "memory" / "state" / "current_state.json")
        timeline_hash = sha256_file(root / "memory" / "state" / "timeline.json")
        proposal_binding = StateProposalBinding(
            proposal=proposal_ref,
            candidate=candidate_ref,
            audit=audit_ref,
            base_state_sha256=state_hash,
            base_timeline_sha256=timeline_hash,
        )
        projection_path = _build_revision_projection(root, running, proposal_result.proposal)
        ready = _transition_revision_session(
            running,
            RevisionSessionPhase.AWAITING_REVIEW,
            update={
                "patch": patch_ref,
                "candidate": candidate_ref,
                "audit": audit_binding,
                "state_proposal": proposal_binding,
                "projection_path": projection_path.relative_to(root).as_posix(),
            }
        )
        _write_revision_session(root, ready)
        return RevisionSessionResult(
            session=ready,
            session_path=revision_session_path(root, ready.revision_session_id),
            message="Scoped revision passed audit and is ready for review.",
        )
    except Exception as exc:
        failed = _transition_revision_session(running, RevisionSessionPhase.FAILED_RECOVERABLE)
        _write_revision_session(root, failed)
        if isinstance(exc, RevisionWorkflowError):
            raise
        raise RevisionWorkflowError(str(exc)) from exc


def accept_revision_session(options: RevisionActionOptions) -> RevisionSessionResult:
    root = options.root.resolve()
    recover_incomplete_transactions(root)
    session = load_revision_session(root, options.revision_session_id)
    if session.phase != RevisionSessionPhase.AWAITING_REVIEW:
        raise RevisionWorkflowError(f"revision session cannot be accepted from phase {session.phase.value}")
    if not session.candidate or not session.audit or not session.state_proposal or not session.projection_path:
        raise RevisionWorkflowError("revision session is missing reviewed artifacts")
    _require_revision_base_is_current(root, session)
    store = ArtifactStore(root)
    for ref in (
        session.selection.source_candidate,
        session.patch,
        session.candidate,
        session.audit.audit,
        session.state_proposal.proposal,
    ):
        if ref is not None:
            store.verify(ref)
    chapter_dir = _chapter_dir(root, session.chapter_number)
    if sha256_file(chapter_dir / "polished.md") != session.candidate.sha256:
        raise RevisionWorkflowError("working polished.md does not match reviewed revision candidate")
    if sha256_file(chapter_dir / "audit.json") != session.audit.audit.sha256:
        raise RevisionWorkflowError("working audit.json does not match reviewed revision audit")
    if sha256_file(chapter_dir / "state_update_proposal.json") != session.state_proposal.proposal.sha256:
        raise RevisionWorkflowError("working state proposal does not match reviewed revision proposal")
    state_path = root / "memory" / "state" / "current_state.json"
    timeline_path = root / "memory" / "state" / "timeline.json"
    if (
        sha256_file(state_path) != session.state_proposal.base_state_sha256
        or sha256_file(timeline_path) != session.state_proposal.base_timeline_sha256
    ):
        raise RevisionWorkflowError("canonical state/timeline changed after revision review")
    projection_manifest_path = resolve_project_path(root, session.projection_path)
    projection_manifest = load_json_model(projection_manifest_path, SessionProjection)
    projection_dir = projection_manifest_path.parent
    projected_state_path = projection_dir / "current_state.json"
    projected_timeline_path = projection_dir / "timeline.json"
    if (
        sha256_file(projected_state_path) != projection_manifest.current_state_sha256
        or sha256_file(projected_timeline_path) != projection_manifest.current_timeline_sha256
    ):
        raise RevisionWorkflowError("revision projection is stale")
    lifecycle = load_lifecycle(root, session.chapter_number)
    if not lifecycle or not lifecycle.active_plan:
        raise RevisionWorkflowError("chapter lifecycle or active plan is missing")
    plan = load_json_model(resolve_project_path(root, lifecycle.active_plan.path), ChapterPlan)
    audit = load_json_model(resolve_project_path(root, session.audit.audit.path), AuditReport)
    proposal = load_json_model(
        resolve_project_path(root, session.state_proposal.proposal.path),
        StateUpdateProposal,
    )
    candidate_document = read_markdown_with_front_matter(
        resolve_project_path(root, session.candidate.path)
    )
    projected_timeline = load_json_model(projected_timeline_path, TimelineFile)
    source = ChapterMemorySource(
        polished_path=session.candidate.path,
        polished_sha256=session.candidate.sha256,
        plan_path=lifecycle.active_plan.path,
        audit_path=session.audit.audit.path,
        state_update_proposal_path=session.state_proposal.proposal.path,
    )
    memory = build_deterministic_chapter_memory(
        ChapterMemoryContext(
            root=root,
            chapter_number=session.chapter_number,
            project=load_yaml_model(root / "project.yaml", ProjectConfig),
            plan=plan,
            polished=ChapterMemoryDocument(
                metadata=candidate_document.metadata,
                body=candidate_document.body,
            ),
            audit=audit,
            proposal=proposal,
            apply_log=None,
            timeline=projected_timeline,
            source=source,
        ),
        warnings=["chapter memory staged for scoped revision acceptance"],
    )
    memory_bytes = (memory.model_dump_json(indent=2) + "\n").encode("utf-8")
    memory_ref = store.create(
        chapter_number=session.chapter_number,
        kind=ArtifactKind.CHAPTER_MEMORY,
        content=memory_bytes,
        suffix=".json",
    )
    acceptance = AcceptanceCommit(
        commit_id=f"accept_{uuid.uuid4().hex}",
        session_id=session.revision_session_id,
        chapter_number=session.chapter_number,
        candidate=session.candidate,
        audit=session.audit.audit,
        state_proposal=session.state_proposal.proposal,
        chapter_memory=memory_ref,
        pre_state_sha256=session.state_proposal.base_state_sha256,
        pre_timeline_sha256=session.state_proposal.base_timeline_sha256,
        post_state_sha256=projection_manifest.current_state_sha256,
        post_timeline_sha256=projection_manifest.current_timeline_sha256,
        accepted_content_sha256=session.candidate.sha256,
        created_at=utc_now(),
    )
    acceptance_bytes = (acceptance.model_dump_json(indent=2) + "\n").encode("utf-8")
    acceptance_ref = store.create(
        chapter_number=session.chapter_number,
        kind=ArtifactKind.ACCEPTANCE,
        content=acceptance_bytes,
        suffix=".json",
    )
    updated_lifecycle = lifecycle.model_copy(
        update={
            "active_candidate": session.candidate,
            "active_audit": session.audit,
            "active_state_proposal": session.state_proposal,
            "active_acceptance": acceptance_ref,
            "updated_at": utc_now(),
        }
    )
    metadata = ChapterMetadata(
        chapter_number=session.chapter_number,
        status="accepted",
        plan_path=lifecycle.active_plan.path,
        polished_path=(chapter_dir / "accepted.md").relative_to(root).as_posix(),
        audit_path=session.audit.audit.path,
        state_update_proposal_path=session.state_proposal.proposal.path,
        chapter_memory_path=(chapter_dir / "chapter_memory.json").relative_to(root).as_posix(),
        accepted_at=utc_now(),
        updated_at=utc_now(),
    )
    committing = _transition_revision_session(session, RevisionSessionPhase.COMMITTING)
    committed = _transition_revision_session(committing, RevisionSessionPhase.COMMITTED)
    mutations = [
        FileMutation(state_path, projected_state_path.read_bytes()),
        FileMutation(timeline_path, projected_timeline_path.read_bytes()),
        FileMutation(chapter_dir / "accepted.md", store.read_bytes(session.candidate)),
        FileMutation(chapter_dir / "acceptance.json", acceptance_bytes),
        FileMutation(chapter_dir / "chapter_memory.json", memory_bytes),
        FileMutation(
            chapter_dir / "metadata.json",
            (metadata.model_dump_json(indent=2) + "\n").encode("utf-8"),
        ),
        FileMutation(
            chapter_dir / "lifecycle.json",
            (updated_lifecycle.model_dump_json(indent=2) + "\n").encode("utf-8"),
        ),
        FileMutation(
            revision_session_path(root, session.revision_session_id),
            (committed.model_dump_json(indent=2) + "\n").encode("utf-8"),
        ),
    ]
    journal_path, _ = prepare_transaction(
        root,
        purpose=f"accept scoped revision {session.revision_session_id}",
        mutations=mutations,
    )
    try:
        commit_transaction(root, journal_path)
    except TransactionError as exc:
        raise RevisionWorkflowError(str(exc)) from exc
    return RevisionSessionResult(
        session=committed,
        session_path=revision_session_path(root, committed.revision_session_id),
        message="Scoped revision accepted.",
    )


def show_revision_session(root: Path, revision_session_id: str) -> RevisionSessionResult:
    session = load_revision_session(root, revision_session_id)
    return RevisionSessionResult(
        session=session,
        session_path=revision_session_path(root, revision_session_id),
        message="Revision session loaded.",
    )


def load_revision_session(root: Path, revision_session_id: str) -> RevisionSession:
    path = revision_session_path(root, revision_session_id)
    if not path.is_file():
        raise RevisionWorkflowError(f"revision session not found: {revision_session_id}")
    return load_json_model(path, RevisionSession)


def revision_session_path(root: Path, revision_session_id: str) -> Path:
    if not _REVISION_SESSION_ID.fullmatch(revision_session_id):
        raise RevisionWorkflowError("invalid revision session id")
    return root.resolve() / "memory" / "revision_sessions" / revision_session_id / "session.json"


def load_segment_revision_provider(
    root: Path,
    provider_name: str,
    session: RevisionSession,
    selected_markdown: str,
) -> ModelProvider:
    return create_agent_provider(
        default_agent_config_path(root),
        "revision",
        overrides=ProviderOverrides(provider_name=provider_name),
        mock_response=default_mock_segment_patch_json(session, selected_markdown),
    )


def generate_segment_patch(
    root: Path,
    session: RevisionSession,
    selected_markdown: str,
    provider: ModelProvider,
) -> SegmentPatch:
    request = ModelRequest(
        system_prompt=load_prompt_template("segment_revision_system"),
        prompt_version=prompt_template_version("segment_revision_system"),
        user_prompt=(
            f"selection_id: {session.selection.selection_id}\n"
            f"source_sha256: {session.selection.source_candidate.sha256}\n"
            f"start_block: {session.selection.start_block}\n"
            f"end_block: {session.selection.end_block}\n"
            f"用户修订要求：{session.instruction}\n\n"
            f"授权范围原文：\n{selected_markdown}\n"
        ),
        json_schema_name="SegmentPatch",
    )
    contract = AgentOutputContract(
        output_kind="json",
        target_name="SegmentPatch",
        json_schema_name="SegmentPatch",
    )

    def parse(content: str) -> SegmentPatch:
        try:
            payload = json.loads(extract_json_object(content))
            patch = SegmentPatch.model_validate(payload)
        except (JsonExtractionError, json.JSONDecodeError, ValidationError) as exc:
            raise RevisionWorkflowError(f"provider returned invalid SegmentPatch: {exc}") from exc
        if patch.selection_id != session.selection.selection_id:
            raise RevisionWorkflowError("provider SegmentPatch selection_id does not match authorization")
        if patch.source_sha256 != session.selection.source_candidate.sha256:
            raise RevisionWorkflowError("provider SegmentPatch source hash does not match authorization")
        if (
            patch.start_block != session.selection.start_block
            or patch.end_block != session.selection.end_block
        ):
            raise RevisionWorkflowError("provider SegmentPatch range does not match authorization")
        return patch

    try:
        return generate_json_with_repair(
            provider,
            request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="revision",
                interaction_mode="internal_task",
                task="segment_patch",
                chapter_number=session.chapter_number,
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="revision",
                interaction_mode="internal_task",
                task="segment_patch_repair",
                chapter_number=session.chapter_number,
            ),
            contract=contract,
            parse=parse,
            repair_prompt=lambda invalid, error: (
                "上一次 SegmentPatch 不符合严格 schema 或授权范围。"
                f"错误：{error}\n无效输出：\n{invalid}"
            ),
        )
    except JsonRepairExhaustedError as exc:
        raise RevisionWorkflowError(str(exc)) from exc


def default_mock_segment_patch_json(session: RevisionSession, selected_markdown: str) -> str:
    replacement = selected_markdown.rstrip("\r\n") + "\n\n（已按局部修订要求调整。）\n"
    return SegmentPatch(
        patch_id=f"patch_{uuid.uuid4().hex}",
        selection_id=session.selection.selection_id,
        source_sha256=session.selection.source_candidate.sha256,
        start_block=session.selection.start_block,
        end_block=session.selection.end_block,
        replacement_markdown=replacement,
        created_at=utc_now(),
    ).model_dump_json()


def _build_revision_projection(
    root: Path,
    session: RevisionSession,
    proposal: StateUpdateProposal,
) -> Path:
    state_source = root / "memory" / "state" / "current_state.json"
    timeline_source = root / "memory" / "state" / "timeline.json"
    state = load_json_model(state_source, EntityState)
    timeline = load_json_model(timeline_source, TimelineFile)
    _validate_state_change_old_values(state, proposal.state_changes, root)
    updated_state = apply_state_changes_to_state(state, proposal.state_changes, root)
    preserved_events = [
        event
        for event in timeline.events
        if not event.narrative_position or event.narrative_position.chapter != session.chapter_number
    ]
    updated_timeline = TimelineFile(events=[*preserved_events, *proposal.timeline_events])
    _validate_applied_state(updated_state)
    _validate_applied_timeline(root, updated_timeline)
    directory = (
        root
        / "memory"
        / "revision_sessions"
        / session.revision_session_id
        / "projection"
    )
    state_path = directory / "current_state.json"
    timeline_path = directory / "timeline.json"
    atomic_write_model_json(state_path, updated_state)
    atomic_write_model_json(timeline_path, updated_timeline)
    before_state = sha256_file(state_source)
    before_timeline = sha256_file(timeline_source)
    after_state = sha256_file(state_path)
    after_timeline = sha256_file(timeline_path)
    manifest = SessionProjection(
        session_id=session.revision_session_id,
        base_state_sha256=before_state,
        base_timeline_sha256=before_timeline,
        current_state_sha256=after_state,
        current_timeline_sha256=after_timeline,
        state_path=state_path.relative_to(root).as_posix(),
        timeline_path=timeline_path.relative_to(root).as_posix(),
        checkpoints=[
            ProjectionCheckpoint(
                chapter_number=session.chapter_number,
                before_state_sha256=before_state,
                before_timeline_sha256=before_timeline,
                after_state_sha256=after_state,
                after_timeline_sha256=after_timeline,
                created_at=utc_now(),
            )
        ],
        updated_at=utc_now(),
    )
    manifest_path = directory / "projection.json"
    atomic_write_model_json(manifest_path, manifest)
    return manifest_path


def _require_revision_base_is_current(root: Path, session: RevisionSession) -> None:
    try:
        current = accepted_chapter_commit(root, session.chapter_number)
    except LifecycleError as exc:
        raise RevisionWorkflowError(str(exc)) from exc
    if current.commit_id != session.base_acceptance_commit_id:
        raise RevisionWorkflowError("accepted chapter changed after revision selection was created")
    if current.candidate != session.selection.source_candidate:
        raise RevisionWorkflowError("revision selection source is no longer the accepted candidate")


def _selected_text(markdown: str, start_block: int, end_block: int) -> str:
    parsed = parse_markdown_blocks(markdown)
    if start_block < 1 or end_block > len(parsed.blocks):
        raise RevisionWorkflowError("selected block range is no longer valid")
    start = parsed.blocks[start_block - 1].start
    end = parsed.blocks[end_block - 1].end
    return markdown[start:end]


def _canonical_context_hash(root: Path) -> str:
    return combined_sha256(
        sha256_file(root / "memory" / "state" / "current_state.json"),
        sha256_file(root / "memory" / "state" / "timeline.json"),
    )


def _write_revision_session(root: Path, session: RevisionSession) -> None:
    atomic_write_model_json(revision_session_path(root, session.revision_session_id), session)


def _transition_revision_session(
    session: RevisionSession,
    phase: RevisionSessionPhase,
    *,
    update: dict[str, object] | None = None,
) -> RevisionSession:
    ensure_revision_phase_transition(session.phase, phase)
    return session.model_copy(
        update={**(update or {}), "phase": phase, "updated_at": utc_now()}
    )


def _chapter_dir(root: Path, chapter_number: int) -> Path:
    return root / "memory" / "chapters" / f"{chapter_number:03d}"


def _new_revision_session_id() -> str:
    return f"revision_session_{utc_timestamp()}"
