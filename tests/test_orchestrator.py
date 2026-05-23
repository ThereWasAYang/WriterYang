from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.workflow import GenerateChapterOptions, generate_chapter
from novel.core.workspace import InitOptions, init_workspace


def test_ask_identifies_and_runs_chapter_plan_task(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(
        ["ask", "请为第1章生成章节计划", "--path", str(root), "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    assert "Task: plan" in stdout
    assert "orchestrator -> plot" in stdout
    assert (root / "memory" / "chapters" / "001" / "plan.json").is_file()


def test_ask_identifies_and_runs_write_task(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    assert _run_cli(
        ["ask", "请为第1章生成章节计划", "--path", str(root), "--provider", "mock"]
    )[0] == 0

    code, stdout, stderr = _run_cli(
        ["ask", "请写第1章初稿", "--path", str(root), "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    assert "Task: write" in stdout
    assert (root / "memory" / "chapters" / "001" / "draft.md").is_file()


def test_ask_identifies_and_runs_audit_task(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    result = generate_chapter(
        GenerateChapterOptions(root=root, chapter_number=1, provider_name="mock", skip_audit=True)
    )
    assert result.run_log.status == "completed"

    code, stdout, stderr = _run_cli(
        ["ask", "请审核第1章一致性", "--path", str(root), "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    assert "Task: audit" in stdout
    assert (root / "memory" / "chapters" / "001" / "audit.json").is_file()


def test_ask_dry_run_does_not_write_files(tmp_path: Path) -> None:
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


def test_ask_run_log_records_handoff_trace(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, _, stderr = _run_cli(
        ["ask", "请为第1章生成章节计划", "--path", str(root), "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    run_log = _latest_run_log(root)
    assert run_log["task"] == "ask"
    assert run_log["orchestrator_task"] == "plan"
    assert run_log["handoff_trace"][0]["source"] == "orchestrator"
    assert run_log["handoff_trace"][0]["target"] == "plot"
    assert run_log["steps"][0]["agent"] == "plot_agent"


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


def _latest_run_log(root: Path) -> dict[str, object]:
    paths = sorted((root / "runs").glob("run_*.json"))
    assert paths
    return json.loads(paths[-1].read_text(encoding="utf-8"))


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
