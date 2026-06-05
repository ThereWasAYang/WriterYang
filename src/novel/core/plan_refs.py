from __future__ import annotations

from novel.core.schemas import ChapterPlan


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


def plan_search_terms(plan: ChapterPlan | None) -> list[str]:
    if plan is None:
        return []
    terms: list[str] = [plan.goal, plan.summary, plan.ending_hook]
    terms.extend(plan.must_include)
    for scene in plan.scenes:
        terms.extend([scene.purpose, scene.summary, scene.emotional_beat])
        terms.extend(scene.plot_points)
    return [term.strip() for term in terms if term and term.strip()]
