from __future__ import annotations

import fnmatch
import re
import subprocess
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


def test_repository_does_not_track_local_sensitive_or_build_files() -> None:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    tracked_paths = [path for path in completed.stdout.split("\0") if path]

    violations = [path for path in tracked_paths if _is_local_sensitive_or_build_file(path)]

    assert violations == []


def _is_local_sensitive_or_build_file(path: str) -> bool:
    parts = Path(path).parts
    filename = parts[-1]
    local_doc_paths = {
        "AGENTS.md",
        "docs/PRODUCT_SPEC.md",
        "docs/ARCHITECTURE.md",
        "docs/WORKFLOW.md",
        "docs/ROADMAP.md",
    }
    local_dirs = {
        ".agents",
        ".codex",
        ".idea",
        ".mypy_cache",
        ".playwright-cli",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".vscode",
        "build",
        "dist",
        "venv",
    }

    if filename.startswith(".env") and filename != ".env.example":
        return True
    if path in local_doc_paths:
        return True
    if any(part in local_dirs or part.endswith(".egg-info") for part in parts):
        return True
    if "runs" in parts and filename != ".gitkeep":
        return True
    if path.endswith(("memory/search_index.json", "memory/search_index.sqlite")):
        return True
    return fnmatch.fnmatch(filename, "*.bak_*")


def test_docs_mention_current_generated_files() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    integration = (ROOT / "docs" / "INTEGRATION.md").read_text(encoding="utf-8")
    docs = readme + "\n" + integration

    for name in (
        "memory/search_index.json",
        "state_update_proposal.json",
        "revision_log.json",
        "export_manifest.json",
    ):
        assert name in docs
    assert "accepted" in docs
    assert "不直接修改 `current_state.json` 或 `timeline.json`" in docs


def test_readme_prefers_current_canon_show_command() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    command_docs = (ROOT / "docs" / "CLI_COMMANDS.md").read_text(encoding="utf-8")
    docs = readme + "\n" + command_docs

    assert "novel canon show --path" in docs
    assert "不保留历史命令别名" in docs
