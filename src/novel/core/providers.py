from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import time
from typing import Iterable, Mapping
from urllib import error, request

from novel.core.io import atomic_write_json
from novel.core.json_schema import (
    model_output_schema_payload,
    model_output_schema_skeleton,
    strict_model_output_schema_payload,
)
from novel.core.schemas import AgentConfig, AgentConfigPatch
from novel.core.usage import refresh_provider_usage_summary_for_log


@dataclass(frozen=True)
class ModelRequest:
    system_prompt: str
    user_prompt: str
    context: str | None = None
    json_schema_name: str | None = None
    request_id: str | None = None


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
    reasoning_content: str | None = None


class ProviderError(RuntimeError):
    """Base error for provider configuration or generation failures."""


class MissingProviderEnvError(ProviderError):
    """Raised when a provider requires an environment variable that is absent."""


class ProviderHTTPError(ProviderError):
    """Raised when a provider returns a non-success HTTP response."""


class ProviderRateLimitError(ProviderHTTPError):
    """Raised when a provider rate-limits a request."""


class ProviderAuthError(ProviderHTTPError):
    """Raised when provider authentication fails."""


class ProviderTimeoutError(ProviderError):
    """Raised when a provider request times out."""


class ProviderNetworkError(ProviderError):
    """Raised when transport fails before a provider response is received."""


class ProviderResponseError(ProviderError):
    """Raised when a provider returns invalid or unsupported response data."""


@dataclass(frozen=True)
class ProviderCallLog:
    request_id: str
    provider: str
    model: str
    endpoint: str
    started_at: str
    ended_at: str
    status: str
    attempt_count: int
    duration_ms: int
    stream: bool
    json_schema_name: str | None = None
    http_status: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    model_io_path: str | None = None


class ModelProvider(ABC):
    @abstractmethod
    def generate(self, model_request: ModelRequest) -> ModelResponse:
        """Generate text from a model request."""

    def chat(self, model_request: ModelRequest) -> ModelResponse:
        return self.generate(model_request)

    def stream(self, model_request: ModelRequest) -> Iterable[str]:
        yield self.generate(model_request).content

    def debug_payload(self, model_request: ModelRequest, *, stream: bool) -> object | None:
        return None


@dataclass
class MockProvider(ModelProvider):
    fixed_text: str = ""
    fake_response: ModelResponse | str | Mapping[str, object] | list[ModelResponse | str | Mapping[str, object]] | None = None
    stream_chunks: list[str] | None = None
    requests: list[ModelRequest] = field(default_factory=list)
    response_index: int = 0

    def generate(self, model_request: ModelRequest) -> ModelResponse:
        self.requests.append(model_request)
        return self._coerce_response(self._next_fake_response())

    def stream(self, model_request: ModelRequest) -> Iterable[str]:
        self.requests.append(model_request)
        if self.stream_chunks is not None:
            yield from self.stream_chunks
            return
        yield self._coerce_response(self._next_fake_response()).content

    def debug_payload(self, model_request: ModelRequest, *, stream: bool) -> object | None:
        payload: dict[str, object] = {
            "provider": "mock",
            "messages": _messages_from_request(model_request),
        }
        if stream:
            payload["stream"] = True
        if model_request.json_schema_name:
            payload["json_schema_name"] = model_request.json_schema_name
        return payload

    def _next_fake_response(self) -> ModelResponse | str | Mapping[str, object] | None:
        if isinstance(self.fake_response, list):
            if not self.fake_response:
                return None
            index = min(self.response_index, len(self.fake_response) - 1)
            self.response_index += 1
            return self.fake_response[index]
        return self.fake_response

    def _coerce_response(self, fake_response: ModelResponse | str | Mapping[str, object] | None) -> ModelResponse:
        if isinstance(fake_response, ModelResponse):
            return fake_response
        if isinstance(fake_response, str):
            return ModelResponse(content=fake_response, raw_response=fake_response)
        if isinstance(fake_response, Mapping):
            content = str(fake_response.get("content", self.fixed_text))
            usage = fake_response.get("usage")
            token_usage = _token_usage_from_raw(usage) if isinstance(usage, Mapping) else None
            reasoning = fake_response.get("reasoning_content")
            return ModelResponse(
                content=content,
                raw_response=dict(fake_response),
                token_usage=token_usage,
                reasoning_content=reasoning if isinstance(reasoning, str) else None,
            )
        return ModelResponse(content=self.fixed_text, raw_response=self.fixed_text)


@dataclass
class LoggingModelProvider(ModelProvider):
    provider: ModelProvider
    agent_name: str
    provider_name: str
    model: str
    root: Path

    def generate(self, model_request: ModelRequest) -> ModelResponse:
        request_id = model_request.request_id or _request_id()
        request_with_id = replace(model_request, request_id=request_id)
        started = time.monotonic()
        started_at = _utc_now()
        try:
            response = self.provider.generate(request_with_id)
        except Exception as exc:
            self._write_model_io(
                request_with_id,
                request_id=request_id,
                started_at=started_at,
                ended_at=_utc_now(),
                duration_ms=_duration_ms(started),
                status="failed",
                stream=False,
                error_payload={
                    "type": exc.__class__.__name__,
                    "message": _redact_text(str(exc), self._secret_values()),
                },
            )
            raise
        self._write_model_io(
            request_with_id,
            request_id=request_id,
            started_at=started_at,
            ended_at=_utc_now(),
            duration_ms=_duration_ms(started),
            status="success",
            stream=False,
            response=response,
        )
        return response

    def stream(self, model_request: ModelRequest) -> Iterable[str]:
        request_id = model_request.request_id or _request_id()
        request_with_id = replace(model_request, request_id=request_id)
        started = time.monotonic()
        started_at = _utc_now()
        chunks: list[str] = []
        try:
            for chunk in self.provider.stream(request_with_id):
                chunks.append(chunk)
                yield chunk
        except Exception as exc:
            self._write_model_io(
                request_with_id,
                request_id=request_id,
                started_at=started_at,
                ended_at=_utc_now(),
                duration_ms=_duration_ms(started),
                status="failed",
                stream=True,
                stream_chunks=len(chunks),
                response=ModelResponse(content="".join(chunks)),
                error_payload={
                    "type": exc.__class__.__name__,
                    "message": _redact_text(str(exc), self._secret_values()),
                },
            )
            raise
        self._write_model_io(
            request_with_id,
            request_id=request_id,
            started_at=started_at,
            ended_at=_utc_now(),
            duration_ms=_duration_ms(started),
            status="success",
            stream=True,
            stream_chunks=len(chunks),
            response=ModelResponse(content="".join(chunks)),
        )

    def debug_payload(self, model_request: ModelRequest, *, stream: bool) -> object | None:
        return self.provider.debug_payload(model_request, stream=stream)

    def _write_model_io(
        self,
        model_request: ModelRequest,
        *,
        request_id: str,
        started_at: str,
        ended_at: str,
        duration_ms: int,
        status: str,
        stream: bool,
        response: ModelResponse | None = None,
        stream_chunks: int | None = None,
        error_payload: dict[str, object] | None = None,
    ) -> None:
        model_io_dir = self.root / "runs" / "model_io"
        model_io_path = model_io_dir / f"{request_id}.json"
        secret_values = self._secret_values()
        payload = _redact_data(self.provider.debug_payload(model_request, stream=stream), secret_values)
        log = {
            "schema_version": "1.0",
            "request_id": request_id,
            "agent_name": self.agent_name,
            "provider": self.provider_name,
            "model": self.model,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": duration_ms,
            "status": status,
            "stream": stream,
            "stream_chunks": stream_chunks,
            "json_schema_name": model_request.json_schema_name,
            "request": {
                "system_prompt": _redact_text(model_request.system_prompt, secret_values),
                "user_prompt": _redact_text(model_request.user_prompt, secret_values),
                "context": _redact_text(model_request.context, secret_values),
                "payload": payload,
            },
            "response": _response_payload(response, secret_values),
            "error": error_payload,
            "token_usage": _token_usage_payload(response.token_usage if response else None),
            "provider_call_log_path": "runs/provider_calls.jsonl",
        }
        atomic_write_json(model_io_path, log)
        _append_jsonl(
            model_io_dir / "index.jsonl",
            {
                "request_id": request_id,
                "agent_name": self.agent_name,
                "provider": self.provider_name,
                "model": self.model,
                "started_at": started_at,
                "ended_at": ended_at,
                "status": status,
                "stream": stream,
                "json_schema_name": model_request.json_schema_name,
                "model_io_path": f"runs/model_io/{request_id}.json",
            },
        )

    def _secret_values(self) -> tuple[str, ...]:
        api_key = getattr(self.provider, "api_key", None)
        return (api_key,) if isinstance(api_key, str) and api_key else ()


@dataclass(frozen=True)
class ProviderParameterCapability:
    field: str
    effective: bool
    editable: bool
    reason: str | None = None
    allowed_values: tuple[str, ...] | None = None

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "field": self.field,
            "effective": self.effective,
            "editable": self.editable,
        }
        if self.reason:
            data["reason"] = self.reason
        if self.allowed_values is not None:
            data["allowed_values"] = list(self.allowed_values)
        return data


JSON_RESPONSE_FORMAT_VALUES = ("auto", "json_object", "json_schema", "json_schema_strict")


def provider_parameter_capabilities(
    provider_name: str,
    *,
    thinking_type: str | None = None,
) -> dict[str, ProviderParameterCapability]:
    provider = provider_name.lower()
    thinking = thinking_type or "disabled"
    capabilities = {
        field: ProviderParameterCapability(field=field, effective=True, editable=True)
        for field in (
            "provider",
            "model",
            "base_url_env",
            "api_key_env",
            "max_context_tokens",
            "max_tokens",
            "timeout_seconds",
            "max_retries",
        )
    }
    if provider in {"deepseek", "zai"}:
        capabilities["thinking"] = ProviderParameterCapability("thinking", True, True)
    else:
        capabilities["thinking"] = ProviderParameterCapability(
            "thinking",
            False,
            False,
            "当前 provider 不发送 thinking 参数",
        )

    if provider == "deepseek" and thinking == "enabled":
        capabilities["reasoning"] = ProviderParameterCapability("reasoning", True, True)
    else:
        capabilities["reasoning"] = ProviderParameterCapability(
            "reasoning",
            False,
            False,
            "仅 DeepSeek 且 thinking enabled 时发送 reasoning_effort",
        )

    if provider == "mock":
        capabilities["temperature"] = ProviderParameterCapability(
            "temperature",
            False,
            False,
            "mock provider 不发送 temperature",
        )
    elif provider == "deepseek" and thinking == "enabled":
        capabilities["temperature"] = ProviderParameterCapability(
            "temperature",
            False,
            False,
            "DeepSeek thinking enabled 时不会发送 temperature",
        )
    else:
        capabilities["temperature"] = ProviderParameterCapability("temperature", True, True)

    json_values = ("auto", "json_object") if provider in {"deepseek", "zai"} else JSON_RESPONSE_FORMAT_VALUES
    capabilities["json_response_format"] = ProviderParameterCapability(
        "json_response_format",
        True,
        True,
        allowed_values=json_values,
    )
    return capabilities


@dataclass(frozen=True)
class OpenAICompatibleProvider(ModelProvider):
    model: str
    api_key: str = field(repr=False)
    base_url: str
    api_provider: str = "openai"
    temperature: float | None = None
    thinking_type: str | None = None
    reasoning_effort: str | None = None
    max_tokens: int | None = None
    json_response_format: str = "auto"
    timeout_seconds: float = 60.0
    max_retries: int = 0
    retry_backoff_seconds: float = 0.25
    log_path: Path | None = None

    def with_log_path(self, log_path: Path) -> OpenAICompatibleProvider:
        return OpenAICompatibleProvider(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            api_provider=self.api_provider,
            temperature=self.temperature,
            thinking_type=self.thinking_type,
            reasoning_effort=self.reasoning_effort,
            max_tokens=self.max_tokens,
            json_response_format=self.json_response_format,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            retry_backoff_seconds=self.retry_backoff_seconds,
            log_path=log_path,
        )

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

        provider_name = config.provider.lower()
        base_url = _default_base_url(provider_name)
        if config.base_url_env:
            configured_base_url = env_map.get(config.base_url_env)
            if configured_base_url:
                base_url = configured_base_url
            elif provider_name == "openai_compatible":
                raise MissingProviderEnvError(
                    f"required environment variable {config.base_url_env} is not set "
                    "for base_url_env"
                )

        return cls(
            model=config.model,
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            api_provider=provider_name,
            temperature=config.temperature,
            thinking_type=config.thinking.type if provider_name in {"deepseek", "zai"} else None,
            reasoning_effort=config.reasoning if provider_name == "deepseek" else None,
            max_tokens=config.max_tokens,
            json_response_format=resolve_json_response_format(provider_name, config.json_response_format),
            timeout_seconds=timeout_seconds or config.timeout_seconds or 60.0,
            max_retries=config.max_retries or 0,
        )

    def generate(self, model_request: ModelRequest) -> ModelResponse:
        payload = self._payload(model_request, stream=False)
        raw = self._request_json(payload, model_request, stream=False)
        return _model_response_from_openai_raw(raw)

    def stream(self, model_request: ModelRequest) -> Iterable[str]:
        payload = self._payload(model_request, stream=True)
        yield from self._request_stream(payload, model_request)

    def debug_payload(self, model_request: ModelRequest, *, stream: bool) -> object | None:
        return self._payload(model_request, stream=stream)

    def _payload(self, model_request: ModelRequest, *, stream: bool) -> dict[str, object]:
        json_format = self._effective_json_response_format()
        schema_payload = (
            model_output_schema_payload(model_request.json_schema_name)
            if model_request.json_schema_name and json_format in {"json_schema", "json_schema_strict"}
            else None
        )
        use_json_object = bool(
            model_request.json_schema_name
            and (json_format == "json_object" or schema_payload is None)
        )
        if model_request.json_schema_name and json_format == "json_schema_strict":
            schema_payload = strict_model_output_schema_payload(model_request.json_schema_name)
            if schema_payload is None:
                raise ProviderError(
                    f"unknown json_schema_name for strict structured output: {model_request.json_schema_name}"
                )
        messages = _messages_from_request(model_request)
        if model_request.json_schema_name and use_json_object:
            messages = _ensure_json_mode_messages(messages, model_request.json_schema_name)
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
        }
        if stream:
            payload["stream"] = True
        if self.temperature is not None and not (
            self.api_provider == "deepseek" and self.thinking_type == "enabled"
        ):
            payload["temperature"] = self.temperature
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if self.thinking_type:
            payload["thinking"] = {"type": self.thinking_type}
        if self.reasoning_effort and self.thinking_type == "enabled":
            payload["reasoning_effort"] = self.reasoning_effort
        if model_request.json_schema_name:
            if use_json_object:
                payload["response_format"] = {"type": "json_object"}
            else:
                json_schema: dict[str, object] = {
                    "name": model_request.json_schema_name,
                    "schema": schema_payload,
                }
                if json_format == "json_schema_strict":
                    json_schema["strict"] = True
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": json_schema,
                }
        return payload

    def _effective_json_response_format(self) -> str:
        if self.json_response_format == "auto":
            return "json_schema" if self.api_provider == "openai" else "json_object"
        return self.json_response_format

    def _request_json(
        self,
        payload: Mapping[str, object],
        model_request: ModelRequest,
        *,
        stream: bool,
    ) -> Mapping[str, object]:
        response_body = self._send_with_retry(payload, model_request, stream=stream)
        try:
            raw = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise ProviderResponseError("provider returned invalid JSON") from exc
        if not isinstance(raw, Mapping):
            raise ProviderResponseError("provider JSON response must be an object")
        return raw

    def _request_stream(
        self,
        payload: Mapping[str, object],
        model_request: ModelRequest,
    ) -> Iterable[str]:
        response_body = self._send_with_retry(payload, model_request, stream=True)
        for line in response_body.splitlines():
            chunk = _stream_content_from_line(line)
            if chunk:
                yield chunk

    def _send_with_retry(
        self,
        payload: Mapping[str, object],
        model_request: ModelRequest,
        *,
        stream: bool,
    ) -> str:
        endpoint = f"{self.base_url}/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        request_id = model_request.request_id or _request_id()
        started = time.monotonic()
        started_at = _utc_now()
        attempts = self.max_retries + 1
        last_error: ProviderError | None = None
        for attempt in range(1, attempts + 1):
            try:
                http_request = request.Request(
                    endpoint,
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                        "X-WriterYang-Request-Id": request_id,
                    },
                    method="POST",
                )
                with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                    response_body = response.read().decode("utf-8")
                usage = _token_usage_from_response_body(response_body)
                self._write_call_log(
                    ProviderCallLog(
                        request_id=request_id,
                        provider=self.api_provider,
                        model=self.model,
                        endpoint=_safe_endpoint(endpoint),
                        started_at=started_at,
                        ended_at=_utc_now(),
                        status="success",
                        attempt_count=attempt,
                        duration_ms=_duration_ms(started),
                        stream=stream,
                        json_schema_name=model_request.json_schema_name,
                        prompt_tokens=usage.prompt_tokens if usage else None,
                        completion_tokens=usage.completion_tokens if usage else None,
                        total_tokens=usage.total_tokens if usage else None,
                        model_io_path=f"runs/model_io/{request_id}.json",
                    )
                )
                return response_body
            except error.HTTPError as exc:
                last_error = _http_error_from_status(self.api_provider, exc.code)
                if not _is_retryable_http_status(exc.code) or attempt == attempts:
                    self._log_failure(request_id, endpoint, started_at, started, attempt, stream, model_request, last_error, exc.code)
                    raise last_error from None
            except socket.timeout:
                last_error = ProviderTimeoutError(f"{self.api_provider} provider request timed out")
                if attempt == attempts:
                    self._log_failure(request_id, endpoint, started_at, started, attempt, stream, model_request, last_error)
                    raise last_error from None
            except error.URLError:
                last_error = ProviderNetworkError(f"{self.api_provider} provider network request failed")
                if attempt == attempts:
                    self._log_failure(request_id, endpoint, started_at, started, attempt, stream, model_request, last_error)
                    raise last_error from None
            except Exception as exc:
                last_error = ProviderError(
                    f"{self.api_provider} provider request failed: {exc.__class__.__name__}"
                )
                if attempt == attempts:
                    self._log_failure(request_id, endpoint, started_at, started, attempt, stream, model_request, last_error)
                    raise last_error from None
            time.sleep(self.retry_backoff_seconds * attempt)
        assert last_error is not None
        raise last_error

    def _log_failure(
        self,
        request_id: str,
        endpoint: str,
        started_at: str,
        started: float,
        attempt: int,
        stream: bool,
        model_request: ModelRequest,
        err: ProviderError,
        http_status: int | None = None,
    ) -> None:
        self._write_call_log(
            ProviderCallLog(
                request_id=request_id,
                provider=self.api_provider,
                model=self.model,
                endpoint=_safe_endpoint(endpoint),
                started_at=started_at,
                ended_at=_utc_now(),
                status="failed",
                attempt_count=attempt,
                duration_ms=_duration_ms(started),
                stream=stream,
                json_schema_name=model_request.json_schema_name,
                http_status=http_status,
                error_type=err.__class__.__name__,
                error_message=str(err),
                model_io_path=f"runs/model_io/{request_id}.json",
            )
        )

    def _write_call_log(self, entry: ProviderCallLog) -> None:
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry.__dict__, ensure_ascii=False, default=str) + "\n")
        try:
            refresh_provider_usage_summary_for_log(self.log_path)
        except Exception:
            return


class ProviderFactory:
    def __init__(self, env: Mapping[str, str] | None = None, log_path: Path | None = None) -> None:
        self.env = env
        self.log_path = log_path

    def create(self, config: AgentConfig) -> ModelProvider:
        provider = config.provider.lower()
        if provider == "mock":
            return MockProvider()
        if provider in {"openai", "openai_compatible", "deepseek", "zai"}:
            provider_instance = OpenAICompatibleProvider.from_config(config, env=self.env)
            if self.log_path:
                provider_instance = provider_instance.with_log_path(self.log_path)
            return provider_instance
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
        if provider_override and provider_override.lower() == "mock":
            return AgentConfig(
                provider="mock",
                model=model_override or "mock-model",
                api_key_env="MOCK_API_KEY",
            )
        agents = getattr(agents_config, "agents", None)
        if not isinstance(agents, dict):
            raise ProviderError("agents config is missing agents mapping")
        default_config = getattr(agents_config, "default", None)
        selected_name = next((name for name in (agent_name, *fallback_agents) if name in agents), None)
        selected_config = agents[selected_name] if selected_name else None
        if selected_config is None and default_config is None:
            names = ", ".join((agent_name, *fallback_agents))
            raise ProviderError(
                f"config/agents.yaml is missing agent config and default API config: {names}"
            )
        config = _merge_agent_config(default_config, selected_config)
        updates: dict[str, object] = {}
        if provider_override and provider_override != "config":
            updates["provider"] = provider_override
        if model_override:
            updates["model"] = model_override
        return config.model_copy(update=updates) if updates else config


def _merge_agent_config(
    default_config: AgentConfig | None,
    agent_config: AgentConfig | AgentConfigPatch | None,
) -> AgentConfig:
    if agent_config is None:
        if default_config is None:
            raise ProviderError("config/agents.yaml is missing default API config")
        return default_config.model_copy(update={"inherit_default": False})
    if getattr(agent_config, "inherit_default", False) is True:
        if default_config is None:
            raise ProviderError("config/agents.yaml agent inherits default but default API config is missing")
        return default_config.model_copy(update={"inherit_default": False})
    if default_config is None:
        if isinstance(agent_config, AgentConfig):
            return agent_config.model_copy(update={"inherit_default": False})
        missing = ", ".join(sorted(_missing_required_agent_fields(agent_config)))
        raise ProviderError(
            "config/agents.yaml agent override is incomplete and no default API config is defined"
            + (f": missing {missing}" if missing else "")
        )
    merged = default_config.model_dump(mode="python")
    merged.update(agent_config.model_dump(mode="python", exclude_unset=True, exclude_none=True))
    merged["inherit_default"] = False
    return AgentConfig.model_validate(merged)


def _missing_required_agent_fields(config: AgentConfigPatch) -> set[str]:
    provided = set(config.model_dump(mode="python", exclude_unset=True, exclude_none=True)) - {"inherit_default"}
    return {"provider", "model", "api_key_env"} - provided


def _required_env(env: Mapping[str, str], name: str, config_field: str) -> str:
    value = env.get(name)
    if not value:
        raise MissingProviderEnvError(
            f"required environment variable {name} is not set for {config_field}"
        )
    return value


def _default_base_url(provider: str) -> str:
    if provider == "deepseek":
        return "https://api.deepseek.com"
    if provider == "zai":
        return "https://open.bigmodel.cn/api/paas/v4"
    return "https://api.openai.com/v1"


def resolve_json_response_format(provider_name: str, configured: str) -> str:
    if configured == "auto":
        return "json_schema" if provider_name == "openai" else "json_object"
    if configured == "json_schema_strict" and provider_name not in {"openai", "openai_compatible"}:
        raise ProviderError(
            f"{provider_name} provider does not support json_schema_strict for chat completions; "
            "use json_object or auto"
        )
    if configured == "json_schema" and provider_name in {"deepseek", "zai"}:
        raise ProviderError(
            f"{provider_name} provider uses JSON Output mode for structured chat completions; "
            "use json_object or auto"
        )
    return configured


def _request_id() -> str:
    return "provider_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _duration_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _safe_endpoint(endpoint: str) -> str:
    return endpoint.split("?", 1)[0]


def _response_payload(response: ModelResponse | None, secret_values: tuple[str, ...]) -> dict[str, object] | None:
    if response is None:
        return None
    return {
        "content": _redact_text(response.content, secret_values),
        "reasoning_content": _redact_text(response.reasoning_content, secret_values),
        "raw_response": _redact_data(response.raw_response, secret_values),
    }


def _token_usage_payload(token_usage: TokenUsage | None) -> dict[str, int | None] | None:
    return asdict(token_usage) if token_usage else None


def _append_jsonl(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")


def _redact_data(value: object, secret_values: tuple[str, ...]) -> object:
    if isinstance(value, str):
        return _redact_text(value, secret_values)
    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in {"authorization", "api_key", "apikey", "token", "secret"}:
                redacted[key_text] = "[redacted]"
            else:
                redacted[key_text] = _redact_data(item, secret_values)
        return redacted
    if isinstance(value, list):
        return [_redact_data(item, secret_values) for item in value]
    if isinstance(value, tuple):
        return [_redact_data(item, secret_values) for item in value]
    return value


def _redact_text(value: str | None, secret_values: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    redacted = value.replace("Authorization", "[redacted-header]")
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
            redacted = redacted.replace(f"Bearer {secret}", "Bearer [redacted]")
    return redacted


def _is_retryable_http_status(status: int) -> bool:
    return status in {408, 409, 425, 429, 500, 502, 503, 504}


def _http_error_from_status(provider: str, status: int) -> ProviderHTTPError:
    if status in {401, 403}:
        return ProviderAuthError(f"{provider} provider authentication failed with HTTP {status}")
    if status == 429:
        return ProviderRateLimitError(f"{provider} provider rate limited the request with HTTP 429")
    return ProviderHTTPError(f"{provider} provider returned HTTP {status}")


def _messages_from_request(model_request: ModelRequest) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": model_request.system_prompt}]
    user_parts = []
    if model_request.context:
        user_parts.append(f"Context:\n{model_request.context}")
    user_parts.append(model_request.user_prompt)
    messages.append({"role": "user", "content": "\n\n".join(user_parts)})
    return messages


def _ensure_json_mode_messages(
    messages: list[dict[str, str]],
    schema_name: str,
) -> list[dict[str, str]]:
    combined = "\n".join(message.get("content", "") for message in messages)
    marker = "WriterYang JSON mode guard"
    if marker in combined:
        return messages
    updated = [dict(message) for message in messages]
    skeleton = model_output_schema_skeleton(schema_name)
    skeleton_text = f"\n\nExpected JSON structure skeleton:\n{skeleton}" if skeleton else ""
    updated[0]["content"] = (
        updated[0].get("content", "")
        + "\n\n"
        f"{marker}: output must be a single valid JSON object for schema {schema_name}. "
        "Do not include Markdown code fences, explanations, comments, or wrapper text. "
        "Use double-quoted JSON keys and values where JSON requires strings."
        + skeleton_text
    )
    return updated


def _stream_content_from_line(line: str) -> str | None:
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None
    data = line.removeprefix("data:").strip()
    if data == "[DONE]":
        return None
    try:
        raw = json.loads(data)
    except json.JSONDecodeError:
        return None
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        return None
    delta = first_choice.get("delta")
    if isinstance(delta, Mapping):
        content = delta.get("content")
        if isinstance(content, str):
            return content
    message = first_choice.get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return None


def _model_response_from_openai_raw(raw: Mapping[str, object]) -> ModelResponse:
    content = ""
    reasoning_content = None
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, Mapping):
            message = first_choice.get("message")
            if isinstance(message, Mapping):
                raw_content = message.get("content")
                if isinstance(raw_content, str):
                    content = raw_content
                raw_reasoning = message.get("reasoning_content")
                if isinstance(raw_reasoning, str):
                    reasoning_content = raw_reasoning

    usage = raw.get("usage")
    token_usage = _token_usage_from_raw(usage) if isinstance(usage, Mapping) else None
    return ModelResponse(
        content=content,
        raw_response=dict(raw),
        token_usage=token_usage,
        reasoning_content=reasoning_content,
    )


def _token_usage_from_raw(raw: Mapping[str, object]) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=_optional_int(raw.get("prompt_tokens")),
        completion_tokens=_optional_int(raw.get("completion_tokens")),
        total_tokens=_optional_int(raw.get("total_tokens")),
    )


def _token_usage_from_response_body(response_body: str) -> TokenUsage | None:
    try:
        raw = json.loads(response_body)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, Mapping):
        return None
    usage = raw.get("usage")
    return _token_usage_from_raw(usage) if isinstance(usage, Mapping) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
