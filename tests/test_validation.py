from __future__ import annotations

import json
from pathlib import Path

from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.io import load_json_model, load_yaml_model
from novel.core.planning import ChapterPlanningOptions, default_mock_chapter_plan_json, plan_chapter
from novel.core.providers import MockProvider
from novel.core.schemas import Character, ProjectConfig, StateUpdateProposal, TimelineEvent
from novel.core.session import (
    SessionActionOptions,
    SessionRunOptions,
    SessionStartOptions,
    accept_session,
    approve_outline,
    run_session,
    start_session,
)
from novel.core.validation import validate_canon, validate_project
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
                "role": "主角",
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


def test_timeline_event_rejects_legacy_top_level_fields() -> None:
    payload = {
        "id": "event_legacy",
        "chapter": 2,
        "scene": 1,
        "in_story_time": "第2章",
        "narrative_position": {"chapter": 1, "scene": 1},
        "story_position": {"time_label": "第2章"},
        "summary": "旧字段不再接受。",
        "reader_visible": True,
    }

    try:
        TimelineEvent.model_validate(payload)
    except Exception as exc:
        assert "chapter" in str(exc)
    else:
        raise AssertionError("expected legacy timeline field rejection")


def test_timeline_event_allows_unrevealed_background_without_narrative_position() -> None:
    payload = {
        "id": "event_background",
        "story_position": {"time_label": "开篇前约十年"},
        "summary": "尚未在正文揭示的背景事件。",
        "reader_visible": False,
        "event_role": "backstory",
    }

    event = TimelineEvent.model_validate(payload)
    null_event = TimelineEvent.model_validate({**payload, "id": "event_background_null", "narrative_position": None})

    assert event.narrative_position is None
    assert null_event.narrative_position is None


def test_timeline_event_still_rejects_chapter_zero_when_narrative_position_exists() -> None:
    payload = {
        "id": "event_bad_chapter",
        "narrative_position": {"chapter": 0},
        "story_position": {"time_label": "开篇前约十年"},
        "summary": "错误使用第 0 章的背景事件。",
        "reader_visible": False,
        "event_role": "backstory",
    }

    try:
        TimelineEvent.model_validate(payload)
    except Exception as exc:
        assert "greater than or equal to 1" in str(exc)
    else:
        raise AssertionError("expected chapter zero rejection")


def test_state_update_proposal_still_requires_anchored_timeline_events() -> None:
    payload = {
        "chapter_number": 1,
        "state_changes": [],
        "timeline_events": [
            {
                "id": "event_unanchored",
                "story_position": {"time_label": "第1章"},
                "summary": "章节抽取不能省略正文位置。",
                "reader_visible": True,
            }
        ],
        "warnings": [],
        "created_at": "2026-06-14T00:00:00Z",
    }

    try:
        StateUpdateProposal.model_validate(payload)
    except Exception as exc:
        assert "narrative_position" in str(exc)
    else:
        raise AssertionError("expected StateUpdateProposal narrative_position requirement")


def test_validate_fresh_workspace_passes(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))

    report = validate_project(root)

    assert report.ok
    assert report.errors == []


def test_validate_project_allows_unrevealed_background_timeline_events(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "state" / "timeline.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "events": [
                    {
                        "id": "event_background",
                        "summary": "尚未正文揭示的背景事件。",
                        "reader_visible": False,
                        "story_position": {"time_label": "开篇前约十年"},
                        "event_role": "backstory",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    report = validate_project(root)

    assert report.ok
    assert report.errors == []


def test_validate_warns_when_default_agent_config_missing(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "config" / "agents.yaml").write_text(
        "\n".join(
            [
                "profiles:",
                "  scribe:",
                '    provider: "openai_compatible"',
                '    api_key_env: "SCRIBE_API_KEY"',
                '    model: "scribe-model"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = validate_project(root)

    assert report.ok
    assert any("缺少 default API 配置" in message.message for message in report.warnings)


def test_validate_warns_when_agent_config_uses_mock(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "config" / "agents.yaml").write_text(
        "\n".join(
            [
                "default:",
                '  provider: "mock"',
                '  api_key_env: "MOCK_API_KEY"',
                '  model: "mock-model"',
                "profiles:",
                "  scribe:",
                '    provider: "mock"',
                '    api_key_env: "MOCK_API_KEY"',
                '    model: "mock-model"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = validate_project(root)

    assert report.ok
    assert any("default API 配置使用 mock provider" in message.message for message in report.warnings)
    assert any("profile scribe 使用 mock provider" in message.message for message in report.warnings)


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
            "by_task": {"writer": {"calls": 1, "total_tokens": 1000}},
        },
    )

    report = validate_project(root)

    assert report.ok
    assert not any("provider_usage" in str(message.path) for message in report.errors)


def test_validate_canon_does_not_warn_about_chapter_timeline_context(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text("# Inspiration\n\n有灵感。\n", encoding="utf-8")
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    _write_json(
        root / "memory" / "state" / "timeline.json",
        {
            "events": [
                {
                    "id": "event_existing",
                    "narrative_position": {"chapter": 1},
                    "story_position": {"time_label": "第一章"},
                    "summary": "已有事件。",
                    "reader_visible": True,
                }
            ]
        },
    )
    payload = json.loads(default_mock_chapter_plan_json(1))
    payload["required_context"]["timeline_event_ids"] = ["event_existing"]
    plan_chapter(
        ChapterPlanningOptions(root=root, chapter_number=1),
        MockProvider(fake_response=json.dumps(payload, ensure_ascii=False)),
    )

    report = validate_canon(root)

    assert report.ok
    assert not any("required_context" in message.message for message in report.warnings)


def test_validate_reports_unsupported_file_schema_version(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_json(root / "memory" / "canon" / "characters.json", {"schema_version": 999, "characters": []})

    report = validate_project(root)

    assert not report.ok
    assert any("不支持的 schema_version：999" in msg.message for msg in report.errors)


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
                    "role": "主角",
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
    assert any("重复的 character id：char_lin_che" in msg.message for msg in report.errors)


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
                    "role": "主角",
                }
            ]
        },
    )

    report = validate_project(root)

    assert not report.ok
    assert any("reader_visible_summary" in msg.message for msg in report.errors)
    assert any("缺少必填字段" in msg.message for msg in report.errors)


def test_validate_common_error_messages_do_not_leak_english_prose(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "canon" / "characters.json").write_text("{bad json", encoding="utf-8")
    (root / "config" / "agents.yaml").write_text("default: [\n", encoding="utf-8")

    report = validate_project(root)

    messages = [message.message for message in report.messages]
    assert any("无法读取 JSON 文件" in message for message in messages)
    assert any("无法读取 YAML 文件" in message for message in messages)
    forbidden_fragments = (
        "could not load",
        "required file is missing",
        "unsupported schema_version",
        "duplicate character id",
        "references missing",
    )
    for fragment in forbidden_fragments:
        assert not any(fragment in message for message in messages)


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
                    "role": "主角",
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
                    "narrative_position": {"chapter": 1},
                    "story_position": {"time_label": "第1天"},
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
                    "role": "主角",
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
    assert any("同时设置了 holder_id 和 location_id" in msg.message for msg in report.errors)


def test_validate_reports_item_holder_missing_possession_mirror(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_json(
        root / "memory" / "canon" / "characters.json",
        {
            "characters": [
                {"id": "char_lin_che", "name": "林澈", "role": "主角", "reader_visible_summary": "旧物修复师。"}
            ]
        },
    )
    _write_json(
        root / "memory" / "canon" / "items.json",
        {"items": [{"id": "item_ticket", "name": "车票", "type": "线索", "reader_visible_summary": "半张旧车票。"}]},
    )
    _write_json(
        root / "memory" / "state" / "current_state.json",
        {
            "story_position": {"latest_chapter": 1},
            "character_states": [{"entity_id": "char_lin_che", "possessions": [], "last_updated_chapter": 1}],
            "item_states": [{"entity_id": "item_ticket", "holder_id": "char_lin_che", "last_updated_chapter": 1}],
            "location_states": [],
        },
    )

    report = validate_project(root)

    assert report.ok
    assert any("cons_item_holder_missing_possession_item_ticket" in msg.message for msg in report.warnings)
    assert any("持有角色的 possessions 未包含该物品" in msg.message for msg in report.warnings)


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
                    "narrative_position": {"chapter": 2},
                    "story_position": {"time_label": "第二章"},
                    "summary": "甲再次出现。",
                    "reader_visible": True,
                    "participant_ids": ["char_a"],
                }
            ]
        },
    )

    report = validate_project(root)

    assert not report.ok
    assert any("同时出现在多个角色的 possessions 中" in msg.message for msg in report.errors)
    assert any("在死亡记录章节 1 之后仍出现在事件 event_after_death 中" in msg.message for msg in report.warnings)


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
            "audited_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
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
    assert any(
        msg.path.name == "plan.json"
        and "字段值未通过业务校验：scene_number 必须从 1 开始连续" in msg.message
        for msg in report.errors
    )
    assert any(
        msg.path.name == "audit.json"
        and "字段值未通过业务校验：passed audit report 不能包含 medium、high 或 critical issue" in msg.message
        for msg in report.errors
    )
    assert not any("scene numbers must be sequential" in msg.message for msg in report.errors)
    assert not any("passed audit reports cannot contain" in msg.message for msg in report.errors)


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
    assert any("polished_path 引用了不存在的文件" in msg.message for msg in report.errors)
    assert any("front matter chapter_number" in msg.message and "不一致" in msg.message for msg in report.errors)


def test_validate_fails_accepted_chapter_with_missing_acceptance(tmp_path: Path) -> None:
    root = _workspace_with_accepted_chapter(tmp_path)
    audit_path = root / "memory" / "chapters" / "001" / "audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["overall_status"] = "blocked"
    audit["summary"] = "存在阻断问题。"
    audit["issues"] = [
        {
            "id": "audit_001_critical",
            "severity": "critical",
            "type": "state_conflict",
            "description": "章节编号错乱。",
            "evidence": [{"source": "audit.json", "quote": "critical"}],
            "suggested_fix": "重新审核并修复。",
        }
    ]
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "memory" / "chapters" / "001" / "acceptance.json").unlink()

    report = validate_project(root)

    assert not report.ok
    assert any("有效的 AcceptanceCommit" in msg.message for msg in report.errors)


def test_validate_ignores_non_authoritative_working_audit_for_accepted_chapter(tmp_path: Path) -> None:
    root = _workspace_with_accepted_chapter(tmp_path)
    audit_path = root / "memory" / "chapters" / "001" / "audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["overall_status"] = "needs_revision"
    audit["summary"] = "存在轻微问题。"
    audit["issues"] = [
        {
            "id": "audit_001_low",
            "severity": "low",
            "type": "style_mismatch",
            "description": "局部语气略可调整。",
            "evidence": [{"source": "polished.md", "quote": "雨声"}],
            "suggested_fix": "可后续微调。",
        }
    ]
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = validate_project(root)

    assert report.ok
    assert not any("已认可章节的 audit 仍有非阻断问题" in msg.message for msg in report.warnings)


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
    leak_errors = [msg for msg in report.errors if "reader_visible_summary 包含隐藏真相" in msg.message]
    assert len(leak_errors) == 1
    assert "hidden truth" not in leak_errors[0].message


def _workspace_with_accepted_chapter(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text("# Inspiration\n\n旧车站广播。\n", encoding="utf-8")
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    started = start_session(
        SessionStartOptions(
            root=root,
            user_intent="写第一章。",
            chapter_range=(1,),
            provider_name="mock",
        )
    )
    session_id = started.session.session_id
    approve_outline(SessionActionOptions(root=root, session_id=session_id))
    run_session(SessionRunOptions(root=root, session_id=session_id, provider_name="mock"))
    accept_session(SessionActionOptions(root=root, session_id=session_id, provider_name="mock"))
    return root


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
