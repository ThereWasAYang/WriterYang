from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from novel.core.io import load_yaml_model
from novel.core.providers import MockProvider, ModelProvider, ProviderFactory
from novel.core.schemas import AgentConfig, AgentsConfig


@dataclass(frozen=True)
class ProviderOverrides:
    provider_name: str = "config"
    model_name: str | None = None


@dataclass(frozen=True)
class ProviderDescriptor:
    agent_name: str
    provider: str
    model: str
    api_key_env: str
    base_url_env: str | None
    reasoning: str | None
    thinking: str
    max_context_tokens: int | None
    temperature: float | None
    timeout_seconds: float | None
    max_retries: int | None

    def format(self) -> str:
        lines = [
            f"agent: {self.agent_name}",
            f"provider: {self.provider}",
            f"model: {self.model}",
            f"api_key_env: {self.api_key_env}",
        ]
        if self.base_url_env:
            lines.append(f"base_url_env: {self.base_url_env}")
        if self.reasoning:
            lines.append(f"reasoning: {self.reasoning}")
        lines.append(f"thinking: {self.thinking}")
        if self.max_context_tokens is not None:
            lines.append(f"max_context_tokens: {self.max_context_tokens}")
        if self.temperature is not None:
            lines.append(f"temperature: {self.temperature}")
        if self.timeout_seconds is not None:
            lines.append(f"timeout_seconds: {self.timeout_seconds}")
        if self.max_retries is not None:
            lines.append(f"max_retries: {self.max_retries}")
        return "\n".join(lines)


def default_agent_config_path(root: Path) -> Path:
    return root / "config" / "agents.yaml"


def load_agents_config(path: Path) -> AgentsConfig:
    return load_yaml_model(path, AgentsConfig)


def resolve_agent_config(
    config_path: Path,
    agent_name: str,
    *,
    fallback_agents: tuple[str, ...] = (),
    overrides: ProviderOverrides | None = None,
) -> AgentConfig:
    overrides = overrides or ProviderOverrides()
    agents_config = load_agents_config(config_path)
    return ProviderFactory().resolve_agent_config(
        agents_config,
        agent_name,
        fallback_agents=fallback_agents,
        provider_override=overrides.provider_name,
        model_override=overrides.model_name,
    )


def create_agent_provider(
    config_path: Path,
    agent_name: str,
    *,
    fallback_agents: tuple[str, ...] = (),
    overrides: ProviderOverrides | None = None,
    mock_response: str | None = None,
) -> ModelProvider:
    config = resolve_agent_config(
        config_path,
        agent_name,
        fallback_agents=fallback_agents,
        overrides=overrides,
    )
    if config.provider == "mock":
        return MockProvider(fake_response=mock_response)
    return ProviderFactory().create(config)


def describe_agent_provider(
    config_path: Path,
    agent_name: str,
    *,
    fallback_agents: tuple[str, ...] = (),
    overrides: ProviderOverrides | None = None,
) -> ProviderDescriptor:
    config = resolve_agent_config(
        config_path,
        agent_name,
        fallback_agents=fallback_agents,
        overrides=overrides,
    )
    return ProviderDescriptor(
        agent_name=agent_name,
        provider=config.provider,
        model=config.model,
        api_key_env=config.api_key_env,
        base_url_env=config.base_url_env,
        reasoning=config.reasoning,
        thinking=config.thinking.type,
        max_context_tokens=config.max_context_tokens,
        temperature=config.temperature,
        timeout_seconds=config.timeout_seconds,
        max_retries=config.max_retries,
    )
