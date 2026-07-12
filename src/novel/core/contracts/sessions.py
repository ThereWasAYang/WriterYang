from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from novel.core.contracts.common import SchemaV3Model


class SessionPhase(StrEnum):
    DRAFTING_OUTLINE = "drafting_outline"
    AWAITING_OUTLINE_APPROVAL = "awaiting_outline_approval"
    READY_TO_RUN = "ready_to_run"
    RUNNING = "running"
    AWAITING_CONTENT_REVIEW = "awaiting_content_review"
    REVISING = "revising"
    READY_TO_COMMIT = "ready_to_commit"
    COMMITTING = "committing"
    COMMITTED = "committed"
    ARCHIVED = "archived"
    FAILED_RECOVERABLE = "failed_recoverable"
    FAILED_TERMINAL = "failed_terminal"
    CANCELLED = "cancelled"
    RECOVERING = "recovering"


class ChapterNodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"


class ChapterNodeState(SchemaV3Model):
    chapter_number: int = Field(ge=1)
    plan: ChapterNodeStatus = ChapterNodeStatus.PENDING
    write: ChapterNodeStatus = ChapterNodeStatus.PENDING
    polish: ChapterNodeStatus = ChapterNodeStatus.PENDING
    audit: ChapterNodeStatus = ChapterNodeStatus.PENDING
    state_update: ChapterNodeStatus = ChapterNodeStatus.PENDING
    chapter_memory: ChapterNodeStatus = ChapterNodeStatus.PENDING

    def completed(self) -> bool:
        return all(
            value is ChapterNodeStatus.COMPLETED
            for value in (
                self.plan,
                self.write,
                self.polish,
                self.audit,
                self.state_update,
                self.chapter_memory,
            )
        )


ALLOWED_SESSION_TRANSITIONS: dict[SessionPhase, frozenset[SessionPhase]] = {
    SessionPhase.DRAFTING_OUTLINE: frozenset({SessionPhase.AWAITING_OUTLINE_APPROVAL, SessionPhase.FAILED_RECOVERABLE}),
    SessionPhase.AWAITING_OUTLINE_APPROVAL: frozenset({SessionPhase.DRAFTING_OUTLINE, SessionPhase.READY_TO_RUN, SessionPhase.CANCELLED}),
    SessionPhase.READY_TO_RUN: frozenset({SessionPhase.DRAFTING_OUTLINE, SessionPhase.RUNNING, SessionPhase.CANCELLED}),
    SessionPhase.RUNNING: frozenset({SessionPhase.AWAITING_CONTENT_REVIEW, SessionPhase.FAILED_RECOVERABLE, SessionPhase.CANCELLED}),
    SessionPhase.AWAITING_CONTENT_REVIEW: frozenset({SessionPhase.REVISING, SessionPhase.READY_TO_COMMIT, SessionPhase.CANCELLED}),
    SessionPhase.REVISING: frozenset({SessionPhase.AWAITING_CONTENT_REVIEW, SessionPhase.FAILED_RECOVERABLE, SessionPhase.CANCELLED}),
    SessionPhase.READY_TO_COMMIT: frozenset({SessionPhase.COMMITTING, SessionPhase.REVISING, SessionPhase.CANCELLED}),
    SessionPhase.COMMITTING: frozenset({SessionPhase.COMMITTED, SessionPhase.RECOVERING}),
    SessionPhase.RECOVERING: frozenset({SessionPhase.READY_TO_COMMIT, SessionPhase.COMMITTED, SessionPhase.FAILED_TERMINAL}),
    SessionPhase.FAILED_RECOVERABLE: frozenset({SessionPhase.RUNNING, SessionPhase.REVISING, SessionPhase.CANCELLED}),
    SessionPhase.COMMITTED: frozenset({SessionPhase.ARCHIVED}),
    SessionPhase.ARCHIVED: frozenset(),
    SessionPhase.FAILED_TERMINAL: frozenset(),
    SessionPhase.CANCELLED: frozenset(),
}


def validate_session_transition(current: SessionPhase, target: SessionPhase) -> None:
    if target not in ALLOWED_SESSION_TRANSITIONS[current]:
        raise ValueError(f"illegal session transition: {current.value} -> {target.value}")
