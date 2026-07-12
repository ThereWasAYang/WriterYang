from __future__ import annotations

from pathlib import Path

import pytest

from novel.core.artifact_store import (
    ArtifactStore,
    ArtifactStoreError,
    AuditCandidateMismatchError,
    freshness_errors,
    require_audit_matches_candidate,
    resolve_project_path,
)
from novel.core.contracts import ArtifactKind, ChapterLifecycle, TaskId
from novel.core.io import atomic_write_model_json
from novel.core.schemas import AuditReport
from novel.core.timeutil import utc_now


def test_artifact_store_creates_immutable_hashed_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ref = store.create(
        chapter_number=1,
        kind=ArtifactKind.CANDIDATE,
        content=b"chapter body\n",
        suffix=".md",
        producer_task_id=TaskId.WRITE,
    )

    store.verify(ref)
    assert ref.path.startswith("memory/chapters/001/candidates/candidate_art_")
    assert (tmp_path / ref.path).read_bytes() == b"chapter body\n"

    (tmp_path / ref.path).write_bytes(b"edited\n")
    with pytest.raises(ArtifactStoreError, match="stale artifact"):
        store.verify(ref)


def test_artifact_store_rejects_workspace_escape(tmp_path: Path) -> None:
    with pytest.raises(ArtifactStoreError, match="escapes project root"):
        resolve_project_path(tmp_path, "../outside.json")


def test_artifact_store_enforces_task_write_permissions(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    with pytest.raises(ArtifactStoreError, match="cannot write artifact kind audit"):
        store.create(
            chapter_number=1,
            kind=ArtifactKind.AUDIT,
            content=b"{}\n",
            suffix=".json",
            producer_task_id=TaskId.WRITE,
        )


def test_artifact_store_requires_explicit_non_agent_authority(tmp_path: Path) -> None:
    with pytest.raises(ArtifactStoreError, match="requires a registered producer task"):
        ArtifactStore(tmp_path).create(
            chapter_number=1,
            kind=ArtifactKind.CANDIDATE,
            content=b"text\n",
            suffix=".md",
        )


def test_freshness_errors_reports_modified_active_candidate(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    candidate = store.create(
        chapter_number=1,
        kind=ArtifactKind.CANDIDATE,
        content=b"candidate\n",
        suffix=".md",
        producer_task_id=TaskId.WRITE,
    )
    lifecycle = ChapterLifecycle(
        chapter_number=1,
        active_candidate=candidate,
        lineages=[store.load_lineage(candidate)],
        updated_at=utc_now(),
    )
    (tmp_path / candidate.path).write_bytes(b"manual edit\n")

    errors = freshness_errors(tmp_path, lifecycle)

    assert len(errors) == 1
    assert "stale artifact" in errors[0]


def test_audit_binding_can_validate_frozen_candidate_without_rereading_working_file(
    tmp_path: Path,
) -> None:
    chapter_dir = tmp_path / "memory" / "chapters" / "001"
    atomic_write_model_json(
        chapter_dir / "audit.json",
        AuditReport(
            chapter_number=1,
            audited_file="polished.md",
            audited_sha256="a" * 64,
            overall_status="passed",
            summary="审核通过。",
            issues=[],
            created_at=utc_now(),
        ),
    )

    require_audit_matches_candidate(tmp_path, 1, "a" * 64)

    with pytest.raises(AuditCandidateMismatchError, match="start a revision"):
        require_audit_matches_candidate(tmp_path, 1, "b" * 64)
