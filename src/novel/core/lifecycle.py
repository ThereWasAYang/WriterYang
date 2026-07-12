from __future__ import annotations

from pathlib import Path
import uuid

from novel.core.artifact_store import (
    ArtifactStore,
    freshness_errors,
    load_lifecycle,
    resolve_project_path,
    sha256_bytes,
    sha256_file,
)
from novel.core.chapter_memory import (
    ChapterMemoryContext,
    ChapterMemoryDocument,
    build_deterministic_chapter_memory,
)
from novel.core.contracts import (
    AcceptanceCommit,
    ArtifactKind,
    ChapterNodeStatus,
    SessionPhase,
    TaskId,
    validate_session_transition,
)
from novel.core.io import load_json_model, load_yaml_model
from novel.core.polishing import read_markdown_with_front_matter
from novel.core.projection import load_projection, projection_paths, verify_canonical_base
from novel.core.schemas import (
    AuditReport,
    ChapterMemorySource,
    ChapterMetadata,
    ChapterPlan,
    CreationSession,
    ProjectConfig,
    StateUpdateProposal,
    TimelineFile,
)
from novel.core.timeutil import utc_now
from novel.core.transactions import (
    FileMutation,
    commit_transaction,
    prepare_transaction,
    recover_incomplete_transactions,
    new_transaction_id,
)


class LifecycleError(RuntimeError):
    """Raised when artifact lineage does not permit acceptance or export."""


def require_fresh_chapter(
    root: Path,
    chapter_number: int,
    *,
    require_working_matches: bool = True,
) -> None:
    lifecycle = load_lifecycle(root, chapter_number)
    if lifecycle is None:
        raise LifecycleError(f"chapter {chapter_number} has no lifecycle.json")
    errors = freshness_errors(root, lifecycle)
    if errors:
        raise LifecycleError(f"chapter {chapter_number} has stale lineage: " + "; ".join(errors))
    if (
        not lifecycle.active_plan
        or not lifecycle.active_candidate
        or not lifecycle.active_audit
        or not lifecycle.active_state_proposal
    ):
        raise LifecycleError(f"chapter {chapter_number} lifecycle is incomplete")
    report = load_json_model(resolve_project_path(root, lifecycle.active_audit.audit.path), AuditReport)
    if report.overall_status != "passed":
        raise LifecycleError(f"chapter {chapter_number} audit did not pass: {report.overall_status}")
    if require_working_matches:
        chapter_dir = root.resolve() / "memory" / "chapters" / f"{chapter_number:03d}"
        working_refs = (
            (chapter_dir / "plan.json", lifecycle.active_plan),
            (chapter_dir / "polished.md", lifecycle.active_candidate),
            (chapter_dir / "audit.json", lifecycle.active_audit.audit),
            (chapter_dir / "state_update_proposal.json", lifecycle.active_state_proposal.proposal),
        )
        for working_path, ref in working_refs:
            if not working_path.is_file() or sha256_file(working_path) != ref.sha256:
                raise LifecycleError(
                    f"chapter {chapter_number} working file no longer matches reviewed artifact: "
                    f"{working_path.relative_to(root)}"
                )


def commit_creation_session(
    root: Path,
    session: CreationSession,
    session_path: Path,
) -> CreationSession:
    root = root.resolve()
    recover_incomplete_transactions(root)
    projection = load_projection(root, session.session_id)
    verify_canonical_base(root, projection)
    state_path, timeline_path = projection_paths(root, projection)
    projected_timeline = load_json_model(timeline_path, TimelineFile)
    store = ArtifactStore(root)
    checkpoint_by_chapter = {item.chapter_number: item for item in projection.checkpoints}
    mutations: list[FileMutation] = [
        FileMutation(root / "memory" / "state" / "current_state.json", state_path.read_bytes()),
        FileMutation(root / "memory" / "state" / "timeline.json", timeline_path.read_bytes()),
    ]
    transaction_id = new_transaction_id()

    for chapter_number in session.chapter_range:
        require_fresh_chapter(root, chapter_number)
        lifecycle = load_lifecycle(root, chapter_number)
        assert (
            lifecycle
            and lifecycle.active_plan
            and lifecycle.active_candidate
            and lifecycle.active_audit
            and lifecycle.active_state_proposal
        )
        checkpoint = checkpoint_by_chapter.get(chapter_number)
        if checkpoint is None:
            raise LifecycleError(f"chapter {chapter_number} has no projection checkpoint")
        binding = lifecycle.active_state_proposal
        if (
            binding.base_state_sha256 != checkpoint.before_state_sha256
            or binding.base_timeline_sha256 != checkpoint.before_timeline_sha256
        ):
            raise LifecycleError(f"chapter {chapter_number} proposal base does not match projection checkpoint")

        candidate_bytes = store.read_bytes(lifecycle.active_candidate)
        plan = load_json_model(resolve_project_path(root, lifecycle.active_plan.path), ChapterPlan)
        audit = load_json_model(resolve_project_path(root, lifecycle.active_audit.audit.path), AuditReport)
        proposal = load_json_model(resolve_project_path(root, binding.proposal.path), StateUpdateProposal)
        candidate_document = read_markdown_with_front_matter(
            resolve_project_path(root, lifecycle.active_candidate.path)
        )
        source = ChapterMemorySource(
            polished_path=lifecycle.active_candidate.path,
            polished_sha256=lifecycle.active_candidate.sha256,
            plan_path=lifecycle.active_plan.path,
            audit_path=lifecycle.active_audit.audit.path,
            state_update_proposal_path=binding.proposal.path,
        )
        memory = build_deterministic_chapter_memory(
            ChapterMemoryContext(
                root=root,
                chapter_number=chapter_number,
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
            warnings=["chapter memory staged before transactional acceptance"],
        )
        memory_bytes = (memory.model_dump_json(indent=2) + "\n").encode("utf-8")
        memory_ref = store.create(
            chapter_number=chapter_number,
            kind=ArtifactKind.CHAPTER_MEMORY,
            content=memory_bytes,
            suffix=".json",
            producer_task_id=TaskId.CHAPTER_MEMORY,
            inputs=[lifecycle.active_candidate, lifecycle.active_audit.audit, binding.proposal],
        )
        acceptance = AcceptanceCommit(
            commit_id=f"accept_{uuid.uuid4().hex}",
            session_id=session.session_id,
            transaction_id=transaction_id,
            chapter_number=chapter_number,
            candidate=lifecycle.active_candidate,
            audit=lifecycle.active_audit.audit,
            state_proposal=binding.proposal,
            chapter_memory=memory_ref,
            pre_state_sha256=checkpoint.before_state_sha256,
            pre_timeline_sha256=checkpoint.before_timeline_sha256,
            post_state_sha256=checkpoint.after_state_sha256,
            post_timeline_sha256=checkpoint.after_timeline_sha256,
            accepted_content_sha256=lifecycle.active_candidate.sha256,
            created_at=utc_now(),
        )
        acceptance_bytes = (acceptance.model_dump_json(indent=2) + "\n").encode("utf-8")
        acceptance_ref = store.create(
            chapter_number=chapter_number,
            kind=ArtifactKind.ACCEPTANCE,
            content=acceptance_bytes,
            suffix=".json",
            authority="deterministic",
            inputs=[lifecycle.active_candidate, lifecycle.active_audit.audit, binding.proposal, memory_ref],
            policy_version="transaction-acceptance-v3",
        )
        new_lineages = [store.load_lineage(memory_ref), store.load_lineage(acceptance_ref)]
        updated_lifecycle = lifecycle.model_copy(
            update={
                "active_acceptance": acceptance_ref,
                "lineages": [*lifecycle.lineages, *new_lineages],
                "updated_at": utc_now(),
            }
        )
        chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
        metadata = ChapterMetadata(
            chapter_number=chapter_number,
            status="accepted",
            plan_path=lifecycle.active_plan.path,
            polished_path=(chapter_dir / "accepted.md").relative_to(root).as_posix(),
            audit_path=lifecycle.active_audit.audit.path,
            state_update_proposal_path=binding.proposal.path,
            chapter_memory_path=(chapter_dir / "chapter_memory.json").relative_to(root).as_posix(),
            accepted_at=utc_now(),
            updated_at=utc_now(),
        )
        mutations.extend(
            [
                FileMutation(chapter_dir / "accepted.md", candidate_bytes),
                FileMutation(chapter_dir / "acceptance.json", acceptance_bytes),
                FileMutation(chapter_dir / "chapter_memory.json", memory_bytes),
                FileMutation(chapter_dir / "metadata.json", (metadata.model_dump_json(indent=2) + "\n").encode("utf-8")),
                FileMutation(chapter_dir / "lifecycle.json", (updated_lifecycle.model_dump_json(indent=2) + "\n").encode("utf-8")),
            ]
        )

    validate_session_transition(session.phase, SessionPhase.COMMITTED)
    session_payload = session.model_dump(mode="python")
    session_payload.update(
        {
            "phase": SessionPhase.COMMITTED,
            "chapter_runs": {
                chapter_number: run.model_copy(update={"chapter_memory": ChapterNodeStatus.COMPLETED})
                for chapter_number, run in session.chapter_runs.items()
            },
            "last_completed_node": "acceptance",
            "failure_node": None,
            "failure_message": None,
            "updated_at": utc_now(),
        }
    )
    updated_session = CreationSession.model_validate(session_payload)
    mutations.append(
        FileMutation(session_path, (updated_session.model_dump_json(indent=2) + "\n").encode("utf-8"))
    )
    journal_path, _ = prepare_transaction(
        root,
        purpose=f"accept creation session {session.session_id}",
        mutations=mutations,
        transaction_id=transaction_id,
    )
    commit_transaction(root, journal_path)
    return updated_session


def accepted_chapter_commit(root: Path, chapter_number: int) -> AcceptanceCommit:
    require_fresh_chapter(root, chapter_number, require_working_matches=False)
    chapter_dir = root.resolve() / "memory" / "chapters" / f"{chapter_number:03d}"
    path = chapter_dir / "acceptance.json"
    if not path.is_file():
        raise LifecycleError(f"chapter {chapter_number} has no acceptance.json")
    commit = load_json_model(path, AcceptanceCommit)
    lifecycle = load_lifecycle(root, chapter_number)
    if not lifecycle or not lifecycle.active_acceptance:
        raise LifecycleError(f"chapter {chapter_number} lifecycle has no active acceptance")
    if not lifecycle.active_candidate or not lifecycle.active_audit or not lifecycle.active_state_proposal:
        raise LifecycleError(f"chapter {chapter_number} lifecycle is incomplete")
    if commit.candidate != lifecycle.active_candidate:
        raise LifecycleError(f"chapter {chapter_number} acceptance candidate is not active")
    if commit.audit != lifecycle.active_audit.audit:
        raise LifecycleError(f"chapter {chapter_number} acceptance audit is not active")
    if commit.state_proposal != lifecycle.active_state_proposal.proposal:
        raise LifecycleError(f"chapter {chapter_number} acceptance state proposal is not active")
    store = ArtifactStore(root)
    for ref in (
        lifecycle.active_acceptance,
        commit.candidate,
        commit.audit,
        commit.state_proposal,
        commit.chapter_memory,
    ):
        store.verify(ref)
    if sha256_bytes(path.read_bytes()) != lifecycle.active_acceptance.sha256:
        raise LifecycleError(f"chapter {chapter_number} acceptance.json is stale")
    accepted = chapter_dir / "accepted.md"
    if not accepted.is_file() or sha256_bytes(accepted.read_bytes()) != commit.accepted_content_sha256:
        raise LifecycleError(f"chapter {chapter_number} accepted.md is stale")
    return commit
