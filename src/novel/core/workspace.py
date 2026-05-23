from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re


DEFAULT_WORKSPACE_DIR = "novel-project"


class WorkspaceExistsError(RuntimeError):
    """Raised when initialization would overwrite existing workspace data."""


@dataclass(frozen=True)
class InitOptions:
    title: str
    root: Path = Path(DEFAULT_WORKSPACE_DIR)
    project_id: str | None = None
    language: str = "zh-CN"
    genre: list[str] | None = None


@dataclass(frozen=True)
class InitResult:
    root: Path
    created_files: tuple[Path, ...]
    created_dirs: tuple[Path, ...]


def init_workspace(options: InitOptions) -> InitResult:
    root = options.root
    title = options.title.strip()
    if not title:
        raise ValueError("title must not be empty")

    if _has_existing_workspace_data(root):
        raise WorkspaceExistsError(
            f"{root} already contains workspace files; refusing to overwrite existing data"
        )

    created_dirs: list[Path] = []
    created_files: list[Path] = []

    for directory in _workspace_dirs(root):
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=False)
            created_dirs.append(directory)

    timestamp = _utc_now()
    project_id = options.project_id or _project_id_from_title(title)
    genre = options.genre if options.genre else ["未分类"]

    files = {
        root / "project.yaml": _project_yaml(
            project_id=project_id,
            title=title,
            language=options.language,
            genre=genre,
            created_at=timestamp,
            updated_at=timestamp,
        ),
        root / "config" / "agents.yaml": _agents_yaml(),
        root / "memory" / "inspiration.md": _inspiration_markdown(),
        root / "memory" / "style_guide.md": _style_guide_markdown(),
        root / "memory" / "canon" / "characters.json": _json({"characters": []}),
        root / "memory" / "canon" / "locations.json": _json({"locations": []}),
        root / "memory" / "canon" / "items.json": _json({"items": []}),
        root / "memory" / "canon" / "world.json": _json({"world_rules": []}),
        root / "memory" / "canon" / "hidden_truths.json": _json({"hidden_truths": []}),
        root / "memory" / "canon" / "foreshadowing.json": _json(
            {"foreshadowing_threads": []}
        ),
        root / "memory" / "state" / "current_state.json": _json(
            {
                "story_position": {
                    "latest_chapter": 0,
                    "in_story_time": None,
                    "summary": None,
                },
                "character_states": [],
                "item_states": [],
                "location_states": [],
            }
        ),
        root / "memory" / "state" / "timeline.json": _json({"events": []}),
        root / "runs" / ".gitkeep": "",
        root / "exports" / ".gitkeep": "",
    }

    for path, content in files.items():
        _write_new_file(path, content)
        created_files.append(path)

    return InitResult(
        root=root,
        created_files=tuple(created_files),
        created_dirs=tuple(created_dirs),
    )


def _has_existing_workspace_data(root: Path) -> bool:
    workspace_markers = [
        root / "project.yaml",
        root / "config" / "agents.yaml",
        root / "memory",
        root / "runs",
        root / "exports",
    ]
    return any(marker.exists() for marker in workspace_markers)


def _workspace_dirs(root: Path) -> tuple[Path, ...]:
    return (
        root,
        root / "config",
        root / "memory",
        root / "memory" / "canon",
        root / "memory" / "state",
        root / "memory" / "chapters",
        root / "runs",
        root / "exports",
    )


def _write_new_file(path: Path, content: str) -> None:
    if path.exists():
        raise WorkspaceExistsError(f"{path} already exists")
    path.write_text(content, encoding="utf-8")


def _project_id_from_title(title: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", title.lower()).strip("_")
    if not normalized:
        normalized = "project"
    if not normalized.startswith("novel_"):
        normalized = f"novel_{normalized}"
    return normalized


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(data: dict[str, object]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _yaml_list(values: list[str], indent: int = 2) -> str:
    prefix = " " * indent
    return "".join(f'{prefix}- "{value}"\n' for value in values)


def _project_yaml(
    *,
    project_id: str,
    title: str,
    language: str,
    genre: list[str],
    created_at: str,
    updated_at: str,
) -> str:
    return (
        'schema_version: 1\n'
        f'project_id: "{project_id}"\n'
        f'title: "{title}"\n'
        f'language: "{language}"\n'
        "genre:\n"
        f"{_yaml_list(genre)}"
        "target_length:\n"
        '  type: "long_novel"\n'
        "  planned_chapters: 80\n"
        "narration:\n"
        '  pov: "third_person_limited"\n'
        '  tense: "past"\n'
        'default_style_profile_id: "style_default"\n'
        f'created_at: "{created_at}"\n'
        f'updated_at: "{updated_at}"\n'
    )


def _agents_yaml() -> str:
    agents = {
        "orchestrator": ("OPENAI_BASE_URL", "OPENAI_API_KEY", "model-name", "medium", 128000, 0.4, 60, 1),
        "inspiration": ("INSPIRATION_BASE_URL", "INSPIRATION_API_KEY", "model-name", "medium", 64000, 0.8, 60, 1),
        "canon": ("CANON_BASE_URL", "CANON_API_KEY", "model-name", "medium", 64000, 0.5, 60, 1),
        "plot": ("PLOT_BASE_URL", "PLOT_API_KEY", "model-name", "high", 128000, 0.6, 90, 1),
        "writer": ("WRITER_BASE_URL", "WRITER_API_KEY", "model-name", "high", 128000, 0.9, 120, 1),
        "polish": ("POLISH_BASE_URL", "POLISH_API_KEY", "model-name", "medium", 128000, 0.7, 90, 1),
        "audit": ("AUDIT_BASE_URL", "AUDIT_API_KEY", "model-name", "low", 64000, 0.2, 60, 1),
        "state_update": ("STATE_UPDATE_BASE_URL", "STATE_UPDATE_API_KEY", "model-name", "low", 64000, 0.2, 60, 1),
    }
    lines = ["agents:\n"]
    for name, (base_url_env, api_key_env, model, reasoning, max_tokens, temperature, timeout, retries) in agents.items():
        lines.extend(
            [
                f"  {name}:\n",
                '    provider: "openai_compatible"\n',
                f'    base_url_env: "{base_url_env}"\n',
                f'    api_key_env: "{api_key_env}"\n',
                f'    model: "{model}"\n',
                f'    reasoning: "{reasoning}"\n',
                "    thinking:\n",
                '      type: "disabled"\n',
                f"    max_context_tokens: {max_tokens}\n",
                f"    temperature: {temperature}\n",
                f"    timeout_seconds: {timeout}\n",
                f"    max_retries: {retries}\n",
            ]
        )
    return "".join(lines)


def _inspiration_markdown() -> str:
    return (
        "# Inspiration\n\n"
        "## Seed Ideas\n\n"
        "## Themes\n\n"
        "## Mood\n\n"
        "## Weak Outline\n\n"
        "## Constraints\n"
    )


def _style_guide_markdown() -> str:
    return (
        "# Style Guide\n\n"
        "## Overall Style\n\n"
        "## Narrative POV\n\n"
        "## Prose Requirements\n\n"
        "## Dialogue Requirements\n\n"
        "## Pacing\n\n"
        "## Things to Avoid\n\n"
        "## Example Passages\n\n"
        "## Revision Notes\n"
    )
