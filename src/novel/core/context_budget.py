from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
from typing import Iterable

from novel.core.plan_refs import (
    KEY_TIMELINE_EVENT_ROLES,
    plan_focus_entity_ids,
    plan_related_timeline_event_ids,
    plan_timeline_event_ids,
)
from novel.core.schemas import (
    ChapterPlan,
    ContextBudgetConfig,
    ContextTask,
    EntityState,
    ProjectConfig,
    TimelineEvent,
    TimelineFile,
)

_KEY_EVENT_ROLES = KEY_TIMELINE_EVENT_ROLES
_PRIVATE_TIMELINE_DIGEST_REDACT_TASKS: set[ContextTask] = {
    "inspiration",
    "write",
    "polish",
    "revision",
}


@dataclass(frozen=True)
class TimelineView:
    full_events_json: str
    digest_text: str
    dropped_count: int


@dataclass(frozen=True)
class StateView:
    full_states_json: str
    digest_text: str
    dropped_count: int


def project_context_budget(project: ProjectConfig) -> ContextBudgetConfig:
    return project.context_budget or ContextBudgetConfig()


def render_timeline_prompt_text(
    timeline: TimelineFile,
    *,
    project: ProjectConfig,
    chapter_number: int,
    task: ContextTask,
    plan: ChapterPlan | None = None,
) -> str:
    config = project_context_budget(project)
    if not config.enabled:
        return timeline.model_dump_json(indent=2)
    view = select_timeline_view(
        timeline,
        chapter_number=chapter_number,
        focus_ids=plan_focus_entity_ids(plan),
        required_event_ids=plan_timeline_event_ids(plan) | plan_related_timeline_event_ids(plan, timeline.events),
        task=task,
        config=config,
    )
    return render_timeline_for_prompt(view)


def render_state_prompt_text(
    state: EntityState,
    *,
    project: ProjectConfig,
    chapter_number: int,
    plan: ChapterPlan | None = None,
) -> str:
    config = project_context_budget(project)
    if not config.enabled:
        return state.model_dump_json(indent=2)
    view = select_state_view(
        state,
        chapter_number=chapter_number,
        focus_ids=plan_focus_entity_ids(plan),
        config=config,
    )
    return render_state_for_prompt(view)


def select_timeline_view(
    timeline: TimelineFile,
    *,
    chapter_number: int,
    focus_ids: set[str],
    required_event_ids: set[str],
    task: ContextTask,
    config: ContextBudgetConfig,
) -> TimelineView:
    if not config.enabled:
        return TimelineView(timeline.model_dump_json(indent=2), "", 0)
    recent_min = max(1, chapter_number - config.recent_window_chapters)
    mandatory: list[TimelineEvent] = []
    optional: list[TimelineEvent] = []
    for event in timeline.events:
        event_entities = set(event.participant_ids)
        if event.location_id:
            event_entities.add(event.location_id)
        is_focus = bool(event_entities.intersection(focus_ids))
        is_required = event.id in required_event_ids
        if is_focus or is_required:
            mandatory.append(event)
            continue
        narrative = event.narrative_position
        if (narrative is not None and narrative.chapter >= recent_min) or event.event_role in _KEY_EVENT_ROLES:
            optional.append(event)
    remaining_slots = max(config.max_full_timeline_events - len(mandatory), 0)
    optional_keep = sorted(
        optional,
        key=lambda event: _timeline_optional_priority(event, chapter_number=chapter_number),
        reverse=True,
    )[:remaining_slots]
    keep_ids = {event.id for event in mandatory}
    keep_ids.update(event.id for event in optional_keep)
    kept = [event for event in timeline.events if event.id in keep_ids]
    rest = [event for event in timeline.events if event.id not in keep_ids]
    if not rest:
        return TimelineView(timeline.model_dump_json(indent=2), "", 0)
    subset = TimelineFile(events=kept)
    digest = _timeline_digest(rest, task=task, digest_dropped=config.digest_dropped)
    return TimelineView(subset.model_dump_json(indent=2), digest, len(rest))


def select_state_view(
    state: EntityState,
    *,
    chapter_number: int,
    focus_ids: set[str],
    config: ContextBudgetConfig,
) -> StateView:
    if not config.enabled:
        return StateView(state.model_dump_json(indent=2), "", 0)
    recent_min = max(0, chapter_number - config.recent_window_chapters)
    all_entries = (
        [("character", item.entity_id, item.last_updated_chapter, item) for item in state.character_states]
        + [("item", item.entity_id, item.last_updated_chapter, item) for item in state.item_states]
        + [("location", item.entity_id, item.last_updated_chapter, item) for item in state.location_states]
    )
    mandatory = [entry for entry in all_entries if entry[1] in focus_ids]
    optional = [entry for entry in all_entries if entry[1] not in focus_ids and entry[2] >= recent_min]
    remaining_slots = max(config.max_full_state_entities - len(mandatory), 0)
    optional_keep = sorted(optional, key=lambda entry: (entry[2], entry[0], entry[1]), reverse=True)[:remaining_slots]
    keep = {(kind, entity_id) for kind, entity_id, _, _ in [*mandatory, *optional_keep]}
    rest = [entry for entry in all_entries if (entry[0], entry[1]) not in keep]
    if not rest:
        return StateView(state.model_dump_json(indent=2), "", 0)
    subset = EntityState(
        story_position=state.story_position,
        character_states=[item for item in state.character_states if ("character", item.entity_id) in keep],
        item_states=[item for item in state.item_states if ("item", item.entity_id) in keep],
        location_states=[item for item in state.location_states if ("location", item.entity_id) in keep],
    )
    digest = _state_digest(rest, digest_dropped=config.digest_dropped)
    return StateView(subset.model_dump_json(indent=2), digest, len(rest))


def render_timeline_for_prompt(view: TimelineView) -> str:
    if view.dropped_count == 0:
        return view.full_events_json
    return (
        "Budgeted timeline view:\n"
        "Full events retained:\n"
        f"{view.full_events_json}\n\n"
        "Folded timeline digest:\n"
        f"{view.digest_text}\n"
        f"Folded event count: {view.dropped_count}"
    )


def render_state_for_prompt(view: StateView) -> str:
    if view.dropped_count == 0:
        return view.full_states_json
    return (
        "Budgeted current-state view:\n"
        "Full entity states retained:\n"
        f"{view.full_states_json}\n\n"
        "Folded entity-state digest:\n"
        f"{view.digest_text}\n"
        f"Folded entity-state count: {view.dropped_count}"
    )


def _timeline_optional_priority(event: TimelineEvent, *, chapter_number: int) -> tuple[int, int, int]:
    role_score = 1 if event.event_role in _KEY_EVENT_ROLES else 0
    visible_score = 1 if event.reader_visible else 0
    narrative = event.narrative_position
    chapter_key = narrative.chapter if narrative is not None else chapter_number
    return (chapter_key, role_score, visible_score)


def _timeline_digest(events: list[TimelineEvent], *, task: ContextTask, digest_dropped: bool) -> str:
    visible_events = [
        event for event in events if event.reader_visible or task not in _PRIVATE_TIMELINE_DIGEST_REDACT_TASKS
    ]
    if not visible_events:
        return "No reader-visible folded timeline events for this task."
    lines: list[str] = []
    background_events = [event for event in visible_events if event.narrative_position is None]
    if background_events:
        summaries = [
            f"{event.summary.strip()}（故事时间：{event.story_position.time_label}）"
            for event in background_events
            if event.summary.strip()
        ]
        shown = summaries[:3]
        suffix = ""
        if digest_dropped and len(summaries) > len(shown):
            suffix = f" (+{len(summaries) - len(shown)} more)"
        lines.append(f"- 背景（未在正文揭示）: {'; '.join(shown) if shown else 'summary unavailable'}{suffix}")
    anchored_events = [event for event in visible_events if event.narrative_position is not None]
    sorted_events = sorted(
        anchored_events, key=lambda event: event.narrative_position.chapter if event.narrative_position else 0
    )
    for chapter, grouped in groupby(
        sorted_events,
        key=lambda event: event.narrative_position.chapter if event.narrative_position else 0,
    ):
        chapter_events = list(grouped)
        summaries = [event.summary.strip() for event in chapter_events if event.summary.strip()]
        shown = summaries[:3]
        suffix = ""
        if digest_dropped and len(summaries) > len(shown):
            suffix = f" (+{len(summaries) - len(shown)} more)"
        lines.append(f"- Chapter {chapter}: {'; '.join(shown) if shown else 'summary unavailable'}{suffix}")
    return "\n".join(lines)


def _state_digest(entries: Iterable[tuple[str, str, int, object]], *, digest_dropped: bool) -> str:
    lines: list[str] = []
    for kind, entity_id, last_updated, item in sorted(entries, key=lambda entry: (entry[0], entry[1])):
        details = item.model_dump(exclude_none=True)  # type: ignore[attr-defined]
        summary_parts: list[str] = []
        for key in ("location_id", "holder_id", "health", "condition", "mental_state", "accessibility"):
            value = details.get(key)
            if value:
                summary_parts.append(f"{key}={value}")
        extra = "; ".join(summary_parts[:3]) if summary_parts else "no compact state fields"
        lines.append(f"- {kind} {entity_id} (last_updated_chapter={last_updated}): {extra}")
    if digest_dropped:
        return "\n".join(lines)
    return "\n".join(lines[:1])
