from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from novel.core.contracts.artifacts import ArtifactRef
from novel.core.contracts.common import SchemaV3Model, Sha256


class WorldSnapshotRef(SchemaV3Model):
    state: ArtifactRef
    timeline: ArtifactRef
    combined_sha256: Sha256


class ProjectionCheckpoint(SchemaV3Model):
    chapter_number: int = Field(ge=0)
    before_state_sha256: Sha256
    before_timeline_sha256: Sha256
    after_state_sha256: Sha256
    after_timeline_sha256: Sha256
    proposal_artifact_id: str | None = None
    created_at: datetime


class SessionProjection(SchemaV3Model):
    session_id: str = Field(min_length=1)
    base_state_sha256: Sha256
    base_timeline_sha256: Sha256
    current_state_sha256: Sha256
    current_timeline_sha256: Sha256
    state_path: str = Field(min_length=1)
    timeline_path: str = Field(min_length=1)
    checkpoints: list[ProjectionCheckpoint] = Field(default_factory=list)
    updated_at: datetime


class AcceptanceStatus(StrEnum):
    COMMITTED = "committed"


class AcceptanceCommit(SchemaV3Model):
    commit_id: str = Field(pattern=r"^accept_[0-9a-f]{32}$")
    session_id: str = Field(min_length=1)
    chapter_number: int = Field(ge=1)
    candidate: ArtifactRef
    audit: ArtifactRef
    state_proposal: ArtifactRef
    chapter_memory: ArtifactRef
    pre_state_sha256: Sha256
    pre_timeline_sha256: Sha256
    post_state_sha256: Sha256
    post_timeline_sha256: Sha256
    accepted_content_sha256: Sha256
    status: AcceptanceStatus = AcceptanceStatus.COMMITTED
    created_at: datetime

    @model_validator(mode="after")
    def accepted_content_matches_candidate(self) -> AcceptanceCommit:
        if self.accepted_content_sha256 != self.candidate.sha256:
            raise ValueError("accepted content hash must match candidate hash")
        return self


class TransactionStatus(StrEnum):
    PREPARED = "prepared"
    APPLYING = "applying"
    COMMITTED = "committed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


class TransactionEntry(SchemaV3Model):
    target_path: str = Field(min_length=1)
    staged_path: str = Field(min_length=1)
    backup_path: str | None = None
    existed: bool
    before_sha256: Sha256 | None = None
    after_sha256: Sha256


class TransactionJournal(SchemaV3Model):
    transaction_id: str = Field(pattern=r"^tx_[0-9a-f]{32}$")
    purpose: str = Field(min_length=1)
    status: TransactionStatus
    entries: list[TransactionEntry] = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    error: str | None = None
