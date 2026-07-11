from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from novel.core.contracts.artifacts import ArtifactRef, AuditBinding, StateProposalBinding
from novel.core.contracts.common import ArtifactKind, SchemaV3Model, Sha256


class RevisionSessionPhase(StrEnum):
    AWAITING_PATCH = "awaiting_patch"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    READY_TO_COMMIT = "ready_to_commit"
    COMMITTING = "committing"
    COMMITTED = "committed"
    ARCHIVED = "archived"
    FAILED_RECOVERABLE = "failed_recoverable"
    CANCELLED = "cancelled"


REVISION_PHASE_TRANSITIONS: dict[RevisionSessionPhase, frozenset[RevisionSessionPhase]] = {
    RevisionSessionPhase.AWAITING_PATCH: frozenset(
        {RevisionSessionPhase.RUNNING, RevisionSessionPhase.CANCELLED}
    ),
    RevisionSessionPhase.RUNNING: frozenset(
        {RevisionSessionPhase.AWAITING_REVIEW, RevisionSessionPhase.FAILED_RECOVERABLE}
    ),
    RevisionSessionPhase.AWAITING_REVIEW: frozenset(
        {RevisionSessionPhase.COMMITTING, RevisionSessionPhase.CANCELLED}
    ),
    RevisionSessionPhase.COMMITTING: frozenset(
        {RevisionSessionPhase.COMMITTED, RevisionSessionPhase.FAILED_RECOVERABLE}
    ),
    RevisionSessionPhase.FAILED_RECOVERABLE: frozenset(
        {RevisionSessionPhase.RUNNING, RevisionSessionPhase.CANCELLED}
    ),
    RevisionSessionPhase.COMMITTED: frozenset({RevisionSessionPhase.ARCHIVED}),
    RevisionSessionPhase.ARCHIVED: frozenset(),
    RevisionSessionPhase.CANCELLED: frozenset(),
    RevisionSessionPhase.READY_TO_COMMIT: frozenset({RevisionSessionPhase.COMMITTING}),
}


def ensure_revision_phase_transition(
    current: RevisionSessionPhase,
    target: RevisionSessionPhase,
) -> None:
    if target not in REVISION_PHASE_TRANSITIONS[current]:
        raise ValueError(f"illegal revision phase transition: {current.value} -> {target.value}")


class MarkdownBlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    QUOTE = "quote"
    LIST = "list"
    THEMATIC_BREAK = "thematic_break"
    CODE = "code"


class SegmentSelection(SchemaV3Model):
    selection_id: str = Field(pattern=r"^selection_[0-9a-f]{32}$")
    chapter_number: int = Field(ge=1)
    source_candidate: ArtifactRef
    start_block: int = Field(ge=1)
    end_block: int = Field(ge=1)
    selected_sha256: Sha256
    prefix_sha256: Sha256
    suffix_sha256: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def validate_selection(self) -> SegmentSelection:
        if self.source_candidate.kind != ArtifactKind.CANDIDATE:
            raise ValueError("segment selection source must be a candidate artifact")
        if self.end_block < self.start_block:
            raise ValueError("end_block cannot be smaller than start_block")
        return self


class SegmentPatch(SchemaV3Model):
    patch_id: str = Field(pattern=r"^patch_[0-9a-f]{32}$")
    selection_id: str = Field(pattern=r"^selection_[0-9a-f]{32}$")
    source_sha256: Sha256
    start_block: int = Field(ge=1)
    end_block: int = Field(ge=1)
    replacement_markdown: str = Field(min_length=1)
    addressed_issue_ids: list[str] = Field(default_factory=list)
    created_at: datetime

    @model_validator(mode="after")
    def validate_range(self) -> SegmentPatch:
        if self.end_block < self.start_block:
            raise ValueError("end_block cannot be smaller than start_block")
        if not self.replacement_markdown.strip():
            raise ValueError("replacement_markdown cannot be blank")
        return self


class RevisionSession(SchemaV3Model):
    revision_session_id: str = Field(pattern=r"^revision_session_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    chapter_number: int = Field(ge=1)
    base_acceptance_commit_id: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    phase: RevisionSessionPhase
    selection: SegmentSelection
    patch: ArtifactRef | None = None
    candidate: ArtifactRef | None = None
    audit: AuditBinding | None = None
    state_proposal: StateProposalBinding | None = None
    projection_path: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_phase_artifacts(self) -> RevisionSession:
        if self.selection.chapter_number != self.chapter_number:
            raise ValueError("revision selection chapter must match revision session chapter")
        if self.patch is not None and self.patch.kind != ArtifactKind.SEGMENT_PATCH:
            raise ValueError("revision patch ref has invalid artifact kind")
        if self.candidate is not None and self.candidate.kind != ArtifactKind.CANDIDATE:
            raise ValueError("revision candidate ref has invalid artifact kind")
        if self.phase in {
            RevisionSessionPhase.AWAITING_REVIEW,
            RevisionSessionPhase.READY_TO_COMMIT,
            RevisionSessionPhase.COMMITTING,
            RevisionSessionPhase.COMMITTED,
            RevisionSessionPhase.ARCHIVED,
        }:
            if not self.patch or not self.candidate or not self.audit or not self.state_proposal:
                raise ValueError("reviewable revision session requires patch, candidate, audit and state proposal")
        if self.candidate and self.audit and self.audit.candidate != self.candidate:
            raise ValueError("revision audit must bind the revision candidate")
        if self.candidate and self.state_proposal and self.state_proposal.candidate != self.candidate:
            raise ValueError("revision state proposal must bind the revision candidate")
        if self.audit and self.state_proposal and self.state_proposal.audit != self.audit.audit:
            raise ValueError("revision state proposal must bind the revision audit")
        return self
