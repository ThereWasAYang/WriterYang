from __future__ import annotations

import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from novel.cli import main
from novel.core.workspace import InitOptions, init_workspace


def test_validate_cli_reports_success(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))

    code, stdout, stderr = _run_cli(["validate", "--path", str(root)])

    assert code == 0
    assert "agent default api_key_env is not set: OPENAI_API_KEY" in stdout
    assert "Validation passed: 2 warning(s)" in stdout
    assert stderr == ""


def test_validate_cli_reports_errors_and_nonzero_exit(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "canon" / "characters.json").write_text("{bad json", encoding="utf-8")

    code, stdout, stderr = _run_cli(["validate", "--path", str(root)])

    assert code == 1
    assert "error: memory/canon/characters.json: could not load JSON" in stdout
    assert "Validation failed:" in stderr


def test_status_cli_shows_project_summary_and_latest_run(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_sample_project_data(root)
    _write_json(
        root / "runs" / "run_20260522_001.json",
        {"run_id": "run_20260522_001", "task": "validate", "status": "completed"},
    )

    code, stdout, stderr = _run_cli(["status", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "Project: 雨夜旧车站" in stdout
    assert "Latest chapter: 1" in stdout
    assert "Inspiration: present" in stdout
    assert "Characters: 1" in stdout
    assert "Locations: 1" in stdout
    assert "Items: 1" in stdout
    assert "Timeline events: 1" in stdout
    assert "Latest run log: runs/run_20260522_001.json" in stdout
    assert "run_id=run_20260522_001" in stdout


def test_show_characters_cli_formats_character_list(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_sample_project_data(root)

    code, stdout, stderr = _run_cli(["show", "characters", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "Characters:" in stdout
    assert "- 林澈 [char_lin_che]" in stdout
    assert "Role: protagonist" in stdout
    assert "Aliases: 阿澈" in stdout


def test_show_timeline_cli_formats_events(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_sample_project_data(root)

    code, stdout, stderr = _run_cli(["show", "timeline", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "Timeline:" in stdout
    assert "Chapter 1, scene 1" in stdout
    assert "林澈第一次听见旧广播。" in stdout
    assert "Participants: char_lin_che" in stdout


def test_show_state_cli_formats_current_state(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_sample_project_data(root)

    code, stdout, stderr = _run_cli(["show", "state", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "Current State:" in stdout
    assert "Latest chapter: 1" in stdout
    assert "char_lin_che: location=loc_old_station" in stdout
    assert "item_ticket: holder=char_lin_che" in stdout


def test_show_cli_reports_missing_or_invalid_files_clearly(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "state" / "timeline.json").unlink()

    code, stdout, stderr = _run_cli(["show", "timeline", "--path", str(root)])

    assert code == 1
    assert stdout == ""
    assert "error:" in stderr
    assert "timeline.json is missing" in stderr


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()


def _write_sample_project_data(root: Path) -> None:
    _write_json(
        root / "memory" / "canon" / "characters.json",
        {
            "characters": [
                {
                    "id": "char_lin_che",
                    "name": "林澈",
                    "aliases": ["阿澈"],
                    "role": "protagonist",
                    "reader_visible_summary": "年轻的旧物修复师，性格沉静。",
                    "tags": ["主角"],
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
                    "reader_visible_summary": "废弃多年的郊区车站。",
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
                    "name": "破损车票",
                    "type": "线索",
                    "reader_visible_summary": "一张被雨水泡皱的旧车票。",
                }
            ]
        },
    )
    _write_json(
        root / "memory" / "state" / "current_state.json",
        {
            "story_position": {
                "latest_chapter": 1,
                "in_story_time": "第1天，23:40",
                "summary": "林澈进入旧车站。",
            },
            "character_states": [
                {
                    "entity_id": "char_lin_che",
                    "location_id": "loc_old_station",
                    "mental_state": "警觉",
                    "possessions": ["item_ticket"],
                    "last_updated_chapter": 1,
                }
            ],
            "item_states": [
                {
                    "entity_id": "item_ticket",
                    "holder_id": "char_lin_che",
                    "condition": "潮湿",
                    "last_updated_chapter": 1,
                }
            ],
            "location_states": [
                {
                    "entity_id": "loc_old_station",
                    "accessibility": "可进入",
                    "condition": "雨夜异常活跃",
                    "last_updated_chapter": 1,
                }
            ],
        },
    )
    _write_json(
        root / "memory" / "state" / "timeline.json",
        {
            "events": [
                {
                    "id": "event_001",
                    "chapter": 1,
                    "scene": 1,
                    "in_story_time": "第1天，23:40",
                    "location_id": "loc_old_station",
                    "participant_ids": ["char_lin_che"],
                    "summary": "林澈第一次听见旧广播。",
                    "reader_visible": True,
                }
            ]
        },
    )


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
