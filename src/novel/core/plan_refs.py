from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from novel.core.schemas import ChapterPlan, TimelineEvent

KEY_TIMELINE_EVENT_ROLES = frozenset({"backstory", "flashback", "memory", "revelation", "summary"})


def plan_focus_entity_ids(plan: ChapterPlan | None) -> set[str]:
    if plan is None:
        return set()
    entity_ids = set(plan.required_context.canon_entity_ids)
    entity_ids.update(plan.required_context.state_entity_ids)
    for scene in plan.scenes:
        entity_ids.add(scene.location_id)
        entity_ids.update(scene.participant_ids)
    return {entity_id for entity_id in entity_ids if entity_id}


def plan_timeline_event_ids(plan: ChapterPlan | None) -> set[str]:
    if plan is None:
        return set()
    return {event_id for event_id in plan.required_context.timeline_event_ids if event_id}


def plan_related_timeline_event_ids(
    plan: ChapterPlan | None,
    events: Iterable[TimelineEvent | Mapping[str, Any]],
) -> set[str]:
    focus_ids = plan_focus_entity_ids(plan)
    if not focus_ids:
        return set()
    event_ids: set[str] = set()
    for event in events:
        if not timeline_event_has_key_recall_role(event):
            continue
        if not timeline_event_focus_ids(event).intersection(focus_ids):
            continue
        event_id = _event_value(event, "id")
        if isinstance(event_id, str) and event_id:
            event_ids.add(event_id)
    return event_ids


def timeline_event_has_key_recall_role(event: TimelineEvent | Mapping[str, Any]) -> bool:
    role = _event_value(event, "event_role")
    return isinstance(role, str) and role in KEY_TIMELINE_EVENT_ROLES


def timeline_event_focus_ids(event: TimelineEvent | Mapping[str, Any]) -> set[str]:
    focus_ids: set[str] = set()
    location_id = _event_value(event, "location_id")
    if isinstance(location_id, str) and location_id:
        focus_ids.add(location_id)
    participant_ids = _event_value(event, "participant_ids")
    if isinstance(participant_ids, list):
        focus_ids.update(item for item in participant_ids if isinstance(item, str) and item)
    return focus_ids


def plan_search_terms(plan: ChapterPlan | None) -> list[str]:
    if plan is None:
        return []
    terms: list[str] = [plan.goal, plan.summary, plan.ending_hook]
    terms.extend(plan.must_include)
    for scene in plan.scenes:
        terms.extend([scene.purpose, scene.summary, scene.emotional_beat])
        terms.extend(scene.plot_points)
    return [term.strip() for term in terms if term and term.strip()]


def _event_value(event: TimelineEvent | Mapping[str, Any], key: str) -> Any:
    if isinstance(event, Mapping):
        return event.get(key)
    return getattr(event, key)
