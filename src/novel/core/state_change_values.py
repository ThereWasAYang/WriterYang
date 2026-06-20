from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel.core.schemas import EntityState, StateChange


@dataclass(frozen=True)
class StateChangeOldValueComparison:
    should_check: bool
    actual: Any | None = None
    matches: bool = True


def compare_state_change_old_value(
    state: EntityState,
    change: StateChange,
    *,
    character_ids: set[str],
    item_ids: set[str],
    location_ids: set[str],
) -> StateChangeOldValueComparison:
    if change.old_value is None:
        return StateChangeOldValueComparison(should_check=False)

    target = _target_for_change(
        state,
        change,
        character_ids=character_ids,
        item_ids=item_ids,
        location_ids=location_ids,
    )
    if target is None and change.entity_id != "story_position":
        return StateChangeOldValueComparison(should_check=False)

    actual = current_state_value_for_change(target, change)
    return StateChangeOldValueComparison(
        should_check=True,
        actual=actual,
        matches=state_values_equivalent(actual, change.old_value),
    )


def current_state_value_for_change(target: Any | None, change: StateChange) -> Any:
    if target is not None:
        return getattr(target, change.field, None)
    defaults: dict[str, Any] = {
        "possessions": [],
        "knowledge": [],
        "goals": [],
        "known_properties": [],
        "active_events": [],
    }
    return defaults.get(change.field)


def state_values_equivalent(actual: Any, expected: Any) -> bool:
    if actual == expected:
        return True
    if _numeric_values_equivalent(actual, expected):
        return True
    return _is_empty_state_scalar(actual) and _is_empty_state_scalar(expected)


def _target_for_change(
    state: EntityState,
    change: StateChange,
    *,
    character_ids: set[str],
    item_ids: set[str],
    location_ids: set[str],
) -> Any | None:
    if change.entity_id == "story_position":
        return state.story_position
    if change.entity_id in character_ids:
        return {item.entity_id: item for item in state.character_states}.get(change.entity_id)
    if change.entity_id in item_ids:
        return {item.entity_id: item for item in state.item_states}.get(change.entity_id)
    if change.entity_id in location_ids:
        return {item.entity_id: item for item in state.location_states}.get(change.entity_id)
    return None


def _numeric_values_equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if isinstance(left, int | float) and isinstance(right, str):
        try:
            return left == float(right.strip()) if "." in right else left == int(right.strip())
        except ValueError:
            return False
    if isinstance(right, int | float) and isinstance(left, str):
        try:
            return right == float(left.strip()) if "." in left else right == int(left.strip())
        except ValueError:
            return False
    return False


def _is_empty_state_scalar(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in {"", "none", "null", "n/a", "unknown", "无", "未知"})
