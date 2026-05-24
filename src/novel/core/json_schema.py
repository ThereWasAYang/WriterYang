from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Type

from pydantic import BaseModel

from novel.core.schemas import (
    AgentRunLog,
    AgentsConfig,
    AuditReport,
    CanonProposal,
    ChapterMetadata,
    ChapterPlan,
    CharactersFile,
    EmbeddingsConfig,
    EntityState,
    ExportManifest,
    ForeshadowingFile,
    HiddenTruthsFile,
    InspirationBrief,
    ItemsFile,
    LocationsFile,
    ProjectConfig,
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
    SchemaDefinition("current_state", EntityState, "memory/state/current_state.json"),
    SchemaDefinition("timeline", TimelineFile, "memory/state/timeline.json"),
    SchemaDefinition("chapter_plan", ChapterPlan, "memory/chapters/{chapter}/plan.json"),
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
    SchemaDefinition("run_log", AgentRunLog, "runs/run_*.json"),
    SchemaDefinition("export_manifest", ExportManifest, "exports/export_manifest.json"),
)


def schema_payloads() -> dict[str, dict[str, object]]:
    return {
        definition.name: definition.model.model_json_schema()
        for definition in SCHEMA_DEFINITIONS
    }


def export_json_schemas(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in schema_payloads().items():
        path = output_dir / f"{name}.schema.json"
        path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written
