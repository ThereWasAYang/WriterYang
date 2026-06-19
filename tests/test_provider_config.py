from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import yaml

from novel.cli import main
from novel.core.agent_defaults import PROFILE_NAMES, TASK_TO_PROFILE
from novel.core.provider_config import (
    ProviderOverrides,
    create_agent_provider,
    describe_agent_provider,
    load_agents_config,
    resolve_agent_config,
    resolve_profile_config,
)
from novel.core.providers import LoggingModelProvider, MissingProviderEnvError, MockProvider, ProviderFactory
from novel.core.revision import load_revision_provider
from novel.core.workspace import InitOptions, init_workspace


def test_each_task_reads_its_profile_config(tmp_path: Path) -> None:
    config_path = _profiles_config(tmp_path)

    for task_name, profile_name in TASK_TO_PROFILE.items():
        config = resolve_agent_config(config_path, task_name)
        assert config.provider == "openai_compatible"
        assert config.model == f"{profile_name}-model"
        assert config.api_key_env == f"{profile_name.upper()}_API_KEY"
        assert config.base_url_env == f"{profile_name.upper()}_BASE_URL"
        assert config.thinking.type == "disabled"
        assert config.max_tokens == 6789
        assert config.timeout_seconds == 42
        assert config.max_retries == 2
        assert config.json_response_format == "json_schema"


def test_profile_config_can_be_resolved_directly(tmp_path: Path) -> None:
    config_path = _profiles_config(tmp_path)

    config = resolve_profile_config(config_path, "scribe")

    assert config.model == "scribe-model"
    assert config.api_key_env == "SCRIBE_API_KEY"


def test_provider_factory_resolve_then_create_supports_mock_override(tmp_path: Path) -> None:
    agents_config = load_agents_config(_profiles_config(tmp_path))

    factory = ProviderFactory(env={})
    config = factory.resolve_agent_config(
        agents_config,
        "writer",
        provider_override="mock",
    )
    provider = factory.create(config)

    assert isinstance(provider, MockProvider)


def test_model_override_is_temporary(tmp_path: Path) -> None:
    config_path = _profiles_config(tmp_path)

    config = resolve_agent_config(
        config_path,
        "writer",
        overrides=ProviderOverrides(provider_name="config", model_name="temporary-model"),
    )
    original = resolve_agent_config(config_path, "writer")

    assert config.model == "temporary-model"
    assert original.model == "scribe-model"


def test_default_profile_and_task_patch_merge(tmp_path: Path) -> None:
    config_path = _default_profiles_config(tmp_path)

    writer = resolve_agent_config(config_path, "writer")
    scribe = resolve_profile_config(config_path, "scribe")
    audit = resolve_agent_config(config_path, "audit")
    router = resolve_agent_config(config_path, "intent_router")

    assert writer.provider == "deepseek"
    assert writer.model == "default-model"
    assert writer.api_key_env == "DEFAULT_API_KEY"
    assert writer.temperature == 0.9
    assert writer.max_tokens == 24000
    assert writer.json_response_format == "json_object"
    assert scribe.temperature is None
    assert scribe.reasoning is None
    assert scribe.max_tokens == 24000
    assert audit.temperature == 0.2
    assert audit.reasoning == "medium"
    assert router.model == "router-model"
    assert router.temperature == 0.0
    assert router.max_tokens == 8192


def test_inherit_default_ignores_stale_profile_snapshot(tmp_path: Path) -> None:
    config_path = tmp_path / "agents.inherit.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "default": {
                    "provider": "deepseek",
                    "base_url_env": "DEFAULT_BASE_URL",
                    "api_key_env": "DEFAULT_API_KEY",
                    "model": "fresh-default-model",
                    "max_tokens": 8192,
                },
                "profiles": {
                    "scribe": {
                        "inherit_default": True,
                        "provider": "deepseek",
                        "base_url_env": "DEFAULT_BASE_URL",
                        "api_key_env": "DEFAULT_API_KEY",
                        "model": "stale-profile-model",
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
    assert config.temperature == 0.8
    assert config.max_tokens == 24000
    assert config.inherit_default is False
    assert descriptor.source == "default+profile:scribe+task-defaults:writer"


def test_explicit_non_inherited_profile_uses_independent_config(tmp_path: Path) -> None:
    config_path = tmp_path / "agents.independent.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "default": {
                    "provider": "deepseek",
                    "api_key_env": "DEFAULT_API_KEY",
                    "model": "default-model",
                },
                "profiles": {
                    "scribe": {
                        "inherit_default": False,
                        "provider": "openai",
                        "api_key_env": "SCRIBE_API_KEY",
                        "model": "scribe-model",
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

    assert config.provider == "openai"
    assert config.model == "scribe-model"
    assert config.api_key_env == "SCRIBE_API_KEY"
    assert config.thinking.type == "disabled"
    assert config.temperature == 0.8
    assert config.inherit_default is False


def test_partial_profile_without_default_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    path.write_text(
        yaml.safe_dump({"profiles": {"scribe": {"max_tokens": 24000}}}, allow_unicode=True),
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


def test_profile_task_only_fields_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "default": {"provider": "mock", "api_key_env": "MOCK_API_KEY", "model": "mock-model"},
                "profiles": {"scribe": {"inherit_default": True, "temperature": 0.9}},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    try:
        load_agents_config(path)
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("expected schema rejection")

    assert "task-only" in message
    assert "tasks.<task>" in message


def test_default_task_only_fields_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "default": {
                    "provider": "mock",
                    "api_key_env": "MOCK_API_KEY",
                    "model": "mock-model",
                    "temperature": 0.9,
                },
                "profiles": {"scribe": {"inherit_default": True}},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    try:
        load_agents_config(path)
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("expected schema rejection")

    assert "default config contains task-only" in message
    assert "tasks.<task>" in message


def test_missing_profile_uses_default_with_profile_and_task_defaults(tmp_path: Path) -> None:
    config_path = _default_profiles_config(tmp_path, include_profiles=False)

    config = resolve_agent_config(config_path, "revision")

    assert config.provider == "deepseek"
    assert config.model == "default-model"
    assert config.api_key_env == "DEFAULT_API_KEY"
    assert config.max_tokens == 24000
    assert config.temperature == 0.5


def test_unknown_task_config_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "default": {"provider": "mock", "api_key_env": "MOCK_API_KEY", "model": "mock-model"},
                "tasks": {"not_a_task": {"temperature": 0.1}},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    try:
        load_agents_config(path)
    except Exception as exc:
        assert "unknown task config" in str(exc)
    else:
        raise AssertionError("expected schema rejection")


def test_load_revision_provider_uses_revision_task_override(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "config" / "agents.yaml").write_text(
        yaml.safe_dump(
            {
                "profiles": {
                    "scribe": {"provider": "mock", "api_key_env": "MOCK_API_KEY", "model": "scribe-model"},
                },
                "tasks": {"revision": {"model": "revision-model"}},
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


def test_missing_api_key_env_is_clear_and_does_not_leak_secret(tmp_path: Path) -> None:
    agents_config = load_agents_config(_profiles_config(tmp_path))

    try:
        factory = ProviderFactory(env={"SCRIBE_BASE_URL": "https://example.test/v1"})
        config = factory.resolve_agent_config(
            agents_config,
            "writer",
        )
        factory.create(config)
    except MissingProviderEnvError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected MissingProviderEnvError")

    assert "SCRIBE_API_KEY" in message
    assert "required environment variable" in message
    assert "secret" not in message


def test_cli_provider_mock_overrides_real_config(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    config_path = _profiles_config(tmp_path)

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
    config_path = _profiles_config(tmp_path)

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
    assert "task: writer" in stdout
    assert "profile: scribe" in stdout
    assert "model: override-model" in stdout
    assert "source: profile:scribe+task-defaults:writer" in stdout
    assert "thinking: disabled" in stdout


def test_generate_chapter_dry_run_shows_per_step_profile_models(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    config_path = _profiles_config(tmp_path)

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
    assert "task: plot" in stdout
    assert "model: architect-model" in stdout
    assert "task: writer" in stdout
    assert "model: scribe-model" in stdout
    assert "task: polish" in stdout
    assert "task: audit" in stdout


def test_provider_descriptor_does_not_include_real_api_key(tmp_path: Path) -> None:
    descriptor = describe_agent_provider(_profiles_config(tmp_path), "writer")

    text = descriptor.format()

    assert "SCRIBE_API_KEY" in text
    assert "json_response_format: json_schema" in text
    assert "sk-test" not in text


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


def _profiles_config(tmp_path: Path) -> Path:
    path = tmp_path / "agents.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "profiles": {
                    profile_name: {
                        "provider": "openai_compatible",
                        "base_url_env": f"{profile_name.upper()}_BASE_URL",
                        "api_key_env": f"{profile_name.upper()}_API_KEY",
                        "model": f"{profile_name}-model",
                        "max_context_tokens": 12345,
                        "max_tokens": 6789,
                        "timeout_seconds": 42,
                        "max_retries": 2,
                        "json_response_format": "json_schema",
                    }
                    for profile_name in PROFILE_NAMES
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _default_profiles_config(tmp_path: Path, *, include_profiles: bool = True) -> Path:
    path = tmp_path / "agents.default.yaml"
    data: dict[str, object] = {
        "default": {
            "provider": "deepseek",
            "base_url_env": "DEFAULT_BASE_URL",
            "api_key_env": "DEFAULT_API_KEY",
            "model": "default-model",
            "max_tokens": 8192,
            "json_response_format": "json_object",
        },
        "tasks": {
            "writer": {"temperature": 0.9},
            "intent_router": {"model": "router-model", "temperature": 0.0},
        },
    }
    if include_profiles:
        data["profiles"] = {
            "scribe": {"inherit_default": True, "max_tokens": 24000},
            "architect": {"inherit_default": True, "max_tokens": 8192},
            "clerk": {"inherit_default": True, "max_tokens": 8192},
        }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
