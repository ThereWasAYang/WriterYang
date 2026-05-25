from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.workspace import InitOptions, init_workspace


def test_ask_creates_creation_session_and_outline(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(
        ["ask", "请为第1章生成章节计划", "--path", str(root), "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    assert "Session:" in stdout
    assert "outline proposal generated" in stdout
    assert list((root / "memory" / "sessions").glob("session_*/session.json"))
    assert (root / "memory" / "chapters" / "001" / "plan.json").is_file()


def test_ask_json_returns_session_id(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(
        ["ask", "请写第1章初稿", "--path", str(root), "--provider", "mock", "--json", "--quiet"]
    )

    assert code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["task"] == "creation_session"
    assert payload["session_id"].startswith("session_")


def test_ask_dry_run_keeps_legacy_orchestrator_plan_without_writing(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "ask",
            "请为第1章生成章节计划",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--dry-run",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "Dry run complete" in stdout
    assert not (root / "memory" / "chapters" / "001" / "plan.json").exists()
    assert not list((root / "runs").glob("run_*.json"))


def test_ask_stops_when_max_steps_exceeded(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "ask",
            "请为第1章生成章节计划",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--max-steps",
            "0",
        ]
    )

    assert code == 1
    assert stdout == ""
    assert "max_steps" in stderr
    assert not (root / "memory" / "chapters" / "001" / "plan.json").exists()


def test_ask_no_longer_writes_run_log_for_session_start(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, _, stderr = _run_cli(
        ["ask", "请为第1章生成章节计划", "--path", str(root), "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    assert not list((root / "runs").glob("run_*.json"))


def _workspace_ready(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# Inspiration\n\n## Weak Outline\n\n雨夜旧车站传来停播多年的广播声。\n",
        encoding="utf-8",
    )
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    return root


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
