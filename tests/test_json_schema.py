from __future__ import annotations

import json
from pathlib import Path

from novel.cli import main
from novel.core.json_schema import (
    MODEL_OUTPUT_SCHEMA_DEFINITIONS,
    SCHEMA_DEFINITIONS,
    export_json_schemas,
    model_output_schema_payload,
    model_output_schema_payloads,
    schema_payloads,
)


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
        "memory_change_impact",
        "memory_change_followup_action",
        "memory_change_clarification_decision",
        "memory_change_clarification_session",
        "memory_change_batch_plan",
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
    assert payloads["memory_change_batch_plan"]["title"] == "MemoryChangeBatchPlan"
    assert "schema_version" in payloads["characters"]["properties"]
    assert "schema_version" in payloads["audit_report"]["properties"]
    timeline_defs = payloads["timeline"]["$defs"]
    event_props = timeline_defs["TimelineEvent"]["properties"]
    assert "narrative_position" in event_props
    assert "story_position" in event_props
    assert "event_role" in event_props
    assert "chapter" not in event_props
    assert "scene" not in event_props
    assert "in_story_time" not in event_props
    assert timeline_defs["TimelineEvent"].get("additionalProperties") is False


def test_agents_schema_forbids_task_only_fields_outside_tasks() -> None:
    agents_schema = schema_payloads()["agents"]
    properties = agents_schema["properties"]
    forbidden_fields = {"reasoning", "thinking", "temperature"}

    default_forbidden = {
        item["required"][0]
        for item in properties["default"]["not"]["anyOf"]
    }
    profile_forbidden = {
        item["required"][0]
        for item in properties["profiles"]["additionalProperties"]["not"]["anyOf"]
    }

    assert default_forbidden == forbidden_fields
    assert profile_forbidden == forbidden_fields
    assert "not" not in properties["tasks"]["additionalProperties"]


def test_model_output_schema_payloads_cover_agent_structured_outputs() -> None:
    payloads = model_output_schema_payloads()
    expected = {
        "InspirationBrief",
        "GeneratedStyleGuide",
        "CanonProposal",
        "ChapterPlan",
        "AuditReport",
        "StateUpdateProposal",
        "ChapterMemory",
        "MemoryChangeImpact",
        "MemoryChangeFollowupAction",
        "MemoryChangeClarificationDecision",
        "MemoryChangeClarificationSession",
        "MemoryChangeBatchPlan",
        "MemoryRepairDecision",
        "MemoryRepairProposal",
        "AskIntentDecision",
        "RevisionRouteDecision",
        "AuditRepairRouteDecision",
    }

    assert set(payloads) == expected
    assert len(MODEL_OUTPUT_SCHEMA_DEFINITIONS) == len(expected)
    assert payloads["GeneratedStyleGuide"]["properties"]["style_sources"]
    assert payloads["ChapterPlan"]["title"] == "ChapterPlan"
    assert payloads["MemoryRepairDecision"]["properties"]["operations"]
    assert model_output_schema_payload("AuditReport") == payloads["AuditReport"]
    assert model_output_schema_payload("UnknownSchema") is None


def test_export_json_schemas_writes_files(tmp_path: Path) -> None:
    output = tmp_path / "schemas"

    paths = export_json_schemas(output)

    assert len(paths) == len(SCHEMA_DEFINITIONS)
    schema = json.loads((output / "chapter_plan.schema.json").read_text(encoding="utf-8"))
    assert schema["title"] == "ChapterPlan"


def test_checked_in_json_schemas_match_models() -> None:
    payloads = schema_payloads()

    for name, payload in payloads.items():
        path = Path("schemas") / f"{name}.schema.json"
        assert path.is_file(), path
        checked_in = json.loads(path.read_text(encoding="utf-8"))
        assert checked_in == payload, name


def test_schema_export_cli(tmp_path: Path) -> None:
    output = tmp_path / "schemas"

    code = main(["schema", "export", "--output", str(output), "--quiet"])

    assert code == 0
    assert (output / "audit_report.schema.json").is_file()
