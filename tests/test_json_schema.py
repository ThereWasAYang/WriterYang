from __future__ import annotations

import json
from pathlib import Path

from novel.cli import main
from novel.core.json_schema import SCHEMA_DEFINITIONS, export_json_schemas, schema_payloads


def test_schema_payloads_cover_project_json_files() -> None:
    payloads = schema_payloads()
    expected = {
        "project",
        "agents",
        "embeddings",
        "inspiration",
        "characters",
        "locations",
        "items",
        "world",
        "hidden_truths",
        "foreshadowing",
        "canon_proposal",
        "canon_apply_log",
        "current_state",
        "timeline",
        "chapter_plan",
        "chapter_memory",
        "context_bundle",
        "audit_report",
        "state_update_proposal",
        "state_update_apply_log",
        "chapter_metadata",
        "revision_log",
        "creation_session",
        "creation_outline",
        "session_rewrite_events",
        "memory_repair_proposal",
        "memory_repair_apply_log",
        "management_event",
        "creation_archive",
        "run_log",
        "export_manifest",
    }

    assert set(payloads) == expected
    assert len(SCHEMA_DEFINITIONS) == len(expected)
    assert payloads["chapter_plan"]["title"] == "ChapterPlan"
    assert "schema_version" in payloads["characters"]["properties"]
    assert "schema_version" in payloads["audit_report"]["properties"]
    timeline_defs = payloads["timeline"]["$defs"]
    event_props = timeline_defs["TimelineEvent"]["properties"]
    assert "narrative_position" in event_props
    assert "story_position" in event_props
    assert "event_role" in event_props


def test_export_json_schemas_writes_files(tmp_path: Path) -> None:
    output = tmp_path / "schemas"

    paths = export_json_schemas(output)

    assert len(paths) == len(SCHEMA_DEFINITIONS)
    schema = json.loads((output / "chapter_plan.schema.json").read_text(encoding="utf-8"))
    assert schema["title"] == "ChapterPlan"


def test_schema_export_cli(tmp_path: Path) -> None:
    output = tmp_path / "schemas"

    code = main(["schema", "export", "--output", str(output), "--quiet"])

    assert code == 0
    assert (output / "audit_report.schema.json").is_file()
