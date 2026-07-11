from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, replace
import json
import os
from pathlib import Path
import socket
import time
from typing import Callable, Iterable, Mapping
from urllib import error, request

from novel.core.budget import consume_model_call, consume_provider_attempt, consume_response_tokens
from novel.core.context_policy import render_untrusted_workspace_data
from novel.core.agent_defaults import (
    PROFILE_INHERITED_PATCH_FIELDS,
    PROFILE_NAMES,
    TASK_ONLY_CONFIG_FIELDS,
    TASK_TO_PROFILE,
    config_patch_fields,
    task_business_defaults,
)
from novel.core.io import append_jsonl, atomic_write_json
from novel.core.json_schema import (
    model_output_schema_payload,
    model_output_schema_skeleton,
    strict_model_output_schema_payload,
)
from novel.core.model_io import (
    compact_model_io_payload,
    content_sha256,
    model_io_retention_policy_from_env,
    prune_model_io_dir,
)
from novel.core.task_registry import prompt_registry_entry, task_definition_for_agent
from novel.core.workflow_runtime import active_trace_metadata, active_workflow_runtime
from novel.core.schemas import AgentConfig, AgentConfigPatch
from novel.core.security import redact_secret_text
from novel.core.timeutil import new_request_id, utc_now_iso
from novel.core.usage import refresh_provider_usage_summary_for_log


@dataclass(frozen=True)
class ModelRequest:
    system_prompt: str
    user_prompt: str
    context: str | None = None
    json_schema_name: str | None = None
    request_id: str | None = None
    agent_name: str | None = None
    prompt_version: str | None = None
    repair_count: int = 0
    workflow_run_id: str | None = None
    surface: str | None = None
    session_id: str | None = None
    parent_request_id: str | None = None


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
    finish_reason: str | None = None


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


class ProviderContextLimitError(ProviderError):
    """Raised before a request when the assembled prompt exceeds the configured context window."""


class ProviderOutputTruncatedError(ProviderError):
    """Raised when the provider reports that output was cut off by max_tokens."""


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
    agent_name: str | None = None
    json_schema_name: str | None = None
    finish_reason: str | None = None
    http_status: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    model_io_path: str | None = None
    workflow_run_id: str | None = None
    surface: str | None = None
    session_id: str | None = None
    parent_request_id: str | None = None
    node_id: str | None = None


class ModelProvider(ABC):
    @abstractmethod
    def generate(self, model_request: ModelRequest) -> ModelResponse:
        """Generate text from a model request."""

    def stream(self, model_request: ModelRequest) -> Iterable[str]:
        response = self.stream_response(model_request)
        if response.content:
            yield response.content

    def stream_response(self, model_request: ModelRequest) -> ModelResponse:
        return self.generate(model_request)

    def debug_payload(self, model_request: ModelRequest, *, stream: bool) -> object | None:
        return None


@dataclass
class MockProvider(ModelProvider):
    fixed_text: str = ""
    fake_response: (
        ModelResponse | str | Mapping[str, object] | list[ModelResponse | str | Mapping[str, object]] | None
    ) = None
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

    def stream_response(self, model_request: ModelRequest) -> ModelResponse:
        self.requests.append(model_request)
        if self.stream_chunks is not None:
            return ModelResponse(
                content="".join(self.stream_chunks),
                raw_response={"stream": True, "stream_chunks": len(self.stream_chunks)},
            )
        return self._coerce_response(self._next_fake_response())

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
                finish_reason=str(fake_response["finish_reason"])
                if fake_response.get("finish_reason") is not None
                else None,
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
        request_id = model_request.request_id or new_request_id("provider")
        request_with_id = _request_with_active_trace(replace(
            model_request,
            request_id=request_id,
            agent_name=model_request.agent_name or self.agent_name,
        ))
        started = time.monotonic()
        started_at = utc_now_iso()
        try:
            response = self._execute_model_node(
                request_with_id,
                lambda: self.provider.generate(request_with_id),
            )
        except Exception as exc:
            self._write_model_io(
                request_with_id,
                request_id=request_id,
                started_at=started_at,
                ended_at=utc_now_iso(),
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
            ended_at=utc_now_iso(),
            duration_ms=_duration_ms(started),
            status="success",
            stream=False,
            response=response,
        )
        return response

    def stream_response(self, model_request: ModelRequest) -> ModelResponse:
        request_id = model_request.request_id or new_request_id("provider")
        request_with_id = _request_with_active_trace(replace(
            model_request,
            request_id=request_id,
            agent_name=model_request.agent_name or self.agent_name,
        ))
        started = time.monotonic()
        started_at = utc_now_iso()
        try:
            response = self._execute_model_node(
                request_with_id,
                lambda: self.provider.stream_response(request_with_id),
            )
        except Exception as exc:
            self._write_model_io(
                request_with_id,
                request_id=request_id,
                started_at=started_at,
                ended_at=utc_now_iso(),
                duration_ms=_duration_ms(started),
                status="failed",
                stream=True,
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
            ended_at=utc_now_iso(),
            duration_ms=_duration_ms(started),
            status="success",
            stream=True,
            stream_chunks=_stream_chunk_count(response),
            response=response,
        )
        return response

    def _execute_model_node(
        self,
        model_request: ModelRequest,
        invoke: Callable[[], ModelResponse],
    ) -> ModelResponse:
        def budgeted_invoke() -> ModelResponse:
            consume_model_call()
            consume_provider_attempt()
            response = invoke()
            if response.token_usage:
                consume_response_tokens(
                    response.token_usage.prompt_tokens,
                    response.token_usage.completion_tokens,
                )
            return response

        runtime = active_workflow_runtime()
        if runtime is None:
            return budgeted_invoke()
        definition = task_definition_for_agent(self.agent_name)
        if definition is None:
            raise RuntimeError(f"agent task is not registered: {self.agent_name}")
        prompt_entry = prompt_registry_entry(definition.task_id)
        return runtime.execute_node(
            name=f"model:{self.agent_name}",
            node_type="model",
            function=budgeted_invoke,
            task_id=definition.task_id,
            profile_id=definition.profile,
            provider=self.provider_name,
            model=self.model,
            prompt_template=model_request.system_prompt,
            prompt_policy_hash=prompt_entry.policy_hash,
            rendered_prompt="\n".join(
                value
                for value in (
                    model_request.system_prompt,
                    model_request.user_prompt,
                    model_request.context,
                )
                if value
            ),
            repair_count=model_request.repair_count,
            request_id=model_request.request_id,
            input_paths=["project.yaml"],
            output_details=lambda _: ([], [f"runs/model_io/{model_request.request_id}.json"]),
        )

    def stream(self, model_request: ModelRequest) -> Iterable[str]:
        response = self.stream_response(model_request)
        if response.content:
            yield response.content

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
        runtime = active_workflow_runtime()
        model_node_id = runtime.last_completed_node_id if runtime else active_trace_metadata().node_id
        secret_values = self._secret_values()
        try:
            payload = _redact_data(self.provider.debug_payload(model_request, stream=stream), secret_values)
        except Exception as exc:
            payload = {"debug_payload_error": _redact_text(str(exc), secret_values)}
        log = {
            "schema_version": "1.0",
            "request_id": request_id,
            "agent_name": self.agent_name,
            "workflow_run_id": model_request.workflow_run_id,
            "surface": model_request.surface,
            "session_id": model_request.session_id,
            "parent_request_id": model_request.parent_request_id,
            "node_id": model_node_id,
            "provider": self.provider_name,
            "model": self.model,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": duration_ms,
            "status": status,
            "stream": stream,
            "stream_chunks": stream_chunks,
            "json_schema_name": model_request.json_schema_name,
            "finish_reason": response.finish_reason if response else None,
            "request": {
                "system_prompt": _redact_text(model_request.system_prompt, secret_values),
                "user_prompt": _redact_text(model_request.user_prompt, secret_values),
                "context": _redact_text(model_request.context, secret_values),
                "prompt_version": model_request.prompt_version,
                "payload": payload,
                "hashes": {
                    "system_prompt_sha256": content_sha256(model_request.system_prompt),
                    "user_prompt_sha256": content_sha256(model_request.user_prompt),
                    "context_sha256": content_sha256(model_request.context),
                    "payload_sha256": content_sha256(payload),
                },
            },
            "response": _response_payload_with_hashes(response, secret_values),
            "error": error_payload,
            "token_usage": _token_usage_payload(response.token_usage if response else None),
            "provider_call_log_path": "runs/provider_calls.jsonl",
        }
        policy = model_io_retention_policy_from_env()
        if policy.mode == "metadata":
            log = compact_model_io_payload(log)
        atomic_write_json(model_io_path, log)
        append_jsonl(
            model_io_dir / "index.jsonl",
            {
                "request_id": request_id,
                "agent_name": self.agent_name,
                "workflow_run_id": model_request.workflow_run_id,
                "surface": model_request.surface,
                "session_id": model_request.session_id,
                "parent_request_id": model_request.parent_request_id,
                "node_id": model_node_id,
                "provider": self.provider_name,
                "model": self.model,
                "started_at": started_at,
                "ended_at": ended_at,
                "status": status,
                "stream": stream,
                "json_schema_name": model_request.json_schema_name,
                "finish_reason": response.finish_reason if response else None,
                "model_io_path": f"runs/model_io/{request_id}.json",
            },
        )
        try:
            prune_model_io_dir(model_io_dir, policy)
        except Exception:
            return

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
    max_context_tokens: int | None = None
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
            max_context_tokens=self.max_context_tokens,
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
                    f"required environment variable {config.base_url_env} is not set for base_url_env"
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
            max_context_tokens=config.max_context_tokens,
            json_response_format=resolve_json_response_format(provider_name, config.json_response_format),
            timeout_seconds=timeout_seconds or config.timeout_seconds or 60.0,
            max_retries=config.max_retries or 0,
        )

    def generate(self, model_request: ModelRequest) -> ModelResponse:
        payload = self._payload(model_request, stream=False)
        raw = self._request_json(payload, model_request, stream=False)
        return _model_response_from_openai_raw(raw)

    def stream_response(self, model_request: ModelRequest) -> ModelResponse:
        payload = self._payload(model_request, stream=True)
        return self._request_stream_response(payload, model_request)

    def stream(self, model_request: ModelRequest) -> Iterable[str]:
        response = self.stream_response(model_request)
        if response.content:
            yield response.content

    def debug_payload(self, model_request: ModelRequest, *, stream: bool) -> object | None:
        return self._payload(model_request, stream=stream, validate_context=False)

    def _payload(
        self,
        model_request: ModelRequest,
        *,
        stream: bool,
        validate_context: bool = True,
    ) -> dict[str, object]:
        json_format = self._effective_json_response_format()
        schema_payload = (
            model_output_schema_payload(model_request.json_schema_name)
            if model_request.json_schema_name and json_format in {"json_schema", "json_schema_strict"}
            else None
        )
        use_json_object = bool(
            model_request.json_schema_name and (json_format == "json_object" or schema_payload is None)
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
        if validate_context:
            _validate_context_window(messages, max_context_tokens=self.max_context_tokens)
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
        }
        if stream:
            payload["stream"] = True
            if self.api_provider in {"deepseek", "openai"}:
                payload["stream_options"] = {"include_usage": True}
        if self.temperature is not None and not (self.api_provider == "deepseek" and self.thinking_type == "enabled"):
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
        response = self._request_stream_response(payload, model_request)
        if response.content:
            yield response.content

    def _request_stream_response(
        self,
        payload: Mapping[str, object],
        model_request: ModelRequest,
    ) -> ModelResponse:
        response_body = self._send_with_retry(payload, model_request, stream=True)
        return _model_response_from_openai_sse(response_body)

    def _send_with_retry(
        self,
        payload: Mapping[str, object],
        model_request: ModelRequest,
        *,
        stream: bool,
    ) -> str:
        endpoint = f"{self.base_url}/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        request_id = model_request.request_id or new_request_id("provider")
        started = time.monotonic()
        started_at = utc_now_iso()
        attempts = self.max_retries + 1
        last_error: ProviderError | None = None
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                consume_provider_attempt()
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
                    response_body = str(response.read().decode("utf-8"))
                usage, finish_reason = _response_metadata_from_body(response_body, stream=stream)
                self._write_call_log(
                    ProviderCallLog(
                        request_id=request_id,
                        agent_name=model_request.agent_name,
                        provider=self.api_provider,
                        model=self.model,
                        endpoint=_safe_endpoint(endpoint),
                        started_at=started_at,
                        ended_at=utc_now_iso(),
                        status="success",
                        attempt_count=attempt,
                        duration_ms=_duration_ms(started),
                        stream=stream,
                        json_schema_name=model_request.json_schema_name,
                        finish_reason=finish_reason,
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
                    self._log_failure(
                        request_id, endpoint, started_at, started, attempt, stream, model_request, last_error, exc.code
                    )
                    raise last_error from None
            except socket.timeout:
                last_error = ProviderTimeoutError(f"{self.api_provider} provider request timed out")
                if attempt == attempts:
                    self._log_failure(
                        request_id, endpoint, started_at, started, attempt, stream, model_request, last_error
                    )
                    raise last_error from None
            except error.URLError:
                last_error = ProviderNetworkError(f"{self.api_provider} provider network request failed")
                if attempt == attempts:
                    self._log_failure(
                        request_id, endpoint, started_at, started, attempt, stream, model_request, last_error
                    )
                    raise last_error from None
            except Exception as exc:
                last_error = ProviderError(f"{self.api_provider} provider request failed: {exc.__class__.__name__}")
                if attempt == attempts:
                    self._log_failure(
                        request_id, endpoint, started_at, started, attempt, stream, model_request, last_error
                    )
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
                agent_name=model_request.agent_name,
                provider=self.api_provider,
                model=self.model,
                endpoint=_safe_endpoint(endpoint),
                started_at=started_at,
                ended_at=utc_now_iso(),
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
        trace = active_trace_metadata()
        traced_entry = replace(
            entry,
            workflow_run_id=entry.workflow_run_id or trace.workflow_run_id,
            surface=entry.surface or (trace.surface.value if trace.surface else None),
            session_id=entry.session_id or trace.session_id,
            parent_request_id=entry.parent_request_id or trace.request_id,
            node_id=entry.node_id or trace.node_id,
        )
        append_jsonl(self.log_path, traced_entry.__dict__)
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

    def resolve_agent_config(
        self,
        agents_config: object,
        task_name: str,
        *,
        provider_override: str | None = None,
        model_override: str | None = None,
    ) -> AgentConfig:
        if provider_override and provider_override.lower() == "mock":
            return AgentConfig(
                provider="mock",
                model=model_override or "mock-model",
                api_key_env="MOCK_API_KEY",
            )
        profiles = getattr(agents_config, "profiles", None)
        tasks = getattr(agents_config, "tasks", None)
        if not isinstance(profiles, dict):
            raise ProviderError("agents config is missing profiles mapping")
        if not isinstance(tasks, dict):
            raise ProviderError("agents config is missing tasks mapping")
        default_config = getattr(agents_config, "default", None)
        profile_name = _profile_name_for_task(task_name)
        profile_config = _merge_profile_config(default_config, profiles.get(profile_name), profile_name)
        config = _merge_task_config(profile_config, tasks.get(task_name), task_name)
        updates: dict[str, object] = {}
        if provider_override and provider_override != "config":
            updates["provider"] = provider_override
        if model_override:
            updates["model"] = model_override
        return config.model_copy(update=updates) if updates else config

    def resolve_profile_config(
        self,
        agents_config: object,
        profile_name: str,
        *,
        provider_override: str | None = None,
        model_override: str | None = None,
    ) -> AgentConfig:
        if provider_override and provider_override.lower() == "mock":
            return AgentConfig(
                provider="mock",
                model=model_override or "mock-model",
                api_key_env="MOCK_API_KEY",
            )
        profiles = getattr(agents_config, "profiles", None)
        if not isinstance(profiles, dict):
            raise ProviderError("agents config is missing profiles mapping")
        default_config = getattr(agents_config, "default", None)
        config = _merge_profile_config(default_config, profiles.get(profile_name), profile_name)
        updates: dict[str, object] = {}
        if provider_override and provider_override != "config":
            updates["provider"] = provider_override
        if model_override:
            updates["model"] = model_override
        return config.model_copy(update=updates) if updates else config


def _merge_profile_config(
    default_config: AgentConfig | None,
    profile_config: AgentConfig | AgentConfigPatch | None,
    profile_name: str,
) -> AgentConfig:
    if profile_name not in PROFILE_NAMES:
        raise ProviderError(f"unknown profile: {profile_name}")
    base = default_config
    patch_source = profile_config
    if patch_source is None:
        patch_source = AgentConfigPatch.model_validate({"inherit_default": True})
    if getattr(patch_source, "inherit_default", False) is True:
        if base is None:
            raise ProviderError(f"profile {profile_name} inherits default but default API config is missing")
        raw_patch = patch_source.model_dump(mode="python", exclude_unset=True, exclude_none=True)
        merged = base.model_dump(mode="python", exclude=_exclude_fields(TASK_ONLY_CONFIG_FIELDS))
        merged.update({key: value for key, value in raw_patch.items() if key in PROFILE_INHERITED_PATCH_FIELDS})
        merged["inherit_default"] = False
        return AgentConfig.model_validate(merged)
    return _merge_config_patch(
        base,
        patch_source,
        missing_message=f"config/agents.yaml is missing profile config and default API config: {profile_name}",
        incomplete_message="config/agents.yaml profile config is incomplete",
        exclude_base_fields=TASK_ONLY_CONFIG_FIELDS,
    )


def _merge_task_config(
    profile_config: AgentConfig,
    task_config: AgentConfig | AgentConfigPatch | None,
    task_name: str,
) -> AgentConfig:
    task_defaults = task_business_defaults(task_name)
    merged = profile_config.model_dump(mode="python", exclude_none=True, exclude={"inherit_default"})
    merged.update(task_defaults)
    if task_config is not None:
        raw_patch = task_config.model_dump(mode="python", exclude_unset=True, exclude_none=True)
        merged.update(config_patch_fields(raw_patch))
    merged["inherit_default"] = False
    return AgentConfig.model_validate(merged)


def _merge_config_patch(
    default_config: AgentConfig | None,
    patch_config: AgentConfig | AgentConfigPatch | None,
    *,
    missing_message: str,
    incomplete_message: str,
    exclude_base_fields: set[str] | frozenset[str] | None = None,
) -> AgentConfig:
    if patch_config is None:
        if default_config is None:
            raise ProviderError(missing_message)
        raw_default = default_config.model_dump(mode="python", exclude=_exclude_fields(exclude_base_fields))
        raw_default["inherit_default"] = False
        return AgentConfig.model_validate(raw_default)
    raw_patch = patch_config.model_dump(mode="python", exclude_unset=True, exclude_none=True)
    updates = config_patch_fields(raw_patch)
    if default_config is not None:
        merged = default_config.model_dump(mode="python", exclude=_exclude_fields(exclude_base_fields))
        merged.update(updates)
        merged["inherit_default"] = False
        return AgentConfig.model_validate(merged)
    missing = ", ".join(sorted(_missing_required_fields(updates)))
    if missing:
        raise ProviderError(f"{incomplete_message} and no default API config is defined: missing {missing}")
    updates["inherit_default"] = False
    return AgentConfig.model_validate(updates)


def _missing_required_fields(config: Mapping[str, object]) -> set[str]:
    provided = set(config)
    return {"provider", "model", "api_key_env"} - provided


def _exclude_fields(fields: Iterable[str] | None) -> dict[str, bool]:
    return {field: True for field in fields or ()}


def _profile_name_for_task(task_name: str) -> str:
    if task_name in PROFILE_NAMES:
        return task_name
    profile_name = TASK_TO_PROFILE.get(task_name)
    if profile_name is None:
        raise ProviderError(f"unknown task: {task_name}")
    return profile_name


def _required_env(env: Mapping[str, str], name: str, config_field: str) -> str:
    value = env.get(name)
    if not value:
        raise MissingProviderEnvError(f"required environment variable {name} is not set for {config_field}")
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
            f"{provider_name} provider uses JSON Output mode for structured chat completions; use json_object or auto"
        )
    return configured


def _duration_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _request_with_active_trace(model_request: ModelRequest) -> ModelRequest:
    trace = active_trace_metadata()
    return replace(
        model_request,
        workflow_run_id=model_request.workflow_run_id or trace.workflow_run_id,
        surface=model_request.surface or (trace.surface.value if trace.surface else None),
        session_id=model_request.session_id or trace.session_id,
        parent_request_id=model_request.parent_request_id or trace.request_id,
    )


def _safe_endpoint(endpoint: str) -> str:
    return endpoint.split("?", 1)[0]


def _response_payload(response: ModelResponse | None, secret_values: tuple[str, ...]) -> dict[str, object] | None:
    if response is None:
        return None
    return {
        "content": _redact_text(response.content, secret_values),
        "reasoning_content": _redact_text(response.reasoning_content, secret_values),
        "finish_reason": response.finish_reason,
        "raw_response": _redact_data(response.raw_response, secret_values),
    }


def _response_payload_with_hashes(
    response: ModelResponse | None,
    secret_values: tuple[str, ...],
) -> dict[str, object] | None:
    payload = _response_payload(response, secret_values)
    if payload is None:
        return None
    payload["hashes"] = {
        "content_sha256": content_sha256(response.content if response else None),
        "reasoning_content_sha256": content_sha256(response.reasoning_content if response else None),
        "raw_response_sha256": content_sha256(response.raw_response if response else None),
    }
    return payload


def _token_usage_payload(token_usage: TokenUsage | None) -> dict[str, int | None] | None:
    return asdict(token_usage) if token_usage else None


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
    return redact_secret_text(value, extra_secrets=secret_values)


def _validate_context_window(
    messages: list[dict[str, str]],
    *,
    max_context_tokens: int | None,
) -> None:
    if max_context_tokens is None:
        return
    estimated = _estimate_messages_tokens(messages)
    if estimated <= max_context_tokens:
        return
    char_count = sum(len(message.get("content", "")) for message in messages)
    raise ProviderContextLimitError(
        "assembled prompt exceeds max_context_tokens: "
        f"estimated_prompt_tokens={estimated}, max_context_tokens={max_context_tokens}, message_chars={char_count}. "
        "Context budget is disabled by default; shorten project context or use a larger-context model before retrying."
    )


def _estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    total = 2
    for message in messages:
        total += 4
        total += _estimate_text_tokens(message.get("content", ""))
    return total


def _estimate_text_tokens(text: str) -> int:
    cjk = 0
    other = 0
    for char in text:
        codepoint = ord(char)
        if (
            0x3400 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0x3040 <= codepoint <= 0x30FF
            or 0xAC00 <= codepoint <= 0xD7AF
        ):
            cjk += 1
        else:
            other += 1
    return cjk + ((other + 3) // 4 if other else 0)


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
        user_parts.append(render_untrusted_workspace_data("model_request_context", model_request.context))
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
        updated[0].get("content", "") + "\n\n"
        f"{marker}: output must be a single valid JSON object for schema {schema_name}. "
        "Do not include Markdown code fences, explanations, comments, or wrapper text. "
        "Use double-quoted JSON keys and values where JSON requires strings." + skeleton_text
    )
    return updated


def _stream_content_from_line(line: str) -> str | None:
    raw = _stream_raw_from_line(line)
    if raw is None:
        return None
    content, _reasoning = _stream_content_from_raw(raw)
    return content


def _stream_raw_from_line(line: str) -> Mapping[str, object] | None:
    stripped = line.strip()
    if not stripped or not stripped.startswith("data:"):
        return None
    data = stripped.removeprefix("data:").strip()
    if data == "[DONE]":
        return None
    try:
        raw = json.loads(data)
    except json.JSONDecodeError:
        return None
    return raw if isinstance(raw, Mapping) else None


def _stream_content_from_raw(raw: Mapping[str, object]) -> tuple[str | None, str | None]:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return None, None
    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        return None, None
    delta = first_choice.get("delta")
    if isinstance(delta, Mapping):
        content = delta.get("content")
        reasoning = delta.get("reasoning_content")
        return (
            content if isinstance(content, str) else None,
            reasoning if isinstance(reasoning, str) else None,
        )
    message = first_choice.get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
        reasoning = message.get("reasoning_content")
        return (
            content if isinstance(content, str) else None,
            reasoning if isinstance(reasoning, str) else None,
        )
    return None, None


def _finish_reason_from_raw(raw: Mapping[str, object]) -> str | None:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        return None
    finish_reason = first_choice.get("finish_reason")
    return finish_reason if isinstance(finish_reason, str) else None


def _model_response_from_openai_sse(response_body: str) -> ModelResponse:
    content_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    stream_chunks = 0
    finish_chunk: dict[str, object] | None = None
    usage_chunk: dict[str, object] | None = None
    token_usage: TokenUsage | None = None
    finish_reason: str | None = None
    for line in response_body.splitlines():
        raw = _stream_raw_from_line(line)
        if raw is None:
            continue
        stream_chunks += 1
        usage = raw.get("usage")
        if isinstance(usage, Mapping):
            token_usage = _token_usage_from_raw(usage)
            usage_chunk = dict(raw)
        chunk, reasoning = _stream_content_from_raw(raw)
        if chunk:
            content_chunks.append(chunk)
        if reasoning:
            reasoning_chunks.append(reasoning)
        raw_finish_reason = _finish_reason_from_raw(raw)
        if raw_finish_reason:
            finish_reason = raw_finish_reason
            finish_chunk = dict(raw)
    return ModelResponse(
        content="".join(content_chunks),
        raw_response={
            "stream_chunks": stream_chunks,
            "finish_chunk": finish_chunk,
            "usage_chunk": usage_chunk,
        },
        token_usage=token_usage,
        reasoning_content="".join(reasoning_chunks) or None,
        finish_reason=finish_reason,
    )


def _model_response_from_openai_raw(raw: Mapping[str, object]) -> ModelResponse:
    content = ""
    reasoning_content = None
    finish_reason = None
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, Mapping):
            raw_finish_reason = first_choice.get("finish_reason")
            if isinstance(raw_finish_reason, str):
                finish_reason = raw_finish_reason
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
        finish_reason=finish_reason,
    )


def _token_usage_from_raw(raw: Mapping[str, object]) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=_optional_int(raw.get("prompt_tokens")),
        completion_tokens=_optional_int(raw.get("completion_tokens")),
        total_tokens=_optional_int(raw.get("total_tokens")),
    )


def _token_usage_from_response_body(response_body: str) -> TokenUsage | None:
    usage, _finish_reason = _response_metadata_from_body(response_body, stream=False)
    return usage


def _response_metadata_from_body(response_body: str, *, stream: bool) -> tuple[TokenUsage | None, str | None]:
    if stream:
        response = _model_response_from_openai_sse(response_body)
        return response.token_usage, response.finish_reason
    try:
        raw = json.loads(response_body)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(raw, Mapping):
        return None, None
    response = _model_response_from_openai_raw(raw)
    return response.token_usage, response.finish_reason


def _stream_chunk_count(response: ModelResponse) -> int:
    raw = response.raw_response
    if isinstance(raw, Mapping):
        stream_chunks = raw.get("stream_chunks")
        if isinstance(stream_chunks, int):
            return stream_chunks
        chunks = raw.get("chunks")
        if isinstance(chunks, list):
            return len(chunks)
    return 1 if response.content else 0


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
