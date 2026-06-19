from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Type

from pydantic import BaseModel

from novel.core.io import atomic_write_json, backup_if_exists
from novel.core.schemas import (
    AgentRunLog,
    AgentsConfig,
    AskIntentDecision,
    AuditReport,
    AuditRepairRouteDecision,
    CanonApplyLog,
    CanonProposal,
    ChapterMemory,
    ChapterMetadata,
    ChapterPlan,
    CharactersFile,
    ContextBundle,
    CreationArchiveManifest,
    CreationOutline,
    CreationSession,
    SessionRewriteEvents,
    EmbeddingsConfig,
    EntityState,
    ExportManifest,
    ForeshadowingFile,
    GeneratedStyleGuide,
    HiddenTruthsFile,
    InspirationBrief,
    ItemsFile,
    LocationsFile,
    ManagementEvent,
    MemoryChangeClarificationDecision,
    MemoryChangeClarificationSession,
    MemoryChangeBatchPlan,
    MemoryChangeFollowupAction,
    MemoryChangeImpact,
    MemoryRepairApplyLog,
    MemoryRepairDecision,
    MemoryRepairProposal,
    ProjectConfig,
    RevisionRouteDecision,
    RevisionLog,
    StateUpdateApplyLog,
    StateUpdateProposal,
    TimelineFile,
    WorldFile,
)


@dataclass(frozen=True)
class SchemaDefinition:
    name: str
    model: Type[BaseModel]
    description: str


SCHEMA_DEFINITIONS: tuple[SchemaDefinition, ...] = (
    SchemaDefinition("project", ProjectConfig, "project.yaml"),
    SchemaDefinition("agents", AgentsConfig, "config/agents.yaml"),
    SchemaDefinition("embeddings", EmbeddingsConfig, "config/embeddings.yaml"),
    SchemaDefinition("inspiration", InspirationBrief, "memory/inspiration.json"),
    SchemaDefinition("characters", CharactersFile, "memory/canon/characters.json"),
    SchemaDefinition("locations", LocationsFile, "memory/canon/locations.json"),
    SchemaDefinition("items", ItemsFile, "memory/canon/items.json"),
    SchemaDefinition("world", WorldFile, "memory/canon/world.json"),
    SchemaDefinition("hidden_truths", HiddenTruthsFile, "memory/canon/hidden_truths.json"),
    SchemaDefinition("foreshadowing", ForeshadowingFile, "memory/canon/foreshadowing.json"),
    SchemaDefinition("canon_proposal", CanonProposal, "canon proposal JSON"),
    SchemaDefinition("canon_apply_log", CanonApplyLog, "memory/canon/applied_proposals/{canon_apply_id}/apply_log.json"),
    SchemaDefinition("current_state", EntityState, "memory/state/current_state.json"),
    SchemaDefinition("timeline", TimelineFile, "memory/state/timeline.json"),
    SchemaDefinition("chapter_plan", ChapterPlan, "memory/chapters/{chapter}/plan.json"),
    SchemaDefinition("chapter_memory", ChapterMemory, "memory/chapters/{chapter}/chapter_memory.json"),
    SchemaDefinition("context_bundle", ContextBundle, "memory/chapters/{chapter}/context_report.json"),
    SchemaDefinition("audit_report", AuditReport, "memory/chapters/{chapter}/audit.json"),
    SchemaDefinition(
        "state_update_proposal",
        StateUpdateProposal,
        "memory/chapters/{chapter}/state_update_proposal.json",
    ),
    SchemaDefinition(
        "state_update_apply_log",
        StateUpdateApplyLog,
        "memory/chapters/{chapter}/state_update_apply_log.json",
    ),
    SchemaDefinition("chapter_metadata", ChapterMetadata, "memory/chapters/{chapter}/metadata.json"),
    SchemaDefinition("revision_log", RevisionLog, "memory/chapters/{chapter}/revision_log.json"),
    SchemaDefinition("creation_session", CreationSession, "memory/sessions/{session_id}/session.json"),
    SchemaDefinition("creation_outline", CreationOutline, "memory/sessions/{session_id}/outline_proposal.json"),
    SchemaDefinition("session_rewrite_events", SessionRewriteEvents, "memory/sessions/{session_id}/rewrite_events.json"),
    SchemaDefinition("memory_change_impact", MemoryChangeImpact, "embedded setting-change impact analysis"),
    SchemaDefinition("memory_change_followup_action", MemoryChangeFollowupAction, "embedded setting-change follow-up action"),
    SchemaDefinition("memory_change_clarification_decision", MemoryChangeClarificationDecision, "setting-change clarification gate output"),
    SchemaDefinition("memory_change_clarification_session", MemoryChangeClarificationSession, "memory/repairs/clarifications/{clarification_id}/session.json"),
    SchemaDefinition("memory_change_batch_plan", MemoryChangeBatchPlan, "setting-change batched generation plan"),
    SchemaDefinition("memory_repair_proposal", MemoryRepairProposal, "memory/repairs/{repair_id}/proposal.json"),
    SchemaDefinition("memory_repair_apply_log", MemoryRepairApplyLog, "memory/repairs/{repair_id}/apply_log.json"),
    SchemaDefinition("management_event", ManagementEvent, "memory/management_events.jsonl line"),
    SchemaDefinition("creation_archive", CreationArchiveManifest, "memory/archive/{session_id}/manifest.json"),
    SchemaDefinition("run_log", AgentRunLog, "runs/run_*.json"),
    SchemaDefinition("export_manifest", ExportManifest, "exports/export_manifest.json"),
)

MODEL_OUTPUT_SCHEMA_DEFINITIONS: tuple[SchemaDefinition, ...] = (
    SchemaDefinition("InspirationBrief", InspirationBrief, "Inspiration Agent structured output"),
    SchemaDefinition("GeneratedStyleGuide", GeneratedStyleGuide, "Style Guide Agent structured output"),
    SchemaDefinition("CanonProposal", CanonProposal, "Canon Agent structured output"),
    SchemaDefinition("ChapterPlan", ChapterPlan, "Plot Agent structured output"),
    SchemaDefinition("AuditReport", AuditReport, "Audit Agent structured output"),
    SchemaDefinition("StateUpdateProposal", StateUpdateProposal, "State Manager structured output"),
    SchemaDefinition("ChapterMemory", ChapterMemory, "ChapterMemory Agent structured output"),
    SchemaDefinition("MemoryChangeImpact", MemoryChangeImpact, "setting-change impact analysis"),
    SchemaDefinition("MemoryChangeFollowupAction", MemoryChangeFollowupAction, "setting-change follow-up action"),
    SchemaDefinition(
        "MemoryChangeClarificationDecision",
        MemoryChangeClarificationDecision,
        "setting-change clarification gate output",
    ),
    SchemaDefinition(
        "MemoryChangeClarificationSession",
        MemoryChangeClarificationSession,
        "setting-change clarification session",
    ),
    SchemaDefinition("MemoryChangeBatchPlan", MemoryChangeBatchPlan, "setting-change batched generation plan"),
    SchemaDefinition("MemoryRepairDecision", MemoryRepairDecision, "memory repair decision output"),
    SchemaDefinition("MemoryRepairProposal", MemoryRepairProposal, "memory repair proposal"),
    SchemaDefinition("AskIntentDecision", AskIntentDecision, "orchestrator ask intent route output"),
    SchemaDefinition("RevisionRouteDecision", RevisionRouteDecision, "orchestrator revision route output"),
    SchemaDefinition("AuditRepairRouteDecision", AuditRepairRouteDecision, "orchestrator audit repair route output"),
)


def schema_payloads() -> dict[str, dict[str, object]]:
    return {
        definition.name: definition.model.model_json_schema()
        for definition in SCHEMA_DEFINITIONS
    }


def model_output_schema_payloads() -> dict[str, dict[str, object]]:
    return {
        definition.name: definition.model.model_json_schema()
        for definition in MODEL_OUTPUT_SCHEMA_DEFINITIONS
    }


def model_output_schema_payload(name: str) -> dict[str, object] | None:
    return model_output_schema_payloads().get(name)


def model_output_schema_skeleton(name: str, *, max_chars: int = 2400) -> str | None:
    schema = model_output_schema_payload(name)
    if schema is None:
        return None
    defs_obj = schema.get("$defs")
    defs = defs_obj if isinstance(defs_obj, dict) else {}
    skeleton = _schema_skeleton(schema, defs)
    text = json.dumps(skeleton, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n... <truncated schema skeleton>"


def strict_model_output_schema_payload(name: str) -> dict[str, object] | None:
    schema = model_output_schema_payload(name)
    if schema is None:
        return None
    strict_schema = deepcopy(schema)
    _strictify_schema_node(strict_schema, path=name)
    return strict_schema


def export_json_schemas(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in schema_payloads().items():
        path = output_dir / f"{name}.schema.json"
        backup_if_exists(path, reason="schema_export")
        atomic_write_json(path, schema)
        written.append(path)
    return written


def _schema_skeleton(node: object, defs: dict[str, object], *, depth: int = 0) -> object:
    if depth > 6:
        return "..."
    if not isinstance(node, dict):
        return "value"
    ref = node.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        target = defs.get(name)
        return _schema_skeleton(target, defs, depth=depth + 1) if target is not None else "value"
    if "const" in node:
        return node["const"]
    enum = node.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    any_of = node.get("anyOf")
    if isinstance(any_of, list) and any_of:
        non_null = [item for item in any_of if not (isinstance(item, dict) and item.get("type") == "null")]
        return _schema_skeleton(non_null[0] if non_null else any_of[0], defs, depth=depth + 1)
    node_type = node.get("type")
    if node_type == "object" or "properties" in node:
        props = node.get("properties")
        if not isinstance(props, dict):
            return {}
        return {
            str(key): _schema_skeleton(value, defs, depth=depth + 1)
            for key, value in list(props.items())[:24]
        }
    if node_type == "array":
        return [_schema_skeleton(node.get("items"), defs, depth=depth + 1)]
    if node_type == "integer":
        return 1
    if node_type == "number":
        return 0
    if node_type == "boolean":
        return False
    if node_type == "null":
        return None
    return "string"


def _strictify_schema_node(node: object, *, path: str) -> None:
    if isinstance(node, list):
        for index, item in enumerate(node):
            _strictify_schema_node(item, path=f"{path}/{index}")
        return
    if not isinstance(node, dict):
        return

    if "const" in node:
        node["enum"] = [node.pop("const")]
    for key in ("default", "format", "title", "examples", "minimum", "maximum", "minLength", "maxLength", "pattern", "minItems", "maxItems"):
        node.pop(key, None)

    if node.get("type") == "object" or "properties" in node:
        props = node.get("properties")
        if props is None:
            raise ValueError(f"{path} contains an unconstrained object and cannot be converted to strict JSON schema")
        if not isinstance(props, dict):
            raise ValueError(f"{path}.properties must be an object for strict JSON schema")
        node["required"] = list(props.keys())
        node["additionalProperties"] = False

    for key, value in list(node.items()):
        if key in {"properties", "$defs"} and isinstance(value, dict):
            for child_key, child_value in value.items():
                _strictify_schema_node(child_value, path=f"{path}/{key}/{child_key}")
            continue
        _strictify_schema_node(value, path=f"{path}/{key}")
