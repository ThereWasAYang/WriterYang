from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import main
from novel.core.io import load_json_model
from novel.core.memory_repair import (
    MemoryRepairError,
    answer_setting_change_clarification,
    apply_memory_repair,
    build_memory_repair_user_prompt,
    suggest_setting_change,
    suggest_setting_change_interactive,
)
from novel.core.schemas import CharactersFile, MemoryChangeClarificationSession, MemoryRepairProposal, TimelineFile, WorldFile
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
    assert proposal.needs_user_confirmation is True
    assert proposal.confidence > 0
    assert proposal.operations[0].file == "memory/state/timeline.json"
    assert proposal.operations[0].value == "flashback"
    assert (root / "memory" / "management_events.jsonl").is_file()


def test_ask_memory_repair_apply_refuses_fallback_natural_language_apply(tmp_path: Path) -> None:
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

    assert apply_code == 1
    assert stdout == ""
    assert "memory-repair apply" in stderr


def test_memory_repair_apply_command_updates_timeline_and_writes_event(tmp_path: Path) -> None:
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
            "memory-repair",
            "apply",
            proposal.repair_id,
            "--path",
            str(root),
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


def test_setting_change_suggest_adds_character_proposal_and_apply(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)

    result = suggest_setting_change(root, "新增人物沈微", provider_name="mock", stage="pre_creation")
    proposal = result.proposal

    assert proposal.change_kind == "setting_change"
    assert "characters" in proposal.domains
    assert proposal.stage == "pre_creation"
    assert proposal.impact is not None
    assert proposal.operations[0].op == "add"
    assert proposal.operations[0].file == "memory/canon/characters.json"

    apply_memory_repair(root, result.proposal_path)
    characters = load_json_model(root / "memory" / "canon" / "characters.json", CharactersFile)
    assert any(character.name == "沈微" for character in characters.characters)


def test_setting_change_modifies_character_summary(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_lin_che", "林澈")

    result = suggest_setting_change(
        root,
        "把 char_lin_che 设定为林澈表面温和但做决定非常谨慎",
        provider_name="mock",
        stage="outline_discussion",
    )

    assert result.proposal.operations[0].path == "/characters/0/reader_visible_summary"
    apply_memory_repair(root, result.proposal_path)
    characters = load_json_model(root / "memory" / "canon" / "characters.json", CharactersFile)
    assert characters.characters[0].reader_visible_summary == "林澈表面温和但做决定非常谨慎"


def test_setting_change_prompt_includes_json_pointer_structure(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_lin_che", "林澈")

    prompt = build_memory_repair_user_prompt(
        root,
        "把 char_lin_che 设定为林澈表面温和但做决定非常谨慎",
        change_kind="setting_change",
    )

    assert "当前文件结构与 JSON Pointer 路径索引" in prompt
    assert "/characters/-" in prompt
    assert "/characters/0/reader_visible_summary" in prompt
    assert "/events/0/event_role" in prompt


def test_setting_change_interactive_can_request_clarification(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_lin_che", "林澈")

    result = suggest_setting_change_interactive(root, "把某个人物改一下", provider_name="mock")

    assert result.status == "needs_clarification"
    assert result.clarification is not None
    assert result.clarification.questions
    assert not list((root / "memory" / "repairs").glob("repair_*/proposal.json"))
    assert (
        root
        / "memory"
        / "repairs"
        / "clarifications"
        / result.clarification.clarification_id
        / "session.json"
    ).is_file()


def test_setting_change_clarification_answer_generates_proposal(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_lin_che", "林澈")
    first = suggest_setting_change_interactive(root, "把某个人物改一下", provider_name="mock")
    assert first.clarification is not None

    result = answer_setting_change_clarification(
        root,
        first.clarification.clarification_id,
        "目标是 char_lin_che，改成林澈表面温和但做决定非常谨慎。",
        provider_name="mock",
    )

    assert result.status == "proposal_ready"
    assert result.proposal_result is not None
    assert result.proposal_result.proposal.operations[0].path == "/characters/0/reader_visible_summary"


def test_setting_change_modifies_world_rule(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)
    (root / "memory" / "canon" / "world.json").write_text(
        json.dumps(
            {
                "world_rules": [
                    {
                        "id": "world_rain_signal",
                        "name": "雨声信号",
                        "description": "雨声只是气氛。",
                        "visibility": "reader_visible",
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = suggest_setting_change(
        root,
        "把 world_rain_signal 规则为雨声会干扰记忆读取",
        provider_name="mock",
    )

    assert result.proposal.operations[0].file == "memory/canon/world.json"
    apply_memory_repair(root, result.proposal_path)
    world = load_json_model(root / "memory" / "canon" / "world.json", WorldFile)
    assert world.world_rules[0].description == "雨声会干扰记忆读取"


def test_setting_change_refuses_deleting_referenced_character(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_ref", "被引用者")
    (root / "memory" / "state" / "current_state.json").write_text(
        json.dumps(
            {
                "story_position": {"latest_chapter": 1},
                "character_states": [
                    {
                        "entity_id": "char_ref",
                        "health": "alive",
                        "last_updated_chapter": 1,
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = suggest_setting_change(root, "删除人物 char_ref", provider_name="mock")

    assert result.proposal.operations == []
    assert any("已拒绝删除" in note for note in result.proposal.notes)


def test_setting_change_allows_deleting_unreferenced_character(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_unused", "未引用者")

    result = suggest_setting_change(root, "删除人物 char_unused", provider_name="mock")

    assert result.proposal.operations[0].op == "remove"
    apply_memory_repair(root, result.proposal_path)
    characters = load_json_model(root / "memory" / "canon" / "characters.json", CharactersFile)
    assert characters.characters == []


def test_setting_change_cli_alias_creates_proposal(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)

    code, stdout, stderr = _run_cli(
        ["setting-change", "suggest", "新增人物沈微", "--path", str(root), "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    assert "Setting change proposal:" in stdout
    proposal = load_json_model(next((root / "memory" / "repairs").glob("repair_*/proposal.json")), MemoryRepairProposal)
    assert proposal.change_kind == "setting_change"


def test_setting_change_cli_can_answer_clarification(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_lin_che", "林澈")

    suggest_code, suggest_stdout, suggest_stderr = _run_cli(
        ["setting-change", "suggest", "把某个人物改一下", "--path", str(root), "--provider", "mock"]
    )
    clarification = load_json_model(
        next((root / "memory" / "repairs" / "clarifications").glob("clarify_*/session.json")),
        MemoryChangeClarificationSession,
    )
    answer_code, answer_stdout, answer_stderr = _run_cli(
        [
            "setting-change",
            "answer",
            clarification.clarification_id,
            "--answer",
            "目标是 char_lin_che，改成林澈表面温和但做决定非常谨慎。",
            "--path",
            str(root),
            "--provider",
            "mock",
        ]
    )

    assert suggest_code == 0
    assert suggest_stderr == ""
    assert "needs clarification" in suggest_stdout
    assert answer_code == 0
    assert answer_stderr == ""
    assert "Setting change proposal:" in answer_stdout


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


def _workspace_with_character(tmp_path: Path, character_id: str, name: str) -> Path:
    root = _workspace_with_timeline_event(tmp_path)
    (root / "memory" / "canon" / "characters.json").write_text(
        json.dumps(
            {
                "characters": [
                    {
                        "id": character_id,
                        "name": name,
                        "role": "protagonist",
                        "reader_visible_summary": f"{name}的旧设定。",
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
