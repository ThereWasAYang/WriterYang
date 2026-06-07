from __future__ import annotations

import json
import os

import pytest

from novel.core.embeddings import EmbeddingProviderFactory
from novel.core.schemas import EmbeddingProviderConfig
from novel.core.workspace import InitOptions, init_workspace
from novel.web_api import handle_api_request


pytestmark = pytest.mark.real_api


def test_real_dashscope_text_embedding_v4_when_configured() -> None:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        pytest.skip("DASHSCOPE_API_KEY is not configured")

    provider = EmbeddingProviderFactory(env=os.environ).create(
        EmbeddingProviderConfig(
            provider="dashscope",
            model=os.getenv("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v4"),
            api_key_env="DASHSCOPE_API_KEY",
            base_url_env="DASHSCOPE_EMBEDDING_BASE_URL",
            dimensions=int(os.getenv("DASHSCOPE_EMBEDDING_DIMENSIONS", "2048")),
            timeout_seconds=30,
            max_retries=1,
        )
    )

    response = provider.embed_texts(["雨夜旧车站传来停播多年的广播声。"])

    assert len(response.vectors) == 1
    assert len(response.vectors[0]) > 0


def test_real_dashscope_embedding_uses_max_batch_and_dimensions_when_configured() -> None:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        pytest.skip("DASHSCOPE_API_KEY is not configured")
    model = os.getenv("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v4")
    if model not in {"text-embedding-v3", "text-embedding-v4"}:
        pytest.skip("DASHSCOPE_EMBEDDING_MODEL is not text-embedding-v3/v4")
    dimensions = 2048 if model == "text-embedding-v4" else 1024
    batch_size = 10

    provider = EmbeddingProviderFactory(env=os.environ).create(
        EmbeddingProviderConfig(
            provider="dashscope",
            model=model,
            api_key_env="DASHSCOPE_API_KEY",
            base_url_env="DASHSCOPE_EMBEDDING_BASE_URL",
            dimensions=dimensions,
            batch_size=batch_size,
            timeout_seconds=30,
            max_retries=1,
        )
    )

    response = provider.embed_texts(
        [f"WriterYang real DashScope embedding validation {index + 1}" for index in range(batch_size)]
    )

    assert len(response.vectors) == batch_size
    assert all(len(vector) == dimensions for vector in response.vectors)


def test_real_web_setup_embedding_validates_dashscope_max_batch_before_save(tmp_path) -> None:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    base_url = os.getenv("DASHSCOPE_EMBEDDING_BASE_URL")
    if not api_key or not base_url:
        pytest.skip("DASHSCOPE_API_KEY or DASHSCOPE_EMBEDDING_BASE_URL is not configured")
    model = os.getenv("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v4")
    if model not in {"text-embedding-v3", "text-embedding-v4"}:
        pytest.skip("DASHSCOPE_EMBEDDING_MODEL is not text-embedding-v3/v4")
    dimensions = 2048 if model == "text-embedding-v4" else 1024

    root = tmp_path / "novel"
    init_workspace(InitOptions(title="真实 Embedding 验证", root=root))
    status, payload = handle_api_request(
        "POST",
        "/api/setup/embedding",
        "",
        json.dumps(
            {
                "path": str(root),
                "provider": "dashscope",
                "provider_name": "configured",
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
                "dimensions": dimensions,
                "batch_size": 10,
                "ping": True,
            }
        ),
    )

    assert status == 200
    serialized = json.dumps(payload, ensure_ascii=False)
    assert api_key not in serialized
    data = payload["data"]  # type: ignore[index]
    assert data["provider"] == "dashscope"
    assert data["model"] == model
    assert data["dimensions"] == dimensions
    assert data["batch_size"] == 10
    assert data["embedding_api"]["configured"] is True
    assert data["embedding_api"]["effective_batch_size"] == 10


def test_real_zhipu_embedding_3_when_configured() -> None:
    api_key = os.getenv("ZHIPU_API_KEY")
    if not api_key:
        pytest.skip("ZHIPU_API_KEY is not configured")

    provider = EmbeddingProviderFactory(env=os.environ).create(
        EmbeddingProviderConfig(
            provider="zhipu",
            model=os.getenv("ZHIPU_EMBEDDING_MODEL", "embedding-3"),
            api_key_env="ZHIPU_API_KEY",
            base_url_env="ZHIPU_EMBEDDING_BASE_URL",
            dimensions=int(os.getenv("ZHIPU_EMBEDDING_DIMENSIONS", "2048")),
            timeout_seconds=30,
            max_retries=1,
        )
    )

    response = provider.embed_texts(["雨夜旧车站传来停播多年的广播声。"])

    assert len(response.vectors) == 1
    assert len(response.vectors[0]) > 0
