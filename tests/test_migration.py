from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

import yaml

from novel.cli import main
from novel.core.migration import CURRENT_SCHEMA_VERSION, migrate_project
from novel.core.workspace import InitOptions, init_workspace


def test_migrate_adds_missing_schema_version(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    project_path = root / "project.yaml"
    data = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    data.pop("schema_version", None)
    project_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    result = migrate_project(root)

    migrated = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    assert result.changed is True
    assert result.from_version is None
    assert result.to_version == CURRENT_SCHEMA_VERSION
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION


def test_migrate_adds_schema_version_to_json_and_agents_yaml(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    agents_path = root / "config" / "agents.yaml"
    characters_path = root / "memory" / "canon" / "characters.json"
    state_path = root / "memory" / "state" / "current_state.json"

    agents = yaml.safe_load(agents_path.read_text(encoding="utf-8"))
    agents.pop("schema_version", None)
    agents_path.write_text(yaml.safe_dump(agents, allow_unicode=True, sort_keys=False), encoding="utf-8")
    for path in (characters_path, state_path):
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("schema_version", None)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = migrate_project(root)

    assert result.changed is True
    assert agents_path in result.updated_files
    assert characters_path in result.updated_files
    assert state_path in result.updated_files
    assert yaml.safe_load(agents_path.read_text(encoding="utf-8"))["schema_version"] == CURRENT_SCHEMA_VERSION
    assert json.loads(characters_path.read_text(encoding="utf-8"))["schema_version"] == CURRENT_SCHEMA_VERSION
    assert json.loads(state_path.read_text(encoding="utf-8"))["schema_version"] == CURRENT_SCHEMA_VERSION


def test_migrate_dry_run_does_not_write(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    project_path = root / "project.yaml"
    data = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    data.pop("schema_version", None)
    project_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    result = migrate_project(root, dry_run=True)

    assert result.changed is True
    assert "schema_version" not in yaml.safe_load(project_path.read_text(encoding="utf-8"))


def test_migrate_cli_json(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))

    code, stdout, stderr = _run_cli(["migrate", "--path", str(root), "--json"])

    assert code == 0
    assert stderr == ""
    assert '"command": "migrate"' in stdout
    assert '"changed": false' in stdout


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
