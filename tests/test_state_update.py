from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

import yaml

from tests.internal_task_cli import run_test_cli
from novel.core.auditing import ChapterAuditOptions, audit_chapter, default_mock_audit_report_json
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.drafting import ChapterDraftingOptions, write_chapter_draft
from novel.core.planning import ChapterPlanningOptions, default_mock_chapter_plan_json, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, polish_chapter
from novel.core.providers import MockProvider
from novel.core.schemas import ChapterMetadata, EntityState, StateUpdateApplyLog, StateUpdateProposal, TimelineFile
from novel.core import state_update as state_update_module
from novel.core.contracts import CURRENT_SCHEMA_VERSION
from novel.core.state_update import (
    AcceptChapterOptions,
    StateUpdateApplyOptions,
    StateUpdateProposeOptions,
    accept_chapter,
    default_mock_state_update_proposal_json,
    apply_state_update,
    parse_state_update_proposal,
    propose_state_update,
    validate_state_update_proposal,
)
from novel.core.workspace import InitOptions, init_workspace


def test_mock_provider_can_generate_state_update_proposal(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    provider = MockProvider(fake_response=default_mock_state_update_proposal_json(1))

    result = propose_state_update(
        StateUpdateProposeOptions(
            root=root,
            chapter_number=1,
            instruction="只记录正文中实际发生的变化",
        ),
        provider,
    )

    assert result.proposal.chapter_number == 1
    assert result.proposal_path.is_file()
    assert len(result.proposal.state_changes) == 3
    assert len(result.proposal.timeline_events) == 1
    assert "只记录正文中实际发生的变化" in provider.requests[0].user_prompt
    assert "只输出结构化 JSON" in provider.requests[0].system_prompt


def test_state_update_can_receive_search_context(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    provider = MockProvider(fake_response=default_mock_state_update_proposal_json(1))

    result = propose_state_update(
        StateUpdateProposeOptions(root=root, chapter_number=1, use_search_context=True),
        provider,
    )

    assert "Context bundle" in provider.requests[0].user_prompt
    assert result.context_report_path is not None
    assert result.context_report_path.is_file()


def test_state_update_agent_question_repairs_once(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    provider = MockProvider(fake_response=["是否现在更新状态文件？", default_mock_state_update_proposal_json(1)])

    result = propose_state_update(StateUpdateProposeOptions(root=root, chapter_number=1), provider)

    assert result.proposal.chapter_number == 1
    assert len(provider.requests) == 2
    assert "不要向用户或上游 Agent 提问" in provider.requests[1].user_prompt


def test_propose_state_update_does_not_modify_state_or_timeline(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    state_path = root / "memory" / "state" / "current_state.json"
    timeline_path = root / "memory" / "state" / "timeline.json"
    original_state = state_path.read_text(encoding="utf-8")
    original_timeline = timeline_path.read_text(encoding="utf-8")

    code, stdout, stderr = _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert "Wrote state update proposal:" in stdout
    assert state_path.read_text(encoding="utf-8") == original_state
    assert timeline_path.read_text(encoding="utf-8") == original_timeline
    proposal = StateUpdateProposal.model_validate(
        json.loads((root / "memory" / "chapters" / "001" / "state_update_proposal.json").read_text(encoding="utf-8"))
    )
    assert proposal.timeline_events[0].id == "event_001_001"
    events_text = (root / "memory" / "management_events.jsonl").read_text(encoding="utf-8")
    assert "state_update_proposed" in events_text


def test_apply_state_update_applies_legal_proposal_and_creates_backups(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    code, stdout, stderr = _run_cli(["apply-state-update", "1", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "Backed up current state:" in stdout
    assert "Backed up timeline:" in stdout
    assert list((root / "memory" / "state").glob("current_state.json.bak_*"))
    assert list((root / "memory" / "state").glob("timeline.json.bak_*"))
    apply_log_path = root / "memory" / "chapters" / "001" / "state_update_apply_log.json"
    assert apply_log_path.is_file()
    apply_log = StateUpdateApplyLog.model_validate(json.loads(apply_log_path.read_text(encoding="utf-8")))
    assert apply_log.status == "applied"
    state = EntityState.model_validate(
        json.loads((root / "memory" / "state" / "current_state.json").read_text(encoding="utf-8"))
    )
    timeline = TimelineFile.model_validate(
        json.loads((root / "memory" / "state" / "timeline.json").read_text(encoding="utf-8"))
    )
    assert state.story_position.latest_chapter == 1
    assert state.item_states[0].holder_id == "char_lin_che"
    assert timeline.events[0].id == "event_001_001"
    events_text = (root / "memory" / "management_events.jsonl").read_text(encoding="utf-8")
    assert "state_update_applied" in events_text
    assert "timeline_updated" in events_text


def test_apply_state_update_normalizes_saved_proposal_list_strings(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])
    proposal_path = root / "memory" / "chapters" / "001" / "state_update_proposal.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    proposal["state_changes"][0]["new_value"] = "破损车票（item_broken_ticket）"
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    code, stdout, stderr = _run_cli(["apply-state-update", "1", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "state_update_apply_log.json" in stdout


def test_apply_state_update_coerces_story_position_latest_chapter_string(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])
    proposal_path = root / "memory" / "chapters" / "001" / "state_update_proposal.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    for change in proposal["state_changes"]:
        if change["entity_id"] == "story_position" and change["field"] == "latest_chapter":
            change["old_value"] = "0"
            change["new_value"] = "1"
            break
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    code, stdout, stderr = _run_cli(["apply-state-update", "1", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "state_update_apply_log.json" in stdout
    state = EntityState.model_validate(
        json.loads((root / "memory" / "state" / "current_state.json").read_text(encoding="utf-8"))
    )
    assert state.story_position.latest_chapter == 1


def test_apply_state_update_rolls_back_when_timeline_write_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])
    state_path = root / "memory" / "state" / "current_state.json"
    timeline_path = root / "memory" / "state" / "timeline.json"
    original_state = state_path.read_text(encoding="utf-8")
    original_timeline = timeline_path.read_text(encoding="utf-8")
    from novel.core import state_update as state_update_module

    original_atomic_write_model_json = state_update_module.atomic_write_model_json

    def flaky_atomic_write_model_json(path: Path, model) -> None:
        if path == timeline_path:
            raise OSError("simulated timeline write failure")
        original_atomic_write_model_json(path, model)

    monkeypatch.setattr(state_update_module, "atomic_write_model_json", flaky_atomic_write_model_json)

    try:
        apply_state_update(StateUpdateApplyOptions(root=root, chapter_number=1))
    except Exception as exc:
        assert "rolled back" in str(exc)
    else:
        raise AssertionError("expected rollback failure")

    assert state_path.read_text(encoding="utf-8") == original_state
    assert timeline_path.read_text(encoding="utf-8") == original_timeline
    apply_log_path = root / "memory" / "chapters" / "001" / "state_update_apply_log.json"
    assert apply_log_path.is_file()
    apply_log = StateUpdateApplyLog.model_validate(json.loads(apply_log_path.read_text(encoding="utf-8")))
    assert apply_log.status == "rolled_back"


def test_apply_state_update_fails_on_duplicate_timeline_event_id(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    timeline_path = root / "memory" / "state" / "timeline.json"
    timeline_path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "event_001_001",
                        "narrative_position": {"chapter": 1},
                        "story_position": {"time_label": "第1天，雨夜"},
                        "summary": "已存在事件。",
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
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    code, stdout, stderr = _run_cli(["apply-state-update", "1", "--path", str(root)])

    assert code == 1
    assert stdout == ""
    assert "timeline event id conflict" in stderr


def test_apply_state_update_fails_on_old_value_mismatch(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])
    proposal_path = root / "memory" / "chapters" / "001" / "state_update_proposal.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    proposal["state_changes"][2]["old_value"] = 99
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    code, stdout, stderr = _run_cli(["apply-state-update", "1", "--path", str(root)])

    assert code == 1
    assert stdout == ""
    assert "old_value mismatch" in stderr


def test_apply_state_update_treats_empty_string_old_value_as_unset(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])
    proposal_path = root / "memory" / "chapters" / "001" / "state_update_proposal.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    proposal["state_changes"].append(
        {
            "id": "change_001_004",
            "chapter": 1,
            "entity_id": "story_position",
            "field": "in_story_time",
            "old_value": "",
            "new_value": "第1天，雨夜",
            "reason": "模型把未设置旧值写成空字符串。",
            "source": "memory/chapters/001/polished.md",
        }
    )
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    code, stdout, stderr = _run_cli(["apply-state-update", "1", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "state_update_apply_log.json" in stdout


def test_apply_state_update_ignores_old_value_when_entity_state_is_missing(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])
    proposal_path = root / "memory" / "chapters" / "001" / "state_update_proposal.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    proposal["state_changes"][0]["old_value"] = ["model inferred prior state"]
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    code, stdout, stderr = _run_cli(["apply-state-update", "1", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "state_update_apply_log.json" in stdout


def test_apply_state_update_fails_on_item_holder_location_conflict(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    state_path = root / "memory" / "state" / "current_state.json"
    state_path.write_text(
        json.dumps(
            {
                "story_position": {"latest_chapter": 0, "in_story_time": None, "summary": None},
                "character_states": [],
                "item_states": [
                    {
                        "entity_id": "item_broken_ticket",
                        "holder_id": None,
                        "location_id": "loc_old_station",
                        "condition": None,
                        "known_properties": [],
                        "last_updated_chapter": 0,
                    }
                ],
                "location_states": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    propose_code, _, propose_stderr = _run_cli(
        ["propose-state-update", "1", "--path", str(root), "--provider", "mock"]
    )

    code, stdout, stderr = _run_cli(["apply-state-update", "1", "--path", str(root)])

    assert propose_code == 1
    assert "item holder/location conflict" in propose_stderr
    assert code == 1
    assert stdout == ""
    assert "state_update_proposal.json is missing" in stderr


def test_parse_state_update_proposal_normalizes_common_field_aliases() -> None:
    data = json.loads(default_mock_state_update_proposal_json(1))
    data["schema_version"] = CURRENT_SCHEMA_VERSION
    data["state_changes"][0]["field"] = "location"
    data["state_changes"][0]["new_value"] = "loc_old_station"
    data["state_changes"][1]["old_value"] = "none"
    data["timeline_events"][0]["location"] = data["timeline_events"][0].pop("location_id")

    proposal = parse_state_update_proposal(json.dumps(data, ensure_ascii=False))

    assert proposal.schema_version == CURRENT_SCHEMA_VERSION
    assert proposal.state_changes[0].field == "location_id"
    assert proposal.state_changes[1].old_value is None
    assert proposal.timeline_events[0].location_id == "loc_old_station"


def test_state_update_reference_normalization_moves_item_holder_location_to_location_id(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    data = json.loads(default_mock_state_update_proposal_json(1))
    data["state_changes"] = [data["state_changes"][1]]
    data["timeline_events"] = []
    data["state_changes"][0]["new_value"] = "loc_old_station"
    proposal = parse_state_update_proposal(json.dumps(data, ensure_ascii=False))

    normalized = state_update_module._normalize_state_update_references(root, proposal)

    assert normalized.state_changes[0].field == "location_id"
    assert normalized.state_changes[0].new_value == "loc_old_station"
    assert any("holder_id location reference" in warning for warning in normalized.warnings)
    validate_state_update_proposal(root, normalized, check_existing_timeline_ids=False)


def test_parse_state_update_proposal_rejects_legacy_timeline_fields() -> None:
    data = json.loads(default_mock_state_update_proposal_json(1))
    data["timeline_events"][0]["in_story_time"] = "旧别名"

    try:
        parse_state_update_proposal(json.dumps(data, ensure_ascii=False))
    except Exception as exc:
        assert "in_story_time" in str(exc)
    else:
        raise AssertionError("expected legacy timeline field rejection")


def test_parse_state_update_proposal_normalizes_list_field_strings() -> None:
    data = json.loads(default_mock_state_update_proposal_json(1))
    data["state_changes"][0]["field"] = "knowledge"
    data["state_changes"][0]["new_value"] = "知道旧车站广播异常"
    data["state_changes"][1]["field"] = "possessions"
    data["state_changes"][1]["new_value"] = "破损车票（item_broken_ticket）"

    proposal = parse_state_update_proposal(json.dumps(data, ensure_ascii=False))

    assert proposal.state_changes[0].new_value == ["知道旧车站广播异常"]
    assert proposal.state_changes[1].new_value == ["item_broken_ticket"]


def test_validate_state_update_rejects_unknown_field(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    proposal = parse_state_update_proposal(default_mock_state_update_proposal_json(1))
    proposal.state_changes[0].field = "unknown_field"

    try:
        validate_state_update_proposal(root, proposal, check_existing_timeline_ids=False)
    except Exception as exc:
        assert "unsupported field: unknown_field" in str(exc)
    else:
        raise AssertionError("expected unsupported field failure")


def test_validate_state_update_rejects_item_holder_location_conflict(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    proposal = parse_state_update_proposal(default_mock_state_update_proposal_json(1))
    proposal.state_changes.append(
        proposal.state_changes[1].model_copy(
            update={
                "id": "change_001_004",
                "field": "location_id",
                "new_value": "loc_old_station",
            }
        )
    )

    try:
        validate_state_update_proposal(root, proposal, check_existing_timeline_ids=False)
    except Exception as exc:
        assert "item holder/location conflict" in str(exc)
    else:
        raise AssertionError("expected holder/location conflict failure")


def test_validate_state_update_rejects_timeline_order_regression(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    (root / "memory" / "state" / "timeline.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "event_existing_scene_2",
                        "narrative_position": {"chapter": 1, "scene": 2},
                        "story_position": {"time_label": "第一章第二场"},
                        "summary": "已有较晚事件。",
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
    proposal = parse_state_update_proposal(default_mock_state_update_proposal_json(1))

    try:
        validate_state_update_proposal(root, proposal, check_existing_timeline_ids=False)
    except Exception as exc:
        assert "timeline event order conflict" in str(exc)
    else:
        raise AssertionError("expected timeline order regression failure")


def test_validate_state_update_allows_earlier_story_order_when_narrative_advances(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    (root / "memory" / "state" / "timeline.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "event_current",
                        "narrative_position": {"chapter": 1, "scene": 1},
                        "story_position": {"time_label": "第1天", "order": 10, "thread_id": "main"},
                        "summary": "当前事件。",
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
    proposal = parse_state_update_proposal(default_mock_state_update_proposal_json(1))
    event = proposal.timeline_events[0]
    event.narrative_position.scene = 2
    event.story_position.order = 1
    event.story_position.thread_id = "main"
    event.event_role = "flashback"

    warnings = validate_state_update_proposal(root, proposal, check_existing_timeline_ids=False)

    assert isinstance(warnings, list)


def test_validate_state_update_rejects_timeline_scene_outside_plan(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    proposal = parse_state_update_proposal(default_mock_state_update_proposal_json(1))
    proposal.timeline_events[0].narrative_position.scene = 3

    try:
        validate_state_update_proposal(root, proposal, check_existing_timeline_ids=False)
    except Exception as exc:
        assert "exceeds ChapterPlan scene count" in str(exc)
    else:
        raise AssertionError("expected scene bounds failure")


def test_state_update_repairs_timeline_scene_outside_plan(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    bad_data = json.loads(default_mock_state_update_proposal_json(1))
    bad_data["timeline_events"][0]["narrative_position"]["scene"] = 3
    provider = MockProvider(
        fake_response=[
            json.dumps(bad_data, ensure_ascii=False),
            default_mock_state_update_proposal_json(1),
        ]
    )

    result = propose_state_update(StateUpdateProposeOptions(root=root, chapter_number=1), provider)

    assert result.proposal.timeline_events[0].narrative_position.scene == 1
    assert len(provider.requests) == 2


def test_validate_state_update_rejects_timeline_chapter_mismatch(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    proposal = parse_state_update_proposal(default_mock_state_update_proposal_json(1))
    proposal.timeline_events[0].narrative_position.chapter = 2

    try:
        validate_state_update_proposal(root, proposal, check_existing_timeline_ids=False)
    except Exception as exc:
        assert "narrative_position.chapter must match proposal chapter_number" in str(exc)
    else:
        raise AssertionError("expected timeline chapter mismatch failure")


def test_accept_chapter_passed_audit_applies_update_and_marks_accepted(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    code, stdout, stderr = _run_cli(["accept-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert "Accepted chapter:" in stdout
    assert "warning:" not in stdout
    assert not (root / "memory" / "chapters" / "001" / "canon_drift_proposal.json").exists()
    metadata = _read_front_matter(root / "memory" / "chapters" / "001" / "polished.md")
    assert metadata["status"] == "accepted"
    assert "accepted_at" in metadata
    structured = ChapterMetadata.model_validate(
        json.loads((root / "memory" / "chapters" / "001" / "metadata.json").read_text(encoding="utf-8"))
    )
    assert structured.status == "accepted"
    assert structured.accepted_at is not None
    assert structured.state_update_apply_log_path == "memory/chapters/001/state_update_apply_log.json"


def test_accept_chapter_after_apply_is_idempotent(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])
    first_apply, _, _ = _run_cli(["apply-state-update", "1", "--path", str(root)])
    timeline_before = json.loads((root / "memory" / "state" / "timeline.json").read_text(encoding="utf-8"))

    code, stdout, stderr = _run_cli(["accept-chapter", "1", "--path", str(root), "--provider", "mock"])
    timeline_after = json.loads((root / "memory" / "state" / "timeline.json").read_text(encoding="utf-8"))

    assert first_apply == 0
    assert code == 0
    assert stderr == ""
    assert "Accepted chapter:" in stdout
    assert "warning:" not in stdout
    assert timeline_after == timeline_before
    assert len(timeline_after["events"]) == 1


def test_accept_chapter_writes_canon_drift_proposal_without_applying_it(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])
    drift_data = {
        "characters": [],
        "locations": [],
        "items": [
            {
                "id": "item_blue_lamp",
                "name": "蓝色信号灯",
                "type": "clue",
                "reader_visible_summary": "旧车站站台深处短暂亮起的蓝色信号灯。",
                "tags": ["线索"],
            }
        ],
        "world_rules": [],
        "hidden_truths": [
            {
                "id": "truth_blue_lamp_signal",
                "title": "蓝灯来自旧车站异常",
                "description": "蓝色信号灯会在旧车站异常开启时短暂显现。",
                "visibility": "hidden",
                "importance": "medium",
                "related_entity_ids": ["item_blue_lamp", "loc_old_station"],
                "foreshadowing_ids": [],
            }
        ],
        "foreshadowing_threads": [],
        "notes": ["补登本章新增线索。"],
    }
    drift_provider = MockProvider(fake_response=json.dumps(drift_data, ensure_ascii=False))
    monkeypatch.setattr(state_update_module, "load_canon_drift_provider", lambda *args, **kwargs: drift_provider)

    result = accept_chapter(AcceptChapterOptions(root=root, chapter_number=1))

    proposal_path = root / "memory" / "chapters" / "001" / "canon_drift_proposal.json"
    assert result.canon_drift_proposal_path == proposal_path
    assert proposal_path.is_file()
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    assert proposal["items"][0]["id"] == "item_blue_lamp"
    canon_items = json.loads((root / "memory" / "canon" / "items.json").read_text(encoding="utf-8"))
    assert all(item["id"] != "item_blue_lamp" for item in canon_items["items"])
    events_text = (root / "memory" / "management_events.jsonl").read_text(encoding="utf-8")
    assert "canon_drift_proposed" in events_text


def test_propose_state_update_blocked_audit_fails_by_default(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _write_blocked_audit(root)

    code, stdout, stderr = _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    assert code == 1
    assert stdout == ""
    assert "unresolved medium, high, or critical issues" in stderr


def test_accept_chapter_can_auto_propose_when_missing(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)

    code, stdout, stderr = _run_cli(
        ["accept-chapter", "1", "--path", str(root), "--propose", "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote state update proposal:" in stdout
    assert "Accepted chapter:" in stdout
    assert (root / "memory" / "chapters" / "001" / "state_update_proposal.json").is_file()


def test_accept_chapter_blocked_audit_fails_by_default(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _write_blocked_audit(root)
    _run_cli(
        [
            "propose-state-update",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--allow-unresolved-audit",
            "--force",
        ]
    )

    code, stdout, stderr = _run_cli(["accept-chapter", "1", "--path", str(root)])

    assert code == 1
    assert stdout == ""
    assert "unresolved medium, high, or critical issues" in stderr


def test_accept_chapter_allow_issues_can_continue_with_blocked_audit(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _write_blocked_audit(root)
    _run_cli(
        [
            "propose-state-update",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--allow-unresolved-audit",
            "--force",
        ]
    )

    code, stdout, stderr = _run_cli(
        ["accept-chapter", "1", "--path", str(root), "--allow-issues", "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    assert "Accepted chapter:" in stdout


def _workspace_with_audit(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# Inspiration\n\n## Weak Outline\n\n雨夜旧车站传来停播多年的广播声。\n",
        encoding="utf-8",
    )
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    plan_chapter(
        ChapterPlanningOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_chapter_plan_json(1)),
    )
    write_chapter_draft(
        ChapterDraftingOptions(root=root, chapter_number=1),
        MockProvider(fake_response="雨落在旧车站。林澈听见广播，拾起半张车票。"),
    )
    polish_chapter(
        ChapterPolishingOptions(root=root, chapter_number=1),
        MockProvider(fake_response="雨声更深，旧车站像在夜里醒来。林澈收起车票。"),
    )
    audit_chapter(
        ChapterAuditOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_audit_report_json(1, "polished.md")),
    )
    return root


def _write_blocked_audit(root: Path) -> None:
    audit_path = root / "memory" / "chapters" / "001" / "audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "audited_file": "polished.md",
                "overall_status": "blocked",
                "summary": "存在重大连续性问题。",
                "issues": [
                    {
                        "id": "issue_blocking",
                        "severity": "critical",
                        "type": "continuity_issue",
                        "description": "章节编号或事实存在阻断问题。",
                        "evidence": [{"source": "polished.md", "quote": "example"}],
                        "suggested_fix": "先修订章节再接受。",
                    }
                ],
                "passed_checks": [],
                "created_at": "2026-05-23T00:00:00Z",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _read_front_matter(path: Path) -> dict[str, object]:
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    _, metadata_text, _ = content.split("---\n", 2)
    return yaml.safe_load(metadata_text)


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = run_test_cli(args)
    return code, stdout.getvalue(), stderr.getvalue()
