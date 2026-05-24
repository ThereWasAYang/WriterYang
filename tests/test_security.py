from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from novel.core.io import atomic_write_text, backup_file
from novel.core.locking import ProjectLock, ProjectLockError
from novel.core.security import scan_security, validate_env_example, validate_secret_config_file
from novel.core.workspace import InitOptions, init_workspace


def test_repository_secret_scan_passes() -> None:
    result = scan_security(Path("."))
    assert result.ok, [f"{item.code}:{item.path}:{item.line}" for item in result.findings]


def test_env_example_requires_empty_values(tmp_path: Path) -> None:
    path = tmp_path / ".env.example"
    path.write_text("OPENAI_API_KEY=real-value\n", encoding="utf-8")

    findings = validate_env_example(path)

    assert findings
    assert findings[0].code == "invalid_env_example"
    assert "real-value" not in findings[0].message


def test_config_rejects_literal_api_key(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    path.write_text(
        "agents:\n  writer:\n    provider: openai\n    api_key_env: sk-real-looking-secret-value\n",
        encoding="utf-8",
    )

    findings = validate_secret_config_file(path)

    assert findings
    assert findings[0].code == "unsafe_config_secret"
    assert "sk-real-looking-secret-value" not in findings[0].message


def test_atomic_write_preserves_original_on_replace_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "data.txt"
    path.write_text("old\n", encoding="utf-8")

    def fail_replace(src: str, dst: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(RuntimeError):
        atomic_write_text(path, "new\n")

    assert path.read_text(encoding="utf-8") == "old\n"


def test_backup_file_creates_timestamped_copy(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_text('{"old": true}\n', encoding="utf-8")

    backup = backup_file(path, reason="test")

    assert backup.exists()
    assert backup.name.startswith("data.json.bak_")
    assert backup.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")


def test_project_lock_blocks_second_writer(tmp_path: Path) -> None:
    root = tmp_path / "project"
    init_workspace(InitOptions(title="锁测试", root=root))

    with ProjectLock(root, task="first"):
        with pytest.raises(ProjectLockError) as exc_info:
            with ProjectLock(root, task="second"):
                pass

    assert "project is locked" in str(exc_info.value)
    assert not (root / ".writeryang.lock").exists()


def test_project_lock_clears_stale_lock(tmp_path: Path) -> None:
    root = tmp_path / "project"
    init_workspace(InitOptions(title="陈旧锁测试", root=root))
    lock_path = root / ".writeryang.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": 99999999,
                "task": "dead",
                "created_at": (datetime.now(timezone.utc) - timedelta(days=2))
                .isoformat()
                .replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )

    with ProjectLock(root, task="new"):
        assert lock_path.exists()

    assert not lock_path.exists()
