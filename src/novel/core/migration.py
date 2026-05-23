from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


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
    data = _load_yaml_mapping(project_path)
    raw_version = data.get("schema_version")
    from_version = int(raw_version) if raw_version is not None else None
    if from_version and from_version > CURRENT_SCHEMA_VERSION:
        raise MigrationError(
            f"project schema_version {from_version} is newer than supported {CURRENT_SCHEMA_VERSION}"
        )
    if from_version == CURRENT_SCHEMA_VERSION:
        return MigrationResult(
            root=root,
            changed=False,
            from_version=from_version,
            to_version=CURRENT_SCHEMA_VERSION,
            updated_files=(),
        )

    updated_files: list[Path] = []
    data["schema_version"] = CURRENT_SCHEMA_VERSION
    if not dry_run:
        project_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    updated_files.append(project_path)
    return MigrationResult(
        root=root,
        changed=True,
        from_version=from_version,
        to_version=CURRENT_SCHEMA_VERSION,
        updated_files=tuple(updated_files),
    )


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MigrationError(f"could not read YAML file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MigrationError(f"{path} must contain a YAML mapping")
    return dict(data)
