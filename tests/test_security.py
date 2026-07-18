from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from novel.core import locking as locking_module
from novel.core import security as security_module
from novel.core.io import atomic_write_text, backup_file
from novel.core.locking import ProjectLock, ProjectLockError, read_project_lock
from novel.core.security import redact_secret_text, scan_security, validate_env_example, validate_secret_config_file
from novel.core.workspace import InitOptions, init_workspace


def test_repository_secret_scan_passes() -> None:
    result = scan_security(Path("."))
    assert result.ok, [f"{item.code}:{item.path}:{item.line}" for item in result.findings]


def test_security_scan_skips_deleted_tracked_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "present.txt").write_text("safe text\n", encoding="utf-8")

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="missing.txt\npresent.txt\n", stderr="")

    monkeypatch.setattr(security_module.subprocess, "run", fake_run)

    result = scan_security(tmp_path)

    assert result.ok


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
        "profiles:\n  scribe:\n    provider: openai\n    api_key_env: sk-test-real-looking-secret-value\n",
        encoding="utf-8",
    )

    findings = validate_secret_config_file(path)

    assert findings
    assert findings[0].code == "unsafe_config_secret"
    assert "sk-test-real-looking-secret-value" not in findings[0].message


def test_redact_secret_text_masks_common_secret_shapes() -> None:
    secret = "secret-provider-token"
    short_key = "sk-" + "testsecret"
    project_key = "sk-proj-" + "abcdefghijklmnop"
    bearer_token = "abcdefghijklmnopqrstuvwxyz"
    text = (
        f"Authorization: Bearer {secret}\n"
        f"api_key={short_key}\n"
        f"raw {project_key}\n"
        f"bearer {bearer_token}"
    )

    redacted = redact_secret_text(text, extra_secrets=(secret,))

    assert secret not in redacted
    assert short_key not in redacted
    assert project_key not in redacted
    assert bearer_token not in redacted
    assert "Authorization: Bearer [redacted]" in redacted


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

    with ProjectLock(root, task="first", workflow_run_id="run_" + "1" * 32, command_id="cmd_" + "2" * 32):
        info = read_project_lock(root)
        assert info.heartbeat_at
        assert info.host == socket.gethostname()
        assert info.process_start_time
        assert info.workflow_run_id == "run_" + "1" * 32
        assert info.command_id == "cmd_" + "2" * 32
        with pytest.raises(ProjectLockError) as exc_info, ProjectLock(root, task="second"):
            pass

    assert "project is locked" in str(exc_info.value)
    assert not (root / ".writeryang.lock").exists()


def test_project_lock_heartbeat_keeps_long_live_process_lock(tmp_path: Path) -> None:
    root = tmp_path / "project"
    init_workspace(InitOptions(title="长任务锁测试", root=root))

    with ProjectLock(
        root,
        task="long-running",
        stale_after=timedelta(milliseconds=250),
        heartbeat_interval_seconds=0.05,
    ):
        first = read_project_lock(root).heartbeat_at
        time.sleep(0.35)
        second = read_project_lock(root).heartbeat_at
        assert first != second
        with pytest.raises(ProjectLockError):
            ProjectLock(root, task="contender", stale_after=timedelta(milliseconds=250)).acquire()


def test_project_lock_does_not_reclaim_from_created_at_age_alone(tmp_path: Path) -> None:
    root = tmp_path / "project"
    init_workspace(InitOptions(title="进程身份锁测试", root=root))
    lock_path = root / ".writeryang.lock"
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    lock_path.write_text(
        json.dumps(
            {
                "lock_id": "lock_existing",
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "process_start_time": locking_module._process_start_time(os.getpid()),
                "task": "live",
                "created_at": (datetime.now(UTC) - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
                "heartbeat_at": now,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProjectLockError):
        ProjectLock(root, task="contender", stale_after=timedelta(hours=1)).acquire()


def test_project_lock_clears_stale_lock(tmp_path: Path) -> None:
    root = tmp_path / "project"
    init_workspace(InitOptions(title="陈旧锁测试", root=root))
    lock_path = root / ".writeryang.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": 99999999,
                "task": "dead",
                "created_at": (datetime.now(UTC) - timedelta(days=2))
                .isoformat()
                .replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )

    with ProjectLock(root, task="new"):
        assert lock_path.exists()

    assert not lock_path.exists()
