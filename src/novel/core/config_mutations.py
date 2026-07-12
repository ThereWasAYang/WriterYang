from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from novel.core.agent_defaults import (
    PROFILE_NAMES,
    TASK_ONLY_CONFIG_FIELDS,
    TASK_TO_PROFILE,
    inherited_profile_config_patch,
    profile_inherited_patch_fields,
)
from novel.core.io import atomic_write_text, atomic_write_yaml, backup_if_exists, load_yaml
from novel.core.providers import ProviderError, ProviderFactory, resolve_json_response_format
from novel.core.schemas import AgentConfig, AgentsConfig
from novel.core.security import validate_secret_config_file


EDITABLE_PROFILE_NAMES = frozenset(PROFILE_NAMES)
EDITABLE_TASK_NAMES = frozenset(TASK_TO_PROFILE)


class ConfigMutationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AgentConfigUpdateResult:
    path: Path
    backup_path: Path | None
    cleared_profiles: tuple[str, ...]
    cleared_tasks: tuple[str, ...]
    config: AgentsConfig


def update_agent_config(
    root: Path,
    *,
    default_update: dict[str, object] | None,
    profiles_update: dict[str, dict[str, object]],
    tasks_update: dict[str, dict[str, object]],
    clear_profiles: list[str],
    clear_tasks: list[str],
) -> AgentConfigUpdateResult:
    root = root.expanduser().resolve()
    config_path = root / "config" / "agents.yaml"
    raw_config = load_yaml(config_path)
    if not isinstance(raw_config, dict):
        raise ConfigMutationError("invalid_config", "config/agents.yaml must be a YAML mapping")
    if not any((default_update is not None, profiles_update, tasks_update, clear_profiles, clear_tasks)):
        raise ConfigMutationError(
            "invalid_request",
            "default, profiles, tasks, clear_profiles, or clear_tasks must be provided",
        )
    updated = dict(raw_config)
    if default_update is not None:
        current_default = updated.get("default")
        if current_default is not None and not isinstance(current_default, dict):
            raise ConfigMutationError("invalid_config", "default config must be a mapping")
        cleaned_default = _clean_agent_config_patch(default_update, allow_task_only_fields=False)
        if "inherit_default" in cleaned_default:
            raise ConfigMutationError("invalid_request", "default config cannot inherit default")
        updated["default"] = {**(current_default or {}), **cleaned_default}
    raw_profiles = updated.get("profiles")
    raw_tasks = updated.get("tasks")
    profiles = dict(raw_profiles) if isinstance(raw_profiles, dict) else {}
    tasks = dict(raw_tasks) if isinstance(raw_tasks, dict) else {}
    cleared_profiles: list[str] = []
    cleared_tasks: list[str] = []
    for profile_name in clear_profiles:
        if profile_name == "default":
            raise ConfigMutationError("invalid_request", "default config cannot be cleared")
        if profile_name not in EDITABLE_PROFILE_NAMES and profile_name not in profiles:
            raise ConfigMutationError("invalid_request", f"unknown profile: {profile_name}")
        if profile_name in profiles:
            profiles.pop(profile_name, None)
            cleared_profiles.append(profile_name)
    for task_name in clear_tasks:
        if task_name not in EDITABLE_TASK_NAMES and task_name not in tasks:
            raise ConfigMutationError("invalid_request", f"unknown task: {task_name}")
        if task_name in tasks:
            tasks.pop(task_name, None)
            cleared_tasks.append(task_name)
    for profile_name, patch in profiles_update.items():
        if profile_name not in EDITABLE_PROFILE_NAMES and profile_name not in profiles:
            raise ConfigMutationError("invalid_request", f"unknown profile: {profile_name}")
        cleaned = _clean_agent_config_patch(patch, allow_task_only_fields=False)
        inherit_default = cleaned.get("inherit_default")
        if inherit_default is True:
            current_profile = profiles.get(profile_name)
            current_mapping = current_profile if isinstance(current_profile, dict) else None
            profiles[profile_name] = {
                **inherited_profile_config_patch(profile_name, current_mapping),
                **profile_inherited_patch_fields(cleaned),
                "inherit_default": True,
            }
            continue
        if profile_name not in profiles or not isinstance(profiles[profile_name], dict):
            profiles[profile_name] = {}
        current_profile = profiles.get(profile_name)
        currently_inherits = isinstance(current_profile, dict) and current_profile.get("inherit_default") is True
        if (
            (inherit_default is False or (inherit_default is None and currently_inherits and cleaned))
            and "default" in updated
        ):
            profiles[profile_name] = {
                **_validated_default_agent_snapshot(updated),
                **cleaned,
                "inherit_default": False,
            }
        else:
            current_mapping = profiles[profile_name]
            profiles[profile_name] = {**current_mapping, **cleaned}
    for task_name, patch in tasks_update.items():
        if task_name not in EDITABLE_TASK_NAMES and task_name not in tasks:
            raise ConfigMutationError("invalid_request", f"unknown task: {task_name}")
        cleaned = _clean_agent_config_patch(patch)
        cleaned.pop("inherit_default", None)
        if cleaned:
            tasks[task_name] = cleaned
        elif task_name in tasks:
            tasks.pop(task_name, None)
            cleared_tasks.append(task_name)
    updated["profiles"] = profiles
    updated["tasks"] = tasks
    updated.pop("agents", None)
    if default_update is not None:
        _refresh_inherited_profile_snapshots(updated)
    validated = AgentsConfig.model_validate(updated)
    _validate_provider_payload_config(validated)
    backup_path = backup_if_exists(config_path, reason="provider_config")
    atomic_write_yaml(config_path, updated)
    if validate_secret_config_file(config_path):
        if backup_path:
            atomic_write_text(config_path, backup_path.read_text(encoding="utf-8"))
        raise ConfigMutationError("unsafe_config_secret", "provider config contains unsafe secret-like values")
    return AgentConfigUpdateResult(
        path=config_path,
        backup_path=backup_path,
        cleared_profiles=tuple(cleared_profiles),
        cleared_tasks=tuple(cleared_tasks),
        config=validated,
    )


def _clean_agent_config_patch(
    patch: dict[str, object],
    *,
    allow_task_only_fields: bool = True,
) -> dict[str, object]:
    allowed = {
        "inherit_default",
        "provider",
        "model",
        "base_url_env",
        "api_key_env",
        "reasoning",
        "thinking",
        "max_context_tokens",
        "max_tokens",
        "temperature",
        "timeout_seconds",
        "max_retries",
        "json_response_format",
    }
    cleaned: dict[str, object] = {}
    for key, value in patch.items():
        if key not in allowed:
            raise ConfigMutationError("invalid_provider_config_field", f"field is not editable: {key}")
        if not allow_task_only_fields and key in TASK_ONLY_CONFIG_FIELDS:
            raise ConfigMutationError(
                "invalid_provider_config_field",
                f"default/profile config field is task-only: {key}; use tasks.<task> overrides",
            )
        if key == "inherit_default" and not isinstance(value, bool):
            raise ConfigMutationError("invalid_provider_config_field", "inherit_default must be a boolean")
        cleaned[key] = value
    return cleaned


def _validated_default_agent_snapshot(config: dict[str, object]) -> dict[str, object]:
    default_config = config.get("default")
    if not isinstance(default_config, dict):
        raise ConfigMutationError("invalid_config", "default config must be configured before agents can inherit it")
    if default_config.get("inherit_default") is True:
        raise ConfigMutationError("invalid_config", "default config cannot inherit default")
    validated = AgentConfig.model_validate(default_config)
    return validated.model_dump(
        mode="json",
        exclude_none=True,
        exclude={"inherit_default"} | set(TASK_ONLY_CONFIG_FIELDS),
    )


def _refresh_inherited_profile_snapshots(config: dict[str, object]) -> None:
    raw_profiles = config.get("profiles")
    profiles = raw_profiles if isinstance(raw_profiles, dict) else {}
    for profile_name in sorted(EDITABLE_PROFILE_NAMES):
        current = profiles.get(profile_name)
        current_mapping = current if isinstance(current, dict) else None
        if current is None or (isinstance(current, dict) and current.get("inherit_default") is True):
            profiles[profile_name] = inherited_profile_config_patch(profile_name, current_mapping)
    config["profiles"] = profiles


def _validate_provider_payload_config(config: AgentsConfig) -> None:
    resolver = ProviderFactory(env={})
    if config.default is not None:
        _validate_json_response_format_for_provider("default", config.default)
    for profile_name in config.profiles:
        try:
            resolved = resolver.resolve_profile_config(config, profile_name)
        except Exception as exc:
            raise ConfigMutationError("invalid_provider_config", str(exc)) from exc
        _validate_json_response_format_for_provider(f"profile {profile_name}", resolved)
    for task_name in config.tasks:
        try:
            resolved = resolver.resolve_agent_config(config, task_name)
        except Exception as exc:
            raise ConfigMutationError("invalid_provider_config", str(exc)) from exc
        _validate_json_response_format_for_provider(f"task {task_name}", resolved)


def _validate_json_response_format_for_provider(config_name: str, config: AgentConfig) -> None:
    try:
        resolve_json_response_format(config.provider.lower(), config.json_response_format)
    except ProviderError as exc:
        raise ConfigMutationError(
            "invalid_provider_config_field",
            f"provider config {config_name} json_response_format is not supported by provider {config.provider}: {exc}",
        ) from exc
