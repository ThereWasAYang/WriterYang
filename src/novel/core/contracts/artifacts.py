from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from novel.core.contracts.common import (
    ArtifactId,
    ArtifactKind,
    SchemaV3Model,
    Sha256,
    TaskId,
)


class ArtifactRef(SchemaV3Model):
    artifact_id: ArtifactId
    kind: ArtifactKind
    path: str = Field(min_length=1)
    sha256: Sha256
    created_at: datetime

    @field_validator("path")
    @classmethod
    def require_project_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
            raise ValueError("artifact path must be project-relative and cannot escape the workspace")
        return normalized


class ArtifactLineage(SchemaV3Model):
    output: ArtifactRef
    inputs: list[ArtifactRef] = Field(default_factory=list)
    task_id: TaskId | None = None
    workflow_run_id: str = Field(min_length=1)
    prompt_hash: Sha256 | None = None
    policy_version: str = Field(min_length=1)


class AuditBinding(SchemaV3Model):
    audit: ArtifactRef
    candidate: ArtifactRef
    context_snapshot_hash: Sha256
    policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_kinds(self) -> AuditBinding:
        if self.audit.kind != ArtifactKind.AUDIT or self.candidate.kind != ArtifactKind.CANDIDATE:
            raise ValueError("audit binding requires audit and candidate artifact kinds")
        return self


class StateProposalBinding(SchemaV3Model):
    proposal: ArtifactRef
    candidate: ArtifactRef
    audit: ArtifactRef
    base_state_sha256: Sha256
    base_timeline_sha256: Sha256

    @model_validator(mode="after")
    def require_kinds(self) -> StateProposalBinding:
        expected = (
            (self.proposal.kind, ArtifactKind.STATE_PROPOSAL),
            (self.candidate.kind, ArtifactKind.CANDIDATE),
            (self.audit.kind, ArtifactKind.AUDIT),
        )
        if any(actual != required for actual, required in expected):
            raise ValueError("state proposal binding contains an invalid artifact kind")
        return self


class ChapterLifecycle(SchemaV3Model):
    chapter_number: int = Field(ge=1)
    active_plan: ArtifactRef | None = None
    active_candidate: ArtifactRef | None = None
    active_audit: AuditBinding | None = None
    active_state_proposal: StateProposalBinding | None = None
    active_acceptance: ArtifactRef | None = None
    lineages: list[ArtifactLineage] = Field(default_factory=list)
    updated_at: datetime

    def lineage_for(self, ref: ArtifactRef) -> ArtifactLineage | None:
        return next((item for item in self.lineages if item.output == ref), None)
