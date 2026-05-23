from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel.core.io import load_json_model, load_yaml_model
from novel.core.schemas import Character, ProjectConfig
from novel.core.validation import validate_project
from novel.core.workspace import InitOptions, init_workspace


def test_load_json_and_yaml_models(tmp_path: Path) -> None:
    yaml_path = tmp_path / "project.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                'project_id: "novel_test"',
                'title: "Test Novel"',
                'language: "zh-CN"',
                "genre:",
                '  - "悬疑"',
                "narration:",
                '  pov: "third_person_limited"',
                '  tense: "past"',
                'created_at: "2026-05-22T00:00:00Z"',
                'updated_at: "2026-05-22T00:00:00Z"',
            ]
        ),
        encoding="utf-8",
    )
    json_path = tmp_path / "character.json"
    json_path.write_text(
        json.dumps(
            {
                "id": "char_lin_che",
                "name": "林澈",
                "role": "protagonist",
                "reader_visible_summary": "旧物修复师。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    project = load_yaml_model(yaml_path, ProjectConfig)
    character = load_json_model(json_path, Character)

    assert project.project_id == "novel_test"
    assert character.id == "char_lin_che"


def test_validate_fresh_workspace_passes(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))

    report = validate_project(root)

    assert report.ok
    assert report.errors == []


def test_validate_reports_duplicate_character_ids(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_json(
        root / "memory" / "canon" / "characters.json",
        {
            "characters": [
                {
                    "id": "char_lin_che",
                    "name": "林澈",
                    "role": "protagonist",
                    "reader_visible_summary": "旧物修复师。",
                },
                {
                    "id": "char_lin_che",
                    "name": "林澈二号",
                    "role": "mirror",
                    "reader_visible_summary": "重复角色。",
                },
            ]
        },
    )

    report = validate_project(root)

    assert not report.ok
    assert any("duplicate character id: char_lin_che" in msg.message for msg in report.errors)


def test_validate_reports_missing_required_fields(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_json(
        root / "memory" / "canon" / "characters.json",
        {
            "characters": [
                {
                    "id": "char_lin_che",
                    "name": "林澈",
                    "role": "protagonist",
                }
            ]
        },
    )

    report = validate_project(root)

    assert not report.ok
    assert any("reader_visible_summary" in msg.message for msg in report.errors)


def test_validate_warns_for_missing_cross_file_references(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_json(
        root / "memory" / "canon" / "characters.json",
        {
            "characters": [
                {
                    "id": "char_lin_che",
                    "name": "林澈",
                    "role": "protagonist",
                    "reader_visible_summary": "旧物修复师。",
                    "relationships": [
                        {"target_id": "char_missing", "type": "盟友"}
                    ],
                }
            ]
        },
    )
    _write_json(
        root / "memory" / "state" / "timeline.json",
        {
            "events": [
                {
                    "id": "event_001",
                    "chapter": 1,
                    "in_story_time": "第1天",
                    "summary": "林澈来到旧车站。",
                    "reader_visible": True,
                    "location_id": "loc_missing",
                    "participant_ids": ["char_missing"],
                }
            ]
        },
    )

    report = validate_project(root)

    assert report.ok
    assert any("char_missing" in msg.message for msg in report.warnings)
    assert any("loc_missing" in msg.message for msg in report.warnings)


def test_validate_reports_state_transition_errors(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_json(
        root / "memory" / "canon" / "characters.json",
        {
            "characters": [
                {
                    "id": "char_lin_che",
                    "name": "林澈",
                    "role": "protagonist",
                    "reader_visible_summary": "旧物修复师。",
                }
            ]
        },
    )
    _write_json(
        root / "memory" / "canon" / "locations.json",
        {
            "locations": [
                {
                    "id": "loc_old_station",
                    "name": "旧车站",
                    "type": "交通设施",
                    "reader_visible_summary": "废弃车站。",
                }
            ]
        },
    )
    _write_json(
        root / "memory" / "canon" / "items.json",
        {
            "items": [
                {
                    "id": "item_ticket",
                    "name": "车票",
                    "type": "线索",
                    "reader_visible_summary": "半张旧车票。",
                }
            ]
        },
    )
    _write_json(
        root / "memory" / "state" / "current_state.json",
        {
            "story_position": {"latest_chapter": 1},
            "character_states": [
                {
                    "entity_id": "char_lin_che",
                    "possessions": ["item_ticket"],
                    "last_updated_chapter": 2,
                }
            ],
            "item_states": [
                {
                    "entity_id": "item_ticket",
                    "holder_id": "char_lin_che",
                    "location_id": "loc_old_station",
                    "last_updated_chapter": 1,
                }
            ],
            "location_states": [],
        },
    )

    report = validate_project(root)

    assert not report.ok
    assert any("last_updated_chapter" in msg.message for msg in report.errors)
    assert any("both holder_id and location_id" in msg.message for msg in report.errors)


def test_validate_reports_invalid_chapter_plan_and_audit(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    chapter_dir = root / "memory" / "chapters" / "001"
    chapter_dir.mkdir()
    _write_json(
        chapter_dir / "plan.json",
        {
            "chapter_number": 1,
            "title": "旧车站",
            "goal": "引出异常。",
            "summary": "林澈进入旧车站。",
            "required_context": {
                "canon_entity_ids": [],
                "state_entity_ids": [],
                "timeline_event_ids": [],
            },
            "scenes": [
                {
                    "scene_number": 2,
                    "location_id": "loc_old_station",
                    "participant_ids": [],
                    "purpose": "制造悬疑",
                    "summary": "广播响起。",
                    "emotional_beat": "紧张",
                    "plot_points": [],
                }
            ],
            "must_include": [],
            "must_avoid": [],
            "expected_state_changes": [],
            "ending_hook": "广播叫出了他的名字。",
        },
    )
    _write_json(
        chapter_dir / "audit.json",
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "overall_status": "passed",
            "summary": "有严重问题但被标为通过。",
            "issues": [
                {
                    "id": "audit_001_001",
                    "severity": "high",
                    "type": "state_conflict",
                    "description": "物品状态矛盾。",
                    "suggested_fix": "修正持有人。",
                }
            ],
            "created_at": "2026-05-22T00:00:00Z",
        },
    )

    report = validate_project(root)

    assert not report.ok
    assert any("scene numbers must be sequential" in msg.message for msg in report.errors)
    assert any("passed audit reports cannot contain high" in msg.message for msg in report.errors)


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
