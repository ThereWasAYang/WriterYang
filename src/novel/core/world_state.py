from __future__ import annotations

from pathlib import Path


def resolve_world_state_paths(root: Path, override_dir: Path | None) -> tuple[Path, Path]:
    root = root.resolve()
    directory = override_dir.resolve() if override_dir else root / "memory" / "state"
    if override_dir is not None:
        try:
            directory.relative_to(root)
        except ValueError as exc:
            raise ValueError("world state override must stay inside the project workspace") from exc
    state_path = directory / "current_state.json"
    timeline_path = directory / "timeline.json"
    if not state_path.is_file() or not timeline_path.is_file():
        raise ValueError(f"world state snapshot is incomplete: {directory}")
    return state_path, timeline_path
