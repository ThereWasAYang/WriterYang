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
    ProviderError,
    ProviderRateLimitError,
    ProviderFactory,
    ProviderHTTPError,
    TokenUsage,
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

    response = provider.chat(ModelRequest(system_prompt="s", user_prompt="u"))

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
                b'{"choices":[{"message":{"content":"ok"}}],'
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
                b'{"choices":[{"message":{"content":"ok"}}],'
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
                b'{"choices":[{"message":{"content":"ok"}}],'
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
    assert data["request"]["system_prompt"] == "system text"
    assert data["request"]["user_prompt"] == "user text"
    assert data["request"]["context"] == "context text"
    assert data["request"]["payload"]["provider"] == "mock"
    assert data["response"]["content"] == "mock output"
    assert data["token_usage"]["total_tokens"] == 5
    index = (tmp_path / "runs" / "model_io" / "index.jsonl").read_text(encoding="utf-8")
    assert "runs/model_io/" in index


def test_logging_provider_links_openai_call_log_to_model_io(
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
                b'{"choices":[{"message":{"content":"ok","reasoning_content":"think"}}],'
                b'"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10}}'
            )

    monkeypatch.setattr("novel.core.providers.request.urlopen", lambda *args, **kwargs: FakeResponse())

    response = provider.generate(ModelRequest(system_prompt="s", user_prompt="u", json_schema_name="AuditReport"))

    assert response.content == "ok"
    provider_call = json.loads((tmp_path / "runs" / "provider_calls.jsonl").read_text(encoding="utf-8"))
    assert provider_call["model_io_path"].startswith("runs/model_io/provider_")
    model_io_path = tmp_path / provider_call["model_io_path"]
    data = json.loads(model_io_path.read_text(encoding="utf-8"))
    assert data["request_id"] == provider_call["request_id"]
    assert data["agent_name"] == "audit"
    assert data["request"]["payload"]["messages"][0]["content"] == "s"
    assert data["response"]["content"] == "ok"
    assert data["response"]["reasoning_content"] == "think"
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


def test_logging_provider_records_stream_output(tmp_path) -> None:
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
                b"data: [DONE]\n\n"
            )

    def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
        captured["body"] = json.loads(http_request.data.decode("utf-8"))  # type: ignore[attr-defined]
        return FakeResponse()

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    chunks = list(provider.stream(ModelRequest(system_prompt="s", user_prompt="u")))

    assert "".join(chunks) == "hello world"
    assert captured["body"]["stream"] is True  # type: ignore[index]


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
