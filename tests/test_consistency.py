from __future__ import annotations

import json
from pathlib import Path

from novel.core.consistency import check_chapter_consistency, check_project_consistency
from novel.core.contracts import CURRENT_SCHEMA_VERSION
from novel.core.workspace import InitOptions, init_workspace


def test_hidden_truth_before_reveal_in_body_produces_finding(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_hidden_truth(root, reveal_chapter=3)

    result = check_chapter_consistency(
        root,
        1,
        audited_body="雨停时，众人才知道师父其实是旧王朝遗孤。",
        audited_file="polished.md",
        include_existing_audit=False,
    )

    assert any(finding.type == "premature_reveal" and finding.severity == "high" for finding in result.findings)


def test_character_knowledge_before_hidden_truth_reveal_produces_finding(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_hidden_truth(root, reveal_chapter=4)
    _write_state(
        root,
        character_states=[
            {
                "entity_id": "char_linyan",
                "location_id": "loc_station",
                "knowledge": ["truth_old_king"],
                "possessions": [],
                "last_updated_chapter": 1,
            }
        ],
    )

    result = check_chapter_consistency(root, 1, include_existing_audit=False)

    assert any(finding.id == "cons_knowledge_char_linyan_truth_old_king" for finding in result.findings)


def test_item_holder_location_and_possession_conflicts_are_reported(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_state(
        root,
        character_states=[
            {
                "entity_id": "char_linyan",
                "location_id": "loc_station",
                "knowledge": [],
                "possessions": ["item_bell"],
                "last_updated_chapter": 1,
            }
        ],
        item_states=[
            {
                "entity_id": "item_bell",
                "holder_id": "char_shenlu",
                "location_id": "loc_station",
                "condition": "完好",
                "last_updated_chapter": 1,
            }
        ],
    )

    result = check_chapter_consistency(root, 1, include_existing_audit=False)
    finding_ids = {finding.id for finding in result.findings}

    assert "cons_item_holder_location_item_bell" in finding_ids
    assert "cons_item_holder_possession_item_bell" in finding_ids
    assert "cons_item_holder_missing_possession_item_bell" not in finding_ids


def test_item_holder_without_possession_mirror_is_reported_as_low_warning(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_state(
        root,
        character_states=[
            {
                "entity_id": "char_linyan",
                "location_id": "loc_station",
                "knowledge": [],
                "possessions": [],
                "last_updated_chapter": 1,
            }
        ],
        item_states=[
            {
                "entity_id": "item_bell",
                "holder_id": "char_linyan",
                "condition": "完好",
                "last_updated_chapter": 1,
            }
        ],
    )

    result = check_chapter_consistency(root, 1, include_existing_audit=False)

    finding = next(
        item for item in result.findings if item.id == "cons_item_holder_missing_possession_item_bell"
    )
    assert finding.severity == "low"
    assert finding.description == "物品 item_bell 设置了 holder_id，但持有角色的 possessions 未包含该物品。"


def test_state_update_old_value_allows_numeric_strings_and_missing_entity_state(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_characters(root)
    _write_state(root, character_states=[])
    _write_state_update_proposal(
        root,
        [
            {
                "id": "sc_sp_latest",
                "chapter": 1,
                "entity_id": "story_position",
                "field": "latest_chapter",
                "old_value": "1",
                "new_value": "2",
                "reason": "模型把数字旧值写成字符串。",
                "source": "memory/chapters/001/polished.md",
            },
            {
                "id": "sc_char_luc",
                "chapter": 1,
                "entity_id": "char_linyan",
                "field": "last_updated_chapter",
                "old_value": "0",
                "new_value": "1",
                "reason": "角色尚未创建 current_state 记录。",
                "source": "memory/chapters/001/polished.md",
            },
        ],
    )

    result = check_chapter_consistency(root, 1, include_existing_audit=False)

    assert not any(finding.id.startswith("cons_state_change_old_value_") for finding in result.findings)


def test_state_update_old_value_real_mismatch_is_reported(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_state_update_proposal(
        root,
        [
            {
                "id": "sc_sp_latest",
                "chapter": 1,
                "entity_id": "story_position",
                "field": "latest_chapter",
                "old_value": 0,
                "new_value": 2,
                "reason": "旧值真的与 current_state 不一致。",
                "source": "memory/chapters/001/polished.md",
            }
        ],
    )

    result = check_chapter_consistency(root, 1, include_existing_audit=False)

    assert any(finding.id == "cons_state_change_old_value_sc_sp_latest" for finding in result.findings)


def test_character_gendered_reference_conflict_is_reported(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_characters(root, gender="男", summary="邵家次子，性格沉稳。")

    result = check_chapter_consistency(
        root,
        1,
        audited_body="邵希夷一直坐在舫心竹几前。她面前的青瓷小盏里，残酒已冷。",
        audited_file="polished.md",
        include_existing_audit=False,
    )

    assert any(finding.id == "cons_gender_reference_001_char_linyan" for finding in result.findings)


def test_character_gender_marker_in_summary_is_used_for_reference_audit(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_characters(root, summary="邵家次子，性格沉稳。")

    result = check_chapter_consistency(
        root,
        1,
        audited_body="邵希夷垂眼看着案上小盏。她面前的残酒已经冷透。",
        audited_file="polished.md",
        include_existing_audit=False,
    )

    assert any(finding.id == "cons_gender_reference_001_char_linyan" for finding in result.findings)


def test_character_appearance_gender_is_used_for_reference_audit(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_characters(root, appearance={"gender": "女性"}, summary="邵希夷是主要人物。")

    result = check_chapter_consistency(
        root,
        1,
        audited_body="邵希夷停在门边。他面前的灯影微微摇晃。",
        audited_file="polished.md",
        include_existing_audit=False,
    )

    assert any(finding.id == "cons_gender_reference_001_char_linyan" for finding in result.findings)


def test_character_gendered_reference_audit_avoids_common_false_positives(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_characters(root, gender="男", summary="邵家次子，性格沉稳。")

    same_gender = check_chapter_consistency(
        root,
        1,
        audited_body="邵希夷一直坐在舫心竹几前。他面前的青瓷小盏里，残酒已冷。",
        audited_file="polished.md",
        include_existing_audit=False,
    )
    group_reference = check_chapter_consistency(
        root,
        1,
        audited_body="邵希夷与许连远一同入席，两个男子都没有先开口。",
        audited_file="polished.md",
        include_existing_audit=False,
    )

    assert not any(finding.id.startswith("cons_gender_reference") for finding in same_gender.findings)
    assert not any(finding.id.startswith("cons_gender_reference") for finding in group_reference.findings)


def test_character_gendered_reference_audit_skips_pronoun_after_other_character_name(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_json(
        root / "memory" / "canon" / "characters.json",
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "characters": [
                {
                    "id": "char_linyan",
                    "name": "邵希夷",
                    "role": "主要人物",
                    "gender": "男",
                    "reader_visible_summary": "邵家次子，性格沉稳。",
                    "tags": [],
                },
                {
                    "id": "char_shenwei",
                    "name": "沈微",
                    "role": "主要人物",
                    "gender": "女",
                    "reader_visible_summary": "沈微是同行者。",
                    "tags": [],
                },
            ],
        },
    )

    result = check_chapter_consistency(
        root,
        1,
        audited_body="邵希夷转头看向沈微。她面前的灯影被风吹得发颤。",
        audited_file="polished.md",
        include_existing_audit=False,
    )

    assert not any(finding.id == "cons_gender_reference_001_char_linyan" for finding in result.findings)


def test_character_gender_marker_ambiguity_is_not_reported(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_characters(root, summary="邵家次子，也是谢家长女传闻中的关键人物。")

    result = check_chapter_consistency(
        root,
        1,
        audited_body="邵希夷坐在舫心。她面前的酒盏已经冷了。",
        audited_file="polished.md",
        include_existing_audit=False,
    )

    assert not any(finding.id.startswith("cons_gender_reference") for finding in result.findings)


def test_timeline_dual_order_allows_flashback_story_order(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_timeline(
        root,
        [
            _timeline_event("event_now", chapter=1, scene=1, story_order=100, role="current_action"),
            _timeline_event("event_memory", chapter=2, scene=1, story_order=10, role="flashback"),
        ],
    )

    result = check_project_consistency(root)

    assert not any(finding.type == "timeline_conflict" for finding in result.findings)


def test_timeline_order_checks_skip_unrevealed_background_events(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _write_timeline(
        root,
        [
            {
                "id": "event_background",
                "summary": "尚未正文揭示的背景事件。",
                "reader_visible": False,
                "story_position": {"time_label": "开篇前约十年", "order": 1, "thread_id": "main"},
                "event_role": "backstory",
            },
            _timeline_event("event_now", chapter=1, scene=1, story_order=100, role="current_action"),
        ],
    )

    result = check_project_consistency(root)

    assert not any(finding.type == "timeline_conflict" for finding in result.findings)


def test_accepted_chapter_without_passed_audit_or_state_apply_log_is_blocked(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    chapter_dir = root / "memory" / "chapters" / "001"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    (chapter_dir / "polished.md").write_text(
        "---\nchapter_number: 1\ntitle: 第一章\nstatus: polished\n---\n\n正文。\n",
        encoding="utf-8",
    )
    _write_json(
        chapter_dir / "metadata.json",
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "chapter_number": 1,
            "status": "accepted",
            "plan_path": "memory/chapters/001/plan.json",
            "draft_path": "memory/chapters/001/draft.md",
            "polished_path": "memory/chapters/001/polished.md",
            "audit_path": "memory/chapters/001/audit.json",
            "state_update_apply_log_path": "memory/chapters/001/state_update_apply_log.json",
            "accepted_at": "2026-06-02T00:00:00Z",
            "updated_at": "2026-06-02T00:00:00Z",
        },
    )

    result = check_project_consistency(root)
    finding_ids = {finding.id for finding in result.findings}

    assert "cons_accepted_audit_001" in finding_ids
    assert "cons_accepted_state_apply_001" in finding_ids


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="一致性测试", root=root))
    _write_state(root)
    _write_timeline(root, [])
    return root


def _write_hidden_truth(root: Path, *, reveal_chapter: int) -> None:
    _write_json(
        root / "memory" / "canon" / "hidden_truths.json",
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "hidden_truths": [
                {
                    "id": "truth_old_king",
                    "title": "旧王遗孤",
                    "description": "师父其实是旧王朝遗孤",
                    "visibility": "hidden",
                    "importance": "high",
                    "related_entity_ids": ["char_linyan"],
                    "planned_reveal": {"chapter": reveal_chapter, "method": "后文揭示"},
                }
            ],
        },
    )


def _write_characters(
    root: Path,
    *,
    gender: str | None = None,
    summary: str = "林砚是测试角色。",
    appearance: dict[str, object] | None = None,
    tags: list[str] | None = None,
) -> None:
    character: dict[str, object] = {
        "id": "char_linyan",
        "name": "邵希夷",
        "role": "主要人物",
        "reader_visible_summary": summary,
        "tags": tags or [],
    }
    if gender is not None:
        character["gender"] = gender
    if appearance is not None:
        character["appearance"] = appearance
    _write_json(
        root / "memory" / "canon" / "characters.json",
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "characters": [character],
        },
    )


def _write_state(
    root: Path,
    *,
    character_states: list[dict[str, object]] | None = None,
    item_states: list[dict[str, object]] | None = None,
) -> None:
    _write_json(
        root / "memory" / "state" / "current_state.json",
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "story_position": {"latest_chapter": 1, "in_story_time": "雨夜", "summary": "测试状态"},
            "character_states": character_states
            if character_states is not None
            else [
                {
                    "entity_id": "char_linyan",
                    "location_id": "loc_station",
                    "knowledge": [],
                    "possessions": [],
                    "last_updated_chapter": 1,
                }
            ],
            "item_states": item_states if item_states is not None else [],
            "location_states": [
                {
                    "entity_id": "loc_station",
                    "accessibility": "open",
                    "condition": "雨夜",
                    "active_events": [],
                    "last_updated_chapter": 1,
                }
            ],
        },
    )


def _write_state_update_proposal(root: Path, state_changes: list[dict[str, object]]) -> None:
    _write_json(
        root / "memory" / "chapters" / "001" / "state_update_proposal.json",
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "chapter_number": 1,
            "state_changes": state_changes,
            "timeline_events": [],
            "warnings": [],
            "created_at": "2026-06-20T00:00:00Z",
        },
    )


def _write_timeline(root: Path, events: list[dict[str, object]]) -> None:
    _write_json(
        root / "memory" / "state" / "timeline.json",
        {"schema_version": CURRENT_SCHEMA_VERSION, "events": events},
    )


def _timeline_event(
    event_id: str,
    *,
    chapter: int,
    scene: int,
    story_order: int,
    role: str,
) -> dict[str, object]:
    return {
        "id": event_id,
        "summary": event_id,
        "reader_visible": True,
        "narrative_position": {"chapter": chapter, "scene": scene},
        "story_position": {"time_label": f"故事时间 {story_order}", "order": story_order, "thread_id": "main"},
        "event_role": role,
    }


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
