from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import time
from typing import Iterable, Mapping
from urllib import error, request

from novel.core.schemas import AgentConfig
from novel.core.usage import refresh_provider_usage_summary_for_log


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


class ModelProvider(ABC):
    @abstractmethod
    def generate(self, model_request: ModelRequest) -> ModelResponse:
        """Generate text from a model request."""

    def chat(self, model_request: ModelRequest) -> ModelResponse:
        return self.generate(model_request)

    def stream(self, model_request: ModelRequest) -> Iterable[str]:
        yield self.generate(model_request).content


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
    json_response_format: str = "json_schema"
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
            json_response_format="json_schema" if provider_name == "openai" else "json_object",
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

    def _payload(self, model_request: ModelRequest, *, stream: bool) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": _messages_from_request(model_request),
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
        return payload

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
        request_id = _request_id()
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


def _default_base_url(provider: str) -> str:
    if provider == "deepseek":
        return "https://api.deepseek.com"
    if provider == "zai":
        return "https://open.bigmodel.cn/api/paas/v4"
    return "https://api.openai.com/v1"


def _request_id() -> str:
    return "provider_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _duration_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _safe_endpoint(endpoint: str) -> str:
    return endpoint.split("?", 1)[0]


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
