from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import yaml

from novel.cli import main
from novel.core.provider_config import (
    ProviderOverrides,
    describe_agent_provider,
    load_agents_config,
    resolve_agent_config,
)
from novel.core.providers import MissingProviderEnvError, MockProvider, ProviderFactory
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
    assert "sk-" not in text


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


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
