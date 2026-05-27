from __future__ import annotations

import json
from pathlib import Path

from novel.core.io import load_json_model, load_yaml_model
from novel.core.schemas import Character, ProjectConfig
from novel.core.validation import validate_project
from novel.core.workspace import InitOptions, init_workspace
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.planning import ChapterPlanningOptions, default_mock_chapter_plan_json, plan_chapter
from novel.core.drafting import ChapterDraftingOptions, write_chapter_draft
from novel.core.polishing import ChapterPolishingOptions, polish_chapter
from novel.core.auditing import ChapterAuditOptions, default_mock_audit_report_json, audit_chapter
from novel.core.providers import MockProvider
from novel.core.state_update import (
    AcceptChapterOptions,
    StateUpdateProposeOptions,
    accept_chapter,
    default_mock_state_update_proposal_json,
    propose_state_update,
)


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


def test_validate_ignores_provider_usage_summary(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    runs_dir = root / "runs"
    runs_dir.mkdir(exist_ok=True)
    _write_json(
        runs_dir / "provider_usage.json",
        {
            "schema_version": 1,
            "total_calls": 2,
            "total_tokens": 1234,
            "by_agent": {"writer": {"calls": 1, "total_tokens": 1000}},
        },
    )

    report = validate_project(root)

    assert report.ok
    assert not any("provider_usage" in str(message.path) for message in report.errors)


def test_validate_reports_unsupported_file_schema_version(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_json(root / "memory" / "canon" / "characters.json", {"schema_version": 999, "characters": []})

    report = validate_project(root)

    assert not report.ok
    assert any("unsupported schema_version: 999" in msg.message for msg in report.errors)


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


def test_validate_reports_duplicate_possession_owner_and_dead_participant(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_json(
        root / "memory" / "canon" / "characters.json",
        {
            "characters": [
                {"id": "char_a", "name": "甲", "role": "主角", "reader_visible_summary": "甲。"},
                {"id": "char_b", "name": "乙", "role": "配角", "reader_visible_summary": "乙。"},
            ]
        },
    )
    _write_json(
        root / "memory" / "canon" / "items.json",
        {"items": [{"id": "item_key", "name": "钥匙", "type": "道具", "reader_visible_summary": "钥匙。"}]},
    )
    _write_json(
        root / "memory" / "state" / "current_state.json",
        {
            "story_position": {"latest_chapter": 3},
            "character_states": [
                {"entity_id": "char_a", "health": "死亡", "possessions": ["item_key"], "last_updated_chapter": 1},
                {"entity_id": "char_b", "possessions": ["item_key"], "last_updated_chapter": 2},
            ],
            "item_states": [{"entity_id": "item_key", "holder_id": "char_b", "last_updated_chapter": 2}],
            "location_states": [],
        },
    )
    _write_json(
        root / "memory" / "state" / "timeline.json",
        {
            "events": [
                {
                    "id": "event_after_death",
                    "chapter": 2,
                    "in_story_time": "第二章",
                    "summary": "甲再次出现。",
                    "reader_visible": True,
                    "participant_ids": ["char_a"],
                }
            ]
        },
    )

    report = validate_project(root)

    assert not report.ok
    assert any("appears in possessions of both" in msg.message for msg in report.errors)
    assert any("after death state" in msg.message for msg in report.warnings)


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


def test_validate_chapter_plan_allows_world_rule_references(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_json(
        root / "memory" / "canon" / "world.json",
        {
            "world_rules": [
                {
                    "id": "rule_no_supernatural",
                    "name": "现实边界",
                    "description": "异常保持现实解释空间。",
                    "visibility": "reader_visible",
                }
            ]
        },
    )
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
                "canon_entity_ids": ["rule_no_supernatural"],
                "state_entity_ids": [],
                "timeline_event_ids": [],
            },
            "scenes": [
                {
                    "scene_number": 1,
                    "location_id": "loc_missing",
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

    report = validate_project(root)

    assert not any("rule_no_supernatural" in message.message for message in report.warnings)


def test_validate_reports_chapter_output_linkage_errors(tmp_path: Path) -> None:
    root = _workspace_with_accepted_chapter(tmp_path)
    metadata_path = root / "memory" / "chapters" / "001" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["status"] = "accepted"
    metadata["polished_path"] = "memory/chapters/001/missing.md"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    polished_path = root / "memory" / "chapters" / "001" / "polished.md"
    polished = polished_path.read_text(encoding="utf-8").replace("chapter_number: 1", "chapter_number: 2")
    polished_path.write_text(polished, encoding="utf-8")

    report = validate_project(root)

    assert not report.ok
    assert any("polished_path references missing file" in msg.message for msg in report.errors)
    assert any("front matter chapter_number 2 does not match 1" in msg.message for msg in report.errors)


def test_validate_fails_accepted_chapter_without_passed_audit_or_state_apply(tmp_path: Path) -> None:
    root = _workspace_with_accepted_chapter(tmp_path)
    audit_path = root / "memory" / "chapters" / "001" / "audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["overall_status"] = "needs_revision"
    audit["summary"] = "仍需修改。"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "memory" / "chapters" / "001" / "state_update_apply_log.json").unlink()

    report = validate_project(root)

    assert not report.ok
    assert any("Accepted chapter must have a passed audit report" in msg.message for msg in report.errors)
    assert any("Accepted chapter must have an applied state update log" in msg.message for msg in report.errors)


def test_validate_reports_hidden_truth_in_reader_visible_summary(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_json(
        root / "memory" / "canon" / "hidden_truths.json",
        {
            "hidden_truths": [
                {
                    "id": "truth_station_overlap",
                    "title": "旧车站是时间交叠点",
                    "description": "旧车站在特定雨夜会短暂连接过去的时间层。",
                    "visibility": "hidden",
                    "importance": "critical",
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
                    "reader_visible_summary": "旧车站在特定雨夜会短暂连接过去的时间层。",
                }
            ]
        },
    )

    report = validate_project(root)

    assert not report.ok
    assert any("Reader-visible summary" in msg.message for msg in report.errors)


def _workspace_with_accepted_chapter(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text("# Inspiration\n\n旧车站广播。\n", encoding="utf-8")
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    plan_chapter(
        ChapterPlanningOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_chapter_plan_json(1)),
    )
    write_chapter_draft(
        ChapterDraftingOptions(root=root, chapter_number=1),
        MockProvider(fake_response="林澈进入旧车站。"),
    )
    polish_chapter(
        ChapterPolishingOptions(root=root, chapter_number=1),
        MockProvider(fake_response="林澈进入旧车站，雨声在身后合拢。"),
    )
    audit_chapter(
        ChapterAuditOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_audit_report_json(1, "polished.md")),
    )
    propose_state_update(
        StateUpdateProposeOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_state_update_proposal_json(1)),
    )
    accept_chapter(AcceptChapterOptions(root=root, chapter_number=1))
    return root


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
