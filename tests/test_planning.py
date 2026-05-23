from __future__ import annotations

import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from novel.cli import main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.io import load_json_model
from novel.core.planning import (
    ChapterPlanningOptions,
    default_mock_chapter_plan_json,
    parse_chapter_plan,
    plan_chapter,
)
from novel.core.providers import MockProvider
from novel.core.schemas import ChapterPlan
from novel.core.workspace import InitOptions, init_workspace


def test_mock_provider_can_generate_chapter_plan(tmp_path: Path) -> None:
    root = _workspace_with_canon(tmp_path)
    provider = MockProvider(fake_response=default_mock_chapter_plan_json(2))

    result = plan_chapter(
        ChapterPlanningOptions(
            root=root,
            chapter_number=2,
            instruction="这一章要让主角第一次怀疑沈鹿的身份，但不要揭示真相",
        ),
        provider,
    )

    assert result.plan.chapter_number == 2
    assert provider.requests[0].json_schema_name == "ChapterPlan"
    assert "不要写正文" in provider.requests[0].system_prompt
    assert "第一次怀疑沈鹿" in provider.requests[0].user_prompt


def test_plan_chapter_cli_creates_plan_json_and_markdown(tmp_path: Path) -> None:
    root = _workspace_with_canon(tmp_path)

    code, stdout, stderr = _run_cli(["plan-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert "Wrote chapter plan JSON:" in stdout
    assert "Validation passed:" in stdout
    plan_json = root / "memory" / "chapters" / "001" / "plan.json"
    plan_md = root / "memory" / "chapters" / "001" / "plan.md"
    assert plan_json.is_file()
    assert plan_md.is_file()
    plan = load_json_model(plan_json, ChapterPlan)
    assert plan.chapter_number == 1
    assert [scene.scene_number for scene in plan.scenes] == [1, 2]
    assert plan.required_context.canon_entity_ids
    assert plan.scenes[0].location_id == "loc_old_station"
    assert plan.scenes[0].participant_ids == ["char_lin_che"]
    markdown = plan_md.read_text(encoding="utf-8")
    assert "# Chapter 001: 雨夜旧车站" in markdown
    assert "## Expected State Changes" in markdown


def test_parse_chapter_plan_normalizes_object_state_changes() -> None:
    payload = json.loads(default_mock_chapter_plan_json(1))
    payload["expected_state_changes"] = [
        {"entity_id": "char_lin_che", "change": "开始怀疑广播来源"},
    ]

    plan = parse_chapter_plan(json.dumps(payload, ensure_ascii=False))

    assert plan.expected_state_changes == [
        '{"change": "开始怀疑广播来源", "entity_id": "char_lin_che"}'
    ]


def test_parse_chapter_plan_normalizes_mapping_state_changes() -> None:
    payload = json.loads(default_mock_chapter_plan_json(1))
    payload["expected_state_changes"] = {
        "character_states": [{"character_id": "char_lin_che", "change": "警觉"}],
    }

    plan = parse_chapter_plan(json.dumps(payload, ensure_ascii=False))

    assert plan.expected_state_changes == [
        '{"character_states": [{"change": "警觉", "character_id": "char_lin_che"}]}'
    ]


def test_parse_chapter_plan_normalizes_required_context_list() -> None:
    payload = json.loads(default_mock_chapter_plan_json(1))
    payload["required_context"] = ["char_lin_che", "loc_old_station"]

    plan = parse_chapter_plan(json.dumps(payload, ensure_ascii=False))

    assert plan.required_context.canon_entity_ids == ["char_lin_che", "loc_old_station"]


def test_plan_chapter_refuses_to_overwrite_existing_plan_by_default(tmp_path: Path) -> None:
    root = _workspace_with_canon(tmp_path)
    first, _, _ = _run_cli(["plan-chapter", "1", "--path", str(root), "--provider", "mock"])
    plan_json = root / "memory" / "chapters" / "001" / "plan.json"
    original = plan_json.read_text(encoding="utf-8")

    second, stdout, stderr = _run_cli(["plan-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert first == 0
    assert second == 1
    assert stdout == ""
    assert "already exists" in stderr
    assert plan_json.read_text(encoding="utf-8") == original


def test_plan_chapter_force_overwrites_existing_plan(tmp_path: Path) -> None:
    root = _workspace_with_canon(tmp_path)
    _run_cli(["plan-chapter", "1", "--path", str(root), "--provider", "mock"])
    plan_json = root / "memory" / "chapters" / "001" / "plan.json"
    plan_json.write_text('{"manually_modified": true}\n', encoding="utf-8")

    code, stdout, stderr = _run_cli(
        ["plan-chapter", "1", "--path", str(root), "--provider", "mock", "--force"]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote chapter plan JSON:" in stdout
    data = json.loads(plan_json.read_text(encoding="utf-8"))
    assert data["chapter_number"] == 1
    assert "manually_modified" not in data


def test_plan_chapter_input_file_instruction_reaches_prompt(tmp_path: Path) -> None:
    root = _workspace_with_canon(tmp_path)
    input_path = tmp_path / "chapter3_request.txt"
    input_path.write_text("这一章要让主角第一次怀疑沈鹿的身份，但不要揭示真相", encoding="utf-8")

    code, stdout, stderr = _run_cli(
        [
            "plan-chapter",
            "3",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--input",
            str(input_path),
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote chapter plan JSON:" in stdout
    plan = load_json_model(root / "memory" / "chapters" / "003" / "plan.json", ChapterPlan)
    assert plan.chapter_number == 3


def test_plan_chapter_missing_inspiration_has_clear_error(tmp_path: Path) -> None:
    root = _workspace_with_canon(tmp_path)
    (root / "memory" / "inspiration.md").unlink()

    code, stdout, stderr = _run_cli(["plan-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 1
    assert stdout == ""
    assert "memory/inspiration.md is missing" in stderr


def test_plan_chapter_missing_canon_has_clear_error(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text("# Inspiration\n\n有灵感。\n", encoding="utf-8")

    code, stdout, stderr = _run_cli(["plan-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 1
    assert stdout == ""
    assert "canon has no characters" in stderr


def test_plan_validation_warns_for_missing_references(tmp_path: Path) -> None:
    root = _workspace_with_canon(tmp_path)
    bad_plan = json.loads(default_mock_chapter_plan_json(1))
    bad_plan["scenes"][0]["location_id"] = "loc_missing"
    provider = MockProvider(fake_response=json.dumps(bad_plan, ensure_ascii=False))

    result = plan_chapter(ChapterPlanningOptions(root=root, chapter_number=1), provider)

    assert result.validation_report.ok
    assert any("loc_missing" in message.message for message in result.validation_report.warnings)


def _workspace_with_canon(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# Inspiration\n\n## Weak Outline\n\n雨夜旧车站传来停播多年的广播声。\n",
        encoding="utf-8",
    )
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    result = apply_canon_proposal(root, proposal_path)
    assert result.validation_report.ok
    return root


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
