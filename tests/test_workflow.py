from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.schemas import AgentRunLog
from novel.core.workspace import InitOptions, init_workspace


def test_generate_chapter_mock_completes_full_pipeline(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    code, stdout, stderr = _run_cli(["generate-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert "Audit passed" in stdout
    chapter_dir = root / "memory" / "chapters" / "001"
    assert (chapter_dir / "plan.json").is_file()
    assert (chapter_dir / "plan.md").is_file()
    assert (chapter_dir / "draft.md").is_file()
    assert (chapter_dir / "polished.md").is_file()
    assert (chapter_dir / "audit.json").is_file()
    run_log = _latest_run_log(root)
    assert run_log.status == "completed"
    assert [step.agent for step in run_log.steps] == [
        "plot_agent",
        "writer_agent",
        "writer_agent",
        "audit_agent",
    ]
    assert "memory/chapters/001/audit.json" in run_log.output_files
    assert "polish_skipped: true" in (chapter_dir / "polished.md").read_text(encoding="utf-8")


def test_generate_chapter_auto_polish_runs_polish_agent(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    code, stdout, stderr = _run_cli(
        ["generate-chapter", "1", "--path", str(root), "--provider", "mock", "--polish-mode", "auto"]
    )

    assert code == 0
    assert stderr == ""
    assert "Audit passed" in stdout
    assert [step.agent for step in _latest_run_log(root).steps] == [
        "plot_agent",
        "writer_agent",
        "polish_agent",
        "audit_agent",
    ]


def test_generate_chapter_stop_after_plan_only_generates_plan(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    code, stdout, stderr = _run_cli(
        ["generate-chapter", "1", "--path", str(root), "--provider", "mock", "--stop-after", "plan"]
    )

    assert code == 0
    assert stderr == ""
    assert "Stopped after plan" in stdout
    chapter_dir = root / "memory" / "chapters" / "001"
    assert (chapter_dir / "plan.json").is_file()
    assert not (chapter_dir / "draft.md").exists()
    run_log = _latest_run_log(root)
    assert [step.agent for step in run_log.steps] == ["plot_agent"]


def test_generate_chapter_stop_after_write_generates_plan_and_draft(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    code, stdout, stderr = _run_cli(
        ["generate-chapter", "1", "--path", str(root), "--provider", "mock", "--stop-after", "write"]
    )

    assert code == 0
    assert stderr == ""
    assert "Stopped after write" in stdout
    chapter_dir = root / "memory" / "chapters" / "001"
    assert (chapter_dir / "plan.json").is_file()
    assert (chapter_dir / "draft.md").is_file()
    assert not (chapter_dir / "polished.md").exists()
    assert [step.agent for step in _latest_run_log(root).steps] == ["plot_agent", "writer_agent"]


def test_generate_chapter_skip_polish_is_single_pass_alias(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    code, stdout, stderr = _run_cli(
        ["generate-chapter", "1", "--path", str(root), "--provider", "mock", "--skip-polish"]
    )

    assert code == 0
    assert stderr == ""
    chapter_dir = root / "memory" / "chapters" / "001"
    assert (chapter_dir / "draft.md").is_file()
    assert (chapter_dir / "polished.md").is_file()
    assert (chapter_dir / "audit.json").is_file()
    assert "polish_skipped: true" in (chapter_dir / "polished.md").read_text(encoding="utf-8")
    assert [step.agent for step in _latest_run_log(root).steps] == [
        "plot_agent",
        "writer_agent",
        "writer_agent",
        "audit_agent",
    ]


def test_generate_chapter_skip_audit_does_not_generate_audit(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    code, stdout, stderr = _run_cli(
        ["generate-chapter", "1", "--path", str(root), "--provider", "mock", "--skip-audit"]
    )

    assert code == 0
    assert stderr == ""
    chapter_dir = root / "memory" / "chapters" / "001"
    assert (chapter_dir / "polished.md").is_file()
    assert not (chapter_dir / "audit.json").exists()
    assert [step.agent for step in _latest_run_log(root).steps] == [
        "plot_agent",
        "writer_agent",
        "writer_agent",
    ]


def test_generate_chapter_run_log_contains_inputs_outputs_and_timestamps(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    _run_cli(["generate-chapter", "1", "--path", str(root), "--provider", "mock"])

    run_log = _latest_run_log(root)
    assert run_log.run_id.startswith("run_")
    assert run_log.task == "generate_chapter"
    assert run_log.chapter_number == 1
    assert run_log.started_at is not None
    assert run_log.ended_at is not None
    assert "project.yaml" in run_log.input_files
    assert "memory/chapters/001/plan.json" in run_log.output_files
    assert run_log.errors == []


def test_generate_chapter_failure_writes_failed_run_log(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    _run_cli(["generate-chapter", "1", "--path", str(root), "--provider", "mock", "--stop-after", "plan"])

    code, stdout, stderr = _run_cli(["generate-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 1
    assert stdout == ""
    assert "already exists" in stderr
    run_log = _latest_run_log(root)
    assert run_log.status == "failed"
    assert run_log.errors
    assert run_log.steps[0].status == "failed"


def test_generate_chapter_resume_reuses_existing_outputs_and_continues(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    _run_cli(["generate-chapter", "1", "--path", str(root), "--provider", "mock", "--stop-after", "plan"])
    plan_path = root / "memory" / "chapters" / "001" / "plan.json"
    original_plan = plan_path.read_text(encoding="utf-8")

    code, stdout, stderr = _run_cli(
        ["generate-chapter", "1", "--path", str(root), "--provider", "mock", "--resume"]
    )

    assert code == 0
    assert stderr == ""
    assert "Audit passed" in stdout
    assert plan_path.read_text(encoding="utf-8") == original_plan
    run_log = _latest_run_log(root)
    assert run_log.status == "completed"
    assert [step.agent for step in run_log.steps] == [
        "plot_agent",
        "writer_agent",
        "writer_agent",
        "audit_agent",
    ]
    assert "memory/chapters/001/plan.json" in run_log.output_files
    assert "memory/chapters/001/audit.json" in run_log.output_files


def test_generate_chapter_default_does_not_silently_overwrite(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    first, _, _ = _run_cli(["generate-chapter", "1", "--path", str(root), "--provider", "mock"])
    plan_path = root / "memory" / "chapters" / "001" / "plan.json"
    original_plan = plan_path.read_text(encoding="utf-8")

    second, stdout, stderr = _run_cli(["generate-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert first == 0
    assert second == 1
    assert stdout == ""
    assert "already exists" in stderr
    assert plan_path.read_text(encoding="utf-8") == original_plan


def _workspace_ready_for_generation(tmp_path: Path) -> Path:
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


def _latest_run_log(root: Path) -> AgentRunLog:
    paths = sorted((root / "runs").glob("run_*.json"))
    assert paths
    return AgentRunLog.model_validate(json.loads(paths[-1].read_text(encoding="utf-8")))


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
