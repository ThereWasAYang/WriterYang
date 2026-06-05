from __future__ import annotations

from datetime import datetime, timezone
from typing import get_args

from novel.core.context_budget import render_state_prompt_text, render_timeline_prompt_text, select_state_view, select_timeline_view
from novel.core.plan_refs import KEY_TIMELINE_EVENT_ROLES
from novel.core.schemas import (
    CharacterState,
    ContextBudgetConfig,
    EntityState,
    LocationState,
    Narration,
    ProjectConfig,
    StoryPosition,
    TimelineEvent,
    TimelineFile,
    TimelineEventRole,
)


def test_timeline_budget_keeps_focus_and_hides_author_only_digest_for_write() -> None:
    timeline = TimelineFile(
        events=[
            _event("event_old_hidden", chapter=1, summary="作者秘密事件", reader_visible=False, participant_ids=["char_hidden"]),
            _event("event_focus", chapter=2, summary="焦点角色旧事", reader_visible=False, participant_ids=["char_focus"]),
            _event("event_recent", chapter=10, summary="最近事件", reader_visible=True, participant_ids=["char_other"]),
        ]
    )

    view = select_timeline_view(
        timeline,
        chapter_number=10,
        focus_ids={"char_focus"},
        required_event_ids=set(),
        task="write",
        config=ContextBudgetConfig(recent_window_chapters=1, max_full_timeline_events=1),
    )

    assert "event_focus" in view.full_events_json
    assert "event_old_hidden" not in view.digest_text
    assert view.dropped_count >= 1


def test_state_budget_keeps_focus_entity_when_over_limit() -> None:
    state = EntityState(
        story_position=StoryPosition(latest_chapter=20),
        character_states=[
            CharacterState(entity_id="char_focus", last_updated_chapter=1, location_id="loc_old"),
            CharacterState(entity_id="char_recent", last_updated_chapter=20, location_id="loc_new"),
        ],
        item_states=[],
        location_states=[
            LocationState(entity_id="loc_old", last_updated_chapter=1, condition="closed"),
            LocationState(entity_id="loc_new", last_updated_chapter=20, condition="open"),
        ],
    )

    view = select_state_view(
        state,
        chapter_number=20,
        focus_ids={"char_focus"},
        config=ContextBudgetConfig(recent_window_chapters=1, max_full_state_entities=1),
    )

    assert "char_focus" in view.full_states_json
    assert "char_recent" in view.digest_text or "loc_new" in view.digest_text


def test_context_budget_rendering_matches_original_json_when_nothing_is_folded() -> None:
    project = _project_config()
    timeline = TimelineFile(
        events=[
            _event("event_recent", chapter=2, summary="最近事件", reader_visible=True, participant_ids=["char_focus"]),
        ]
    )
    state = EntityState(
        story_position=StoryPosition(latest_chapter=2),
        character_states=[
            CharacterState(entity_id="char_focus", last_updated_chapter=2, location_id="loc_station"),
        ],
        item_states=[],
        location_states=[
            LocationState(entity_id="loc_station", last_updated_chapter=2, condition="open"),
        ],
    )

    assert (
        render_timeline_prompt_text(timeline, project=project, chapter_number=2, task="write")
        == timeline.model_dump_json(indent=2)
    )
    assert (
        render_state_prompt_text(state, project=project, chapter_number=2)
        == state.model_dump_json(indent=2)
    )


def test_key_timeline_event_roles_are_schema_roles() -> None:
    assert KEY_TIMELINE_EVENT_ROLES <= set(get_args(TimelineEventRole))


def _project_config() -> ProjectConfig:
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    return ProjectConfig(
        project_id="test_project",
        title="测试项目",
        language="zh",
        genre=["悬疑"],
        narration=Narration(pov="third", tense="past"),
        created_at=now,
        updated_at=now,
        context_budget=ContextBudgetConfig(),
    )


def _event(
    event_id: str,
    *,
    chapter: int,
    summary: str,
    reader_visible: bool,
    participant_ids: list[str],
) -> TimelineEvent:
    return TimelineEvent(
        id=event_id,
        chapter=chapter,
        in_story_time=f"第{chapter}天",
        summary=summary,
        reader_visible=reader_visible,
        narrative_position={"chapter": chapter},
        story_position={"time_label": f"第{chapter}天"},
        participant_ids=participant_ids,
    )
