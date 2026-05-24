from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import yaml

from novel.core.io import atomic_write_json, atomic_write_yaml, backup_if_exists


CURRENT_SCHEMA_VERSION = 1


class MigrationError(RuntimeError):
    """Raised when a project migration cannot be applied."""


@dataclass(frozen=True)
class MigrationResult:
    root: Path
    changed: bool
    from_version: int | None
    to_version: int
    updated_files: tuple[Path, ...]


def migrate_project(root: Path, *, dry_run: bool = False) -> MigrationResult:
    root = root.resolve()
    project_path = root / "project.yaml"
    if not project_path.exists():
        raise MigrationError(f"{project_path} is missing")
    project_data = _load_yaml_mapping(project_path)
    raw_version = project_data.get("schema_version")
    from_version = int(raw_version) if raw_version is not None else None
    if from_version and from_version > CURRENT_SCHEMA_VERSION:
        raise MigrationError(
            f"project schema_version {from_version} is newer than supported {CURRENT_SCHEMA_VERSION}"
        )

    updated_files: list[Path] = []
    _add_schema_version_to_yaml(project_path, project_data, updated_files, dry_run=dry_run)
    for yaml_path in _schema_versioned_yaml_paths(root):
        if yaml_path.exists():
            data = _load_yaml_mapping(yaml_path)
            _add_schema_version_to_yaml(yaml_path, data, updated_files, dry_run=dry_run)
    for json_path in _schema_versioned_json_paths(root):
        if json_path.exists():
            data = _load_json_mapping(json_path)
            _add_schema_version_to_json(json_path, data, updated_files, dry_run=dry_run)

    if not updated_files:
        return MigrationResult(
            root=root,
            changed=False,
            from_version=from_version,
            to_version=CURRENT_SCHEMA_VERSION,
            updated_files=(),
        )

    return MigrationResult(
        root=root,
        changed=True,
        from_version=from_version,
        to_version=CURRENT_SCHEMA_VERSION,
        updated_files=tuple(updated_files),
    )


def _schema_versioned_yaml_paths(root: Path) -> tuple[Path, ...]:
    return (root / "config" / "agents.yaml",)


def _schema_versioned_json_paths(root: Path) -> tuple[Path, ...]:
    static_paths = (
        root / "memory" / "inspiration.json",
        root / "memory" / "canon" / "characters.json",
        root / "memory" / "canon" / "locations.json",
        root / "memory" / "canon" / "items.json",
        root / "memory" / "canon" / "world.json",
        root / "memory" / "canon" / "hidden_truths.json",
        root / "memory" / "canon" / "foreshadowing.json",
        root / "memory" / "state" / "current_state.json",
        root / "memory" / "state" / "timeline.json",
        root / "exports" / "export_manifest.json",
    )
    dynamic_paths: list[Path] = []
    chapters_dir = root / "memory" / "chapters"
    if chapters_dir.exists():
        for chapter_dir in sorted(path for path in chapters_dir.iterdir() if path.is_dir()):
            dynamic_paths.extend(
                [
                    chapter_dir / "plan.json",
                    chapter_dir / "audit.json",
                    chapter_dir / "metadata.json",
                    chapter_dir / "revision_log.json",
                    chapter_dir / "state_update_proposal.json",
                    chapter_dir / "state_update_apply_log.json",
                ]
            )
    runs_dir = root / "runs"
    if runs_dir.exists():
        dynamic_paths.extend(sorted(runs_dir.glob("*.json")))
    return (*static_paths, *dynamic_paths)


def _add_schema_version_to_yaml(
    path: Path,
    data: dict[str, object],
    updated_files: list[Path],
    *,
    dry_run: bool,
) -> None:
    raw_version = data.get("schema_version")
    if raw_version is not None:
        _reject_newer_version(path, raw_version)
        return
    data["schema_version"] = CURRENT_SCHEMA_VERSION
    if not dry_run:
        backup_if_exists(path, reason="migration")
        atomic_write_yaml(path, data)
    updated_files.append(path)


def _add_schema_version_to_json(
    path: Path,
    data: dict[str, object],
    updated_files: list[Path],
    *,
    dry_run: bool,
) -> None:
    raw_version = data.get("schema_version")
    if raw_version is not None:
        _reject_newer_version(path, raw_version)
        return
    data = {"schema_version": CURRENT_SCHEMA_VERSION, **data}
    if not dry_run:
        backup_if_exists(path, reason="migration")
        atomic_write_json(path, data)
    updated_files.append(path)


def _reject_newer_version(path: Path, raw_version: object) -> None:
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise MigrationError(f"{path} has invalid schema_version: {raw_version!r}") from exc
    if version > CURRENT_SCHEMA_VERSION:
        raise MigrationError(
            f"{path} schema_version {version} is newer than supported {CURRENT_SCHEMA_VERSION}"
        )


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MigrationError(f"could not read YAML file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MigrationError(f"{path} must contain a YAML mapping")
    return dict(data)


def _load_json_mapping(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MigrationError(f"could not read JSON file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MigrationError(f"{path} must contain a JSON object")
    return dict(data)
