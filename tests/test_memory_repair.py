from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import main
from novel.core.io import load_json_model
from novel.core.memory_repair import MemoryRepairError, apply_memory_repair
from novel.core.schemas import MemoryRepairProposal, TimelineFile
from novel.core.workspace import InitOptions, init_workspace


def test_ask_memory_repair_creates_proposal_without_modifying_timeline(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)
    timeline_path = root / "memory" / "state" / "timeline.json"
    before = timeline_path.read_text(encoding="utf-8")

    code, stdout, stderr = _run_cli(
        [
            "ask",
            "第2章 event_wrong_current 这个事件其实是回忆，不是当前行动",
            "--path",
            str(root),
            "--provider",
            "mock",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "Memory repair proposal:" in stdout
    assert timeline_path.read_text(encoding="utf-8") == before
    proposals = list((root / "memory" / "repairs").glob("repair_*/proposal.json"))
    assert len(proposals) == 1
    proposal = load_json_model(proposals[0], MemoryRepairProposal)
    assert proposal.created_by == "orchestrator"
    assert proposal.operations[0].file == "memory/state/timeline.json"
    assert proposal.operations[0].value == "flashback"
    assert (root / "memory" / "management_events.jsonl").is_file()


def test_ask_memory_repair_apply_updates_timeline_and_writes_event(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)
    code, _, _ = _run_cli(
        [
            "ask",
            "第2章 event_wrong_current 这个事件其实是回忆，不是当前行动",
            "--path",
            str(root),
            "--provider",
            "mock",
        ]
    )
    assert code == 0
    proposal = load_json_model(next((root / "memory" / "repairs").glob("repair_*/proposal.json")), MemoryRepairProposal)

    apply_code, stdout, stderr = _run_cli(
        [
            "ask",
            f"确认应用 {proposal.repair_id}",
            "--path",
            str(root),
            "--provider",
            "mock",
        ]
    )

    assert apply_code == 0
    assert stderr == ""
    assert f"Applied memory repair: {proposal.repair_id}" in stdout
    timeline = load_json_model(root / "memory" / "state" / "timeline.json", TimelineFile)
    assert timeline.events[0].event_role == "flashback"
    assert list((root / "memory" / "state").glob("timeline.json.bak_*"))
    events_text = (root / "memory" / "management_events.jsonl").read_text(encoding="utf-8")
    assert "memory_repair_applied" in events_text


def test_memory_repair_rejects_non_whitelisted_target_without_modifying_project(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)
    repair_dir = root / "memory" / "repairs" / "repair_20260530_010101_000001"
    repair_dir.mkdir(parents=True)
    proposal_path = repair_dir / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "repair_id": "repair_20260530_010101_000001",
                "created_by": "orchestrator",
                "user_request": "bad target",
                "target_files": ["project.yaml"],
                "operations": [
                    {
                        "op": "replace",
                        "file": "project.yaml",
                        "path": "/title",
                        "value": "坏修改",
                        "reason": "should fail",
                    }
                ],
                "risk_level": "high",
                "validation_before": {},
                "notes": [],
                "created_at": "2026-05-30T00:00:00Z",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    before = (root / "project.yaml").read_text(encoding="utf-8")

    try:
        apply_memory_repair(root, proposal_path)
    except MemoryRepairError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("expected memory repair failure")

    assert (root / "project.yaml").read_text(encoding="utf-8") == before
    assert (repair_dir / "apply_log.json").is_file()


def _workspace_with_timeline_event(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="记忆修复测试", root=root))
    timeline_path = root / "memory" / "state" / "timeline.json"
    timeline_path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "event_wrong_current",
                        "chapter": 2,
                        "scene": 1,
                        "in_story_time": "多年前",
                        "event_role": "current_action",
                        "summary": "这个事件实际是回忆。",
                        "reader_visible": True,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
