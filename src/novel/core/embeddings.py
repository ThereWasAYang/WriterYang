from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import time
from typing import Mapping
from urllib import error, request

from novel.core.env import load_project_env
from novel.core.io import load_yaml_model
from novel.core.schemas import EmbeddingProviderConfig, EmbeddingsConfig


class EmbeddingError(RuntimeError):
    """Base error for embedding provider configuration or requests."""


class MissingEmbeddingEnvError(EmbeddingError):
    """Raised when an embedding provider requires a missing environment variable."""


class EmbeddingHTTPError(EmbeddingError):
    """Raised when an embedding provider returns a non-success HTTP response."""


class EmbeddingTimeoutError(EmbeddingError):
    """Raised when an embedding request times out."""


class EmbeddingNetworkError(EmbeddingError):
    """Raised when an embedding request fails before a provider response is received."""


class EmbeddingResponseError(EmbeddingError):
    """Raised when an embedding provider returns invalid response data."""


@dataclass(frozen=True)
class EmbeddingResponse:
    vectors: list[list[float]]
    model: str
    raw_response: object | None = None


@dataclass(frozen=True)
class EmbeddingProviderCapability:
    canonical_provider: str
    max_batch_size: int | None = None
    max_dimensions: int | None = None
    default_batch_size: int | None = None
    default_dimensions: int | None = None
    encoding_format: str | None = None


DEFAULT_EMBEDDING_BATCH_SIZE = 16


class EmbeddingProvider(ABC):
    provider_name: str
    model: str

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        """Embed a list of text inputs."""


@dataclass(frozen=True)
class LocalHashEmbeddingProvider(EmbeddingProvider):
    dimensions: int = 32
    provider_name: str = "local_hash"
    model: str = "local-hash-v1"

    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        return EmbeddingResponse(
            vectors=[local_embedding_vector(text, dimensions=self.dimensions) for text in texts],
            model=self.model,
            raw_response=None,
        )


@dataclass(frozen=True)
class OpenAIEmbeddingProvider(EmbeddingProvider):
    provider_name: str
    model: str
    api_key: str = field(repr=False)
    base_url: str
    dimensions: int | None = None
    batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE
    timeout_seconds: float = 30.0
    max_retries: int = 0
    retry_backoff_seconds: float = 0.25
    encoding_format: str | None = None

    @classmethod
    def from_config(
        cls,
        config: EmbeddingProviderConfig,
        *,
        env: Mapping[str, str] | None = None,
    ) -> OpenAIEmbeddingProvider:
        env_map = os.environ if env is None else env
        provider_name = config.provider.lower()
        if provider_name == "local_hash":
            raise EmbeddingError("local_hash does not require OpenAIEmbeddingProvider")
        if not config.api_key_env:
            raise MissingEmbeddingEnvError(f"api_key_env is required for embedding provider {provider_name}")
        api_key = _required_env(env_map, config.api_key_env, "api_key_env")
        base_url = _default_embedding_base_url(provider_name)
        if config.base_url_env:
            configured_base_url = env_map.get(config.base_url_env)
            if configured_base_url:
                base_url = configured_base_url
            elif provider_name == "openai_compatible":
                raise MissingEmbeddingEnvError(
                    f"required environment variable {config.base_url_env} is not set for base_url_env"
                )
        normalized_base_url = _normalize_embedding_base_url(base_url)
        dimensions, batch_size, capability = resolve_embedding_parameters(
            provider_name,
            config.model,
            base_url=normalized_base_url,
            dimensions=config.dimensions,
            batch_size=config.batch_size,
            clamp_batch_size=True,
        )
        return cls(
            provider_name=provider_name,
            model=config.model,
            api_key=api_key,
            base_url=normalized_base_url,
            dimensions=dimensions,
            batch_size=batch_size,
            timeout_seconds=config.timeout_seconds or 30.0,
            max_retries=config.max_retries or 0,
            encoding_format=capability.encoding_format,
        )

    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        if not texts:
            return EmbeddingResponse(vectors=[], model=self.model, raw_response=None)
        if len(texts) > self.batch_size:
            vectors: list[list[float]] = []
            raw_batches: list[object] = []
            for start in range(0, len(texts), self.batch_size):
                batch = texts[start : start + self.batch_size]
                response = self.embed_texts(batch)
                vectors.extend(response.vectors)
                raw_batches.append(response.raw_response)
            return EmbeddingResponse(vectors=vectors, model=self.model, raw_response=raw_batches)
        payload: dict[str, object] = {"model": self.model, "input": texts}
        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions
        if self.encoding_format is not None:
            payload["encoding_format"] = self.encoding_format
        raw = self._request_json(payload)
        vectors = _vectors_from_openai_raw(raw, expected_count=len(texts))
        return EmbeddingResponse(vectors=vectors, model=self.model, raw_response=dict(raw))

    def _request_json(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        endpoint = f"{self.base_url}/embeddings"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        attempts = self.max_retries + 1
        last_error: EmbeddingError | None = None
        for attempt in range(1, attempts + 1):
            try:
                http_request = request.Request(
                    endpoint,
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    method="POST",
                )
                with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                    response_body = response.read().decode("utf-8")
                raw = json.loads(response_body)
                if not isinstance(raw, Mapping):
                    raise EmbeddingResponseError("embedding provider JSON response must be an object")
                return raw
            except error.HTTPError as exc:
                message = f"{self.provider_name} embedding provider returned HTTP {exc.code}"
                detail = _safe_http_error_detail(exc, secret=self.api_key)
                if detail:
                    message = f"{message}: {detail}"
                last_error = EmbeddingHTTPError(message)
                if not _is_retryable_http_status(exc.code) or attempt == attempts:
                    raise last_error from None
            except socket.timeout:
                last_error = EmbeddingTimeoutError(f"{self.provider_name} embedding provider request timed out")
                if attempt == attempts:
                    raise last_error from None
            except error.URLError:
                last_error = EmbeddingNetworkError(
                    f"{self.provider_name} embedding provider network request failed"
                )
                if attempt == attempts:
                    raise last_error from None
            except json.JSONDecodeError as exc:
                raise EmbeddingResponseError("embedding provider returned invalid JSON") from exc
            except EmbeddingError:
                raise
            except Exception as exc:
                last_error = EmbeddingError(
                    f"{self.provider_name} embedding provider request failed: {exc.__class__.__name__}"
                )
                if attempt == attempts:
                    raise last_error from None
            time.sleep(self.retry_backoff_seconds * attempt)
        assert last_error is not None
        raise last_error


class EmbeddingProviderFactory:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self.env = env

    def create(self, config: EmbeddingProviderConfig) -> EmbeddingProvider:
        provider = config.provider.lower()
        if provider == "local_hash":
            return LocalHashEmbeddingProvider(dimensions=config.dimensions or 32, model=config.model)
        if provider in {"dashscope", "zhipu", "openai", "openai_compatible"}:
            return OpenAIEmbeddingProvider.from_config(config, env=self.env)
        raise EmbeddingError(f"unsupported embedding provider: {config.provider}")


def default_embedding_config_path(root: Path) -> Path:
    return root / "config" / "embeddings.yaml"


def load_embeddings_config(path: Path) -> EmbeddingsConfig:
    return load_yaml_model(path, EmbeddingsConfig)


def create_embedding_provider(
    root: Path,
    *,
    provider_name: str = "config",
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> EmbeddingProvider:
    root = root.resolve()
    if provider_name == "local_hash":
        return LocalHashEmbeddingProvider()
    path = config_path or default_embedding_config_path(root)
    if not path.exists():
        raise EmbeddingError(f"{path} is missing")
    config = load_embeddings_config(path)
    selected_name = config.active_provider if provider_name == "config" else provider_name
    selected = config.providers.get(selected_name)
    if selected is None:
        raise EmbeddingError(f"embedding provider is not configured: {selected_name}")
    env_map = env if env is not None else load_project_env(root)
    return EmbeddingProviderFactory(env=env_map).create(selected)


def local_embedding_vector(text: str, dimensions: int = 32) -> list[float]:
    vector = [0.0 for _ in range(dimensions)]
    for term in _query_terms(text):
        digest = hashlib.sha256(term.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % dimensions
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[index] += sign
    norm = sum(value * value for value in vector) ** 0.5
    if norm:
        vector = [round(value / norm, 6) for value in vector]
    return vector


def embedding_provider_capability(
    provider: str,
    model: str,
    *,
    base_url: str | None = None,
) -> EmbeddingProviderCapability:
    provider_name = provider.lower()
    model_name = model.lower()
    if _is_dashscope_target(provider_name, base_url):
        if model_name == "text-embedding-v4":
            return EmbeddingProviderCapability(
                canonical_provider="dashscope",
                max_batch_size=10,
                max_dimensions=2048,
                default_batch_size=10,
                default_dimensions=2048,
                encoding_format="float",
            )
        if model_name == "text-embedding-v3":
            return EmbeddingProviderCapability(
                canonical_provider="dashscope",
                max_batch_size=10,
                max_dimensions=1024,
                default_batch_size=10,
                default_dimensions=1024,
                encoding_format="float",
            )
        return EmbeddingProviderCapability(canonical_provider="dashscope", encoding_format="float")
    return EmbeddingProviderCapability(canonical_provider=provider_name)


def resolve_embedding_parameters(
    provider: str,
    model: str,
    *,
    base_url: str | None = None,
    dimensions: int | None = None,
    batch_size: int | None = None,
    clamp_batch_size: bool = False,
    default_batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
) -> tuple[int | None, int, EmbeddingProviderCapability]:
    capability = embedding_provider_capability(provider, model, base_url=base_url)
    if dimensions is not None and dimensions <= 0:
        raise EmbeddingError("embedding dimensions must be a positive integer")
    if batch_size is not None and batch_size <= 0:
        raise EmbeddingError("embedding batch_size must be a positive integer")
    resolved_dimensions = dimensions if dimensions is not None else capability.default_dimensions
    if (
        resolved_dimensions is not None
        and capability.max_dimensions is not None
        and resolved_dimensions > capability.max_dimensions
    ):
        raise EmbeddingError(
            f"{capability.canonical_provider} {model} supports dimensions up to {capability.max_dimensions}"
        )
    resolved_batch_size = batch_size if batch_size is not None else capability.default_batch_size
    if resolved_batch_size is None:
        resolved_batch_size = default_batch_size
    if capability.max_batch_size is not None and resolved_batch_size > capability.max_batch_size:
        if clamp_batch_size:
            resolved_batch_size = capability.max_batch_size
        else:
            raise EmbeddingError(
                f"{capability.canonical_provider} {model} supports batch_size up to {capability.max_batch_size}"
            )
    return resolved_dimensions, resolved_batch_size, capability


def _vectors_from_openai_raw(raw: Mapping[str, object], *, expected_count: int) -> list[list[float]]:
    data = raw.get("data")
    if not isinstance(data, list):
        raise EmbeddingResponseError("embedding provider response is missing data list")
    vectors_by_index: dict[int, list[float]] = {}
    fallback_vectors: list[list[float]] = []
    for offset, item in enumerate(data):
        if not isinstance(item, Mapping):
            raise EmbeddingResponseError("embedding response data item must be an object")
        embedding = item.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise EmbeddingResponseError("embedding response data item is missing embedding vector")
        vector = [_coerce_float(value) for value in embedding]
        index = item.get("index")
        if isinstance(index, int):
            vectors_by_index[index] = vector
        else:
            fallback_vectors.append(vector)
            vectors_by_index[offset] = vector
    vectors = [vectors_by_index[index] for index in sorted(vectors_by_index)]
    if len(vectors) != expected_count:
        raise EmbeddingResponseError(
            f"embedding provider returned {len(vectors)} vectors, expected {expected_count}"
        )
    return vectors or fallback_vectors


def _coerce_float(value: object) -> float:
    if isinstance(value, (float, int)):
        return float(value)
    raise EmbeddingResponseError("embedding vector contains a non-numeric value")


def _required_env(env: Mapping[str, str], name: str, config_field: str) -> str:
    value = env.get(name)
    if not value:
        raise MissingEmbeddingEnvError(
            f"required environment variable {name} is not set for {config_field}"
        )
    return value


def _default_embedding_base_url(provider: str) -> str:
    if provider == "dashscope":
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if provider == "zhipu":
        return "https://open.bigmodel.cn/api/paas/v4"
    return "https://api.openai.com/v1"


def _normalize_embedding_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/embeddings"):
        normalized = normalized.removesuffix("/embeddings")
    return normalized


def _is_dashscope_target(provider: str, base_url: str | None) -> bool:
    if provider == "dashscope":
        return True
    normalized = (base_url or "").lower()
    return "dashscope.aliyuncs.com" in normalized


def _is_retryable_http_status(status: int) -> bool:
    return status in {408, 409, 425, 429, 500, 502, 503, 504}


def _safe_http_error_detail(exc: error.HTTPError, *, secret: str) -> str:
    try:
        raw_body = exc.read(8192)
    except Exception:
        return ""
    if not raw_body:
        return ""
    text = raw_body.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    detail = text
    try:
        raw_json = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        if isinstance(raw_json, Mapping):
            detail = _http_error_json_detail(raw_json) or text
    detail = _redact_embedding_error_text(detail, secret=secret)
    if len(detail) > 500:
        detail = detail[:497] + "..."
    return detail


def _http_error_json_detail(raw: Mapping[str, object]) -> str:
    error_item = raw.get("error")
    source = error_item if isinstance(error_item, Mapping) else raw
    message = source.get("message")
    code = source.get("code")
    if isinstance(code, str) and isinstance(message, str):
        return f"{code}: {message}"
    if isinstance(message, str):
        return message
    if isinstance(code, str):
        return code
    return ""


def _redact_embedding_error_text(text: str, *, secret: str) -> str:
    redacted = text.replace(secret, "[redacted]") if secret else text
    redacted = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer [redacted]", redacted)
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[redacted-api-key]", redacted)
    redacted = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;}]+", r"\1[redacted]", redacted)
    return redacted


def _query_terms(text: str) -> list[str]:
    import re

    terms = [part.lower() for part in re.findall(r"[A-Za-z0-9_]+", text) if len(part) > 1]
    chunks = re.findall(r"[\u4e00-\u9fff]+", text)
    for chunk in chunks:
        if len(chunk) <= 4:
            terms.append(chunk)
        for size in (2, 3):
            for index in range(0, max(len(chunk) - size + 1, 0)):
                terms.append(chunk[index : index + size])
    for chunk in re.split(r"\s+", text.strip()):
        cleaned = chunk.strip().lower()
        if cleaned and cleaned not in terms:
            terms.append(cleaned)
    return terms or [text.strip().lower()]
