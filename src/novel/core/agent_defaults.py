from __future__ import annotations

from copy import deepcopy
from typing import Mapping


PROFILE_NAMES = ("scribe", "architect", "loremaster", "clerk")

TASK_TO_PROFILE: dict[str, str] = {
    "writer": "scribe",
    "polish": "scribe",
    "revision": "scribe",
    "plot": "architect",
    "audit": "architect",
    "inspiration": "loremaster",
    "style_guide": "loremaster",
    "canon": "loremaster",
    "state_update": "clerk",
    "chapter_memory": "clerk",
    "intent_router": "clerk",
    "memory_repair": "clerk",
    "setup": "clerk",
}

TASK_NAMES = tuple(TASK_TO_PROFILE)

TASK_ONLY_CONFIG_FIELDS = frozenset({"reasoning", "thinking", "temperature"})

PROFILE_INHERITED_PATCH_FIELDS = {
    "max_context_tokens",
    "max_tokens",
    "timeout_seconds",
    "max_retries",
    "json_response_format",
}

DEFAULT_AGENT_MAX_CONTEXT_TOKENS = 128000
DEFAULT_AGENT_MAX_TOKENS = 24000
DEFAULT_AGENT_TIMEOUT_SECONDS = 120.0

DEFAULT_AGENT_CONFIG: dict[str, object] = {
    "provider": "openai_compatible",
    "base_url_env": "OPENAI_BASE_URL",
    "api_key_env": "OPENAI_API_KEY",
    "model": "model-name",
    "max_context_tokens": DEFAULT_AGENT_MAX_CONTEXT_TOKENS,
    "max_tokens": DEFAULT_AGENT_MAX_TOKENS,
    "timeout_seconds": DEFAULT_AGENT_TIMEOUT_SECONDS,
    "max_retries": 1,
    "json_response_format": "auto",
}

PROFILE_CONFIG_DEFAULTS: dict[str, dict[str, object]] = {
    "scribe": {
        "max_context_tokens": 128000,
        "max_tokens": 24000,
        "timeout_seconds": 180.0,
    },
    "architect": {
        "max_context_tokens": 128000,
        "max_tokens": 8192,
        "timeout_seconds": 120.0,
    },
    "loremaster": {
        "max_context_tokens": 64000,
        "max_tokens": 8192,
        "timeout_seconds": 120.0,
    },
    "clerk": {
        "max_context_tokens": 64000,
        "max_tokens": 8192,
        "timeout_seconds": 90.0,
    },
}

TASK_BUSINESS_DEFAULTS: dict[str, dict[str, object]] = {
    "intent_router": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.3},
    "inspiration": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.8},
    "style_guide": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.6},
    "canon": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.4},
    "plot": {"reasoning": "high", "thinking": {"type": "disabled"}, "temperature": 0.5},
    "writer": {"reasoning": "high", "thinking": {"type": "disabled"}, "temperature": 0.8},
    "polish": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.6},
    "audit": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.2},
    "revision": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.5},
    "state_update": {"reasoning": "low", "thinking": {"type": "disabled"}, "temperature": 0.2},
    "chapter_memory": {"reasoning": "low", "thinking": {"type": "disabled"}, "temperature": 0.1},
    "memory_repair": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.2},
}


def default_agent_config() -> dict[str, object]:
    return deepcopy(DEFAULT_AGENT_CONFIG)


def profile_config_defaults(profile_name: str) -> dict[str, object]:
    return deepcopy(PROFILE_CONFIG_DEFAULTS.get(profile_name, {}))


def task_business_defaults(task_name: str) -> dict[str, object]:
    return deepcopy(TASK_BUSINESS_DEFAULTS.get(task_name, {}))


def inherited_profile_config_patch(
    profile_name: str,
    current: Mapping[str, object] | None = None,
) -> dict[str, object]:
    patch = {"inherit_default": True, **profile_config_defaults(profile_name)}
    if current:
        patch.update(profile_inherited_patch_fields(current))
    return patch


def config_patch_fields(config: Mapping[str, object]) -> dict[str, object]:
    return {
        key: deepcopy(value)
        for key, value in config.items()
        if key != "inherit_default" and value is not None
    }


def profile_inherited_patch_fields(config: Mapping[str, object]) -> dict[str, object]:
    return {
        key: deepcopy(value)
        for key, value in config.items()
        if key in PROFILE_INHERITED_PATCH_FIELDS and value is not None
    }


def profile_for_task(task_name: str) -> str:
    profile_name = TASK_TO_PROFILE.get(task_name)
    if profile_name is None:
        raise KeyError(task_name)
    return profile_name
