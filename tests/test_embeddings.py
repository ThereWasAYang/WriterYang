from __future__ import annotations

import io
import json
from urllib import error

import pytest

from novel.core.embeddings import (
    EmbeddingHTTPError,
    EmbeddingProviderFactory,
    LocalHashEmbeddingProvider,
    MissingEmbeddingEnvError,
    OpenAIEmbeddingProvider,
)
from novel.core.schemas import EmbeddingProviderConfig


def test_local_hash_embedding_provider_is_deterministic() -> None:
    provider = LocalHashEmbeddingProvider(dimensions=8)

    first = provider.embed_texts(["林澈 调查 旧车站"])
    second = provider.embed_texts(["林澈 调查 旧车站"])

    assert first.vectors == second.vectors
    assert len(first.vectors[0]) == 8
    assert first.model == "local-hash-v1"


def test_dashscope_embedding_provider_uses_openai_compatible_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["authorization"] = req.headers["Authorization"]
        return _FakeResponse({"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    monkeypatch.setattr("novel.core.embeddings.request.urlopen", fake_urlopen)
    config = EmbeddingProviderConfig(
        provider="dashscope",
        model="text-embedding-v4",
        api_key_env="DASHSCOPE_API_KEY",
        dimensions=1024,
        timeout_seconds=12,
    )
    provider = EmbeddingProviderFactory(env={"DASHSCOPE_API_KEY": "secret-key"}).create(config)

    response = provider.embed_texts(["雨夜旧车站"])

    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert captured["url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    assert captured["timeout"] == 12
    assert captured["payload"] == {
        "model": "text-embedding-v4",
        "input": ["雨夜旧车站"],
        "dimensions": 1024,
        "encoding_format": "float",
    }
    assert captured["authorization"] == "Bearer secret-key"
    assert response.vectors == [[0.1, 0.2]]


def test_zhipu_embedding_provider_uses_zhipu_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"data": [{"index": 0, "embedding": [1, 0, -1]}]})

    monkeypatch.setattr("novel.core.embeddings.request.urlopen", fake_urlopen)
    config = EmbeddingProviderConfig(
        provider="zhipu",
        model="embedding-3",
        api_key_env="ZHIPU_API_KEY",
        dimensions=2048,
    )
    provider = EmbeddingProviderFactory(env={"ZHIPU_API_KEY": "secret-key"}).create(config)

    response = provider.embed_texts(["广播响起"])

    assert captured["url"] == "https://open.bigmodel.cn/api/paas/v4/embeddings"
    assert captured["payload"] == {
        "model": "embedding-3",
        "input": ["广播响起"],
        "dimensions": 2048,
    }
    assert response.vectors == [[1.0, 0.0, -1.0]]


def test_dashscope_text_embedding_v4_caps_runtime_batch_to_documented_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[dict[str, object]] = []

    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        payload = json.loads(req.data.decode("utf-8"))
        payloads.append(payload)
        inputs = payload["input"]
        assert isinstance(inputs, list)
        return _FakeResponse(
            {
                "data": [
                    {"index": index, "embedding": [float(index), 0.0]}
                    for index, _ in enumerate(inputs)
                ]
            }
        )

    monkeypatch.setattr("novel.core.embeddings.request.urlopen", fake_urlopen)
    config = EmbeddingProviderConfig(
        provider="dashscope",
        model="text-embedding-v4",
        api_key_env="DASHSCOPE_API_KEY",
        dimensions=2048,
        batch_size=25,
    )
    provider = EmbeddingProviderFactory(env={"DASHSCOPE_API_KEY": "secret-key"}).create(config)

    response = provider.embed_texts([f"文本 {index}" for index in range(25)])

    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.batch_size == 10
    assert [len(payload["input"]) for payload in payloads] == [10, 10, 5]
    assert all(payload["dimensions"] == 2048 for payload in payloads)
    assert all(payload["encoding_format"] == "float" for payload in payloads)
    assert len(response.vectors) == 25


def test_openai_compatible_dashscope_url_uses_dashscope_payload_and_batch_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[dict[str, object]] = []

    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        payload = json.loads(req.data.decode("utf-8"))
        payloads.append(payload)
        inputs = payload["input"]
        assert isinstance(inputs, list)
        return _FakeResponse(
            {
                "data": [
                    {"index": index, "embedding": [float(index), 1.0]}
                    for index, _ in enumerate(inputs)
                ]
            }
        )

    monkeypatch.setattr("novel.core.embeddings.request.urlopen", fake_urlopen)
    config = EmbeddingProviderConfig(
        provider="openai_compatible",
        model="text-embedding-v4",
        api_key_env="WRITERYANG_EMBEDDING_API_KEY",
        base_url_env="WRITERYANG_EMBEDDING_BASE_URL",
        batch_size=16,
    )
    provider = EmbeddingProviderFactory(
        env={
            "WRITERYANG_EMBEDDING_API_KEY": "secret-key",
            "WRITERYANG_EMBEDDING_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
    ).create(config)

    response = provider.embed_texts([f"文本 {index}" for index in range(11)])

    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.provider_name == "openai_compatible"
    assert provider.batch_size == 10
    assert provider.dimensions == 2048
    assert [len(payload["input"]) for payload in payloads] == [10, 1]
    assert all(payload["encoding_format"] == "float" for payload in payloads)
    assert len(response.vectors) == 11


def test_embedding_provider_missing_api_key_env_has_clear_error_without_secret() -> None:
    config = EmbeddingProviderConfig(
        provider="dashscope",
        model="text-embedding-v4",
        api_key_env="DASHSCOPE_API_KEY",
    )

    with pytest.raises(MissingEmbeddingEnvError) as exc_info:
        EmbeddingProviderFactory(env={"OTHER_KEY": "real-secret"}).create(config)

    message = str(exc_info.value)
    assert "DASHSCOPE_API_KEY" in message
    assert "real-secret" not in message


def test_embedding_provider_http_error_does_not_leak_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        body = io.BytesIO(
            json.dumps(
                {"error": {"message": "Invalid request for secret-key", "code": "invalid_request_error"}}
            ).encode("utf-8")
        )
        raise error.HTTPError(req.full_url, 401, "Unauthorized secret-key", {}, body)

    monkeypatch.setattr("novel.core.embeddings.request.urlopen", fake_urlopen)
    provider = OpenAIEmbeddingProvider(
        provider_name="dashscope",
        model="text-embedding-v4",
        api_key="secret-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    with pytest.raises(EmbeddingHTTPError) as exc_info:
        provider.embed_texts(["test"])

    message = str(exc_info.value)
    assert "HTTP 401" in message
    assert "invalid_request_error" in message
    assert "secret-key" not in message


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")
