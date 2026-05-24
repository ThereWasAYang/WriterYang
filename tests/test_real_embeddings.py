from __future__ import annotations

import os

import pytest

from novel.core.embeddings import EmbeddingProviderFactory
from novel.core.schemas import EmbeddingProviderConfig


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
            dimensions=int(os.getenv("DASHSCOPE_EMBEDDING_DIMENSIONS", "1024")),
            timeout_seconds=30,
            max_retries=1,
        )
    )

    response = provider.embed_texts(["雨夜旧车站传来停播多年的广播声。"])

    assert len(response.vectors) == 1
    assert len(response.vectors[0]) > 0


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
