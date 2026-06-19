from __future__ import annotations

from copy import deepcopy
from typing import Mapping


STANDARD_AGENT_NAMES = (
    "orchestrator",
    "inspiration",
    "style_guide",
    "canon",
    "plot",
    "writer",
    "polish",
    "audit",
    "revision",
    "state_update",
    "chapter_memory",
)

AGENT_BUSINESS_CONFIG_FIELDS = {"reasoning", "thinking", "temperature"}

DEFAULT_AGENT_TEMPERATURE = 0.5
DEFAULT_AGENT_MAX_CONTEXT_TOKENS = 128000
DEFAULT_AGENT_MAX_TOKENS = 24000
DEFAULT_AGENT_TIMEOUT_SECONDS = 120.0

DEFAULT_AGENT_CONFIG: dict[str, object] = {
    "provider": "openai_compatible",
    "base_url_env": "OPENAI_BASE_URL",
    "api_key_env": "OPENAI_API_KEY",
    "model": "model-name",
    "reasoning": "medium",
    "thinking": {"type": "disabled"},
    "max_context_tokens": DEFAULT_AGENT_MAX_CONTEXT_TOKENS,
    "max_tokens": DEFAULT_AGENT_MAX_TOKENS,
    "temperature": DEFAULT_AGENT_TEMPERATURE,
    "timeout_seconds": DEFAULT_AGENT_TIMEOUT_SECONDS,
    "max_retries": 1,
    "json_response_format": "auto",
}

AGENT_BUSINESS_DEFAULTS: dict[str, dict[str, object]] = {
    "orchestrator": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.3},
    "inspiration": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.8},
    "style_guide": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.6},
    "canon": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.4},
    "plot": {"reasoning": "high", "thinking": {"type": "disabled"}, "temperature": 0.5},
    "writer": {"reasoning": "high", "thinking": {"type": "disabled"}, "temperature": 0.8},
    "polish": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.6},
    "audit": {"reasoning": "low", "thinking": {"type": "disabled"}, "temperature": 0.2},
    "revision": {"reasoning": "medium", "thinking": {"type": "disabled"}, "temperature": 0.5},
    "state_update": {"reasoning": "low", "thinking": {"type": "disabled"}, "temperature": 0.2},
    "chapter_memory": {"reasoning": "low", "thinking": {"type": "disabled"}, "temperature": 0.1},
}


def default_agent_config() -> dict[str, object]:
    return deepcopy(DEFAULT_AGENT_CONFIG)


def agent_business_defaults(agent_name: str) -> dict[str, object]:
    return deepcopy(AGENT_BUSINESS_DEFAULTS.get(agent_name, {}))


def inherited_agent_config_patch(
    agent_name: str,
    current: Mapping[str, object] | None = None,
) -> dict[str, object]:
    patch = {"inherit_default": True, **agent_business_defaults(agent_name)}
    if current:
        patch.update(agent_business_fields(current))
    return patch


def agent_business_fields(config: Mapping[str, object]) -> dict[str, object]:
    return {
        key: deepcopy(value)
        for key, value in config.items()
        if key in AGENT_BUSINESS_CONFIG_FIELDS and value is not None
    }
