from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from novel.core.io import load_yaml_model
from novel.core.providers import LoggingModelProvider, MockProvider, ModelProvider, ProviderFactory
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
    source: str
    api_key_env: str
    base_url_env: str | None
    reasoning: str | None
    thinking: str
    max_context_tokens: int | None
    max_tokens: int | None
    temperature: float | None
    timeout_seconds: float | None
    max_retries: int | None

    def format(self) -> str:
        lines = [
            f"agent: {self.agent_name}",
            f"provider: {self.provider}",
            f"model: {self.model}",
            f"source: {self.source}",
            f"api_key_env: {self.api_key_env}",
        ]
        if self.base_url_env:
            lines.append(f"base_url_env: {self.base_url_env}")
        if self.reasoning:
            lines.append(f"reasoning: {self.reasoning}")
        lines.append(f"thinking: {self.thinking}")
        if self.max_context_tokens is not None:
            lines.append(f"max_context_tokens: {self.max_context_tokens}")
        if self.max_tokens is not None:
            lines.append(f"max_tokens: {self.max_tokens}")
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
    if overrides.provider_name.lower() == "mock":
        return AgentConfig(
            provider="mock",
            model=overrides.model_name or "mock-model",
            api_key_env="MOCK_API_KEY",
        )
    agents_config = load_agents_config(config_path)
    return ProviderFactory().resolve_agent_config(
        agents_config,
        agent_name,
        fallback_agents=fallback_agents,
        provider_override=overrides.provider_name,
        model_override=overrides.model_name,
    )


def resolve_agent_config_source(
    config_path: Path,
    agent_name: str,
    *,
    fallback_agents: tuple[str, ...] = (),
    overrides: ProviderOverrides | None = None,
) -> str:
    overrides = overrides or ProviderOverrides()
    if overrides.provider_name.lower() == "mock":
        return "override:mock"
    agents_config = load_agents_config(config_path)
    agents = agents_config.agents
    for candidate in (agent_name, *fallback_agents):
        if candidate in agents:
            config = agents[candidate]
            if isinstance(config, AgentConfig) and agents_config.default is None:
                return f"agent:{candidate}"
            return f"default+agent:{candidate}"
    if agents_config.default is not None:
        return "default"
    return "unresolved"


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
        provider: ModelProvider = MockProvider(fake_response=mock_response)
    else:
        log_path = config_path.parent.parent / "runs" / "provider_calls.jsonl"
        provider = ProviderFactory(log_path=log_path).create(config)
    return LoggingModelProvider(
        provider=provider,
        agent_name=agent_name,
        provider_name=config.provider,
        model=config.model,
        root=config_path.parent.parent,
    )


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
    source = resolve_agent_config_source(
        config_path,
        agent_name,
        fallback_agents=fallback_agents,
        overrides=overrides,
    )
    return ProviderDescriptor(
        agent_name=agent_name,
        provider=config.provider,
        model=config.model,
        source=source,
        api_key_env=config.api_key_env,
        base_url_env=config.base_url_env,
        reasoning=config.reasoning,
        thinking=config.thinking.type,
        max_context_tokens=config.max_context_tokens,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        timeout_seconds=config.timeout_seconds,
        max_retries=config.max_retries,
    )
