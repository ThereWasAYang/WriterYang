from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

import pytest

from novel.cli import main
from novel.core.budget import WorkflowBudgetExceeded, workflow_budget_scope
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.command_bus import DomainError, dispatch_command, new_command_envelope
from novel.core.contracts import (
    BudgetUsage,
    SessionStartCommand,
    SessionCommand,
    Surface,
    WorkflowBudget,
    WorkflowNodeRun,
    WorkflowRun,
)
from novel.core.io import load_json_model
from novel.core.providers import LoggingModelProvider, MockProvider, ModelRequest
from novel.core.workspace import InitOptions, init_workspace


def test_budget_tracker_rejects_first_unit_past_limit() -> None:
    budget = _budget(max_model_calls=1, max_provider_attempts=1)
    with workflow_budget_scope(budget) as tracker:
        tracker.consume_model_call()
        with pytest.raises(WorkflowBudgetExceeded) as caught:
            tracker.consume_model_call()
    assert caught.value.dimension == "model_calls"
    assert caught.value.used == 2
    assert caught.value.limit == 1


def test_command_bus_rejects_chapter_scope_over_budget(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    envelope = new_command_envelope(
        surface=Surface.CLI,
        project_root=root,
        command=SessionStartCommand(
            user_intent="写两章",
            chapter_range=[1, 2],
            provider_name="mock",
        ),
        budget=_budget(max_chapters=1),
    )
    with pytest.raises(DomainError) as caught:
        dispatch_command(envelope)
    assert caught.value.code == "budget_exceeded"
    assert caught.value.details["dimension"] == "chapters"
    assert not list((root / "memory" / "sessions").glob("session_*"))
    run = load_json_model(root / "runs" / envelope.workflow_run_id / "run.json", WorkflowRun)
    assert run.status == "failed"


def test_logging_provider_records_calls_attempts_and_tokens(tmp_path: Path) -> None:
    provider = LoggingModelProvider(
        provider=MockProvider(
            fake_response={
                "content": "ok",
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            }
        ),
        agent_name="writer",
        provider_name="mock",
        model="mock",
        root=tmp_path,
    )
    with workflow_budget_scope(_budget()) as tracker:
        provider.generate(ModelRequest(system_prompt="s", user_prompt="u"))
        usage = tracker.snapshot()
    assert usage == BudgetUsage(
        model_calls=1,
        provider_attempts=1,
        input_tokens=11,
        output_tokens=7,
    )


def test_ask_proposes_costly_session_before_execution(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    code, payload = _run_json_cli(["ask", "写第1章", "--path", str(root), "--provider", "mock", "--json"])
    assert code == 0
    assert payload["status"] == "proposed"
    assert payload["proposal"]["command"]["type"] == "session.start"
    assert payload["proposal"]["requires_confirmation"] is True
    assert not list((root / "memory" / "sessions").glob("session_*"))


def test_ask_confirm_executes_same_proposed_command_with_one_budget(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    proposed_code, proposed = _run_json_cli(
        ["ask", "写第1章", "--path", str(root), "--provider", "mock", "--json"]
    )
    assert proposed_code == 0
    workflow_run_id = proposed["workflow_run_id"]
    code, payload = _run_json_cli(
        [
            "ask",
            "写第1章",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--confirm",
            str(workflow_run_id),
            "--json",
        ]
    )
    assert code == 0
    assert payload["status"] == "executed"
    assert payload["execution"]["command_type"] == "session.start"
    assert payload["execution"]["workflow_run_id"] == workflow_run_id
    assert payload["budget_usage"]["model_calls"] >= 1
    run_dir = root / "runs" / str(payload["workflow_run_id"])
    run = load_json_model(run_dir / "run.json", WorkflowRun)
    nodes = {path.stem: load_json_model(path, WorkflowNodeRun) for path in (run_dir / "nodes").glob("node_*.json")}
    names = [nodes[node_id].name for node_id in run.node_ids]
    assert names[0] == "proposal:ask"
    assert "command:session.start" in names
    assert (run_dir / "proposal.json").is_file()


def test_session_commands_inherit_persisted_workflow_budget(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    budget = _budget(max_model_calls=2, max_provider_attempts=4)
    started = dispatch_command(
        new_command_envelope(
            surface=Surface.CLI,
            project_root=root,
            command=SessionStartCommand(
                user_intent="写第1章",
                chapter_range=[1],
                provider_name="mock",
            ),
            budget=budget,
        )
    )
    session_id = str(started.result["session"]["session_id"])  # type: ignore[index]
    approved = dispatch_command(
        new_command_envelope(
            surface=Surface.CLI,
            project_root=root,
            command=SessionCommand(type="session.approve_outline", session_id=session_id),
        )
    )
    assert approved.workflow_run_id == started.workflow_run_id

    with pytest.raises(DomainError) as caught:
        dispatch_command(
            new_command_envelope(
                surface=Surface.CLI,
                project_root=root,
                command=SessionCommand(
                    type="session.run",
                    session_id=session_id,
                    provider_name="mock",
                ),
            )
        )
    assert caught.value.code == "budget_exceeded"
    session_data = json.loads((root / "memory" / "sessions" / session_id / "session.json").read_text(encoding="utf-8"))
    assert session_data["workflow_budget"]["max_model_calls"] == 2
    assert session_data["budget_usage"]["model_calls"] == 2
    assert session_data["workflow_run_id"] == started.workflow_run_id


def _budget(
    *,
    max_chapters: int = 5,
    max_model_calls: int = 10,
    max_provider_attempts: int = 20,
) -> WorkflowBudget:
    return WorkflowBudget(
        max_chapters=max_chapters,
        max_model_calls=max_model_calls,
        max_provider_attempts=max_provider_attempts,
        max_auto_revision_rounds=3,
    )


def _workspace_ready(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    proposal = root / "memory" / "canon" / "proposal.json"
    proposal.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    apply_canon_proposal(root, proposal)
    return root


def _run_json_cli(args: list[str]) -> tuple[int, dict[str, object]]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    assert stderr.getvalue() == ""
    return code, json.loads(stdout.getvalue())
