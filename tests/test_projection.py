from __future__ import annotations

from pathlib import Path

import pytest

from novel.core.artifact_store import sha256_file
from novel.core.projection import (
    ProjectionError,
    advance_projection,
    initialize_projection,
    projection_paths,
    verify_canonical_base,
)
from novel.core.schemas import StateUpdateProposal
from novel.core.workspace import InitOptions, init_workspace


def test_projection_advances_without_changing_canonical_state(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="投影测试", root=root))
    canonical = root / "memory" / "state" / "current_state.json"
    before = canonical.read_bytes()
    projection = initialize_projection(root, "session_20260711_010101_000001")
    proposal = StateUpdateProposal.model_validate(
        {
            "chapter_number": 1,
            "state_changes": [
                {
                    "id": "change_001_003",
                    "chapter": 1,
                    "entity_id": "story_position",
                    "field": "latest_chapter",
                    "old_value": 0,
                    "new_value": 1,
                    "reason": "章节推进。",
                    "source": "candidate",
                }
            ],
            "timeline_events": [],
            "created_at": "2026-07-11T00:00:00Z",
        }
    )

    advanced = advance_projection(root, projection.session_id, proposal)

    state_path, _ = projection_paths(root, advanced)
    assert canonical.read_bytes() == before
    assert sha256_file(state_path) == advanced.current_state_sha256
    assert advanced.checkpoints[0].before_state_sha256 == projection.current_state_sha256


def test_projection_rejects_old_value_against_previous_checkpoint(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="投影测试", root=root))
    session_id = "session_20260711_010101_000002"
    initialize_projection(root, session_id)
    first = StateUpdateProposal.model_validate(
        {
            "chapter_number": 1,
            "state_changes": [{"id": "change_001_003", "chapter": 1, "entity_id": "story_position", "field": "latest_chapter", "old_value": 0, "new_value": 1, "reason": "推进", "source": "candidate"}],
            "timeline_events": [],
            "created_at": "2026-07-11T00:00:00Z",
        }
    )
    advance_projection(root, session_id, first)
    invalid = first.model_copy(
        update={
            "chapter_number": 2,
            "state_changes": [first.state_changes[0].model_copy(update={"id": "change_002_003", "chapter": 2, "old_value": 0, "new_value": 2})],
        }
    )

    with pytest.raises(ProjectionError, match="old_value mismatch"):
        advance_projection(root, session_id, invalid)


def test_projection_detects_canonical_concurrent_change(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="投影测试", root=root))
    projection = initialize_projection(root, "session_20260711_010101_000003")
    canonical = root / "memory" / "state" / "current_state.json"
    canonical.write_text(canonical.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ProjectionError, match="changed after the session started"):
        verify_canonical_base(root, projection)
