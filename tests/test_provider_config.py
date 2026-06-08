from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import yaml

from novel.cli import main
from novel.core.provider_config import (
    ProviderOverrides,
    create_agent_provider,
    describe_agent_provider,
    load_agents_config,
    resolve_agent_config,
)
from novel.core.providers import LoggingModelProvider, MissingProviderEnvError, MockProvider, ProviderFactory
from novel.core.revision import load_revision_provider
from novel.core.workspace import InitOptions, init_workspace


AGENTS = (
    "orchestrator",
    "inspiration",
    "canon",
    "plot",
    "writer",
    "polish",
    "audit",
    "state_update",
    "chapter_memory",
    "revision",
)


def test_each_agent_reads_independent_config(tmp_path: Path) -> None:
    config_path = _agents_config(tmp_path)

    for agent_name in AGENTS:
        config = resolve_agent_config(config_path, agent_name)
        assert config.provider == "openai_compatible"
        assert config.model == f"{agent_name}-model"
        assert config.api_key_env == f"{agent_name.upper()}_API_KEY"
        assert config.base_url_env == f"{agent_name.upper()}_BASE_URL"
        assert config.thinking.type == "disabled"
        assert config.max_tokens == 6789
        assert config.timeout_seconds == 42
        assert config.max_retries == 2
        assert config.json_response_format == "json_schema"


def test_provider_factory_create_for_agent_supports_mock_override(tmp_path: Path) -> None:
    agents_config = load_agents_config(_agents_config(tmp_path))

    provider = ProviderFactory(env={}).create_for_agent(
        agents_config,
        "writer",
        provider_override="mock",
    )

    assert isinstance(provider, MockProvider)


def test_model_override_is_temporary(tmp_path: Path) -> None:
    config_path = _agents_config(tmp_path)

    config = resolve_agent_config(
        config_path,
        "writer",
        overrides=ProviderOverrides(provider_name="config", model_name="temporary-model"),
    )
    original = resolve_agent_config(config_path, "writer")

    assert config.model == "temporary-model"
    assert original.model == "writer-model"


def test_default_config_and_agent_partial_override(tmp_path: Path) -> None:
    config_path = _default_agents_config(tmp_path)

    writer = resolve_agent_config(config_path, "writer")
    audit = resolve_agent_config(config_path, "audit")

    assert writer.provider == "deepseek"
    assert writer.model == "default-model"
    assert writer.api_key_env == "DEFAULT_API_KEY"
    assert writer.temperature == 0.9
    assert writer.max_tokens == 8192
    assert writer.json_response_format == "json_object"
    assert audit.provider == "deepseek"
    assert audit.temperature == 0.2
    assert audit.json_response_format == "json_object"


def test_inherit_default_ignores_stale_agent_snapshot(tmp_path: Path) -> None:
    config_path = tmp_path / "agents.inherit.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "default": {
                    "provider": "deepseek",
                    "base_url_env": "DEFAULT_BASE_URL",
                    "api_key_env": "DEFAULT_API_KEY",
                    "model": "fresh-default-model",
                    "thinking": {"type": "disabled"},
                    "temperature": 0.4,
                    "max_tokens": 8192,
                },
                "agents": {
                    "writer": {
                        "inherit_default": True,
                        "provider": "deepseek",
                        "base_url_env": "DEFAULT_BASE_URL",
                        "api_key_env": "DEFAULT_API_KEY",
                        "model": "stale-agent-model",
                        "thinking": {"type": "disabled"},
                        "temperature": 0.9,
                        "max_tokens": 24000,
                    }
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    config = resolve_agent_config(config_path, "writer")
    descriptor = describe_agent_provider(config_path, "writer")

    assert config.model == "fresh-default-model"
    assert config.temperature == 0.9
    assert config.max_tokens == 8192
    assert config.inherit_default is False
    assert descriptor.source == "default+agent:writer"


def test_explicit_non_inherited_agent_uses_independent_config(tmp_path: Path) -> None:
    config_path = tmp_path / "agents.independent.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "default": {
                    "provider": "deepseek",
                    "api_key_env": "DEFAULT_API_KEY",
                    "model": "default-model",
                    "thinking": {"type": "disabled"},
                    "temperature": 0.4,
                },
                "agents": {
                    "writer": {
                        "inherit_default": False,
                        "provider": "openai",
                        "api_key_env": "WRITER_API_KEY",
                        "model": "writer-model",
                        "thinking": {"type": "enabled"},
                        "temperature": 0.8,
                    }
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    config = resolve_agent_config(config_path, "writer")

    assert config.provider == "openai"
    assert config.model == "writer-model"
    assert config.api_key_env == "WRITER_API_KEY"
    assert config.thinking.type == "enabled"
    assert config.temperature == 0.8
    assert config.inherit_default is False


def test_partial_agent_without_inherit_default_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "agents.partial.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "default": {
                    "provider": "deepseek",
                    "api_key_env": "DEFAULT_API_KEY",
                    "model": "default-model",
                    "thinking": {"type": "disabled"},
                },
                "agents": {"writer": {"temperature": 0.8}},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    try:
        resolve_agent_config(config_path, "writer")
    except Exception as exc:
        assert "set inherit_default: true" in str(exc)
    else:
        raise AssertionError("expected partial agent config rejection")


def test_missing_agent_uses_default_config(tmp_path: Path) -> None:
    config_path = _default_agents_config(tmp_path)

    config = resolve_agent_config(config_path, "revision")

    assert config.provider == "deepseek"
    assert config.model == "default-model"
    assert config.api_key_env == "DEFAULT_API_KEY"


def test_fallback_agent_merges_with_default_config(tmp_path: Path) -> None:
    config_path = _default_agents_config(tmp_path)

    config = resolve_agent_config(config_path, "revision", fallback_agents=("writer",))

    assert config.provider == "deepseek"
    assert config.temperature == 0.9
    assert config.max_tokens == 8192


def test_load_revision_provider_prefers_revision_agent_config(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "config" / "agents.yaml").write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "revision": {"provider": "mock", "api_key_env": "MOCK_API_KEY", "model": "revision-model"},
                    "polish": {"provider": "mock", "api_key_env": "MOCK_API_KEY", "model": "polish-model"},
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    provider = load_revision_provider(root, "config", target="polished")

    assert isinstance(provider, LoggingModelProvider)
    assert provider.agent_name == "revision"
    assert provider.model == "revision-model"


def test_load_revision_provider_falls_back_to_target_agent_config(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "config" / "agents.yaml").write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "polish": {"provider": "mock", "api_key_env": "MOCK_API_KEY", "model": "polish-model"},
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    provider = load_revision_provider(root, "config", target="polished")

    assert isinstance(provider, LoggingModelProvider)
    assert provider.agent_name == "revision"
    assert provider.model == "polish-model"


def test_incomplete_agent_without_default_has_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    path.write_text(
        yaml.safe_dump({"agents": {"writer": {"temperature": 0.9}}}, allow_unicode=True),
        encoding="utf-8",
    )

    try:
        resolve_agent_config(path, "writer")
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("expected provider config failure")

    assert "no default API config" in message
    assert "provider" in message


def test_missing_api_key_env_is_clear_and_does_not_leak_secret(tmp_path: Path) -> None:
    agents_config = load_agents_config(_agents_config(tmp_path))

    try:
        ProviderFactory(env={"WRITER_BASE_URL": "https://example.test/v1"}).create_for_agent(
            agents_config,
            "writer",
        )
    except MissingProviderEnvError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected MissingProviderEnvError")

    assert "WRITER_API_KEY" in message
    assert "required environment variable" in message
    assert "secret" not in message


def test_cli_provider_mock_overrides_real_config(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    config_path = _agents_config(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "inspire",
            "雨夜旧车站",
            "--path",
            str(root),
            "--agent-config",
            str(config_path),
            "--provider",
            "mock",
            "--overwrite",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote inspiration markdown" in stdout


def test_cli_model_override_dry_run(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    config_path = _agents_config(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "write-chapter",
            "1",
            "--path",
            str(root),
            "--agent-config",
            str(config_path),
            "--model",
            "override-model",
            "--dry-run-provider",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "agent: writer" in stdout
    assert "model: override-model" in stdout
    assert "source: agent:writer" in stdout
    assert "thinking: disabled" in stdout


def test_generate_chapter_dry_run_shows_per_step_agent_models(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    config_path = _agents_config(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "generate-chapter",
            "1",
            "--path",
            str(root),
            "--agent-config",
            str(config_path),
            "--dry-run-provider",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "agent: plot" in stdout
    assert "model: plot-model" in stdout
    assert "agent: writer" in stdout
    assert "model: writer-model" in stdout
    assert "agent: polish" in stdout
    assert "model: polish-model" in stdout
    assert "agent: audit" in stdout
    assert "model: audit-model" in stdout


def test_provider_descriptor_does_not_include_real_api_key(tmp_path: Path) -> None:
    descriptor = describe_agent_provider(_agents_config(tmp_path), "writer")

    text = descriptor.format()

    assert "WRITER_API_KEY" in text
    assert "json_response_format: json_schema" in text
    assert "sk-" not in text


def test_provider_descriptor_shows_resolved_auto_json_response_format(tmp_path: Path) -> None:
    config_path = tmp_path / "agents.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "default": {
                    "provider": "openai",
                    "model": "test-model",
                    "api_key_env": "OPENAI_API_KEY",
                }
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    descriptor = describe_agent_provider(config_path, "writer")

    assert descriptor.json_response_format == "json_schema (auto)"
    assert "json_response_format: json_schema (auto)" in descriptor.format()


def test_create_agent_provider_wraps_mock_with_model_io_logging(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    config_path = root / "config" / "agents.yaml"
    provider = create_agent_provider(
        config_path,
        "writer",
        overrides=ProviderOverrides(provider_name="mock"),
        mock_response="章节正文",
    )

    assert isinstance(provider, LoggingModelProvider)
    response = provider.generate(provider_request())

    assert response.content == "章节正文"
    logs = list((root / "runs" / "model_io").glob("provider_*.json"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert '"agent_name": "writer"' in text
    assert "章节正文" in text


def provider_request():
    from novel.core.providers import ModelRequest

    return ModelRequest(system_prompt="系统", user_prompt="用户")


def _agents_config(tmp_path: Path) -> Path:
    path = tmp_path / "agents.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    agent_name: {
                        "provider": "openai_compatible",
                        "base_url_env": f"{agent_name.upper()}_BASE_URL",
                        "api_key_env": f"{agent_name.upper()}_API_KEY",
                        "model": f"{agent_name}-model",
                        "reasoning": "medium",
                        "thinking": {"type": "disabled"},
                        "max_context_tokens": 12345,
                        "max_tokens": 6789,
                        "temperature": 0.4,
                        "timeout_seconds": 42,
                        "max_retries": 2,
                        "json_response_format": "json_schema",
                    }
                    for agent_name in AGENTS
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _default_agents_config(tmp_path: Path) -> Path:
    path = tmp_path / "agents.default.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "default": {
                    "provider": "deepseek",
                    "base_url_env": "DEFAULT_BASE_URL",
                    "api_key_env": "DEFAULT_API_KEY",
                    "model": "default-model",
                    "thinking": {"type": "disabled"},
                    "temperature": 0.5,
                    "max_tokens": 8192,
                    "json_response_format": "json_object",
                },
                "agents": {
                    "writer": {"inherit_default": True, "temperature": 0.9},
                    "audit": {"inherit_default": True, "temperature": 0.2},
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
