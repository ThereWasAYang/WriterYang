from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

import pytest

from novel.cli import main
from novel.core.io import load_json_model
from novel.core.memory_repair import (
    MemoryRepairError,
    _memory_repair_user_prompt,
    answer_setting_change_clarification,
    apply_memory_repair,
    generate_memory_change_batch_plan,
    generate_memory_change_clarification_decision,
    parse_memory_repair_decision,
    suggest_memory_repair,
    suggest_setting_change,
    suggest_setting_change_interactive,
)
from novel.core.prompts import load_prompt_template
from novel.core.providers import MockProvider
from novel.core.schemas import CharactersFile, MemoryChangeClarificationSession, MemoryRepairDecision, MemoryRepairProposal, TimelineFile, WorldFile
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


def test_memory_change_clarification_repairs_invalid_json_once(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_lin_che", "林澈")
    repaired = {
        "status": "ready",
        "questions": [],
        "confidence": 0.8,
        "assumptions": ["按新增设定处理。"],
        "notes": [],
    }
    provider = MockProvider(fake_response=["需要问用户吗？", json.dumps(repaired, ensure_ascii=False)])

    decision = generate_memory_change_clarification_decision(
        root,
        "新增一名配角。",
        provider=provider,
        stage="pre_creation",
    )

    assert decision.status == "ready"
    assert decision.assumptions == ["按新增设定处理。"]
    assert len(provider.requests) == 2


def test_memory_change_batch_plan_repairs_invalid_json_once(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_lin_che", "林澈")
    repaired = {
        "change_kind": "setting_change",
        "stage": "pre_creation",
        "batches": [
            {
                "batch_id": "characters",
                "instruction": "新增一名配角。",
                "target_files": ["memory/canon/characters.json"],
                "domains": ["characters"],
                "reason": "新增人物应写入 characters。",
            }
        ],
        "confidence": 0.82,
        "assumptions": [],
        "notes": [],
    }
    provider = MockProvider(fake_response=["不是 JSON", json.dumps(repaired, ensure_ascii=False)])

    plan = generate_memory_change_batch_plan(
        root,
        "新增一名配角。",
        provider=provider,
        stage="pre_creation",
    )

    assert plan.batches[0].target_files == ["memory/canon/characters.json"]
    assert len(provider.requests) == 2


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


def test_setting_change_suggest_preflights_operation_value_schema(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)
    decision = MemoryRepairDecision(
        change_kind="setting_change",
        target_files=["memory/canon/characters.json"],
        operations=[
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_bad_schema",
                    "name": "坏字段人物",
                    "role": "配角",
                    "reader_visible_summary": "字段类型不符合 schema。",
                    "appearance": "字符串外貌",
                    "personality": "字符串性格",
                    "abilities": ["字符串能力"],
                    "secrets": ["字符串秘密"],
                },
                "reason": "测试非法嵌套字段。",
            }
        ],
        confidence=0.7,
    )

    with pytest.raises(MemoryRepairError) as excinfo:
        suggest_memory_repair(root, "新增坏字段人物", decision=decision, change_kind="setting_change")

    message = str(excinfo.value)
    assert "target schema preflight" in message
    assert "memory/canon/characters.json" in message
    assert "abilities.0" in message
    assert not list((root / "memory" / "repairs").glob("repair_*/proposal.json"))


def test_setting_change_preflight_reports_all_invalid_target_files(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)
    decision = MemoryRepairDecision(
        change_kind="setting_change",
        target_files=[
            "memory/canon/characters.json",
            "memory/canon/locations.json",
            "memory/canon/world.json",
            "memory/canon/hidden_truths.json",
            "memory/canon/foreshadowing.json",
            "memory/canon/items.json",
        ],
        operations=[
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_bad",
                    "name": "坏人物",
                    "role": "配角",
                    "reader_visible_summary": "坏人物。",
                    "abilities": ["字符串能力"],
                },
                "reason": "非法 abilities。",
            },
            {
                "op": "add",
                "file": "memory/canon/locations.json",
                "path": "/locations/-",
                "value": {
                    "id": "loc_bad",
                    "name": "坏地点",
                    "type": "village",
                    "reader_visible_summary": "坏地点。",
                    "rules": ["字符串规则"],
                },
                "reason": "非法 rules。",
            },
            {
                "op": "add",
                "file": "memory/canon/world.json",
                "path": "/world_rules/-",
                "value": {
                    "id": "world_bad",
                    "name": "坏世界规则",
                    "description": "坏世界规则。",
                    "visibility": "visible",
                },
                "reason": "非法 visibility。",
            },
            {
                "op": "add",
                "file": "memory/canon/hidden_truths.json",
                "path": "/hidden_truths/-",
                "value": {
                    "id": "truth_bad",
                    "title": "坏隐藏真相",
                    "description": "坏隐藏真相。",
                    "visibility": "hidden",
                    "importance": "major",
                    "planned_reveal": "后期",
                },
                "reason": "非法 importance/planned_reveal。",
            },
            {
                "op": "add",
                "file": "memory/canon/foreshadowing.json",
                "path": "/foreshadowing_threads/-",
                "value": {
                    "id": "thread_bad",
                    "type": "clue",
                    "title": "坏伏笔",
                    "introduced_in_chapter": "开篇",
                    "description": "坏伏笔。",
                    "status": "active",
                    "importance": "medium",
                    "planned_payoff": "后期揭晓",
                },
                "reason": "非法 introduced_in_chapter/planned_payoff。",
            },
            {
                "op": "add",
                "file": "memory/canon/items.json",
                "path": "/items/-",
                "value": {
                    "id": "item_bad",
                    "name": "坏物品",
                    "type": "weapon",
                    "reader_visible_summary": "坏物品。",
                    "special_properties": ["字符串属性"],
                },
                "reason": "非法 special_properties。",
            },
        ],
        confidence=0.7,
    )

    with pytest.raises(MemoryRepairError) as excinfo:
        suggest_memory_repair(root, "新增多文件坏设定", decision=decision, change_kind="setting_change")

    message = str(excinfo.value)
    for rel_path in decision.target_files:
        assert rel_path in message
    assert "major" in message
    assert "visible" in message


def test_setting_change_preflights_character_role_semantics(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)
    decision = MemoryRepairDecision(
        change_kind="setting_change",
        target_files=["memory/canon/characters.json"],
        operations=[
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_xie_zheyu",
                    "name": "谢蛰雨",
                    "role": "谢家长女",
                    "reader_visible_summary": "谢蛰雨是谢家长女。",
                    "tags": ["谢家"],
                },
                "reason": "测试 role 字段语义错位。",
            }
        ],
        confidence=0.7,
    )

    with pytest.raises(MemoryRepairError) as excinfo:
        suggest_memory_repair(root, "新增主要人物谢蛰雨，谢家长女", decision=decision, change_kind="setting_change")

    message = str(excinfo.value)
    assert "Character.role semantic preflight" in message
    assert "谢家长女" in message
    assert not list((root / "memory" / "repairs").glob("repair_*/proposal.json"))


def test_setting_change_role_semantic_retry_repairs_tags(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)
    first = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/characters.json"],
        "operations": [
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_xie_zheyu",
                    "name": "谢蛰雨",
                    "role": "谢家长女",
                    "reader_visible_summary": "谢蛰雨是谢家长女，出身栖霞山谢氏。",
                    "tags": ["谢家"],
                },
                "reason": "首次模型把身份短语放入 role。",
            }
        ],
        "confidence": 0.8,
    }
    repaired = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/characters.json"],
        "operations": [
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_xie_zheyu",
                    "name": "谢蛰雨",
                    "role": "主要人物",
                    "reader_visible_summary": "谢蛰雨是谢家长女，出身栖霞山谢氏。",
                    "tags": ["谢家", "谢家长女"],
                    "abilities": [],
                    "secrets": [],
                },
                "reason": "修复 Character.role 为叙事角色，并把身份短语移入 tags。",
            }
        ],
        "confidence": 0.8,
    }
    provider = MockProvider(
        fake_response=[
            json.dumps(first, ensure_ascii=False),
            json.dumps(repaired, ensure_ascii=False),
        ]
    )

    result = suggest_memory_repair(
        root,
        "新增主要人物谢蛰雨，女性，谢家长女。",
        provider=provider,
        change_kind="setting_change",
    )

    assert len(provider.requests) == 2
    assert "Character.role" in provider.requests[1].user_prompt
    assert "semantic preflight" in provider.requests[1].user_prompt
    value = result.proposal.operations[0].value
    assert isinstance(value, dict)
    assert value["role"] == "主要人物"
    assert "谢家长女" in value["tags"]
    apply_memory_repair(root, result.proposal_path)
    characters = load_json_model(root / "memory" / "canon" / "characters.json", CharactersFile)
    assert any(character.name == "谢蛰雨" and character.role == "主要人物" for character in characters.characters)


def test_setting_change_local_repair_adds_missing_identity_tags_without_provider_retry(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)
    first = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/characters.json"],
        "operations": [
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_xie_huaiyun",
                    "name": "谢怀云",
                    "role": "主要人物",
                    "reader_visible_summary": "谢怀云是武当俗家弟子。",
                    "tags": ["武当俗家弟子"],
                },
                "reason": "首次模型输出已有叙事角色，但缺 exact identity tag。",
            }
        ],
        "confidence": 0.8,
    }
    provider = MockProvider(fake_response=json.dumps(first, ensure_ascii=False))

    result = suggest_memory_repair(
        root,
        "新增主要人物谢怀云，设定为武当俗家弟子。",
        provider=provider,
        change_kind="setting_change",
    )

    assert len(provider.requests) == 1
    value = result.proposal.operations[0].value
    assert isinstance(value, dict)
    assert value["role"] == "主要人物"
    assert "武当俗家弟子" in value["tags"]
    assert "俗家弟子" in value["tags"]
    assert any("已本地补齐 Character.tags" in note for note in result.proposal.notes)


def test_setting_change_suggest_batches_and_merges_single_proposal(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)
    batch_plan = {
        "change_kind": "setting_change",
        "stage": "pre_creation",
        "batches": [
            {
                "batch_id": "batch_characters",
                "instruction": "新增人物沈微。",
                "target_files": ["memory/canon/characters.json"],
                "domains": ["characters"],
                "reason": "人物设定独立生成。",
            },
            {
                "batch_id": "batch_items",
                "instruction": "新增物品青铜铃。",
                "target_files": ["memory/canon/items.json"],
                "domains": ["items"],
                "reason": "物品设定独立生成。",
            },
        ],
        "confidence": 0.9,
    }
    character_decision = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/characters.json"],
        "domains": ["characters"],
        "operations": [
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_shen_wei",
                    "name": "沈微",
                    "role": "主要人物",
                    "reader_visible_summary": "沈微是新登场人物。",
                    "tags": ["新人物"],
                },
                "reason": "新增人物沈微。",
            }
        ],
        "confidence": 0.8,
    }
    item_decision = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/items.json"],
        "domains": ["items"],
        "operations": [
            {
                "op": "add",
                "file": "memory/canon/items.json",
                "path": "/items/-",
                "value": {
                    "id": "item_bronze_bell",
                    "name": "青铜铃",
                    "type": "信物",
                    "reader_visible_summary": "青铜铃是一枚旧信物。",
                    "special_properties": [],
                    "tags": ["信物"],
                },
                "reason": "新增物品青铜铃。",
            }
        ],
        "confidence": 0.85,
    }
    provider = MockProvider(
        fake_response=[
            json.dumps(batch_plan, ensure_ascii=False),
            json.dumps(character_decision, ensure_ascii=False),
            json.dumps(item_decision, ensure_ascii=False),
        ]
    )

    result = suggest_setting_change(
        root,
        "新增人物沈微，并新增物品青铜铃。",
        provider=provider,
        stage="pre_creation",
    )

    assert len(provider.requests) == 3
    assert "MemoryChangeBatchPlan" in provider.requests[0].json_schema_name
    assert "允许 target_files：\n- memory/canon/characters.json\n\n当前文件结构" in provider.requests[1].user_prompt
    assert "允许 target_files：\n- memory/canon/items.json\n\n当前文件结构" in provider.requests[2].user_prompt
    proposal = result.proposal
    assert len(proposal.operations) == 2
    assert proposal.domains == ["characters", "items"]
    assert any("已按 2 个批次生成设定变更建议" in note for note in proposal.notes)
    assert proposal.operations[0].file == "memory/canon/characters.json"
    assert proposal.operations[1].file == "memory/canon/items.json"


def test_setting_change_repairs_hidden_truth_leak_before_apply(tmp_path: Path) -> None:
    root = _workspace_with_hidden_truth_and_thread(tmp_path)
    first = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/characters.json"],
        "operations": [
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_bai_shuanghan",
                    "name": "白霜瀚",
                    "role": "主要人物",
                    "reader_visible_summary": "白霜瀚知道桃花源村的旧隐藏真相。",
                    "private_author_notes": None,
                    "tags": ["江湖散人"],
                },
                "reason": "首次模型把隐藏真相写进 reader-visible summary。",
            }
        ],
        "confidence": 0.8,
    }
    repaired = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/characters.json"],
        "operations": [
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_bai_shuanghan",
                    "name": "白霜瀚",
                    "role": "主要人物",
                    "reader_visible_summary": "白霜瀚正在调查一桩旧事。",
                    "private_author_notes": "白霜瀚知道桃花源村的旧隐藏真相。",
                    "tags": ["江湖散人"],
                },
                "reason": "把隐藏信息移入 private_author_notes。",
            }
        ],
        "confidence": 0.8,
    }
    provider = MockProvider(
        fake_response=[
            json.dumps(first, ensure_ascii=False),
            json.dumps(repaired, ensure_ascii=False),
        ]
    )

    result = suggest_memory_repair(
        root,
        "新增主要人物白霜瀚，隐藏知道桃花源村旧真相。",
        provider=provider,
        change_kind="setting_change",
    )

    assert len(provider.requests) == 2
    assert "hidden truth" in provider.requests[1].user_prompt
    value = result.proposal.operations[0].value
    assert isinstance(value, dict)
    assert "旧隐藏真相" not in value["reader_visible_summary"]
    assert "旧隐藏真相" in value["private_author_notes"]
    apply_memory_repair(root, result.proposal_path)


def test_setting_change_apply_rejects_existing_bad_character_role_proposal(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)
    characters_path = root / "memory" / "canon" / "characters.json"
    before = characters_path.read_text(encoding="utf-8")
    repair_id = "repair_20260606_020202_000001"
    repair_dir = root / "memory" / "repairs" / repair_id
    repair_dir.mkdir(parents=True)
    proposal_path = repair_dir / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "repair_id": repair_id,
                "created_by": "orchestrator",
                "change_kind": "setting_change",
                "user_request": "新增主要人物谢蛰雨，谢家长女。",
                "target_files": ["memory/canon/characters.json"],
                "operations": [
                    {
                        "op": "add",
                        "file": "memory/canon/characters.json",
                        "path": "/characters/-",
                        "value": {
                            "id": "char_xie_zheyu",
                            "name": "谢蛰雨",
                            "role": "谢家长女",
                            "reader_visible_summary": "谢蛰雨是谢家长女。",
                            "tags": ["谢家"],
                        },
                        "reason": "测试 apply 语义 preflight。",
                    }
                ],
                "risk_level": "medium",
                "validation_before": {},
                "notes": [],
                "created_at": "2026-06-06T00:00:00Z",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(MemoryRepairError) as excinfo:
        apply_memory_repair(root, proposal_path)

    assert "Character.role semantic preflight" in str(excinfo.value)
    assert characters_path.read_text(encoding="utf-8") == before
    apply_log = json.loads((repair_dir / "apply_log.json").read_text(encoding="utf-8"))
    assert apply_log["status"] == "failed"
    assert apply_log["backups"] == []


def test_memory_repair_apply_rolls_back_all_touched_files_after_project_validation_failure(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_lin_che", "林澈")
    characters_path = root / "memory" / "canon" / "characters.json"
    world_path = root / "memory" / "canon" / "world.json"
    before_characters = characters_path.read_text(encoding="utf-8")
    before_world = world_path.read_text(encoding="utf-8")
    repair_id = "repair_20260606_050505_000001"
    repair_dir = root / "memory" / "repairs" / repair_id
    repair_dir.mkdir(parents=True)
    proposal_path = repair_dir / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "repair_id": repair_id,
                "created_by": "orchestrator",
                "change_kind": "setting_change",
                "user_request": "测试多文件回滚。",
                "target_files": ["memory/canon/characters.json", "memory/canon/world.json"],
                "operations": [
                    {
                        "op": "replace",
                        "file": "memory/canon/characters.json",
                        "path": "/characters/0/reader_visible_summary",
                        "value": "林澈的新设定。",
                        "reason": "先写入一个有效文件，确保后续失败需要恢复。",
                    },
                    {
                        "op": "replace",
                        "file": "memory/canon/world.json",
                        "path": "/schema_version",
                        "value": 999,
                        "reason": "触发项目级 schema_version validation 失败。",
                    },
                ],
                "risk_level": "medium",
                "validation_before": {},
                "notes": [],
                "created_at": "2026-06-06T00:00:00Z",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(MemoryRepairError) as excinfo:
        apply_memory_repair(root, proposal_path)

    assert "validation errors" in str(excinfo.value)
    assert characters_path.read_text(encoding="utf-8") == before_characters
    assert world_path.read_text(encoding="utf-8") == before_world
    apply_log = json.loads((repair_dir / "apply_log.json").read_text(encoding="utf-8"))
    assert apply_log["status"] == "rolled_back"
    assert apply_log["target_files"] == ["memory/canon/characters.json", "memory/canon/world.json"]
    assert len(apply_log["backups"]) == 2
    assert all((root / backup).is_file() for backup in apply_log["backups"])
    app_log = root / "runs" / "app.log"
    assert app_log.is_file()
    assert "memory_repair_apply_rolled_back" in app_log.read_text(encoding="utf-8")


def test_memory_repair_apply_missing_allowed_file_does_not_create_residue(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_lin_che", "林澈")
    missing_path = root / "memory" / "canon" / "items.json"
    missing_path.unlink()
    repair_id = "repair_20260606_050505_000002"
    repair_dir = root / "memory" / "repairs" / repair_id
    repair_dir.mkdir(parents=True)
    proposal_path = repair_dir / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "repair_id": repair_id,
                "created_by": "orchestrator",
                "change_kind": "setting_change",
                "user_request": "新增一个物品。",
                "target_files": ["memory/canon/items.json"],
                "operations": [
                    {
                        "op": "add",
                        "file": "memory/canon/items.json",
                        "path": "/items/-",
                        "value": {
                            "id": "item_test",
                            "name": "测试物品",
                            "type": "clue",
                            "reader_visible_summary": "测试物品。",
                        },
                        "reason": "验证缺失文件失败不会留下新文件。",
                    }
                ],
                "risk_level": "medium",
                "validation_before": {},
                "notes": [],
                "created_at": "2026-06-06T00:00:00Z",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(MemoryRepairError):
        apply_memory_repair(root, proposal_path)

    assert not missing_path.exists()
    apply_log = json.loads((repair_dir / "apply_log.json").read_text(encoding="utf-8"))
    assert apply_log["status"] == "failed"
    assert apply_log["backups"] == []


def test_setting_change_allows_narrative_role_with_identity_tags(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)
    decision = MemoryRepairDecision(
        change_kind="setting_change",
        target_files=["memory/canon/characters.json"],
        operations=[
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_xie_zheyu",
                    "name": "谢蛰雨",
                    "role": "主要人物",
                    "reader_visible_summary": "谢蛰雨是谢家长女。",
                    "tags": ["谢家", "谢家长女"],
                },
                "reason": "新增合法叙事角色人物。",
            }
        ],
        confidence=0.7,
    )

    result = suggest_memory_repair(root, "新增主要人物谢蛰雨，谢家长女", decision=decision, change_kind="setting_change")
    apply_memory_repair(root, result.proposal_path)
    characters = load_json_model(root / "memory" / "canon" / "characters.json", CharactersFile)
    assert any(character.name == "谢蛰雨" and "谢家长女" in character.tags for character in characters.characters)


def test_setting_change_target_schema_retry_repairs_invalid_model_value(tmp_path: Path) -> None:
    root = _workspace_with_timeline_event(tmp_path)
    first = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/characters.json"],
        "operations": [
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_retry",
                    "name": "重试人物",
                    "role": "配角",
                    "reader_visible_summary": "需要修复字段类型。",
                    "abilities": ["剑法"],
                },
                "reason": "首次模型输出非法 abilities。",
            }
        ],
        "confidence": 0.8,
    }
    repaired = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/characters.json"],
        "operations": [
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_retry",
                    "name": "重试人物",
                    "role": "配角",
                    "reader_visible_summary": "需要修复字段类型。",
                    "appearance": {"summary": "未详述"},
                    "personality": {"summary": "谨慎"},
                    "abilities": [{"name": "剑法", "description": "擅长剑法。"}],
                    "secrets": [
                        {
                            "id": "secret_retry",
                            "visibility": "hidden",
                            "description": "有隐藏身份。",
                            "planned_reveal": None,
                        }
                    ],
                },
                "reason": "修复为目标 Character schema。",
            }
        ],
        "confidence": 0.8,
    }
    provider = MockProvider(
        fake_response=[
            json.dumps(first, ensure_ascii=False),
            json.dumps(repaired, ensure_ascii=False),
        ]
    )

    result = suggest_memory_repair(
        root,
        "新增重试人物",
        provider=provider,
        change_kind="setting_change",
    )

    assert len(provider.requests) == 2
    assert "目标 schema preflight 错误" in provider.requests[1].user_prompt
    assert result.proposal.operations[0].value["abilities"][0]["description"] == "擅长剑法。"  # type: ignore[index]
    apply_memory_repair(root, result.proposal_path)
    characters = load_json_model(root / "memory" / "canon" / "characters.json", CharactersFile)
    assert any(character.id == "char_retry" for character in characters.characters)


def test_setting_change_target_schema_retries_location_description_path(tmp_path: Path) -> None:
    root = _workspace_with_location(tmp_path)
    target_summary = "桃花源村隐藏在山中，由秦朝避乱者组建，有奇门遁甲大阵守护。"
    first = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/locations.json"],
        "operations": [
            {
                "op": "replace",
                "file": "memory/canon/locations.json",
                "path": "/locations/0/reader_visible_summary",
                "reason": "首次模型漏掉 value。",
            }
        ],
        "confidence": 0.8,
    }
    first_repair = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/locations.json"],
        "operations": [
            {
                "op": "replace",
                "file": "memory/canon/locations.json",
                "path": "/locations/0/description",
                "value": target_summary,
                "reason": "第一次 repair 误用不存在的 Location.description。",
            }
        ],
        "confidence": 0.8,
    }
    second_repair = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/locations.json"],
        "operations": [
            {
                "op": "replace",
                "file": "memory/canon/locations.json",
                "path": "/locations/0/reader_visible_summary",
                "value": target_summary,
                "reason": "改为 Location 的合法公开描述字段。",
            }
        ],
        "confidence": 0.8,
    }
    provider = MockProvider(
        fake_response=[
            json.dumps(first, ensure_ascii=False),
            json.dumps(first_repair, ensure_ascii=False),
            json.dumps(second_repair, ensure_ascii=False),
        ]
    )

    result = suggest_memory_repair(
        root,
        "修改桃花源村地点描述",
        provider=provider,
        change_kind="setting_change",
    )

    assert len(provider.requests) == 3
    assert "replace path does not exist" in provider.requests[1].user_prompt
    assert "Location 顶层没有 description 字段" in provider.requests[1].user_prompt
    assert "replace path does not exist: /locations/0/description" in provider.requests[2].user_prompt
    assert "Location has no top-level description field" in provider.requests[2].user_prompt
    assert result.proposal.operations[0].path == "/locations/0/reader_visible_summary"
    apply_memory_repair(root, result.proposal_path)
    locations = json.loads((root / "memory" / "canon" / "locations.json").read_text(encoding="utf-8"))
    assert locations["locations"][0]["reader_visible_summary"] == target_summary
    assert "description" not in locations["locations"][0]


def test_setting_change_repair_restores_regressed_duplicate_context_adds(tmp_path: Path) -> None:
    root = _workspace_with_hidden_truth_and_thread(tmp_path)
    first = {
        "change_kind": "setting_change",
        "target_files": [
            "memory/canon/characters.json",
            "memory/canon/hidden_truths.json",
            "memory/canon/foreshadowing.json",
        ],
        "operations": [
            {
                "op": "replace",
                "file": "memory/canon/hidden_truths.json",
                "path": "/hidden_truths/0",
                "reason": "首次模型漏掉 hidden truth value。",
            }
        ],
        "confidence": 0.8,
    }
    first_repair = {
        "change_kind": "setting_change",
        "target_files": [
            "memory/canon/characters.json",
            "memory/canon/hidden_truths.json",
            "memory/canon/foreshadowing.json",
        ],
        "operations": [
            {
                "op": "replace",
                "file": "memory/canon/characters.json",
                "path": "/characters/0",
                "value": {
                    "id": "char_xie_huaiyun",
                    "name": "谢怀云",
                    "role": "谢家长女",
                    "reader_visible_summary": "谢怀云是武当俗家弟子。",
                    "tags": ["谢家长女"],
                },
                "reason": "第一次 repair 仍含非法 Character.role。",
            },
            {
                "op": "replace",
                "file": "memory/canon/hidden_truths.json",
                "path": "/hidden_truths/0",
                "value": _hidden_truth_value("桃花源村由秦朝避乱者组建，谢家每代仅两人知晓。"),
                "reason": "正确更新已有隐藏真相。",
            },
            {
                "op": "replace",
                "file": "memory/canon/foreshadowing.json",
                "path": "/foreshadowing_threads/0",
                "value": _foreshadowing_value("第一章只留下桃花源村相关传闻。"),
                "reason": "正确更新已有伏笔。",
            },
        ],
        "confidence": 0.8,
    }
    second_repair = {
        "change_kind": "setting_change",
        "target_files": [
            "memory/canon/characters.json",
            "memory/canon/hidden_truths.json",
            "memory/canon/foreshadowing.json",
        ],
        "operations": [
            {
                "op": "replace",
                "file": "memory/canon/characters.json",
                "path": "/characters/0",
                "value": {
                    "id": "char_xie_huaiyun",
                    "name": "谢怀云",
                    "role": "主要人物",
                    "reader_visible_summary": "谢怀云是武当俗家弟子。",
                    "tags": ["俗家弟子"],
                },
                "reason": "第二次 repair 修复角色 tags。",
            },
            {
                "op": "add",
                "file": "memory/canon/hidden_truths.json",
                "path": "/hidden_truths/-",
                "value": _hidden_truth_value("桃花源村由秦朝避乱者组建，谢家每代仅两人知晓。"),
                "reason": "第二次 repair 错误退化为重复新增隐藏真相。",
            },
            {
                "op": "add",
                "file": "memory/canon/foreshadowing.json",
                "path": "/foreshadowing_threads/-",
                "value": _foreshadowing_value("第一章只留下桃花源村相关传闻。"),
                "reason": "第二次 repair 错误退化为重复新增伏笔。",
            },
        ],
        "confidence": 0.8,
    }
    provider = MockProvider(
        fake_response=[
            json.dumps(first, ensure_ascii=False),
            json.dumps(first_repair, ensure_ascii=False),
            json.dumps(second_repair, ensure_ascii=False),
        ]
    )

    result = suggest_memory_repair(
        root,
        "修改已有桃花源村隐藏真相和伏笔",
        provider=provider,
        change_kind="setting_change",
    )

    assert len(provider.requests) == 3
    assert "只修改下方 preflight 错误直接涉及的 operation" in provider.requests[1].user_prompt
    assert "replace operation must include value" in provider.requests[1].user_prompt
    assert "Character.role semantic preflight" in provider.requests[2].user_prompt
    operations = result.proposal.operations
    assert not any(operation.op == "add" and operation.file == "memory/canon/hidden_truths.json" for operation in operations)
    assert not any(operation.op == "add" and operation.file == "memory/canon/foreshadowing.json" for operation in operations)
    assert any(operation.path == "/hidden_truths/0" for operation in operations)
    assert any(operation.path == "/foreshadowing_threads/0" for operation in operations)
    assert any("已还原 target-schema repair 退化的重复新增操作" in note for note in result.proposal.notes)
    apply_memory_repair(root, result.proposal_path)
    hidden_truths = json.loads((root / "memory" / "canon" / "hidden_truths.json").read_text(encoding="utf-8"))
    foreshadowing = json.loads((root / "memory" / "canon" / "foreshadowing.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in hidden_truths["hidden_truths"]].count("truth_taohuayuan_village") == 1
    assert [item["id"] for item in foreshadowing["foreshadowing_threads"]].count("thread_taohuayuan") == 1


def test_setting_change_rejects_duplicate_existing_hidden_truth_add(tmp_path: Path) -> None:
    root = _workspace_with_hidden_truth_and_thread(tmp_path)
    decision = MemoryRepairDecision(
        change_kind="setting_change",
        target_files=["memory/canon/hidden_truths.json"],
        operations=[
            {
                "op": "add",
                "file": "memory/canon/hidden_truths.json",
                "path": "/hidden_truths/-",
                "value": _hidden_truth_value("重复新增桃花源村真相。"),
                "reason": "测试重复 hidden truth add。",
            }
        ],
        confidence=0.7,
    )

    with pytest.raises(MemoryRepairError) as excinfo:
        suggest_memory_repair(root, "修改已有桃花源村隐藏真相", decision=decision, change_kind="setting_change")

    assert "add would duplicate existing hidden truth id: truth_taohuayuan_village" in str(excinfo.value)
    assert not list((root / "memory" / "repairs").glob("repair_*/proposal.json"))


def test_setting_change_preflights_missing_add_replace_value_contract(tmp_path: Path) -> None:
    root = _workspace_with_hidden_truth_and_thread(tmp_path)
    decision = MemoryRepairDecision(
        change_kind="setting_change",
        target_files=["memory/canon/hidden_truths.json"],
        operations=[
            {
                "op": "replace",
                "file": "memory/canon/hidden_truths.json",
                "path": "/hidden_truths/0/title",
                "reason": "测试缺少 value。",
            }
        ],
        confidence=0.7,
    )

    with pytest.raises(MemoryRepairError) as excinfo:
        suggest_memory_repair(root, "修改已有桃花源村隐藏真相标题", decision=decision, change_kind="setting_change")

    message = str(excinfo.value)
    assert "replace operation must include value" in message
    assert "schema validation failed" not in message


def test_setting_change_rejects_location_top_level_description_path(tmp_path: Path) -> None:
    root = _workspace_with_location(tmp_path)
    decision = MemoryRepairDecision(
        change_kind="setting_change",
        target_files=["memory/canon/locations.json"],
        operations=[
            {
                "op": "replace",
                "file": "memory/canon/locations.json",
                "path": "/locations/0/description",
                "value": "桃花源村隐藏在山中。",
                "reason": "测试非法 Location.description path。",
            }
        ],
        confidence=0.7,
    )

    with pytest.raises(MemoryRepairError) as excinfo:
        suggest_memory_repair(root, "修改桃花源村地点描述", decision=decision, change_kind="setting_change")

    message = str(excinfo.value)
    assert "Location has no top-level description field" in message
    assert "/locations/0/reader_visible_summary" in message
    assert "/locations/0/private_author_notes" in message
    assert "/locations/0/rules" in message
    assert not list((root / "memory" / "repairs").glob("repair_*/proposal.json"))


def test_setting_change_apply_rejects_location_description_path_without_backup(tmp_path: Path) -> None:
    root = _workspace_with_location(tmp_path)
    locations_path = root / "memory" / "canon" / "locations.json"
    before = locations_path.read_text(encoding="utf-8")
    repair_id = "repair_20260606_040404_000001"
    repair_dir = root / "memory" / "repairs" / repair_id
    repair_dir.mkdir(parents=True)
    proposal_path = repair_dir / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "repair_id": repair_id,
                "created_by": "orchestrator",
                "change_kind": "setting_change",
                "user_request": "修改桃花源村地点描述",
                "target_files": ["memory/canon/locations.json"],
                "operations": [
                    {
                        "op": "replace",
                        "file": "memory/canon/locations.json",
                        "path": "/locations/0/description",
                        "value": "桃花源村隐藏在山中。",
                        "reason": "测试 apply 阶段非法 Location.description path。",
                    }
                ],
                "risk_level": "medium",
                "validation_before": {},
                "notes": [],
                "created_at": "2026-06-06T00:00:00Z",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(MemoryRepairError) as excinfo:
        apply_memory_repair(root, proposal_path)

    assert "Location has no top-level description field" in str(excinfo.value)
    assert locations_path.read_text(encoding="utf-8") == before
    apply_log = json.loads((repair_dir / "apply_log.json").read_text(encoding="utf-8"))
    assert apply_log["status"] == "failed"
    assert apply_log["backups"] == []


def test_setting_change_prompt_includes_lightweight_paths_after_first_twenty(tmp_path: Path) -> None:
    root = _workspace_with_twenty_one_characters(tmp_path)

    prompt = _memory_repair_user_prompt(
        root,
        "把白霜瀚开篇设定改成暗中调查桃花源旧族。",
        change_kind="setting_change",
    )

    assert "additional existing id/path index" in prompt
    assert "existing[20]: id=char_bai_shuanghan; name/title=白霜瀚; path=/characters/20" in prompt


def test_setting_change_suggest_rejects_duplicate_existing_character_add(tmp_path: Path) -> None:
    root = _workspace_with_twenty_one_characters(tmp_path)
    decision = MemoryRepairDecision(
        change_kind="setting_change",
        target_files=["memory/canon/characters.json"],
        operations=[
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_bai_shuanghan",
                    "name": "白霜瀚",
                    "role": "主要人物",
                    "reader_visible_summary": "白霜瀚开篇暗中调查桃花源旧族。",
                },
                "reason": "错误地把已有人物当成新增人物。",
            }
        ],
        confidence=0.7,
    )

    with pytest.raises(MemoryRepairError) as excinfo:
        suggest_memory_repair(root, "修改白霜瀚开篇设定", decision=decision, change_kind="setting_change")

    message = str(excinfo.value)
    assert "add would duplicate existing character id: char_bai_shuanghan" in message
    assert "/characters/20" in message
    assert "duplicate character id: char_bai_shuanghan" in message
    assert not list((root / "memory" / "repairs").glob("repair_*/proposal.json"))


def test_setting_change_duplicate_add_retry_can_replace_existing_character(tmp_path: Path) -> None:
    root = _workspace_with_twenty_one_characters(tmp_path)
    target_summary = "白霜瀚开篇伪装成云游书生，暗中调查桃花源旧族线索。"
    first = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/characters.json"],
        "operations": [
            {
                "op": "add",
                "file": "memory/canon/characters.json",
                "path": "/characters/-",
                "value": {
                    "id": "char_bai_shuanghan",
                    "name": "白霜瀚",
                    "role": "主要人物",
                    "reader_visible_summary": target_summary,
                },
                "reason": "首次模型误把已有人物当成新增人物。",
            }
        ],
        "confidence": 0.8,
    }
    repaired = {
        "change_kind": "setting_change",
        "target_files": ["memory/canon/characters.json"],
        "operations": [
            {
                "op": "replace",
                "file": "memory/canon/characters.json",
                "path": "/characters/20/reader_visible_summary",
                "value": target_summary,
                "reason": "白霜瀚已存在，改为字段级 replace。",
            }
        ],
        "confidence": 0.8,
    }
    provider = MockProvider(
        fake_response=[
            json.dumps(first, ensure_ascii=False),
            json.dumps(repaired, ensure_ascii=False),
        ]
    )

    result = suggest_memory_repair(
        root,
        "修改白霜瀚开篇设定",
        provider=provider,
        change_kind="setting_change",
    )

    assert len(provider.requests) == 2
    assert "add would duplicate existing character id: char_bai_shuanghan" in provider.requests[1].user_prompt
    assert result.proposal.operations[0].path == "/characters/20/reader_visible_summary"
    apply_memory_repair(root, result.proposal_path)
    characters = load_json_model(root / "memory" / "canon" / "characters.json", CharactersFile)
    assert [character.id for character in characters.characters].count("char_bai_shuanghan") == 1
    assert characters.characters[20].reader_visible_summary == target_summary


def test_setting_change_apply_rejects_duplicate_existing_character_add_without_backup(tmp_path: Path) -> None:
    root = _workspace_with_twenty_one_characters(tmp_path)
    characters_path = root / "memory" / "canon" / "characters.json"
    before = characters_path.read_text(encoding="utf-8")
    repair_id = "repair_20260606_030303_000001"
    repair_dir = root / "memory" / "repairs" / repair_id
    repair_dir.mkdir(parents=True)
    proposal_path = repair_dir / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "repair_id": repair_id,
                "created_by": "orchestrator",
                "change_kind": "setting_change",
                "user_request": "修改白霜瀚开篇设定",
                "target_files": ["memory/canon/characters.json"],
                "operations": [
                    {
                        "op": "add",
                        "file": "memory/canon/characters.json",
                        "path": "/characters/-",
                        "value": {
                            "id": "char_bai_shuanghan",
                            "name": "白霜瀚",
                            "role": "主要人物",
                            "reader_visible_summary": "白霜瀚开篇暗中调查桃花源旧族。",
                        },
                        "reason": "错误地把已有人物当成新增人物。",
                    }
                ],
                "risk_level": "medium",
                "validation_before": {},
                "notes": [],
                "created_at": "2026-06-06T00:00:00Z",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(MemoryRepairError) as excinfo:
        apply_memory_repair(root, proposal_path)

    assert "add would duplicate existing character id: char_bai_shuanghan" in str(excinfo.value)
    assert characters_path.read_text(encoding="utf-8") == before
    apply_log = json.loads((repair_dir / "apply_log.json").read_text(encoding="utf-8"))
    assert apply_log["status"] == "failed"
    assert apply_log["backups"] == []


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

    prompt = _memory_repair_user_prompt(
        root,
        "把 char_lin_che 设定为林澈表面温和但做决定非常谨慎",
        change_kind="setting_change",
    )

    assert "当前文件结构与 JSON Pointer 路径索引" in prompt
    assert "/characters/-" in prompt
    assert "/characters/0/reader_visible_summary" in prompt
    assert "/events/0/event_role" in prompt


def test_setting_change_prompt_uses_system_owned_pointer_mapping(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_lin_che", "林澈")

    prompt = _memory_repair_user_prompt(
        root,
        "新增人物谢蛰雨，隐藏真相是她出身桃花源旧族，并在开篇埋伏笔。",
        change_kind="setting_change",
    )
    clarification_system = load_prompt_template("memory_change_clarification_system")

    assert "不要要求用户提供文件结构、字段、visibility 或 JSON Pointer" in prompt
    assert "文件、字段、visibility 和 JSON Pointer 映射由外层系统根据当前结构负责" in clarification_system
    assert "不要询问用户该写哪个文件、字段、visibility 或 JSON Pointer" in clarification_system
    assert "只有 exact id、exact name 或 exact alias 匹配" in prompt
    assert "不要把新姓名近似联想到现有角色" in prompt
    assert "Character.role 只表示叙事角色" in prompt
    assert "role=\"主要人物\"" in prompt
    assert "谢家长女" in prompt
    assert "必须写入 tags" in prompt


def test_setting_change_pointer_context_uses_schema_fields(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_lin_che", "林澈")

    prompt = _memory_repair_user_prompt(root, "新增隐藏真相和伏笔", change_kind="setting_change")

    assert "common item fields: id, title, description, visibility, importance, related_entity_ids, planned_reveal, foreshadowing_ids" in prompt
    assert "common item fields: id, type, title, introduced_in_chapter, description, status, importance, reader_visible, hidden_truth, hidden_truth_id, planned_payoff, related_entity_ids" in prompt
    assert "Ability {name: string, description: string, limitations?: string|null}" in prompt
    assert "Character.role is narrative role only" in prompt
    assert "Never put family rank, sect identity, profession, or jianghu identity in role" in prompt
    assert "Secret {id: snake_case, visibility: reader_visible|hidden|partially_revealed" in prompt
    assert "LocationRule {id?: snake_case|null, description: string, visibility: reader_visible|hidden|partially_revealed}" in prompt
    assert "SpecialProperty {description: string, visibility: reader_visible|hidden|partially_revealed}" in prompt
    assert "PlannedReveal {chapter: integer >= 1, method?: string|null}" in prompt
    assert "PlannedPayoff {chapter: integer >= 1, description: string}" in prompt
    assert "Visibility enum is exactly reader_visible | hidden | partially_revealed" in prompt
    assert "Importance enum is exactly low | medium | high | critical" in prompt
    assert "never use visible" in prompt
    assert "never use major" in prompt
    assert "reader_safe_hint" not in prompt
    assert "common item fields: id, title, setup" not in prompt
    assert "common item fields: id, title, setup, payoff" not in prompt


def test_parse_memory_repair_decision_normalizes_invalid_add_paths() -> None:
    decision = parse_memory_repair_decision(
        json.dumps(
            {
                "change_kind": "setting_change",
                "target_files": [],
                "operations": [
                    {
                        "op": "add",
                        "path": "/characters/char_xie_zheyu",
                        "value": {"id": "char_xie_zheyu", "name": "谢蛰雨"},
                    },
                    {
                        "op": "add",
                        "path": "/hidden_truths/truth_taohuayuan",
                        "value": {"id": "truth_taohuayuan", "title": "桃花源旧族"},
                    },
                    {
                        "op": "add",
                        "path": "/foreshadowing_threads/thread_taohuayuan",
                        "value": {"id": "thread_taohuayuan", "title": "开篇线索"},
                    },
                ],
                "confidence": 0.7,
                "needs_user_confirmation": True,
            },
            ensure_ascii=False,
        )
    )

    assert [operation.file for operation in decision.operations] == [
        "memory/canon/characters.json",
        "memory/canon/hidden_truths.json",
        "memory/canon/foreshadowing.json",
    ]
    assert [operation.path for operation in decision.operations] == [
        "/characters/-",
        "/hidden_truths/-",
        "/foreshadowing_threads/-",
    ]
    assert all(operation.reason for operation in decision.operations)


def test_parse_memory_repair_decision_does_not_rewrite_replace_paths() -> None:
    decision = parse_memory_repair_decision(
        json.dumps(
            {
                "change_kind": "setting_change",
                "target_files": [],
                "operations": [
                    {
                        "op": "replace",
                        "file": "memory/canon/characters.json",
                        "path": "/characters/char_xie_zheyu",
                        "value": {"name": "谢蛰雨"},
                        "reason": "用户要求修改人物。",
                    }
                ],
                "confidence": 0.7,
                "needs_user_confirmation": True,
            },
            ensure_ascii=False,
        )
    )

    assert decision.operations[0].path == "/characters/char_xie_zheyu"

    with pytest.raises(MemoryRepairError):
        parse_memory_repair_decision(
            json.dumps(
                {
                    "operations": [
                        {
                            "op": "remove",
                            "path": "/characters/char_xie_zheyu",
                        }
                    ],
                    "confidence": 0.7,
                },
                ensure_ascii=False,
            )
        )


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


def test_setting_change_interactive_ready_for_rich_new_setting(tmp_path: Path) -> None:
    root = _workspace_with_character(tmp_path, "char_shen_zhou", "沈舟")

    result = suggest_setting_change_interactive(
        root,
        "新增人物谢蛰雨，设定为栖霞山谢氏后人；隐藏真相是她知道桃花源旧族仍存在，开篇只埋线索不要揭晓。",
        provider_name="mock",
    )

    assert result.status == "proposal_ready"
    assert result.proposal_result is not None


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
                        "narrative_position": {"chapter": 2, "scene": 1},
                        "story_position": {"time_label": "多年前"},
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
                        "role": "主角",
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


def _workspace_with_location(tmp_path: Path) -> Path:
    root = _workspace_with_timeline_event(tmp_path)
    (root / "memory" / "canon" / "locations.json").write_text(
        json.dumps(
            {
                "locations": [
                    {
                        "id": "loc_taohuayuan_village",
                        "name": "桃花源村",
                        "type": "村庄",
                        "reader_visible_summary": "桃花源村的旧设定。",
                        "private_author_notes": "谢蛰雨可以进出。",
                        "parent_location_id": None,
                        "connected_location_ids": [],
                        "rules": [],
                        "tags": ["隐藏"],
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


def _workspace_with_hidden_truth_and_thread(tmp_path: Path) -> Path:
    root = _workspace_with_timeline_event(tmp_path)
    (root / "memory" / "canon" / "characters.json").write_text(
        json.dumps(
            {
                "characters": [
                    {
                        "id": "char_xie_huaiyun",
                        "name": "谢怀云",
                        "role": "主要人物",
                        "reader_visible_summary": "谢怀云的旧设定。",
                        "tags": [],
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "memory" / "canon" / "hidden_truths.json").write_text(
        json.dumps({"hidden_truths": [_hidden_truth_value("桃花源村的旧隐藏真相。")]}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (root / "memory" / "canon" / "foreshadowing.json").write_text(
        json.dumps(
            {"foreshadowing_threads": [_foreshadowing_value("桃花源村旧伏笔。")]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def _hidden_truth_value(description: str) -> dict[str, object]:
    return {
        "id": "truth_taohuayuan_village",
        "title": "桃花源村",
        "description": description,
        "visibility": "hidden",
        "importance": "high",
        "related_entity_ids": [],
        "planned_reveal": None,
        "foreshadowing_ids": ["thread_taohuayuan"],
    }


def _foreshadowing_value(description: str) -> dict[str, object]:
    return {
        "id": "thread_taohuayuan",
        "type": "clue",
        "title": "桃花源村伏笔",
        "introduced_in_chapter": 1,
        "description": description,
        "status": "active",
        "importance": "high",
        "reader_visible": False,
        "hidden_truth": "桃花源村",
        "hidden_truth_id": "truth_taohuayuan_village",
        "planned_payoff": None,
        "related_entity_ids": [],
    }


def _workspace_with_twenty_one_characters(tmp_path: Path) -> Path:
    root = _workspace_with_timeline_event(tmp_path)
    characters = [
        {
            "id": f"char_existing_{index:02d}",
            "name": f"既有人物{index:02d}",
            "role": "配角",
            "reader_visible_summary": f"既有人物{index:02d}的旧设定。",
        }
        for index in range(20)
    ]
    characters.append(
        {
            "id": "char_bai_shuanghan",
            "name": "白霜瀚",
            "role": "主要人物",
            "reader_visible_summary": "白霜瀚的旧设定。",
        }
    )
    (root / "memory" / "canon" / "characters.json").write_text(
        json.dumps({"characters": characters}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return root


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
