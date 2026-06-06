from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import re


PROMPT_VERSION = "2026-06-07"

PROMPT_VERSIONS: dict[str, str] = {
    "audit_repair_route_system": "2026-05-31",
    "audit_system": "2026-06-05",
    "canon_system": "2026-06-05",
    "chapter_memory_system": "2026-06-05",
    "inspiration_system": "2026-06-05",
    "memory_change_clarification_system": "2026-06-05",
    "memory_change_batch_plan_system": "2026-06-07",
    "memory_repair_system": "2026-05-31",
    "orchestrator_ask_intent_system": "2026-05-31",
    "orchestrator_revision_route_system": "2026-05-31",
    "planning_system": "2026-06-05",
    "polish_system": "2026-06-05",
    "revision_system": "2026-06-05",
    "state_update_system": "2026-06-05",
    "writer_system": "2026-06-05",
}

_PARTIAL_PATTERN = re.compile(r"\{\{partial:([a-z0-9_]+)\}\}")


@lru_cache(maxsize=None)
def load_prompt_template(name: str) -> str:
    if not name.endswith(".txt"):
        name = f"{name}.txt"
    text = files("novel.prompts").joinpath(name).read_text(encoding="utf-8").strip()
    return _resolve_prompt_partials(text)


def render_prompt_template(name: str, **values: object) -> str:
    template = load_prompt_template(name)
    return template.format(**{key: str(value) for key, value in values.items()})


def _resolve_prompt_partials(text: str, *, seen: frozenset[str] = frozenset()) -> str:
    def replace(match: re.Match[str]) -> str:
        partial_name = match.group(1)
        if partial_name in seen:
            chain = " -> ".join((*seen, partial_name))
            raise ValueError(f"recursive prompt partial include: {chain}")
        partial_text = (
            files("novel.prompts")
            .joinpath("partials", f"{partial_name}.txt")
            .read_text(encoding="utf-8")
            .strip()
        )
        return _resolve_prompt_partials(partial_text, seen=seen | {partial_name})

    return _PARTIAL_PATTERN.sub(replace, text).strip()
