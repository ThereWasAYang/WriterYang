from __future__ import annotations

import json
from pathlib import Path

from novel.core.provider_config import resolve_agent_config
from novel.core.env import load_project_env, read_project_env_file
from novel.core.io import load_yaml
from novel.core.setup_guide import (
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL_ENV,
    DEFAULT_EMBEDDING_API_KEY_ENV,
    DEFAULT_EMBEDDING_BASE_URL_ENV,
    configure_default_provider,
    configure_embedding_provider,
    configure_web_port,
    find_available_port,
)
from novel.core.workspace import InitOptions, init_workspace


def test_configure_default_provider_writes_env_and_yaml_without_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "novel"
    init_workspace(InitOptions(title="测试小说", root=root))
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["authorization"] = req.headers["Authorization"]
        return _FakeResponse({"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setattr("novel.core.providers.request.urlopen", fake_urlopen)

    result = configure_default_provider(
        root,
        base_url="https://api.example.test/v1",
        api_key="secret-key",
        model="example-model",
    )

    env = read_project_env_file(root)
    agents = load_yaml(root / "config" / "agents.yaml")
    assert result.ping_ok is True
    assert env[DEFAULT_API_KEY_ENV] == "secret-key"
    assert env[DEFAULT_BASE_URL_ENV] == "https://api.example.test/v1"
    assert agents["default"]["api_key_env"] == DEFAULT_API_KEY_ENV  # type: ignore[index]
    assert agents["default"]["base_url_env"] == DEFAULT_BASE_URL_ENV  # type: ignore[index]
    assert agents["default"]["model"] == "example-model"  # type: ignore[index]
    assert "temperature" not in agents["default"]  # type: ignore[operator]
    assert "reasoning" not in agents["default"]  # type: ignore[operator]
    assert "thinking" not in agents["default"]  # type: ignore[operator]
    assert agents["profiles"]["scribe"]["inherit_default"] is True  # type: ignore[index]
    assert "api_key_env" not in agents["profiles"]["scribe"]  # type: ignore[index]
    assert "base_url_env" not in agents["profiles"]["scribe"]  # type: ignore[index]
    assert "model" not in agents["profiles"]["scribe"]  # type: ignore[index]
    writer = resolve_agent_config(root / "config" / "agents.yaml", "writer")
    assert writer.api_key_env == DEFAULT_API_KEY_ENV
    assert writer.base_url_env == DEFAULT_BASE_URL_ENV
    assert writer.model == "example-model"
    assert "secret-key" not in (root / "config" / "agents.yaml").read_text(encoding="utf-8")
    assert captured["url"] == "https://api.example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret-key"
    assert "temperature" not in captured["payload"]  # type: ignore[operator]
    assert "thinking" not in captured["payload"]  # type: ignore[operator]


def test_project_env_is_used_by_provider_creation(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "novel"
    init_workspace(InitOptions(title="测试小说", root=root))
    configure_default_provider(
        root,
        base_url="https://api.example.test/v1",
        api_key="secret-key",
        model="example-model",
        ping=False,
    )

    env = load_project_env(root, {})

    assert env[DEFAULT_API_KEY_ENV] == "secret-key"
    assert env[DEFAULT_BASE_URL_ENV] == "https://api.example.test/v1"


def test_configure_embedding_provider_is_optional_and_secret_safe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "novel"
    init_workspace(InitOptions(title="测试小说", root=root))

    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        payload = json.loads(req.data.decode("utf-8"))
        inputs = payload["input"]
        assert isinstance(inputs, list)
        return _FakeResponse(
            {
                "data": [
                    {"index": index, "embedding": [0.1, 0.2]}
                    for index, _ in enumerate(inputs)
                ]
            }
        )

    monkeypatch.setattr("novel.core.embeddings.request.urlopen", fake_urlopen)

    result = configure_embedding_provider(
        root,
        base_url="https://embed.example.test/v1",
        api_key="embedding-secret",
        model="embedding-model",
    )

    env = read_project_env_file(root)
    embeddings = load_yaml(root / "config" / "embeddings.yaml")
    assert result.ping_ok is True
    assert env[DEFAULT_EMBEDDING_API_KEY_ENV] == "embedding-secret"
    assert env[DEFAULT_EMBEDDING_BASE_URL_ENV] == "https://embed.example.test/v1"
    assert embeddings["active_provider"] == "configured"  # type: ignore[index]
    configured = embeddings["providers"]["configured"]  # type: ignore[index]
    assert configured["api_key_env"] == DEFAULT_EMBEDDING_API_KEY_ENV
    assert configured["base_url_env"] == DEFAULT_EMBEDDING_BASE_URL_ENV
    assert "embedding-secret" not in (root / "config" / "embeddings.yaml").read_text(encoding="utf-8")


def test_configure_dashscope_embedding_provider_validates_max_batch_and_dimensions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "novel"
    init_workspace(InitOptions(title="测试小说", root=root))
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        payload = json.loads(req.data.decode("utf-8"))
        captured["payload"] = payload
        inputs = payload["input"]
        assert isinstance(inputs, list)
        dimensions = int(payload["dimensions"])
        return _FakeResponse(
            {
                "data": [
                    {"index": index, "embedding": [0.1 for _ in range(dimensions)]}
                    for index, _ in enumerate(inputs)
                ]
            }
        )

    monkeypatch.setattr("novel.core.embeddings.request.urlopen", fake_urlopen)

    result = configure_embedding_provider(
        root,
        provider="openai_compatible",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="embedding-secret",
        model="text-embedding-v4",
    )

    embeddings = load_yaml(root / "config" / "embeddings.yaml")
    configured = embeddings["providers"]["configured"]  # type: ignore[index]
    payload = captured["payload"]
    assert result.dimensions == 2048
    assert result.batch_size == 10
    assert configured["dimensions"] == 2048
    assert configured["batch_size"] == 10
    assert len(payload["input"]) == 10  # type: ignore[arg-type]
    assert payload["dimensions"] == 2048  # type: ignore[index]
    assert payload["encoding_format"] == "float"  # type: ignore[index]


def test_find_available_port_skips_occupied_port(monkeypatch) -> None:
    monkeypatch.setattr("novel.core.setup_guide.is_port_available", lambda port, host="127.0.0.1": port == 8766)

    selected = find_available_port(8765)

    assert selected == 8766


def test_configure_web_port_updates_project_yaml(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "novel"
    init_workspace(InitOptions(title="测试小说", root=root))
    monkeypatch.setattr("novel.core.setup_guide.is_port_available", lambda port, host="127.0.0.1": True)

    result = configure_web_port(root, requested_port=8765)

    project = load_yaml(root / "project.yaml")
    assert project["web"]["default_port"] == result.selected_port  # type: ignore[index]
    assert result.url == f"http://127.0.0.1:{result.selected_port}"


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")
