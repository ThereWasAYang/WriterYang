from __future__ import annotations

from pathlib import Path

import pytest

from novel.core.artifact_store import ArtifactStore, ArtifactStoreError, freshness_errors, resolve_project_path
from novel.core.contracts import ArtifactKind, ChapterLifecycle
from novel.core.timeutil import utc_now


def test_artifact_store_creates_immutable_hashed_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ref = store.create(
        chapter_number=1,
        kind=ArtifactKind.CANDIDATE,
        content=b"chapter body\n",
        suffix=".md",
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


def test_freshness_errors_reports_modified_active_candidate(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    candidate = store.create(
        chapter_number=1,
        kind=ArtifactKind.CANDIDATE,
        content=b"candidate\n",
        suffix=".md",
    )
    lifecycle = ChapterLifecycle(chapter_number=1, active_candidate=candidate, updated_at=utc_now())
    (tmp_path / candidate.path).write_bytes(b"manual edit\n")

    errors = freshness_errors(tmp_path, lifecycle)

    assert len(errors) == 1
    assert "stale artifact" in errors[0]
