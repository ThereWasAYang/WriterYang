from __future__ import annotations

from .deps import (
    Path,
    cast,
    DEFAULT_AGENT_MAX_CONTEXT_TOKENS,
    DEFAULT_AGENT_MAX_TOKENS,
    DEFAULT_AGENT_TIMEOUT_SECONDS,
    TASK_ONLY_CONFIG_FIELDS,
    web_launcher,
    AgentConfig,
    provider_parameter_capabilities,
    default_style_guide_markdown,
)
from novel.core.contracts import (
    AgentConfigUpdateCommand,
    ChapterCandidateSaveCommand,
    DefaultProviderSetupCommand,
    EmbeddingProviderSetupCommand,
    IndexUpdateCommand,
    ProjectInitCommand,
    StyleGuideGenerateCommand,
    StyleGuideSaveCommand,
    WebLauncherConfigCommand,
)

from .common import (
    STYLE_GUIDE_RELATIVE_PATH,
    WebAPIError,
    _sanitize_config,
    _require_workspace,
    _root_from_body,
    _init_project_root_from_body,
    _chapter_number,
    _optional_string,
    _optional_int,
    _optional_float,
    _provider_name,
    _truthy,
    _required_string,
    _configured_web_port,
    _current_web_endpoint,
    _split_csv,
    _dispatch_web_command,
)


def _style_guide(root: Path) -> dict[str, object]:
    _require_workspace(root)
    path = root / STYLE_GUIDE_RELATIVE_PATH
    default_template = default_style_guide_markdown()
    exists = path.exists()
    return {
        "path": STYLE_GUIDE_RELATIVE_PATH,
        "content": path.read_text(encoding="utf-8") if exists else default_template,
        "exists": exists,
        "default_template": default_template,
    }


def _save_style_guide(data: dict[str, object]) -> dict[str, object]:
    content = str(data.get("content") or "")
    if not content.strip():
        raise WebAPIError("invalid_request", "content must not be empty", status=400)
    return _dispatch_web_command(data, StyleGuideSaveCommand(content=content))


def _generate_style_guide(data: dict[str, object]) -> dict[str, object]:
    instruction = _optional_string(data.get("instruction"))
    if not instruction:
        raise WebAPIError("invalid_request", "style guide generation instruction must not be empty", status=400)
    include_project_context = _truthy(data["include_project_context"]) if "include_project_context" in data else True
    include_existing_style = _truthy(data["include_existing_style"]) if "include_existing_style" in data else True
    return _dispatch_web_command(
        data,
        StyleGuideGenerateCommand(
            instruction=instruction,
            provider_name=_provider_name(data.get("provider")),
            include_project_context=include_project_context,
            include_existing_style=include_existing_style,
        ),
    )


def _save_chapter_file(data: dict[str, object]) -> dict[str, object]:
    chapter_number = _chapter_number(data)
    target = str(data.get("target") or "")
    if target not in {"draft", "polished"}:
        raise WebAPIError("invalid_request", "target must be draft or polished", status=400)
    content = str(data.get("content") or "")
    if not content.strip():
        raise WebAPIError("invalid_request", "content must not be empty", status=400)
    source_name = str(data.get("source_file") or f"{target}.md")
    return _dispatch_web_command(
        data,
        ChapterCandidateSaveCommand(
            chapter_number=chapter_number,
            target=target,  # type: ignore[arg-type]
            source_file=source_name,
            content=content,
            instruction=_optional_string(data.get("instruction")),
        ),
    )


def _save_provider_config(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    _require_workspace(root)
    profiles_update = data.get("profiles")
    tasks_update = data.get("tasks")
    default_update = data.get("default")
    clear_profiles = data.get("clear_profiles")
    clear_tasks = data.get("clear_tasks")
    profiles = _config_update_mapping(profiles_update, "profiles")
    tasks = _config_update_mapping(tasks_update, "tasks")
    default = _config_patch(default_update, "default") if default_update is not None else None
    cleared_profiles = _string_update_list(clear_profiles, "clear_profiles")
    cleared_tasks = _string_update_list(clear_tasks, "clear_tasks")
    payload = _dispatch_web_command(
        data,
        AgentConfigUpdateCommand(
            default=default,
            profiles=profiles,
            tasks=tasks,
            clear_profiles=cleared_profiles,
            clear_tasks=cleared_tasks,
        ),
    )
    from .inspection import _provider_config_summary

    summary = _provider_config_summary(root)
    return {
        **payload,
        "config": summary["agents"],
        "effective_profiles": summary["effective_profiles"],
        "effective_tasks": summary["effective_tasks"],
    }


def _config_update_mapping(value: object, field: str) -> dict[str, dict[str, object]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WebAPIError("invalid_request", f"{field} must be a mapping", status=400)
    result: dict[str, dict[str, object]] = {}
    for name, patch in value.items():
        if not isinstance(name, str) or not isinstance(patch, dict):
            raise WebAPIError("invalid_request", f"{field} updates must be mappings", status=400)
        result[name] = {str(key): item for key, item in patch.items()}
    return result


def _config_patch(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise WebAPIError("invalid_request", f"{field} must be a mapping", status=400)
    return {str(key): item for key, item in value.items()}


def _string_update_list(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise WebAPIError("invalid_request", f"{field} must be a list of names", status=400)
    return list(value)


def _parameter_capabilities_payload(config: AgentConfig) -> dict[str, object]:
    capabilities = provider_parameter_capabilities(
        config.provider,
        thinking_type=config.thinking.type if config.thinking else None,
    )
    return {field: capability.as_dict() for field, capability in capabilities.items()}


def _profile_config_payload(config: AgentConfig) -> dict[str, object]:
    return cast(
        dict[str, object],
        _sanitize_config(config.model_dump(mode="json", exclude_none=True, exclude={"inherit_default"} | TASK_ONLY_CONFIG_FIELDS)),
    )


def _profile_parameter_capabilities_payload(config: AgentConfig) -> dict[str, object]:
    capabilities = _parameter_capabilities_payload(config)
    for field in TASK_ONLY_CONFIG_FIELDS:
        capabilities.pop(field, None)
    return capabilities


def _index_refresh(data: dict[str, object]) -> dict[str, object]:
    return _dispatch_web_command(
        data,
        IndexUpdateCommand(
            type="index.refresh",
            embedding_provider_name=str(data.get("embedding_provider") or "config"),
            with_embeddings=bool(data.get("with_embeddings")),
        ),
    )


def _init_project(data: dict[str, object]) -> dict[str, object]:
    root = _init_project_root_from_body(data)
    title = _optional_string(data.get("title")) or root.name or "未命名小说"
    genre_value = data.get("genre")
    genre = _split_csv(str(genre_value)) if genre_value else None
    payload = _dispatch_web_command(
        {**data, "path": str(root)},
        ProjectInitCommand(
            title=title,
            language=_optional_string(data.get("language")) or "zh-CN",
            genre=genre or [],
        ),
    )
    return {
        **payload,
        "setup_required": True,
    }


def _setup_recommend_port(query: dict[str, str]) -> dict[str, object]:
    start = _optional_int(query.get("start_port")) or 8765
    current_host, current_port = _current_web_endpoint(query)
    host = query.get("host") or current_host or "127.0.0.1"
    selected = web_launcher.recommend_web_launcher_port(
        start,
        host=host,
        current_host=current_host,
        current_port=current_port,
    )
    return {
        "host": host,
        "requested_port": start,
        "selected_port": selected,
        "available": selected == start,
        "url": f"http://{host}:{selected}",
    }


def _setup_default_provider(data: dict[str, object]) -> dict[str, object]:
    blocked_fields = (set(TASK_ONLY_CONFIG_FIELDS) | {"thinking_type"}) & set(data)
    if blocked_fields:
        fields = ", ".join(sorted(blocked_fields))
        raise WebAPIError(
            "invalid_provider_config_field",
            f"default provider setup field is task-only: {fields}; use tasks.<task> overrides",
            status=400,
        )
    payload = _dispatch_web_command(
        data,
        DefaultProviderSetupCommand(
            provider=_optional_string(data.get("provider")) or "openai_compatible",
            base_url=_required_string(data.get("base_url"), "base_url"),
            api_key=_required_string(data.get("api_key"), "api_key"),
            model=_required_string(data.get("model"), "model"),
            max_context_tokens=_optional_int(data.get("max_context_tokens")) or DEFAULT_AGENT_MAX_CONTEXT_TOKENS,
            max_tokens=_optional_int(data.get("max_tokens")) or DEFAULT_AGENT_MAX_TOKENS,
            timeout_seconds=_optional_float(data.get("timeout_seconds"), DEFAULT_AGENT_TIMEOUT_SECONDS),
            max_retries=_optional_int(data.get("max_retries")) or 1,
            ping=bool(data.get("ping", True)),
        ),
    )
    return {
        **payload,
        "message": "这组 API 配置已作为所有 profile 的默认配置。可在 config/agents.yaml 中覆盖 profile 的模型能力参数，或在 tasks 中覆盖单个 task 的思考模式、温度等业务参数。",
    }


def _setup_embedding(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    if bool(data.get("skip")):
        return _dispatch_web_command(data, EmbeddingProviderSetupCommand(skip=True))
    dimensions = _optional_int(data.get("dimensions"))
    batch_size = _optional_int(data.get("batch_size"))
    payload = _dispatch_web_command(
        data,
        EmbeddingProviderSetupCommand(
            provider=_optional_string(data.get("provider")) or "openai_compatible",
            provider_name=_optional_string(data.get("provider_name")) or "configured",
            base_url=_required_string(data.get("base_url"), "base_url"),
            api_key=_required_string(data.get("api_key"), "api_key"),
            model=_required_string(data.get("model"), "model"),
            dimensions=dimensions if dimensions and dimensions > 0 else None,
            batch_size=batch_size if batch_size and batch_size > 0 else None,
            timeout_seconds=_optional_float(data.get("timeout_seconds"), 30.0),
            max_retries=_optional_int(data.get("max_retries")) or 1,
            ping=bool(data.get("ping", True)),
        ),
    )
    return {
        **payload,
        "embedding_api": _embedding_api_config_summary_for_response(root),
    }


def _setup_web_port(data: dict[str, object]) -> dict[str, object]:
    current_host, current_port = _current_web_endpoint(data)
    host = _optional_string(data.get("host")) or current_host or "127.0.0.1"
    requested = _optional_int(data.get("port")) or 8765
    payload = _dispatch_web_command(
        data,
        WebLauncherConfigCommand(
            host=host,
            requested_port=requested,
            current_host=current_host,
            current_port=current_port,
        ),
    )
    return {
        **payload,
        "message": "Web UI 启动器端口已保存。下次通过 WriterYang_WebUI.command 启动时会使用这个端口。",
    }


def _setup_open_web(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    host = _optional_string(data.get("host")) or "127.0.0.1"
    port = _optional_int(data.get("port")) or _configured_web_port(root)
    return {"url": f"http://{host}:{port}", "opened": False}


def _embedding_api_config_summary_for_response(root: Path) -> dict[str, object]:
    from .inspection import _embedding_api_config_summary

    return _embedding_api_config_summary(root)
