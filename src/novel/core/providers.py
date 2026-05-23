from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
import os
from typing import Mapping
from urllib import error, request

from novel.core.schemas import AgentConfig


@dataclass(frozen=True)
class ModelRequest:
    system_prompt: str
    user_prompt: str
    context: str | None = None
    json_schema_name: str | None = None


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ModelResponse:
    content: str
    raw_response: object | None = None
    token_usage: TokenUsage | None = None


class ProviderError(RuntimeError):
    """Base error for provider configuration or generation failures."""


class MissingProviderEnvError(ProviderError):
    """Raised when a provider requires an environment variable that is absent."""


class ModelProvider(ABC):
    @abstractmethod
    def generate(self, model_request: ModelRequest) -> ModelResponse:
        """Generate text from a model request."""

    def chat(self, model_request: ModelRequest) -> ModelResponse:
        return self.generate(model_request)


@dataclass
class MockProvider(ModelProvider):
    fixed_text: str = ""
    fake_response: ModelResponse | str | Mapping[str, object] | None = None
    requests: list[ModelRequest] = field(default_factory=list)

    def generate(self, model_request: ModelRequest) -> ModelResponse:
        self.requests.append(model_request)
        if isinstance(self.fake_response, ModelResponse):
            return self.fake_response
        if isinstance(self.fake_response, str):
            return ModelResponse(content=self.fake_response, raw_response=self.fake_response)
        if isinstance(self.fake_response, Mapping):
            content = str(self.fake_response.get("content", self.fixed_text))
            usage = self.fake_response.get("usage")
            token_usage = _token_usage_from_raw(usage) if isinstance(usage, Mapping) else None
            return ModelResponse(
                content=content,
                raw_response=dict(self.fake_response),
                token_usage=token_usage,
            )
        return ModelResponse(content=self.fixed_text, raw_response=self.fixed_text)


@dataclass(frozen=True)
class OpenAICompatibleProvider(ModelProvider):
    model: str
    api_key: str = field(repr=False)
    base_url: str
    temperature: float | None = None
    thinking_type: str | None = None
    json_response_format: str = "json_schema"
    timeout_seconds: float = 60.0
    max_retries: int = 0

    @classmethod
    def from_config(
        cls,
        config: AgentConfig,
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> OpenAICompatibleProvider:
        env_map = os.environ if env is None else env
        api_key = _required_env(env_map, config.api_key_env, "api_key_env")

        base_url = "https://api.openai.com/v1"
        if config.base_url_env:
            configured_base_url = env_map.get(config.base_url_env)
            if configured_base_url:
                base_url = configured_base_url
            elif config.provider != "openai":
                raise MissingProviderEnvError(
                    f"required environment variable {config.base_url_env} is not set "
                    "for base_url_env"
                )

        return cls(
            model=config.model,
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            temperature=config.temperature,
            thinking_type=config.thinking.type if config.provider == "openai_compatible" else None,
            json_response_format="json_object" if config.provider == "openai_compatible" else "json_schema",
            timeout_seconds=timeout_seconds or config.timeout_seconds or 60.0,
            max_retries=config.max_retries or 0,
        )

    def generate(self, model_request: ModelRequest) -> ModelResponse:
        payload = {
            "model": self.model,
            "messages": _messages_from_request(model_request),
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.thinking_type:
            payload["thinking"] = {"type": self.thinking_type}
        if model_request.json_schema_name:
            if self.json_response_format == "json_object":
                payload["response_format"] = {"type": "json_object"}
            else:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": model_request.json_schema_name,
                        "schema": {},
                    },
                }

        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        attempts = self.max_retries + 1
        last_exception: Exception | None = None
        for _ in range(attempts):
            try:
                with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                return _model_response_from_openai_raw(raw)
            except error.HTTPError as exc:
                raise ProviderError(
                    f"OpenAI-compatible provider returned HTTP {exc.code}"
                ) from None
            except Exception as exc:
                last_exception = exc
        assert last_exception is not None
        raise ProviderError(
            f"OpenAI-compatible provider request failed: {last_exception.__class__.__name__}"
        ) from None


class ProviderFactory:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self.env = env

    def create(self, config: AgentConfig) -> ModelProvider:
        provider = config.provider.lower()
        if provider == "mock":
            return MockProvider()
        if provider in {"openai", "openai_compatible"}:
            return OpenAICompatibleProvider.from_config(config, env=self.env)
        raise ProviderError(f"unsupported provider: {config.provider}")

    def create_for_agent(
        self,
        agents_config: object,
        agent_name: str,
        *,
        fallback_agents: tuple[str, ...] = (),
        provider_override: str | None = None,
        model_override: str | None = None,
    ) -> ModelProvider:
        config = self.resolve_agent_config(
            agents_config,
            agent_name,
            fallback_agents=fallback_agents,
            provider_override=provider_override,
            model_override=model_override,
        )
        return self.create(config)

    def resolve_agent_config(
        self,
        agents_config: object,
        agent_name: str,
        *,
        fallback_agents: tuple[str, ...] = (),
        provider_override: str | None = None,
        model_override: str | None = None,
    ) -> AgentConfig:
        agents = getattr(agents_config, "agents", None)
        if not isinstance(agents, dict):
            raise ProviderError("agents config is missing agents mapping")
        selected_name = next(
            (name for name in (agent_name, *fallback_agents) if name in agents),
            None,
        )
        if selected_name is None:
            names = ", ".join((agent_name, *fallback_agents))
            raise ProviderError(f"config/agents.yaml is missing agent config: {names}")
        config = agents[selected_name]
        updates: dict[str, object] = {}
        if provider_override and provider_override != "config":
            updates["provider"] = provider_override
        if model_override:
            updates["model"] = model_override
        return config.model_copy(update=updates) if updates else config


def _required_env(env: Mapping[str, str], name: str, config_field: str) -> str:
    value = env.get(name)
    if not value:
        raise MissingProviderEnvError(
            f"required environment variable {name} is not set for {config_field}"
        )
    return value


def _messages_from_request(model_request: ModelRequest) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": model_request.system_prompt}]
    user_parts = []
    if model_request.context:
        user_parts.append(f"Context:\n{model_request.context}")
    user_parts.append(model_request.user_prompt)
    messages.append({"role": "user", "content": "\n\n".join(user_parts)})
    return messages


def _model_response_from_openai_raw(raw: Mapping[str, object]) -> ModelResponse:
    content = ""
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, Mapping):
            message = first_choice.get("message")
            if isinstance(message, Mapping):
                raw_content = message.get("content")
                if isinstance(raw_content, str):
                    content = raw_content

    usage = raw.get("usage")
    token_usage = _token_usage_from_raw(usage) if isinstance(usage, Mapping) else None
    return ModelResponse(content=content, raw_response=dict(raw), token_usage=token_usage)


def _token_usage_from_raw(raw: Mapping[str, object]) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=_optional_int(raw.get("prompt_tokens")),
        completion_tokens=_optional_int(raw.get("completion_tokens")),
        total_tokens=_optional_int(raw.get("total_tokens")),
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
