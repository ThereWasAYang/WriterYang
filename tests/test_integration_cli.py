from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import build_parser, main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.workspace import InitOptions, init_workspace


def test_status_json_output_is_valid_json(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(["status", "--project", str(root), "--json"])

    assert code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["command"] == "status"
    assert payload["status"]["title"] == "雨夜旧车站"


def test_json_error_output_is_valid_json(tmp_path: Path) -> None:
    root = tmp_path / "missing"

    code, stdout, stderr = _run_cli(["status", "--project", str(root), "--json"])

    assert code == 1
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "project_read_error"


def test_project_alias_selects_workspace(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(["validate", "--project", str(root), "--json"])

    assert code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["validation"]["root"] == str(root.resolve())


def test_quiet_suppresses_success_output(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(["status", "--project", str(root), "--quiet"])

    assert code == 0
    assert stdout == ""
    assert stderr == ""


def test_json_output_does_not_leak_api_key(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready(tmp_path)
    monkeypatch.setenv("WRITER_API_KEY", "sk-test-secret-never-leak")

    code, stdout, stderr = _run_cli(["status", "--project", str(root), "--json"])

    assert code == 0
    assert stderr == ""
    assert "sk-test-secret-never-leak" not in stdout
    assert "api_key_env" not in stdout


def test_integration_doc_commands_match_cli() -> None:
    doc = Path("docs/INTEGRATION.md").read_text(encoding="utf-8")
    parser = build_parser()

    assert "novel status --project ./rain-station --json --quiet" in doc
    assert "novel ask" in doc
    assert "novel generate-chapter 1" in doc
    assert "docs/openclaw_tool_manifest.json" in doc
    assert "status" in parser.format_help()
    assert "ask" in parser.format_help()


def test_ask_json_dry_run_output(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "ask",
            "请为第1章生成章节计划",
            "--project",
            str(root),
            "--provider",
            "mock",
            "--dry-run",
            "--json",
            "--quiet",
        ]
    )

    assert code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["task"] == "plan"
    assert payload["handoff_trace"][0]["target"] == "plot"
    assert not list((root / "runs").glob("run_*.json"))


def test_canon_show_contract_alias_outputs_json(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(["canon", "show", "--project", str(root), "--json", "--quiet"])

    assert code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["command"] == "canon show"
    assert "characters" in payload["output"].lower()


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
