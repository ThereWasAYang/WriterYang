from __future__ import annotations

import hashlib
from pathlib import Path
import uuid

from novel.core.contracts import (
    ArtifactKind,
    ArtifactRef,
    AuditBinding,
    ChapterLifecycle,
    StateProposalBinding,
)
from novel.core.io import atomic_write_bytes, atomic_write_model_json, load_json_model
from novel.core.timeutil import utc_now


class ArtifactStoreError(RuntimeError):
    """Raised when an immutable artifact cannot be stored or verified."""


_KIND_DIRECTORIES: dict[ArtifactKind, str] = {
    ArtifactKind.PLAN: "plans",
    ArtifactKind.CANDIDATE: "candidates",
    ArtifactKind.AUDIT: "audits",
    ArtifactKind.STATE_PROPOSAL: "state_proposals",
    ArtifactKind.CHAPTER_MEMORY: "chapter_memories",
    ArtifactKind.ACCEPTANCE: "acceptances",
}


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
    ) -> ArtifactRef:
        if chapter_number < 1:
            raise ArtifactStoreError("chapter_number must be positive")
        directory_name = _KIND_DIRECTORIES.get(kind)
        if directory_name is None:
            raise ArtifactStoreError(f"artifact kind is not chapter-scoped: {kind.value}")
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
        return ArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            path=path.relative_to(self.root).as_posix(),
            sha256=sha256_bytes(content),
            created_at=utc_now(),
        )

    def create_from_file(
        self,
        *,
        chapter_number: int,
        kind: ArtifactKind,
        source: Path,
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
        )

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
    plan = _capture_or_reuse(store, existing.active_plan if existing else None, chapter_number, ArtifactKind.PLAN, required[ArtifactKind.PLAN])
    candidate = _capture_or_reuse(store, existing.active_candidate if existing else None, chapter_number, ArtifactKind.CANDIDATE, required[ArtifactKind.CANDIDATE])
    audit_ref = _capture_or_reuse(
        store,
        existing.active_audit.audit if existing and existing.active_audit else None,
        chapter_number,
        ArtifactKind.AUDIT,
        required[ArtifactKind.AUDIT],
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
        updated_at=utc_now(),
    )
    write_lifecycle(root, lifecycle)
    return lifecycle


def _capture_or_reuse(
    store: ArtifactStore,
    existing: ArtifactRef | None,
    chapter_number: int,
    kind: ArtifactKind,
    source: Path,
) -> ArtifactRef:
    source_hash = sha256_file(source)
    if existing is not None and existing.kind == kind and existing.sha256 == source_hash:
        try:
            store.verify(existing)
            return existing
        except ArtifactStoreError:
            pass
    return store.create_from_file(chapter_number=chapter_number, kind=kind, source=source)


def freshness_errors(root: Path, lifecycle: ChapterLifecycle) -> list[str]:
    store = ArtifactStore(root)
    errors: list[str] = []
    refs = [lifecycle.active_plan, lifecycle.active_candidate]
    if lifecycle.active_audit:
        refs.append(lifecycle.active_audit.audit)
        if lifecycle.active_audit.candidate != lifecycle.active_candidate:
            errors.append("audit -> candidate binding does not match active candidate")
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
        except ArtifactStoreError as exc:
            errors.append(str(exc))
    return errors
