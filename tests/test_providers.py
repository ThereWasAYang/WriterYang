from __future__ import annotations

import json

import pytest

from novel.core.providers import (
    MissingProviderEnvError,
    MockProvider,
    ModelRequest,
    ModelResponse,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderFactory,
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
    assert provider.base_url == "https://api.z.ai/api/paas/v4"
    assert captured["url"] == "https://api.z.ai/api/paas/v4/chat/completions"
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
