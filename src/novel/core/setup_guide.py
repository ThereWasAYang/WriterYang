from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import socket
from typing import Literal, Mapping

from novel.core.embeddings import EmbeddingError, EmbeddingProviderFactory
from novel.core.env import load_project_env, write_project_env_values
from novel.core.io import atomic_write_yaml, backup_if_exists, load_yaml
from novel.core.providers import (
    LoggingModelProvider,
    ModelRequest,
    MissingProviderEnvError,
    ProviderError,
    ProviderFactory,
)
from novel.core.schemas import (
    AgentConfig,
    AgentsConfig,
    EmbeddingProviderConfig,
    EmbeddingsConfig,
    ProjectConfig,
    ThinkingConfig,
)
from novel.core.security import validate_secret_config_file


DEFAULT_API_KEY_ENV = "WRITERYANG_DEFAULT_API_KEY"
DEFAULT_BASE_URL_ENV = "WRITERYANG_DEFAULT_BASE_URL"
DEFAULT_EMBEDDING_API_KEY_ENV = "WRITERYANG_EMBEDDING_API_KEY"
DEFAULT_EMBEDDING_BASE_URL_ENV = "WRITERYANG_EMBEDDING_BASE_URL"
DEFAULT_INHERITING_AGENT_NAMES = (
    "orchestrator",
    "inspiration",
    "canon",
    "plot",
    "writer",
    "polish",
    "audit",
    "state_update",
    "chapter_memory",
    "revision",
)


class SetupGuideError(RuntimeError):
    """Raised when initial project setup cannot be completed safely."""


@dataclass(frozen=True)
class ProviderSetupResult:
    config_path: Path
    env_path: Path
    provider: str
    model: str
    api_key_env: str
    base_url_env: str
    ping_ok: bool
    ping_message: str


@dataclass(frozen=True)
class EmbeddingSetupResult:
    config_path: Path
    env_path: Path
    active_provider: str
    provider: str
    model: str
    api_key_env: str
    base_url_env: str
    ping_ok: bool
    ping_message: str


@dataclass(frozen=True)
class PortSetupResult:
    project_path: Path
    requested_port: int
    selected_port: int
    host: str = "127.0.0.1"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.selected_port}"


def configure_default_provider(
    root: Path,
    *,
    base_url: str,
    api_key: str,
    model: str,
    provider: str = "openai_compatible",
    thinking_type: str = "disabled",
    temperature: float = 0.5,
    max_tokens: int = 8192,
    max_context_tokens: int = 128000,
    timeout_seconds: float = 60.0,
    max_retries: int = 1,
    ping: bool = True,
) -> ProviderSetupResult:
    root = root.expanduser().resolve()
    base_url = _require_non_empty(base_url, "base_url").rstrip("/")
    api_key = _require_non_empty(api_key, "api_key")
    model = _require_non_empty(model, "model")
    provider = _require_non_empty(provider, "provider")
    config = AgentConfig(
        provider=provider,
        base_url_env=DEFAULT_BASE_URL_ENV,
        api_key_env=DEFAULT_API_KEY_ENV,
        model=model,
        reasoning="medium",
        thinking=ThinkingConfig(type=_normalize_thinking_type(thinking_type)),
        max_context_tokens=max_context_tokens,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )
    env_values = {
        DEFAULT_BASE_URL_ENV: base_url,
        DEFAULT_API_KEY_ENV: api_key,
    }
    env_map = load_project_env(root, {**os.environ, **env_values})
    if ping:
        _ping_model_provider(root, config, env_map)
    env_path = write_project_env_values(root, env_values)
    config_path = update_default_agent_config(root, config)
    return ProviderSetupResult(
        config_path=config_path,
        env_path=env_path,
        provider=provider,
        model=model,
        api_key_env=DEFAULT_API_KEY_ENV,
        base_url_env=DEFAULT_BASE_URL_ENV,
        ping_ok=True,
        ping_message="default model provider connectivity test succeeded",
    )


def update_default_agent_config(root: Path, config: AgentConfig) -> Path:
    config_path = root.expanduser().resolve() / "config" / "agents.yaml"
    data = _load_yaml_mapping(config_path)
    default_snapshot = config.model_dump(mode="json", exclude_none=True, exclude={"inherit_default"})
    data["default"] = default_snapshot
    agents = data.get("agents")
    if not isinstance(agents, dict):
        agents = {}
    for agent_name in DEFAULT_INHERITING_AGENT_NAMES:
        current = agents.get(agent_name)
        if current is None or (isinstance(current, dict) and current.get("inherit_default") is True):
            agents[agent_name] = {**default_snapshot, "inherit_default": True}
    data["agents"] = agents
    AgentsConfig.model_validate(data)
    backup_if_exists(config_path, reason="setup_guide_agents")
    atomic_write_yaml(config_path, data)
    findings = validate_secret_config_file(config_path)
    if findings:
        raise SetupGuideError("config/agents.yaml contains unsafe secret-like values after setup")
    return config_path


def configure_embedding_provider(
    root: Path,
    *,
    base_url: str,
    api_key: str,
    model: str,
    provider: str = "openai_compatible",
    provider_name: str = "configured",
    dimensions: int | None = None,
    batch_size: int = 16,
    timeout_seconds: float = 30.0,
    max_retries: int = 1,
    ping: bool = True,
) -> EmbeddingSetupResult:
    root = root.expanduser().resolve()
    base_url = _require_non_empty(base_url, "embedding base_url").rstrip("/")
    api_key = _require_non_empty(api_key, "embedding api_key")
    model = _require_non_empty(model, "embedding model")
    provider = _require_non_empty(provider, "embedding provider")
    provider_name = _require_non_empty(provider_name, "embedding provider_name")
    config = EmbeddingProviderConfig(
        provider=provider,
        base_url_env=DEFAULT_EMBEDDING_BASE_URL_ENV,
        api_key_env=DEFAULT_EMBEDDING_API_KEY_ENV,
        model=model,
        dimensions=dimensions,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )
    env_values = {
        DEFAULT_EMBEDDING_BASE_URL_ENV: base_url,
        DEFAULT_EMBEDDING_API_KEY_ENV: api_key,
    }
    env_map = load_project_env(root, {**os.environ, **env_values})
    if ping:
        _ping_embedding_provider(config, env_map)
    env_path = write_project_env_values(root, env_values)
    config_path = update_embedding_config(root, provider_name=provider_name, config=config)
    return EmbeddingSetupResult(
        config_path=config_path,
        env_path=env_path,
        active_provider=provider_name,
        provider=provider,
        model=model,
        api_key_env=DEFAULT_EMBEDDING_API_KEY_ENV,
        base_url_env=DEFAULT_EMBEDDING_BASE_URL_ENV,
        ping_ok=True,
        ping_message="embedding provider connectivity test succeeded",
    )


def update_embedding_config(root: Path, *, provider_name: str, config: EmbeddingProviderConfig) -> Path:
    config_path = root.expanduser().resolve() / "config" / "embeddings.yaml"
    data = _load_yaml_mapping(config_path)
    providers = data.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    providers[provider_name] = config.model_dump(mode="json", exclude_none=True)
    data["providers"] = providers
    data["active_provider"] = provider_name
    validated = EmbeddingsConfig.model_validate(data)
    backup_if_exists(config_path, reason="setup_guide_embeddings")
    atomic_write_yaml(config_path, validated.model_dump(mode="json", exclude_none=True))
    findings = validate_secret_config_file(config_path)
    if findings:
        raise SetupGuideError("config/embeddings.yaml contains unsafe secret-like values after setup")
    return config_path


def is_port_available(port: int, *, host: str = "127.0.0.1") -> bool:
    if port < 1 or port > 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def find_available_port(start_port: int = 8765, *, host: str = "127.0.0.1") -> int:
    if start_port < 1 or start_port > 65535:
        raise SetupGuideError(f"invalid port: {start_port}")
    for port in range(start_port, 65536):
        if is_port_available(port, host=host):
            return port
    raise SetupGuideError(f"no available port found from {start_port}")


def configure_web_port(root: Path, *, requested_port: int, host: str = "127.0.0.1") -> PortSetupResult:
    selected = find_available_port(requested_port, host=host)
    project_path = update_project_web_port(root, selected)
    return PortSetupResult(
        project_path=project_path,
        requested_port=requested_port,
        selected_port=selected,
        host=host,
    )


def update_project_web_port(root: Path, port: int) -> Path:
    root = root.expanduser().resolve()
    project_path = root / "project.yaml"
    data = _load_yaml_mapping(project_path)
    web = data.get("web")
    if not isinstance(web, dict):
        web = {}
    web["default_port"] = port
    data["web"] = web
    ProjectConfig.model_validate(data)
    backup_if_exists(project_path, reason="setup_guide_web_port")
    atomic_write_yaml(project_path, data)
    return project_path


def _ping_model_provider(root: Path, config: AgentConfig, env: Mapping[str, str]) -> None:
    try:
        provider = ProviderFactory(
            env=env,
            log_path=root / "runs" / "provider_calls.jsonl",
        ).create(config)
        logging_provider = LoggingModelProvider(
            provider=provider,
            agent_name="setup",
            provider_name=config.provider,
            model=config.model,
            root=root,
        )
        response = logging_provider.generate(
            ModelRequest(
                system_prompt="You are a connectivity test for WriterYang. Reply with a short confirmation.",
                user_prompt="Return OK if you can read this request.",
            )
        )
    except MissingProviderEnvError as exc:
        raise SetupGuideError(str(exc)) from exc
    except ProviderError as exc:
        raise SetupGuideError(f"default provider connectivity test failed: {exc}") from exc
    if not response.content.strip():
        raise SetupGuideError("default provider connectivity test returned empty output")


def _ping_embedding_provider(config: EmbeddingProviderConfig, env: Mapping[str, str]) -> None:
    try:
        provider = EmbeddingProviderFactory(env=env).create(config)
        response = provider.embed_texts(["WriterYang embedding connectivity test"])
    except EmbeddingError as exc:
        raise SetupGuideError(f"embedding provider connectivity test failed: {exc}") from exc
    if not response.vectors or not response.vectors[0]:
        raise SetupGuideError("embedding provider connectivity test returned empty vector")


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    if not path.exists():
        raise SetupGuideError(f"{path} is missing")
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise SetupGuideError(f"{path} must contain a YAML mapping")
    return dict(data)


def _require_non_empty(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise SetupGuideError(f"{field_name} must not be empty")
    return stripped


def _normalize_thinking_type(value: str) -> Literal["enabled", "disabled"]:
    normalized = value.strip().lower()
    if normalized == "enabled":
        return "enabled"
    if normalized == "disabled":
        return "disabled"
    raise SetupGuideError("thinking.type must be enabled or disabled")
