from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_env_example_contains_only_variable_names_with_empty_values() -> None:
    env_example = ROOT / ".env.example"
    lines = [
        line.strip()
        for line in env_example.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert lines
    for line in lines:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*=", line), line


def test_gitignore_protects_local_env_and_build_outputs() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".env" in gitignore
    assert ".env.example" not in {
        entry.strip()
        for entry in gitignore.splitlines()
        if entry.strip() and not entry.strip().startswith("#")
    }
    assert "dist/" in gitignore
    assert "build/" in gitignore


def test_docs_mention_current_generated_files() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    integration = (ROOT / "docs" / "INTEGRATION.md").read_text(encoding="utf-8")

    for name in (
        "memory/search_index.json",
        "state_update_proposal.json",
        "revision_log.json",
        "export_manifest.json",
    ):
        assert name in readme or name in integration
    assert "accepted" in readme
    assert "不直接修改 `current_state.json` 或 `timeline.json`" in readme


def test_readme_prefers_current_canon_show_command() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "novel canon show --path ./rain-station" in readme
    assert "兼容别名" in readme
