from __future__ import annotations

import json
from collections.abc import Mapping

from novel.core.providers import MockProvider, ModelResponse, TokenUsage


def mock_text_response(text: str) -> MockProvider:
    return MockProvider(fake_response=text)


def mock_json_response(payload: Mapping[str, object]) -> MockProvider:
    return MockProvider(fake_response=json.dumps(payload, ensure_ascii=False))


def mock_sequence(*responses: str | Mapping[str, object] | ModelResponse) -> MockProvider:
    return MockProvider(fake_response=list(responses))


def mock_stream(*chunks: str) -> MockProvider:
    return MockProvider(stream_chunks=list(chunks))


def mock_usage_response(
    content: str,
    *,
    prompt_tokens: int = 1,
    completion_tokens: int = 1,
) -> ModelResponse:
    return ModelResponse(
        content=content,
        raw_response={"content": content},
        token_usage=TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )
