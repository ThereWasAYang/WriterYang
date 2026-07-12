from __future__ import annotations

from pathlib import Path

from novel.core.artifact_store import sha256_file
from novel.core.contracts import ProjectionCheckpoint, SessionProjection
from novel.core.io import atomic_write_model_json, load_json_model
from novel.core.schemas import EntityState, StateUpdateProposal, TimelineFile
from novel.core.state_update import (
    StateUpdateError,
    apply_state_changes_to_state,
    validate_applied_state,
    validate_applied_timeline,
    validate_state_change_old_values,
)
from novel.core.timeutil import utc_now


class ProjectionError(RuntimeError):
    """Raised when a session-local world-state projection cannot advance."""


def projection_dir(root: Path, session_id: str) -> Path:
    return root.resolve() / "memory" / "sessions" / session_id / "projection"


def projection_manifest_path(root: Path, session_id: str) -> Path:
    return projection_dir(root, session_id) / "projection.json"


def initialize_projection(root: Path, session_id: str, *, force: bool = False) -> SessionProjection:
    root = root.resolve()
    directory = projection_dir(root, session_id)
    manifest_path = projection_manifest_path(root, session_id)
    if manifest_path.exists() and not force:
        return load_projection(root, session_id)
    canonical_state = root / "memory" / "state" / "current_state.json"
    canonical_timeline = root / "memory" / "state" / "timeline.json"
    state = load_json_model(canonical_state, EntityState)
    timeline = load_json_model(canonical_timeline, TimelineFile)
    state_path = directory / "current_state.json"
    timeline_path = directory / "timeline.json"
    atomic_write_model_json(state_path, state)
    atomic_write_model_json(timeline_path, timeline)
    state_hash = sha256_file(state_path)
    timeline_hash = sha256_file(timeline_path)
    projection = SessionProjection(
        session_id=session_id,
        base_state_sha256=sha256_file(canonical_state),
        base_timeline_sha256=sha256_file(canonical_timeline),
        current_state_sha256=state_hash,
        current_timeline_sha256=timeline_hash,
        state_path=state_path.relative_to(root).as_posix(),
        timeline_path=timeline_path.relative_to(root).as_posix(),
        updated_at=utc_now(),
    )
    atomic_write_model_json(manifest_path, projection)
    return projection


def load_projection(root: Path, session_id: str) -> SessionProjection:
    root = root.resolve()
    manifest = load_json_model(projection_manifest_path(root, session_id), SessionProjection)
    state_path, timeline_path = projection_paths(root, manifest)
    actual_state = sha256_file(state_path)
    actual_timeline = sha256_file(timeline_path)
    if actual_state != manifest.current_state_sha256 or actual_timeline != manifest.current_timeline_sha256:
        raise ProjectionError("session projection files do not match projection.json")
    return manifest


def projection_paths(root: Path, projection: SessionProjection) -> tuple[Path, Path]:
    root = root.resolve()
    state_path = (root / projection.state_path).resolve()
    timeline_path = (root / projection.timeline_path).resolve()
    for path in (state_path, timeline_path):
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ProjectionError(f"projection path escapes project root: {path}") from exc
    return state_path, timeline_path


def advance_projection(
    root: Path,
    session_id: str,
    proposal: StateUpdateProposal,
    *,
    proposal_artifact_id: str | None = None,
) -> SessionProjection:
    root = root.resolve()
    projection = load_projection(root, session_id)
    state_path, timeline_path = projection_paths(root, projection)
    state = load_json_model(state_path, EntityState)
    timeline = load_json_model(timeline_path, TimelineFile)
    try:
        validate_state_change_old_values(state, proposal.state_changes, root)
        updated_state = apply_state_changes_to_state(state, proposal.state_changes, root)
        updated_timeline = TimelineFile(events=[*timeline.events, *proposal.timeline_events])
        validate_applied_state(updated_state)
        validate_applied_timeline(root, updated_timeline)
    except StateUpdateError as exc:
        raise ProjectionError(f"chapter {proposal.chapter_number} cannot advance projection: {exc}") from exc

    before_state = projection.current_state_sha256
    before_timeline = projection.current_timeline_sha256
    atomic_write_model_json(state_path, updated_state)
    atomic_write_model_json(timeline_path, updated_timeline)
    after_state = sha256_file(state_path)
    after_timeline = sha256_file(timeline_path)
    checkpoint = ProjectionCheckpoint(
        chapter_number=proposal.chapter_number,
        before_state_sha256=before_state,
        before_timeline_sha256=before_timeline,
        after_state_sha256=after_state,
        after_timeline_sha256=after_timeline,
        proposal_artifact_id=proposal_artifact_id,
        created_at=utc_now(),
    )
    updated = projection.model_copy(
        update={
            "current_state_sha256": after_state,
            "current_timeline_sha256": after_timeline,
            "checkpoints": [*projection.checkpoints, checkpoint],
            "updated_at": utc_now(),
        }
    )
    atomic_write_model_json(projection_manifest_path(root, session_id), updated)
    return updated


def verify_canonical_base(root: Path, projection: SessionProjection) -> None:
    root = root.resolve()
    state_hash = sha256_file(root / "memory" / "state" / "current_state.json")
    timeline_hash = sha256_file(root / "memory" / "state" / "timeline.json")
    if state_hash != projection.base_state_sha256 or timeline_hash != projection.base_timeline_sha256:
        raise ProjectionError(
            "canonical state/timeline changed after the session started; regenerate or rebase the session"
        )
