from __future__ import annotations

from dataclasses import dataclass
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


def export_json_schemas(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in schema_payloads().items():
        path = output_dir / f"{name}.schema.json"
        backup_if_exists(path, reason="schema_export")
        atomic_write_json(path, schema)
        written.append(path)
    return written
