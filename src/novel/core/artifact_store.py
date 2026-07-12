from __future__ import annotations

import hashlib
from pathlib import Path
import uuid

from novel.core.contracts import (
    ArtifactLineage,
    ArtifactKind,
    ArtifactRef,
    AuditBinding,
    ChapterLifecycle,
    StateProposalBinding,
    TaskId,
)
from novel.core.io import atomic_write_bytes, atomic_write_model_json, load_json_model
from novel.core.schemas import AuditReport
from novel.core.timeutil import utc_now
from novel.core.task_registry import prompt_registry_entry, require_task_write_permission
from novel.core.workflow_runtime import active_trace_metadata, register_runtime_artifact


class ArtifactStoreError(RuntimeError):
    """Raised when an immutable artifact cannot be stored or verified."""


class AuditCandidateMismatchError(ArtifactStoreError):
    """Raised when a persisted audit does not bind the working candidate bytes."""


_KIND_DIRECTORIES: dict[ArtifactKind, str] = {
    ArtifactKind.PLAN: "plans",
    ArtifactKind.CANDIDATE: "candidates",
    ArtifactKind.AUDIT: "audits",
    ArtifactKind.STATE_PROPOSAL: "state_proposals",
    ArtifactKind.CHAPTER_MEMORY: "chapter_memories",
    ArtifactKind.ACCEPTANCE: "acceptances",
    ArtifactKind.SEGMENT_PATCH: "patches",
    ArtifactKind.STATE: "snapshots",
    ArtifactKind.TIMELINE: "snapshots",
}

_NON_AGENT_AUTHORITIES = {"deterministic", "user"}


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise ArtifactStoreError(f"cannot hash artifact {path}: {exc}") from exc


def combined_sha256(*hashes: str) -> str:
    return sha256_bytes("\n".join(hashes).encode("ascii"))


def resolve_project_path(root: Path, relative_path: str) -> Path:
    root = root.expanduser().resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ArtifactStoreError(f"artifact path escapes project root: {relative_path}") from exc
    return candidate


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def create(
        self,
        *,
        chapter_number: int,
        kind: ArtifactKind,
        content: bytes,
        suffix: str,
        producer_task_id: TaskId | None = None,
        authority: str | None = None,
        inputs: list[ArtifactRef] | None = None,
        policy_version: str | None = None,
    ) -> ArtifactRef:
        if chapter_number < 1:
            raise ArtifactStoreError("chapter_number must be positive")
        directory_name = _KIND_DIRECTORIES.get(kind)
        if directory_name is None:
            raise ArtifactStoreError(f"artifact kind is not chapter-scoped: {kind.value}")
        if producer_task_id is not None:
            try:
                require_task_write_permission(producer_task_id, kind)
            except PermissionError as exc:
                raise ArtifactStoreError(str(exc)) from exc
        elif authority not in _NON_AGENT_AUTHORITIES:
            raise ArtifactStoreError(
                f"artifact {kind.value} requires a registered producer task or explicit deterministic/user authority"
            )
        artifact_id = f"art_{uuid.uuid4().hex}"
        safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        path = (
            self.root
            / "memory"
            / "chapters"
            / f"{chapter_number:03d}"
            / directory_name
            / f"{kind.value}_{artifact_id}{safe_suffix}"
        )
        if path.exists():
            raise ArtifactStoreError(f"refusing to overwrite immutable artifact: {path}")
        atomic_write_bytes(path, content)
        ref = ArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            path=path.relative_to(self.root).as_posix(),
            sha256=sha256_bytes(content),
            created_at=utc_now(),
        )
        trace = active_trace_metadata()
        prompt_entry = prompt_registry_entry(producer_task_id) if producer_task_id is not None else None
        lineage = ArtifactLineage(
            output=ref,
            inputs=inputs or [],
            task_id=producer_task_id,
            workflow_run_id=trace.workflow_run_id or f"run_{'0' * 32}",
            prompt_hash=prompt_entry.template_hash if prompt_entry else None,
            policy_version=policy_version or (prompt_entry.policy_hash if prompt_entry else authority or "deterministic"),
        )
        self.write_lineage(lineage)
        register_runtime_artifact(ref)
        return ref

    def create_from_file(
        self,
        *,
        chapter_number: int,
        kind: ArtifactKind,
        source: Path,
        producer_task_id: TaskId | None = None,
        authority: str | None = None,
        inputs: list[ArtifactRef] | None = None,
        policy_version: str | None = None,
    ) -> ArtifactRef:
        try:
            content = source.read_bytes()
        except OSError as exc:
            raise ArtifactStoreError(f"cannot read artifact source {source}: {exc}") from exc
        return self.create(
            chapter_number=chapter_number,
            kind=kind,
            content=content,
            suffix=source.suffix or ".bin",
            producer_task_id=producer_task_id,
            authority=authority,
            inputs=inputs,
            policy_version=policy_version,
        )

    def write_lineage(self, lineage: ArtifactLineage) -> Path:
        path = resolve_project_path(self.root, lineage.output.path + ".lineage.json")
        atomic_write_model_json(path, lineage)
        return path

    def load_lineage(self, ref: ArtifactRef) -> ArtifactLineage:
        path = resolve_project_path(self.root, ref.path + ".lineage.json")
        if not path.is_file():
            raise ArtifactStoreError(f"artifact lineage is missing: {ref.path}.lineage.json")
        lineage = load_json_model(path, ArtifactLineage)
        if lineage.output != ref:
            raise ArtifactStoreError(f"artifact lineage output mismatch: {ref.artifact_id}")
        return lineage

    def verify(self, ref: ArtifactRef) -> None:
        path = resolve_project_path(self.root, ref.path)
        if not path.is_file():
            raise ArtifactStoreError(f"artifact is missing: {ref.path}")
        actual = sha256_file(path)
        if actual != ref.sha256:
            raise ArtifactStoreError(
                f"stale artifact {ref.artifact_id}: expected {ref.sha256}, got {actual}"
            )

    def read_bytes(self, ref: ArtifactRef) -> bytes:
        self.verify(ref)
        return resolve_project_path(self.root, ref.path).read_bytes()


def lifecycle_path(root: Path, chapter_number: int) -> Path:
    return root.resolve() / "memory" / "chapters" / f"{chapter_number:03d}" / "lifecycle.json"


def load_lifecycle(root: Path, chapter_number: int) -> ChapterLifecycle | None:
    path = lifecycle_path(root, chapter_number)
    return load_json_model(path, ChapterLifecycle) if path.exists() else None


def write_lifecycle(root: Path, lifecycle: ChapterLifecycle) -> Path:
    path = lifecycle_path(root, lifecycle.chapter_number)
    atomic_write_model_json(path, lifecycle)
    return path


def capture_working_chapter(
    root: Path,
    chapter_number: int,
    *,
    base_state_sha256: str,
    base_timeline_sha256: str,
    base_state_path: Path,
    base_timeline_path: Path,
) -> ChapterLifecycle:
    """Freeze current working files and bind their lineage in lifecycle.json."""

    root = root.resolve()
    chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
    required = {
        ArtifactKind.PLAN: chapter_dir / "plan.json",
        ArtifactKind.CANDIDATE: chapter_dir / "polished.md",
        ArtifactKind.AUDIT: chapter_dir / "audit.json",
        ArtifactKind.STATE_PROPOSAL: chapter_dir / "state_update_proposal.json",
    }
    missing = [str(path.relative_to(root)) for path in required.values() if not path.is_file()]
    if missing:
        raise ArtifactStoreError("cannot capture incomplete chapter artifacts: " + ", ".join(missing))

    store = ArtifactStore(root)
    existing = load_lifecycle(root, chapter_number)
    state_snapshot = store.create_from_file(
        chapter_number=chapter_number,
        kind=ArtifactKind.STATE,
        source=base_state_path,
        authority="deterministic",
        policy_version="projection-snapshot-v3",
    )
    timeline_snapshot = store.create_from_file(
        chapter_number=chapter_number,
        kind=ArtifactKind.TIMELINE,
        source=base_timeline_path,
        authority="deterministic",
        policy_version="projection-snapshot-v3",
    )
    snapshot_inputs = [state_snapshot, timeline_snapshot]
    plan = _capture_or_reuse(
        store,
        existing.active_plan if existing else None,
        chapter_number,
        ArtifactKind.PLAN,
        required[ArtifactKind.PLAN],
        producer_task_id=TaskId.PLAN,
        inputs=snapshot_inputs,
    )
    candidate = _capture_or_reuse(
        store,
        existing.active_candidate if existing else None,
        chapter_number,
        ArtifactKind.CANDIDATE,
        required[ArtifactKind.CANDIDATE],
        producer_task_id=TaskId.POLISH,
        inputs=[plan, *snapshot_inputs],
    )
    require_working_audit_matches_candidate(root, chapter_number)
    audit_ref = _capture_or_reuse(
        store,
        existing.active_audit.audit if existing and existing.active_audit else None,
        chapter_number,
        ArtifactKind.AUDIT,
        required[ArtifactKind.AUDIT],
        producer_task_id=TaskId.AUDIT,
        inputs=[candidate, plan, *snapshot_inputs],
    )
    context_hash = combined_sha256(base_state_sha256, base_timeline_sha256, plan.sha256)
    audit = AuditBinding(
        audit=audit_ref,
        candidate=candidate,
        context_snapshot_hash=context_hash,
        policy_version="audit-policy-v3",
    )
    proposal_ref = _capture_or_reuse(
        store,
        existing.active_state_proposal.proposal if existing and existing.active_state_proposal else None,
        chapter_number,
        ArtifactKind.STATE_PROPOSAL,
        required[ArtifactKind.STATE_PROPOSAL],
        producer_task_id=TaskId.STATE_UPDATE,
        inputs=[candidate, audit_ref, *snapshot_inputs],
    )
    proposal = StateProposalBinding(
        proposal=proposal_ref,
        candidate=candidate,
        audit=audit_ref,
        base_state_sha256=base_state_sha256,
        base_timeline_sha256=base_timeline_sha256,
    )
    lifecycle = ChapterLifecycle(
        chapter_number=chapter_number,
        active_plan=plan,
        active_candidate=candidate,
        active_audit=audit,
        active_state_proposal=proposal,
        active_acceptance=existing.active_acceptance if existing else None,
        lineages=[
            store.load_lineage(ref)
            for ref in (state_snapshot, timeline_snapshot, plan, candidate, audit_ref, proposal_ref)
        ],
        updated_at=utc_now(),
    )
    write_lifecycle(root, lifecycle)
    return lifecycle


def require_working_audit_matches_candidate(root: Path, chapter_number: int) -> None:
    chapter_dir = root.resolve() / "memory" / "chapters" / f"{chapter_number:03d}"
    audit_report = load_json_model(chapter_dir / "audit.json", AuditReport)
    if audit_report.audited_file != "polished.md":
        raise AuditCandidateMismatchError(
            f"audit reviewed {audit_report.audited_file}, but chapter capture requires polished.md"
        )
    candidate_sha256 = sha256_file(chapter_dir / "polished.md")
    if audit_report.audited_sha256 != candidate_sha256:
        raise AuditCandidateMismatchError(
            "audit content hash does not match the working chapter candidate; rerun the audit"
        )


def _capture_or_reuse(
    store: ArtifactStore,
    existing: ArtifactRef | None,
    chapter_number: int,
    kind: ArtifactKind,
    source: Path,
    *,
    producer_task_id: TaskId,
    inputs: list[ArtifactRef],
) -> ArtifactRef:
    source_hash = sha256_file(source)
    if existing is not None and existing.kind == kind and existing.sha256 == source_hash:
        try:
            store.verify(existing)
            store.load_lineage(existing)
            return existing
        except ArtifactStoreError:
            pass
    return store.create_from_file(
        chapter_number=chapter_number,
        kind=kind,
        source=source,
        producer_task_id=producer_task_id,
        inputs=inputs,
    )


def freshness_errors(root: Path, lifecycle: ChapterLifecycle) -> list[str]:
    store = ArtifactStore(root)
    errors: list[str] = []
    refs = [lifecycle.active_plan, lifecycle.active_candidate]
    if lifecycle.active_audit:
        refs.append(lifecycle.active_audit.audit)
        if lifecycle.active_audit.candidate != lifecycle.active_candidate:
            errors.append("audit -> candidate binding does not match active candidate")
        else:
            try:
                audit_report = load_json_model(
                    resolve_project_path(root, lifecycle.active_audit.audit.path),
                    AuditReport,
                )
                if audit_report.audited_sha256 != lifecycle.active_audit.candidate.sha256:
                    errors.append("audit report content hash does not match active candidate")
            except Exception as exc:
                errors.append(f"cannot verify audit report content binding: {exc}")
    if lifecycle.active_state_proposal:
        refs.append(lifecycle.active_state_proposal.proposal)
        if lifecycle.active_state_proposal.candidate != lifecycle.active_candidate:
            errors.append("state proposal -> candidate binding does not match active candidate")
        if not lifecycle.active_audit or lifecycle.active_state_proposal.audit != lifecycle.active_audit.audit:
            errors.append("state proposal -> audit binding does not match active audit")
    if lifecycle.active_acceptance:
        refs.append(lifecycle.active_acceptance)
    for ref in refs:
        if ref is None:
            continue
        try:
            store.verify(ref)
            lineage = store.load_lineage(ref)
            if lifecycle.lineage_for(ref) != lineage:
                errors.append(f"lifecycle lineage mismatch for {ref.artifact_id}")
        except ArtifactStoreError as exc:
            errors.append(str(exc))
    return errors
