from __future__ import annotations

import json
from pathlib import Path

from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.command_bus import dispatch_command, new_command_envelope
from novel.core.contracts import (
    SessionCommand,
    SessionStartCommand,
    Surface,
    WorkflowNodeRun,
    WorkflowDecision,
    WorkflowRun,
    default_workflow_budget,
)
from novel.core.io import load_json_model
from novel.core.orchestrator import propose_ask_command
from novel.core.providers import LoggingModelProvider, MockProvider
from novel.core.task_registry import prompt_registry_entry
from novel.core.workflow_runtime import WORKFLOW_DEFINITIONS
from novel.core.workspace import InitOptions, init_workspace


def test_static_workflow_definitions_cover_creation_and_revision_nodes() -> None:
    creation = WORKFLOW_DEFINITIONS["creation_session"]
    revision = WORKFLOW_DEFINITIONS["revision_session"]
    assert [node.name for node in creation.nodes] == [
        "outline",
        "writer",
        "polish",
        "audit",
        "state_update",
        "chapter_memory",
        "acceptance",
    ]
    assert [node.name for node in revision.nodes] == [
        "select",
        "revision",
        "audit",
        "state_update",
        "acceptance",
    ]


def test_command_and_model_nodes_share_trace_and_parentage(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    result = dispatch_command(
        new_command_envelope(
            surface=Surface.CLI,
            project_root=root,
            command=SessionStartCommand(
                user_intent="写第1章",
                chapter_range=[1],
                provider_name="mock",
            ),
        )
    )
    run_dir = root / "runs" / result.workflow_run_id
    run = load_json_model(run_dir / "run.json", WorkflowRun)
    nodes = [load_json_model(path, WorkflowNodeRun) for path in sorted((run_dir / "nodes").glob("node_*.json"))]
    command = next(node for node in nodes if node.node_type == "command")
    model = next(node for node in nodes if node.node_type == "model")
    assert run.status == "completed"
    assert run.root_request_id == result.request_id
    assert result.request_id in run.request_ids
    assert set(run.node_ids) == {node.node_id for node in nodes}
    assert model.parent_node_id == command.node_id
    assert model.parent_request_id == result.request_id
    assert model.session_id == str(result.result["session"]["session_id"])  # type: ignore[index]
    assert model.task_id == "plan"
    assert model.profile_id == "architect"
    assert model.provider == "mock"
    assert model.prompt_template_hash
    assert model.prompt_policy_hash
    assert model.rendered_prompt_hash
    prompt_entry = prompt_registry_entry("plan")
    assert model.prompt_template_hash == prompt_entry.template_hash
    assert model.prompt_policy_hash == prompt_entry.policy_hash
    assert model.input_paths == ["project.yaml"]
    assert model.output_paths[0].startswith("runs/model_io/")
    assert (root / model.output_paths[0]).is_file()
    model_io = json.loads((root / model.output_paths[0]).read_text(encoding="utf-8"))
    assert model_io["workflow_run_id"] == result.workflow_run_id
    assert model_io["surface"] == "cli"
    assert model_io["session_id"] == model.session_id
    assert model_io["parent_request_id"] == result.request_id
    assert model_io["node_id"] == model.node_id
    assert model.budget_after.model_calls == 1
    assert command.input_paths == ["project.yaml"]
    assert command.output_paths


def test_session_commands_resume_same_workflow_trace_across_human_gate(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    started = dispatch_command(
        new_command_envelope(
            surface=Surface.CLI,
            project_root=root,
            command=SessionStartCommand(
                user_intent="写第1章",
                chapter_range=[1],
                provider_name="mock",
            ),
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
    run_dir = root / "runs" / started.workflow_run_id
    run = load_json_model(run_dir / "run.json", WorkflowRun)
    nodes = [load_json_model(path, WorkflowNodeRun) for path in sorted((run_dir / "nodes").glob("node_*.json"))]
    nodes_by_id = {node.node_id: node for node in nodes}
    command_names = [
        nodes_by_id[node_id].name for node_id in run.node_ids if nodes_by_id[node_id].node_type == "command"
    ]
    assert command_names == ["command:session.start", "command:session.approve_outline"]
    assert run.root_command_id == started.command_id
    assert run.root_request_id == started.request_id
    assert run.request_ids == [started.request_id, approved.request_id]
    assert run.session_ids == [session_id]
    approved_command = next(node for node in nodes if node.command_id == approved.command_id)
    assert approved_command.parent_request_id == started.request_id


def test_ask_intent_model_is_nested_in_proposal_trace(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    provider = LoggingModelProvider(
        provider=MockProvider(
            fake_response=json.dumps(
                {
                    "task": "session_start",
                    "reason": "用户明确要求开始创作第一章",
                    "chapter_range": [1],
                    "confidence": 0.95,
                },
                ensure_ascii=False,
            )
        ),
        agent_name="intent_router",
        provider_name="mock",
        model="mock",
        root=root,
    )
    proposed = propose_ask_command(
        root,
        "写第1章",
        provider_name="mock",
        budget=default_workflow_budget(),
        intent_provider=provider,
    )

    run_dir = root / "runs" / proposed.workflow_run_id
    run = load_json_model(run_dir / "run.json", WorkflowRun)
    nodes = {path.stem: load_json_model(path, WorkflowNodeRun) for path in (run_dir / "nodes").glob("node_*.json")}
    proposal_node = next(node for node in nodes.values() if node.name == "proposal:ask")
    model_node = next(node for node in nodes.values() if node.node_type == "model")
    assert model_node.parent_node_id == proposal_node.node_id
    assert model_node.task_id == "intent_router"
    decisions = [
        load_json_model(path, WorkflowDecision)
        for path in (run_dir / "decisions").glob("decision_*.json")
    ]
    assert {decision.name for decision in decisions} == {"ask_intent", "command_proposal"}
    assert set(run.decision_ids) == {decision.decision_id for decision in decisions}
    assert all(decision.request_id == proposed.request_id for decision in decisions)
    assert model_node.profile_id == "clerk"
    assert proposed.budget_usage.model_calls == 1
    assert run.budget_usage.model_calls == 1


def _workspace_ready(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    proposal = root / "memory" / "canon" / "proposal.json"
    proposal.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    apply_canon_proposal(root, proposal)
    return root
