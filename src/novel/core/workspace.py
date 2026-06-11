from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from novel.core.agent_defaults import (
    STANDARD_AGENT_NAMES,
    default_agent_config,
    inherited_agent_config_patch,
)
from novel.core.io import atomic_write_text
from novel.core.migration import CURRENT_SCHEMA_VERSION
from novel.core.timeutil import utc_now_iso


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

    timestamp = utc_now_iso()
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
        root / "config" / "embeddings.yaml": _embeddings_yaml(),
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


def is_default_inspiration_placeholder(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return path.read_text(encoding="utf-8") == _inspiration_markdown()
    except OSError:
        return False


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
    atomic_write_text(path, content)


def _project_id_from_title(title: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", title.lower()).strip("_")
    if not normalized:
        normalized = "project"
    if not normalized.startswith("novel_"):
        normalized = f"novel_{normalized}"
    return normalized


def _json(data: dict[str, object]) -> str:
    data = {"schema_version": CURRENT_SCHEMA_VERSION, **data}
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
        f"schema_version: {CURRENT_SCHEMA_VERSION}\n"
        f'project_id: "{project_id}"\n'
        f'title: "{title}"\n'
        f'language: "{language}"\n'
        "genre:\n"
        f"{_yaml_list(genre)}"
        "target_length:\n"
        '  type: "long_novel"\n'
        "  planned_chapters: 80\n"
        "web:\n"
        "  default_port: 8765\n"
        "context_budget:\n"
        "  enabled: false\n"
        "  recent_window_chapters: 3\n"
        "  max_full_timeline_events: 40\n"
        "  max_full_state_entities: 60\n"
        "  digest_dropped: true\n"
        "chapter_memory:\n"
        "  enabled: true\n"
        "  generate_on_accept: true\n"
        "  strict_accept: false\n"
        "  inject_into_tasks:\n"
        '    - "plan"\n'
        '    - "write"\n'
        "polish:\n"
        '  mode: "single_pass"\n'
        "audit_recall:\n"
        "  enabled: true\n"
        "  max_recall_rounds: 1\n"
        "  max_requests_per_round: 3\n"
        "canon_drift:\n"
        "  enabled: true\n"
        "narration:\n"
        '  pov: "third_person_limited"\n'
        '  tense: "past"\n'
        'default_style_profile_id: "style_default"\n'
        f'created_at: "{created_at}"\n'
        f'updated_at: "{updated_at}"\n'
    )


def _agents_yaml() -> str:
    lines = [
        f"schema_version: {CURRENT_SCHEMA_VERSION}\n",
        "default:\n",
        *_yaml_config_lines(default_agent_config(), indent=2),
        "agents:\n",
    ]
    for name in STANDARD_AGENT_NAMES:
        lines.extend(
            [
                f"  {name}:\n",
                *_yaml_config_lines(inherited_agent_config_patch(name), indent=4),
            ]
        )
    return "".join(lines)


_AGENT_CONFIG_FIELD_ORDER = (
    "inherit_default",
    "provider",
    "base_url_env",
    "api_key_env",
    "model",
    "reasoning",
    "thinking",
    "max_context_tokens",
    "max_tokens",
    "temperature",
    "timeout_seconds",
    "max_retries",
    "json_response_format",
)


def _yaml_config_lines(config: dict[str, object], *, indent: int) -> list[str]:
    lines: list[str] = []
    prefix = " " * indent
    ordered_keys = [key for key in _AGENT_CONFIG_FIELD_ORDER if key in config]
    ordered_keys.extend(key for key in config if key not in _AGENT_CONFIG_FIELD_ORDER)
    for key in ordered_keys:
        if key not in config:
            continue
        value = config[key]
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:\n")
            lines.extend(_yaml_config_lines(value, indent=indent + 2))
        elif isinstance(value, bool):
            lines.append(f"{prefix}{key}: {str(value).lower()}\n")
        elif isinstance(value, str):
            lines.append(f'{prefix}{key}: "{value}"\n')
        else:
            lines.append(f"{prefix}{key}: {value}\n")
    return lines


def _embeddings_yaml() -> str:
    return (
        f"schema_version: {CURRENT_SCHEMA_VERSION}\n"
        'active_provider: "dashscope"\n'
        "providers:\n"
        "  test_local_hash:\n"
        '    provider: "local_hash"\n'
        '    model: "local-hash-v1"\n'
        "    dimensions: 32\n"
        "    batch_size: 64\n"
        "  dashscope:\n"
        '    provider: "dashscope"\n'
        '    base_url_env: "DASHSCOPE_EMBEDDING_BASE_URL"\n'
        '    api_key_env: "DASHSCOPE_API_KEY"\n'
        '    model: "text-embedding-v4"\n'
        "    dimensions: 2048\n"
        "    batch_size: 10\n"
        "    timeout_seconds: 30\n"
        "    max_retries: 1\n"
        "  zhipu:\n"
        '    provider: "zhipu"\n'
        '    base_url_env: "ZHIPU_EMBEDDING_BASE_URL"\n'
        '    api_key_env: "ZHIPU_API_KEY"\n'
        '    model: "embedding-3"\n'
        "    dimensions: 2048\n"
        "    batch_size: 16\n"
        "    timeout_seconds: 30\n"
        "    max_retries: 1\n"
    )


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
