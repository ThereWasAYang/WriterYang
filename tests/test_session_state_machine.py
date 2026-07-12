from __future__ import annotations

import pytest

from novel.core.contracts.sessions import (
    ALLOWED_SESSION_TRANSITIONS,
    ChapterNodeState,
    ChapterNodeStatus,
    SessionPhase,
    validate_session_transition,
)


def test_every_declared_session_transition_is_accepted() -> None:
    for current, targets in ALLOWED_SESSION_TRANSITIONS.items():
        for target in targets:
            validate_session_transition(current, target)


def test_terminal_and_gate_bypassing_transitions_are_rejected() -> None:
    forbidden = (
        (SessionPhase.AWAITING_OUTLINE_APPROVAL, SessionPhase.RUNNING),
        (SessionPhase.AWAITING_CONTENT_REVIEW, SessionPhase.COMMITTING),
        (SessionPhase.COMMITTED, SessionPhase.RUNNING),
        (SessionPhase.ARCHIVED, SessionPhase.REVISING),
        (SessionPhase.CANCELLED, SessionPhase.RUNNING),
    )
    for current, target in forbidden:
        with pytest.raises(ValueError, match="illegal session transition"):
            validate_session_transition(current, target)


def test_chapter_node_completion_requires_every_commit_prerequisite() -> None:
    state = ChapterNodeState(
        chapter_number=1,
        plan=ChapterNodeStatus.COMPLETED,
        write=ChapterNodeStatus.COMPLETED,
        polish=ChapterNodeStatus.COMPLETED,
        audit=ChapterNodeStatus.COMPLETED,
        state_update=ChapterNodeStatus.COMPLETED,
    )
    assert state.completed() is False
    assert state.model_copy(update={"chapter_memory": ChapterNodeStatus.COMPLETED}).completed() is True
