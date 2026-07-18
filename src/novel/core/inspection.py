from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from novel.core.canon import format_canon_summary, load_canon_files
from novel.core.io import load_json, load_json_model, load_yaml_model
from novel.core.schemas import (
    ChapterMetadata,
    CharactersFile,
    EntityState,
    ItemsFile,
    LocationsFile,
    ProjectConfig,
    TimelineFile,
)


class ProjectReadError(RuntimeError):
    """Raised when project files cannot be read or parsed."""


@dataclass(frozen=True)
class ProjectStatus:
    title: str
    latest_chapter: int
    inspiration_exists: bool
    character_count: int
    location_count: int
    item_count: int
    timeline_event_count: int
    latest_run_log: Path | None
    latest_run_summary: str | None
    accepted_chapter_count: int = 0


def get_project_status(root: Path) -> ProjectStatus:
    root = root.resolve()
    project = _load_model(root / "project.yaml", ProjectConfig, "YAML")
    characters = _load_model(
        root / "memory" / "canon" / "characters.json", CharactersFile, "JSON"
    )
    locations = _load_model(
        root / "memory" / "canon" / "locations.json", LocationsFile, "JSON"
    )
    items = _load_model(root / "memory" / "canon" / "items.json", ItemsFile, "JSON")
    state = _load_model(
        root / "memory" / "state" / "current_state.json", EntityState, "JSON"
    )
    timeline = _load_model(root / "memory" / "state" / "timeline.json", TimelineFile, "JSON")
    latest_run_log = find_latest_run_log(root)
    latest_run_summary = _summarize_run_log(latest_run_log) if latest_run_log else None

    return ProjectStatus(
        title=project.title,
        latest_chapter=state.story_position.latest_chapter,
        inspiration_exists=_has_nonempty_file(root / "memory" / "inspiration.md"),
        character_count=len(characters.characters),
        location_count=len(locations.locations),
        item_count=len(items.items),
        timeline_event_count=len(timeline.events),
        latest_run_log=latest_run_log,
        latest_run_summary=latest_run_summary,
        accepted_chapter_count=_count_accepted_chapters(root),
    )


def format_status(status: ProjectStatus, root: Path) -> str:
    latest_run = "none"
    if status.latest_run_log:
        latest_run_path = _display_path(status.latest_run_log, root.resolve())
        latest_run = latest_run_path
        if status.latest_run_summary:
            latest_run = f"{latest_run_path} ({status.latest_run_summary})"

    lines = [
        f"Project: {status.title}",
        f"Latest chapter: {status.latest_chapter}",
        f"Inspiration: {'present' if status.inspiration_exists else 'missing'}",
        f"Characters: {status.character_count}",
        f"Locations: {status.location_count}",
        f"Items: {status.item_count}",
        f"Timeline events: {status.timeline_event_count}",
        f"Accepted chapters: {status.accepted_chapter_count}",
        f"Latest run log: {latest_run}",
    ]
    return "\n".join(lines)


def format_characters(root: Path) -> str:
    root = root.resolve()
    characters = _load_model(
        root / "memory" / "canon" / "characters.json", CharactersFile, "JSON"
    )
    if not characters.characters:
        return "No characters found."

    lines = ["Characters:"]
    for character in characters.characters:
        lines.append(f"- {character.name} [{character.id}]")
        lines.append(f"  Role: {character.role}")
        lines.append(f"  Summary: {character.reader_visible_summary}")
        if character.aliases:
            lines.append(f"  Aliases: {', '.join(character.aliases)}")
        if character.tags:
            lines.append(f"  Tags: {', '.join(character.tags)}")
    return "\n".join(lines)


def format_timeline(root: Path) -> str:
    root = root.resolve()
    timeline = _load_model(root / "memory" / "state" / "timeline.json", TimelineFile, "JSON")
    if not timeline.events:
        return "No timeline events found."

    lines = ["Timeline:"]
    for event in timeline.events:
        narrative = event.narrative_position
        story = event.story_position
        scene = f", scene {narrative.scene}" if narrative is not None and narrative.scene is not None else ""
        visible = "reader-visible" if event.reader_visible else "hidden"
        if narrative is None:
            lines.append(f"- 背景（未揭示）: {event.summary}")
        else:
            lines.append(f"- Chapter {narrative.chapter}{scene}: {event.summary}")
        lines.append(f"  ID: {event.id}")
        lines.append(f"  Story time: {story.time_label}")
        if story.order is not None:
            lines.append(f"  Story order: {story.order}")
        if event.event_role:
            lines.append(f"  Role: {event.event_role}")
        lines.append(f"  Visibility: {visible}")
        if event.location_id:
            lines.append(f"  Location: {event.location_id}")
        if event.participant_ids:
            lines.append(f"  Participants: {', '.join(event.participant_ids)}")
    return "\n".join(lines)


def format_state(root: Path) -> str:
    root = root.resolve()
    state = _load_model(
        root / "memory" / "state" / "current_state.json", EntityState, "JSON"
    )
    story_position = state.story_position
    lines = [
        "Current State:",
        f"- Latest chapter: {story_position.latest_chapter}",
        f"- In-story time: {story_position.in_story_time or 'unknown'}",
        f"- Summary: {story_position.summary or 'none'}",
        f"- Character states: {len(state.character_states)}",
        f"- Item states: {len(state.item_states)}",
        f"- Location states: {len(state.location_states)}",
    ]

    if state.character_states:
        lines.append("Characters:")
        for character_state in state.character_states:
            location = character_state.location_id or "unknown"
            mental = character_state.mental_state or "unknown"
            lines.append(f"- {character_state.entity_id}: location={location}, mental_state={mental}")

    if state.item_states:
        lines.append("Items:")
        for item_state in state.item_states:
            holder = item_state.holder_id or "none"
            location = item_state.location_id or "none"
            condition = item_state.condition or "unknown"
            lines.append(
                f"- {item_state.entity_id}: holder={holder}, location={location}, condition={condition}"
            )

    if state.location_states:
        lines.append("Locations:")
        for location_state in state.location_states:
            condition = location_state.condition or "unknown"
            accessibility = location_state.accessibility or "unknown"
            lines.append(
                f"- {location_state.entity_id}: accessibility={accessibility}, condition={condition}"
            )

    return "\n".join(lines)


def format_canon(root: Path) -> str:
    root = root.resolve()
    try:
        return format_canon_summary(load_canon_files(root))
    except Exception as exc:
        raise ProjectReadError(f"could not read canon files: {exc}") from exc


def find_latest_run_log(root: Path) -> Path | None:
    runs_dir = root / "runs"
    if not runs_dir.exists():
        return None
    run_logs = sorted(
        (path for path in runs_dir.glob("*.json") if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    return run_logs[0] if run_logs else None


def _count_accepted_chapters(root: Path) -> int:
    chapters_dir = root / "memory" / "chapters"
    if not chapters_dir.exists():
        return 0
    count = 0
    for metadata_path in chapters_dir.glob("*/metadata.json"):
        try:
            metadata = load_json_model(metadata_path, ChapterMetadata)
        except Exception:
            continue
        if metadata.status == "accepted":
            count += 1
    return count


def _load_model(path: Path, model_type: type, file_type: str):
    if not path.exists():
        raise ProjectReadError(f"{path} is missing")
    try:
        if file_type == "YAML":
            return load_yaml_model(path, model_type)
        return load_json_model(path, model_type)
    except Exception as exc:
        raise ProjectReadError(f"could not read {file_type} file {path}: {exc}") from exc


def _has_nonempty_file(path: Path) -> bool:
    return path.exists() and path.is_file() and bool(path.read_text(encoding="utf-8").strip())


def _summarize_run_log(path: Path) -> str:
    try:
        data = load_json(path)
    except Exception:
        return "unreadable"
    if not isinstance(data, dict):
        return "unreadable"
    parts: list[str] = []
    for key in ("run_id", "task", "status"):
        value = data.get(key)
        if value:
            parts.append(f"{key}={value}")
    return ", ".join(parts) if parts else "no summary"


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
