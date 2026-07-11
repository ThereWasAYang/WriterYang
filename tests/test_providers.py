from __future__ import annotations

import json
from urllib import error

import pytest

from novel.core.providers import (
    LoggingModelProvider,
    MissingProviderEnvError,
    MockProvider,
    ModelRequest,
    ModelResponse,
    OpenAICompatibleProvider,
    ProviderContextLimitError,
    ProviderError,
    ProviderRateLimitError,
    ProviderFactory,
    ProviderHTTPError,
    TokenUsage,
    provider_parameter_capabilities,
)
from novel.core.schemas import AgentConfig


def test_mock_provider_returns_fixed_text_and_records_request() -> None:
    provider = MockProvider(fixed_text="fixed inspiration")
    request = ModelRequest(
        system_prompt="You are a novelist.",
        user_prompt="Write a seed.",
        context="雨夜旧车站",
        json_schema_name="InspirationBrief",
    )

    response = provider.generate(request)

    assert response.content == "fixed inspiration"
    assert response.raw_response == "fixed inspiration"
    assert response.token_usage is None
    assert provider.requests == [request]


def test_mock_provider_returns_fake_model_response() -> None:
    fake_response = ModelResponse(
        content="fake content",
        raw_response={"id": "fake"},
        token_usage=TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5),
    )
    provider = MockProvider(fake_response=fake_response)

    response = provider.generate(ModelRequest(system_prompt="s", user_prompt="u"))

    assert response is fake_response
    assert response.content == "fake content"
    assert response.token_usage
    assert response.token_usage.total_tokens == 5


def test_mock_provider_returns_fake_mapping_response() -> None:
    provider = MockProvider(
        fake_response={
            "content": "mapped content",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
            },
        }
    )

    response = provider.generate(ModelRequest(system_prompt="s", user_prompt="u"))

    assert response.content == "mapped content"
    assert response.token_usage
    assert response.token_usage.prompt_tokens == 10
    assert response.token_usage.completion_tokens == 4
    assert response.token_usage.total_tokens == 14


def test_provider_factory_creates_mock_provider() -> None:
    config = AgentConfig(
        provider="mock",
        model="mock-model",
        api_key_env="MOCK_API_KEY",
    )

    provider = ProviderFactory(env={}).create(config)

    assert isinstance(provider, MockProvider)


def test_openai_compatible_provider_requires_api_key_env() -> None:
    config = AgentConfig(
        provider="openai_compatible",
        model="test-model",
        api_key_env="WRITER_API_KEY",
        base_url_env="WRITER_BASE_URL",
    )

    with pytest.raises(MissingProviderEnvError) as exc_info:
        ProviderFactory(env={"WRITER_BASE_URL": "https://example.test/v1"}).create(config)

    message = str(exc_info.value)
    assert "WRITER_API_KEY" in message
    assert "required environment variable" in message


def test_openai_compatible_provider_requires_base_url_for_compatible_provider() -> None:
    config = AgentConfig(
        provider="openai_compatible",
        model="test-model",
        api_key_env="WRITER_API_KEY",
        base_url_env="WRITER_BASE_URL",
    )

    with pytest.raises(MissingProviderEnvError) as exc_info:
        ProviderFactory(env={"WRITER_API_KEY": "secret-test-key"}).create(config)

    message = str(exc_info.value)
    assert "WRITER_BASE_URL" in message
    assert "secret-test-key" not in message


def test_openai_provider_uses_default_base_url_when_base_url_env_is_missing() -> None:
    config = AgentConfig(
        provider="openai",
        model="test-model",
        api_key_env="OPENAI_API_KEY",
        base_url_env="OPENAI_BASE_URL",
    )

    provider = ProviderFactory(env={"OPENAI_API_KEY": "secret-test-key"}).create(config)

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "test-model"
    assert provider.base_url == "https://api.openai.com/v1"
    assert provider.thinking_type is None


def test_provider_parameter_capabilities_mark_vendor_specific_fields() -> None:
    openai_caps = provider_parameter_capabilities("openai", thinking_type="enabled")
    compatible_caps = provider_parameter_capabilities("openai_compatible", thinking_type="enabled")
    deepseek_disabled_caps = provider_parameter_capabilities("deepseek", thinking_type="disabled")
    zai_caps = provider_parameter_capabilities("zai", thinking_type="enabled")
    mock_caps = provider_parameter_capabilities("mock", thinking_type="enabled")

    assert openai_caps["thinking"].effective is False
    assert compatible_caps["thinking"].effective is False
    assert deepseek_disabled_caps["thinking"].effective is True
    assert deepseek_disabled_caps["reasoning"].effective is False
    assert zai_caps["thinking"].effective is True
    assert zai_caps["reasoning"].effective is False
    assert mock_caps["temperature"].effective is False
    assert openai_caps["temperature"].effective is True
    assert openai_caps["json_response_format"].allowed_values == (
        "auto",
        "json_object",
        "json_schema",
        "json_schema_strict",
    )
    assert deepseek_disabled_caps["json_response_format"].allowed_values == ("auto", "json_object")


def test_deepseek_thinking_enabled_capabilities_use_reasoning_not_temperature() -> None:
    caps = provider_parameter_capabilities("deepseek", thinking_type="enabled")

    assert caps["thinking"].effective is True
    assert caps["reasoning"].effective is True
    assert caps["temperature"].effective is False
    assert "temperature" in (caps["temperature"].reason or "")


def test_openai_compatible_provider_uses_temperature_without_vendor_thinking() -> None:
    config = AgentConfig(
        provider="openai_compatible",
        model="test-model",
        api_key_env="WRITER_API_KEY",
        base_url_env="WRITER_BASE_URL",
        temperature=0.3,
    )

    provider = ProviderFactory(
        env={"WRITER_API_KEY": "secret-test-key", "WRITER_BASE_URL": "https://example.test/v1"}
    ).create(config)

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.temperature == 0.3
    assert provider.thinking_type is None
    assert provider.json_response_format == "json_object"


def test_provider_config_can_force_json_object_for_openai() -> None:
    config = AgentConfig(
        provider="openai",
        model="test-model",
        api_key_env="OPENAI_API_KEY",
        json_response_format="json_object",
    )

    provider = ProviderFactory(env={"OPENAI_API_KEY": "secret-test-key"}).create(config)

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.json_response_format == "json_object"


def test_openai_compatible_provider_fails_fast_when_prompt_exceeds_context() -> None:
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret-test-key",
        base_url="https://example.test/v1",
        max_context_tokens=8,
    )
    request = ModelRequest(system_prompt="系统提示", user_prompt="这是一段很长的中文提示，会超过窗口")

    payload = provider.debug_payload(request, stream=False)

    assert isinstance(payload, dict)
    with pytest.raises(ProviderContextLimitError) as exc_info:
        provider.generate(request)
    message = str(exc_info.value)
    assert "estimated_prompt_tokens" in message
    assert "max_context_tokens=8" in message


def test_deepseek_provider_rejects_strict_json_schema_for_chat() -> None:
    config = AgentConfig(
        provider="deepseek",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        json_response_format="json_schema_strict",
    )

    with pytest.raises(ProviderError, match="json_schema_strict"):
        ProviderFactory(env={"DEEPSEEK_API_KEY": "secret-test-key"}).create(config)


def test_openai_compatible_provider_sends_thinking_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret-test-key",
        base_url="https://example.test/v1",
        temperature=0.2,
        thinking_type="enabled",
    )

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"ok"}}],"usage":{"total_tokens":1}}'

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        captured["timeout"] = timeout
        captured["body"] = json.loads(http_request.data.decode("utf-8"))  # type: ignore[attr-defined]
        captured["auth"] = http_request.headers.get("Authorization")  # type: ignore[attr-defined]
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    response = provider.generate(ModelRequest(system_prompt="s", user_prompt="u"))

    assert response.content == "ok"
    assert captured["body"] == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ],
        "temperature": 0.2,
        "thinking": {"type": "enabled"},
    }
    assert captured["auth"] == "Bearer secret-test-key"


def test_deepseek_provider_sends_vendor_payload_without_temperature_when_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    config = AgentConfig(
        provider="deepseek",
        model="deepseek-v4-pro",
        api_key_env="DEEPSEEK_API_KEY",
        reasoning="high",
        thinking={"type": "enabled"},
        temperature=0.2,
    )
    provider = ProviderFactory(env={"DEEPSEEK_API_KEY": "secret-test-key"}).create(config)

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return (
                b'{"choices":[{"message":{"content":"ok","reasoning_content":"reason"}}],'
                b'"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5}}'
            )

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        captured["url"] = http_request.full_url  # type: ignore[attr-defined]
        captured["body"] = json.loads(http_request.data.decode("utf-8"))  # type: ignore[attr-defined]
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    response = provider.generate(ModelRequest(system_prompt="s", user_prompt="u"))

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://api.deepseek.com"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["body"] == {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ],
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }
    assert response.content == "ok"
    assert response.reasoning_content == "reason"
    assert response.token_usage
    assert response.token_usage.total_tokens == 5


def test_zai_provider_sends_vendor_thinking_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    config = AgentConfig(
        provider="zai",
        model="glm-5.1",
        api_key_env="ZAI_API_KEY",
        thinking={"type": "disabled"},
        temperature=0.8,
    )
    provider = ProviderFactory(env={"ZAI_API_KEY": "secret-test-key"}).create(config)

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"ok","reasoning_content":"reason"}}]}'

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        captured["url"] = http_request.full_url  # type: ignore[attr-defined]
        captured["body"] = json.loads(http_request.data.decode("utf-8"))  # type: ignore[attr-defined]
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    response = provider.generate(ModelRequest(system_prompt="s", user_prompt="u"))

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert captured["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert captured["body"] == {
        "model": "glm-5.1",
        "messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ],
        "temperature": 0.8,
        "thinking": {"type": "disabled"},
    }
    assert response.content == "ok"
    assert response.reasoning_content == "reason"


def test_provider_sends_max_tokens_from_agent_config(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    config = AgentConfig(
        provider="zai",
        model="glm-5.1",
        api_key_env="ZAI_API_KEY",
        max_tokens=1234,
    )
    provider = ProviderFactory(env={"ZAI_API_KEY": "secret-test-key"}).create(config)

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return (
                b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10}}'
            )

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        captured["body"] = json.loads(http_request.data.decode("utf-8"))  # type: ignore[attr-defined]
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    provider.generate(ModelRequest(system_prompt="s", user_prompt="u"))

    assert captured["body"]["max_tokens"] == 1234  # type: ignore[index]


def test_provider_retries_retryable_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret-test-key",
        base_url="https://example.test/v1",
        max_retries=1,
        retry_backoff_seconds=0,
    )

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return (
                b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10}}'
            )

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error.HTTPError("https://example.test", 429, "rate limited", {}, None)
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    response = provider.generate(ModelRequest(system_prompt="s", user_prompt="u"))

    assert response.content == "ok"
    assert calls == 2


def test_provider_classifies_non_retryable_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret-test-key",
        base_url="https://example.test/v1",
        max_retries=2,
        retry_backoff_seconds=0,
    )

    def fake_urlopen(http_request: object, timeout: float) -> object:
        raise error.HTTPError("https://example.test", 400, "bad request", {}, None)

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    with pytest.raises(ProviderHTTPError) as exc_info:
        provider.generate(ModelRequest(system_prompt="s", user_prompt="u"))

    assert "HTTP 400" in str(exc_info.value)


def test_provider_classifies_rate_limit_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret-test-key",
        base_url="https://example.test/v1",
        max_retries=0,
        retry_backoff_seconds=0,
    )

    def fake_urlopen(http_request: object, timeout: float) -> object:
        raise error.HTTPError("https://example.test", 429, "rate limited", {}, None)

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    with pytest.raises(ProviderRateLimitError):
        provider.generate(ModelRequest(system_prompt="s", user_prompt="u"))


def test_provider_writes_safe_call_log(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    secret = "secret-test-key"
    log_path = tmp_path / "provider_calls.jsonl"
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key=secret,
        base_url="https://example.test/v1",
        log_path=log_path,
    )

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return (
                b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10}}'
            )

    monkeypatch.setattr("novel.core.providers.request.urlopen", lambda *args, **kwargs: FakeResponse())

    provider.generate(ModelRequest(system_prompt="s", user_prompt="u", json_schema_name="TestSchema"))

    text = log_path.read_text(encoding="utf-8")
    entry = json.loads(text)
    assert entry["status"] == "success"
    assert entry["json_schema_name"] == "TestSchema"
    assert entry["prompt_tokens"] == 7
    assert entry["completion_tokens"] == 3
    assert entry["total_tokens"] == 10
    assert entry["finish_reason"] == "stop"
    assert secret not in text
    assert "Authorization" not in text
    usage = json.loads((tmp_path / "provider_usage.json").read_text(encoding="utf-8"))
    assert usage["total"]["call_count"] == 1
    assert usage["total"]["total_tokens"] == 10
    assert usage["by_provider"]["openai"]["total_tokens"] == 10


def test_logging_provider_writes_mock_model_io(tmp_path) -> None:
    provider = LoggingModelProvider(
        provider=MockProvider(
            fake_response={
                "content": "mock output",
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            }
        ),
        agent_name="writer",
        provider_name="mock",
        model="mock-model",
        root=tmp_path,
    )

    response = provider.generate(
        ModelRequest(
            system_prompt="system text",
            user_prompt="user text",
            context="context text",
            json_schema_name="DraftChapter",
        )
    )

    assert response.content == "mock output"
    logs = list((tmp_path / "runs" / "model_io").glob("provider_*.json"))
    assert len(logs) == 1
    data = json.loads(logs[0].read_text(encoding="utf-8"))
    assert data["agent_name"] == "writer"
    assert data["provider"] == "mock"
    assert data["model"] == "mock-model"
    assert data["status"] == "success"
    assert data["request"]["system_prompt"].startswith("[omitted")
    assert data["request"]["user_prompt"].startswith("[omitted")
    assert data["request"]["context"].startswith("[omitted")
    assert data["request"]["payload"].startswith("[omitted")
    assert data["request"]["hashes"]["system_prompt_sha256"]
    assert data["response"]["content"].startswith("[omitted")
    assert data["response"]["hashes"]["content_sha256"]
    assert data["token_usage"]["total_tokens"] == 5
    index = (tmp_path / "runs" / "model_io" / "index.jsonl").read_text(encoding="utf-8")
    assert "runs/model_io/" in index


def test_logging_provider_links_openai_call_log_to_model_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("WRITERYANG_MODEL_IO_MODE", "full")
    secret = "secret-test-key"
    inner = OpenAICompatibleProvider(
        model="test-model",
        api_key=secret,
        base_url="https://example.test/v1",
        log_path=tmp_path / "runs" / "provider_calls.jsonl",
    )
    provider = LoggingModelProvider(
        provider=inner,
        agent_name="audit",
        provider_name="openai_compatible",
        model="test-model",
        root=tmp_path,
    )

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return (
                b'{"choices":[{"message":{"content":"ok","reasoning_content":"think"},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10}}'
            )

    monkeypatch.setattr("novel.core.providers.request.urlopen", lambda *args, **kwargs: FakeResponse())

    response = provider.generate(
        ModelRequest(
            system_prompt="s",
            user_prompt="u",
            json_schema_name="AuditReport",
            prompt_version="2026-06-05",
        )
    )

    assert response.content == "ok"
    provider_call = json.loads((tmp_path / "runs" / "provider_calls.jsonl").read_text(encoding="utf-8"))
    assert provider_call["model_io_path"].startswith("runs/model_io/provider_")
    assert provider_call["agent_name"] == "audit"
    assert provider_call["finish_reason"] == "stop"
    model_io_path = tmp_path / provider_call["model_io_path"]
    data = json.loads(model_io_path.read_text(encoding="utf-8"))
    assert data["request_id"] == provider_call["request_id"]
    assert data["agent_name"] == "audit"
    assert data["request"]["prompt_version"] == "2026-06-05"
    assert data["request"]["payload"]["messages"][0]["content"] == "s"
    assert data["response"]["content"] == "ok"
    assert data["response"]["reasoning_content"] == "think"
    assert data["finish_reason"] == "stop"
    assert data["response"]["finish_reason"] == "stop"
    assert data["response"]["raw_response"]["choices"][0]["message"]["content"] == "ok"
    text = model_io_path.read_text(encoding="utf-8") + (tmp_path / "runs" / "provider_calls.jsonl").read_text(encoding="utf-8")
    assert secret not in text
    assert "Authorization" not in text


def test_logging_provider_writes_failed_model_io_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    secret = "secret-test-key"
    inner = OpenAICompatibleProvider(
        model="test-model",
        api_key=secret,
        base_url="https://example.test/v1",
        log_path=tmp_path / "runs" / "provider_calls.jsonl",
    )
    provider = LoggingModelProvider(
        provider=inner,
        agent_name="writer",
        provider_name="openai_compatible",
        model="test-model",
        root=tmp_path,
    )

    def fail_urlopen(*args: object, **kwargs: object) -> object:
        raise RuntimeError(f"transport failed with hidden credential {secret}")

    monkeypatch.setattr("novel.core.providers.request.urlopen", fail_urlopen)

    with pytest.raises(ProviderError):
        provider.generate(ModelRequest(system_prompt="s", user_prompt="u"))

    logs = list((tmp_path / "runs" / "model_io").glob("provider_*.json"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["status"] == "failed"
    assert data["error"]["type"] == "ProviderError"
    assert secret not in text
    assert "Authorization" not in text


def test_logging_provider_records_stream_output(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("WRITERYANG_MODEL_IO_MODE", "full")
    provider = LoggingModelProvider(
        provider=MockProvider(stream_chunks=["hello", " world"]),
        agent_name="writer",
        provider_name="mock",
        model="mock-model",
        root=tmp_path,
    )

    chunks = list(provider.stream(ModelRequest(system_prompt="s", user_prompt="u")))

    assert "".join(chunks) == "hello world"
    logs = list((tmp_path / "runs" / "model_io").glob("provider_*.json"))
    data = json.loads(logs[0].read_text(encoding="utf-8"))
    assert data["stream"] is True
    assert data["stream_chunks"] == 2
    assert data["response"]["content"] == "hello world"


def test_logging_provider_prunes_model_io_by_retention_limit(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("WRITERYANG_MODEL_IO_MAX_FILES", "2")
    monkeypatch.setenv("WRITERYANG_MODEL_IO_MAX_BYTES", "0")
    provider = LoggingModelProvider(
        provider=MockProvider(fixed_text="mock output"),
        agent_name="writer",
        provider_name="mock",
        model="mock-model",
        root=tmp_path,
    )

    for request_id in ("provider_old", "provider_mid", "provider_new"):
        provider.generate(ModelRequest(system_prompt="s", user_prompt="u", request_id=request_id))

    model_io_dir = tmp_path / "runs" / "model_io"
    logs = sorted(path.name for path in model_io_dir.glob("provider_*.json"))
    index_lines = (model_io_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()

    assert logs == ["provider_mid.json", "provider_new.json"]
    assert len(index_lines) == 2
    assert "provider_old" not in "\n".join(index_lines)
    assert "provider_mid" in index_lines[0]
    assert "provider_new" in index_lines[1]


def test_logging_provider_metadata_mode_omits_full_prompt_and_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("WRITERYANG_MODEL_IO_MODE", "metadata")
    provider = LoggingModelProvider(
        provider=MockProvider(fake_response={"content": "完整正文", "reasoning_content": "隐藏推理"}),
        agent_name="writer",
        provider_name="mock",
        model="mock-model",
        root=tmp_path,
    )

    provider.generate(ModelRequest(system_prompt="系统提示", user_prompt="用户提示", context="上下文"))

    log_path = next((tmp_path / "runs" / "model_io").glob("provider_*.json"))
    text = log_path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert "系统提示" not in text
    assert "用户提示" not in text
    assert "完整正文" not in text
    assert data["request"]["system_prompt"].startswith("[omitted")
    assert data["response"]["content"].startswith("[omitted")
    assert data["request"]["hashes"]["system_prompt_sha256"]
    assert data["response"]["hashes"]["content_sha256"]


def test_provider_stream_parses_sse_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret-test-key",
        base_url="https://example.test/v1",
    )

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return (
                b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":8,"completion_tokens":2,"total_tokens":10}}\n\n'
                b"data: [DONE]\n\n"
            )

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        captured["body"] = json.loads(http_request.data.decode("utf-8"))  # type: ignore[attr-defined]
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    chunks = list(provider.stream(ModelRequest(system_prompt="s", user_prompt="u")))

    assert "".join(chunks) == "hello world"
    assert captured["body"]["stream"] is True  # type: ignore[index]
    assert captured["body"]["stream_options"] == {"include_usage": True}  # type: ignore[index]


def test_logging_provider_records_stream_usage_finish_reason_and_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("WRITERYANG_MODEL_IO_MODE", "full")
    inner = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret-test-key",
        base_url="https://example.test/v1",
        log_path=tmp_path / "runs" / "provider_calls.jsonl",
    )
    provider = LoggingModelProvider(
        provider=inner,
        agent_name="writer",
        provider_name="openai",
        model="test-model",
        root=tmp_path,
    )

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return (
                b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":8,"completion_tokens":2,"total_tokens":10}}\n\n'
                b"data: [DONE]\n\n"
            )

    monkeypatch.setattr("novel.core.providers.request.urlopen", lambda *args, **kwargs: FakeResponse())

    response = provider.stream_response(ModelRequest(system_prompt="s", user_prompt="u"))

    assert response.content == "hello world"
    assert response.token_usage
    assert response.token_usage.total_tokens == 10
    assert response.finish_reason == "stop"
    assert isinstance(response.raw_response, dict)
    assert response.raw_response["stream_chunks"] == 3
    assert "chunks" not in response.raw_response
    assert response.raw_response["finish_chunk"]["choices"][0]["finish_reason"] == "stop"  # type: ignore[index]
    assert response.raw_response["usage_chunk"]["usage"]["total_tokens"] == 10  # type: ignore[index]
    provider_call = json.loads((tmp_path / "runs" / "provider_calls.jsonl").read_text(encoding="utf-8"))
    assert provider_call["agent_name"] == "writer"
    assert provider_call["stream"] is True
    assert provider_call["finish_reason"] == "stop"
    assert provider_call["prompt_tokens"] == 8
    model_io = json.loads((tmp_path / provider_call["model_io_path"]).read_text(encoding="utf-8"))
    assert model_io["token_usage"]["total_tokens"] == 10
    assert model_io["finish_reason"] == "stop"
    assert "chunks" not in model_io["response"]["raw_response"]
    assert model_io["response"]["raw_response"]["stream_chunks"] == 3
    assert model_io["response"]["raw_response"]["usage_chunk"]["usage"]["total_tokens"] == 10
    usage = json.loads((tmp_path / "runs" / "provider_usage.json").read_text(encoding="utf-8"))
    assert usage["by_task"]["writer"]["total_tokens"] == 10


def test_openai_compatible_provider_uses_json_object_for_structured_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret-test-key",
        base_url="https://example.test/v1",
        json_response_format="json_object",
    )

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        captured["body"] = json.loads(http_request.data.decode("utf-8"))  # type: ignore[attr-defined]
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    provider.generate(ModelRequest(system_prompt="s", user_prompt="u", json_schema_name="ChapterPlan"))

    assert captured["body"]["response_format"] == {"type": "json_object"}  # type: ignore[index]
    messages = captured["body"]["messages"]  # type: ignore[index]
    assert "WriterYang JSON mode guard" in messages[0]["content"]  # type: ignore[index]
    assert "chapter_number" in messages[0]["content"]  # type: ignore[index]


def test_deepseek_provider_uses_json_object_guard_for_structured_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    config = AgentConfig(
        provider="deepseek",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
    )
    provider = ProviderFactory(env={"DEEPSEEK_API_KEY": "secret-test-key"}).create(config)

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        captured["body"] = json.loads(http_request.data.decode("utf-8"))  # type: ignore[attr-defined]
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    provider.generate(ModelRequest(system_prompt="只输出对象。", user_prompt="生成计划。", json_schema_name="ChapterPlan"))

    assert captured["body"]["response_format"] == {"type": "json_object"}  # type: ignore[index]
    messages = captured["body"]["messages"]  # type: ignore[index]
    assert "WriterYang JSON mode guard" in messages[0]["content"]  # type: ignore[index]
    assert "Expected JSON structure skeleton" in messages[0]["content"]  # type: ignore[index]
    assert "chapter_number" in messages[0]["content"]  # type: ignore[index]


def test_openai_compatible_provider_sends_real_json_schema_for_known_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret-test-key",
        base_url="https://example.test/v1",
    )

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        captured["body"] = json.loads(http_request.data.decode("utf-8"))  # type: ignore[attr-defined]
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    provider.generate(ModelRequest(system_prompt="s", user_prompt="u", json_schema_name="ChapterPlan"))

    response_format = captured["body"]["response_format"]  # type: ignore[index]
    assert response_format["type"] == "json_schema"  # type: ignore[index]
    json_schema = response_format["json_schema"]  # type: ignore[index]
    assert json_schema["name"] == "ChapterPlan"  # type: ignore[index]
    assert json_schema["schema"]["title"] == "ChapterPlan"  # type: ignore[index]
    assert json_schema["schema"]["properties"]  # type: ignore[index]
    assert "strict" not in json_schema  # type: ignore[operator]


def test_openai_provider_can_send_strict_json_schema_for_known_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret-test-key",
        base_url="https://example.test/v1",
        api_provider="openai",
        json_response_format="json_schema_strict",
    )

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        captured["body"] = json.loads(http_request.data.decode("utf-8"))  # type: ignore[attr-defined]
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    provider.generate(ModelRequest(system_prompt="s", user_prompt="u", json_schema_name="ChapterPlan"))

    json_schema = captured["body"]["response_format"]["json_schema"]  # type: ignore[index]
    schema = json_schema["schema"]  # type: ignore[index]
    assert json_schema["strict"] is True  # type: ignore[index]
    assert schema["additionalProperties"] is False  # type: ignore[index]
    assert set(schema["required"]) == set(schema["properties"])  # type: ignore[index]


def test_openai_compatible_provider_falls_back_to_json_object_for_unknown_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret-test-key",
        base_url="https://example.test/v1",
    )

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        captured["body"] = json.loads(http_request.data.decode("utf-8"))  # type: ignore[attr-defined]
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    provider.generate(ModelRequest(system_prompt="s", user_prompt="u", json_schema_name="TestSchema"))

    assert captured["body"]["response_format"] == {"type": "json_object"}  # type: ignore[index]
    messages = captured["body"]["messages"]  # type: ignore[index]
    assert "JSON" in messages[0]["content"]  # type: ignore[index]
    assert "TestSchema" in messages[0]["content"]  # type: ignore[index]


def test_json_object_provider_adds_json_prompt_hint_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret-test-key",
        base_url="https://example.test/v1",
        json_response_format="json_object",
    )

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        captured["body"] = json.loads(http_request.data.decode("utf-8"))  # type: ignore[attr-defined]
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    provider.generate(ModelRequest(system_prompt="只输出对象。", user_prompt="生成结果。", json_schema_name="AuditReport"))

    messages = captured["body"]["messages"]  # type: ignore[index]
    assert "JSON" in messages[0]["content"]  # type: ignore[index]


def test_json_object_provider_does_not_duplicate_existing_json_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret-test-key",
        base_url="https://example.test/v1",
        json_response_format="json_object",
    )

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        captured["body"] = json.loads(http_request.data.decode("utf-8"))  # type: ignore[attr-defined]
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    provider.generate(
        ModelRequest(
            system_prompt="只输出 JSON。",
            user_prompt="生成结果。",
            json_schema_name="AuditReport",
        )
    )

    messages = captured["body"]["messages"]  # type: ignore[index]
    assert messages[0]["content"].count("WriterYang JSON mode guard") == 1  # type: ignore[index]
    assert "只输出 JSON。" in messages[0]["content"]  # type: ignore[index]


def test_provider_errors_do_not_leak_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "secret-test-key"
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key=secret,
        base_url="https://example.test/v1",
    )

    def fail_urlopen(*args: object, **kwargs: object) -> object:
        raise RuntimeError(f"transport failed with hidden credential {secret}")

    monkeypatch.setattr("novel.core.providers.request.urlopen", fail_urlopen)

    with pytest.raises(ProviderError) as exc_info:
        provider.generate(ModelRequest(system_prompt="s", user_prompt="u"))

    message = str(exc_info.value)
    assert "RuntimeError" in message
    assert secret not in message
