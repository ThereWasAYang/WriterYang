from __future__ import annotations

import json
from pathlib import Path

from novel.core.consistency import check_chapter_consistency, check_project_consistency
from novel.core.migration import CURRENT_SCHEMA_VERSION
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
