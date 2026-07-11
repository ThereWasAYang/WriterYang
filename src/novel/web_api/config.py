from __future__ import annotations

from .deps import (
    Path,
    cast,
    DEFAULT_AGENT_MAX_CONTEXT_TOKENS,
    DEFAULT_AGENT_MAX_TOKENS,
    DEFAULT_AGENT_TIMEOUT_SECONDS,
    TASK_ONLY_CONFIG_FIELDS,
    drop_legacy_profile_default_patch,
    inherited_profile_config_patch,
    profile_inherited_patch_fields,
    atomic_write_text,
    atomic_write_yaml,
    backup_if_exists,
    load_yaml,
    refresh_search_index,
    search_index_status,
    configure_default_provider,
    configure_embedding_provider,
    StyleGuideGenerationOptions,
    generate_style_guide,
    load_style_guide_provider,
    utc_now,
    web_launcher,
    AgentConfig,
    AgentsConfig,
    RevisionRecord,
    validate_secret_config_file,
    ProviderError,
    ProviderFactory,
    provider_parameter_capabilities,
    resolve_json_response_format,
    default_style_guide_markdown,
)
from novel.core.contracts import ProjectInitCommand
from novel.core.artifact_store import ArtifactStore
from novel.core.contracts import ArtifactKind

from .common import (
    EDITABLE_PROFILE_NAMES,
    EDITABLE_TASK_NAMES,
    STYLE_GUIDE_RELATIVE_PATH,
    WebAPIError,
    _sanitize_config,
    _is_allowed_chapter_version_name,
    _new_revision_id,
    _append_web_revision_log,
    _is_archived_chapter,
    _clean_agent_config_patch,
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
    _relative,
    _dispatch_web_command,
    _safe_error,
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
    root = _root_from_body(data)
    _require_workspace(root)
    content = str(data.get("content") or "")
    if not content.strip():
        raise WebAPIError("invalid_request", "content must not be empty", status=400)

    path = root / STYLE_GUIDE_RELATIVE_PATH
    backup_path = backup_if_exists(path, reason="web_style_guide")
    atomic_write_text(path, content.rstrip() + "\n")
    return {
        "path": STYLE_GUIDE_RELATIVE_PATH,
        "backup_path": _relative(root, backup_path) if backup_path else None,
        "content": path.read_text(encoding="utf-8"),
    }


def _generate_style_guide(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    _require_workspace(root)
    instruction = _optional_string(data.get("instruction"))
    if not instruction:
        raise WebAPIError("invalid_request", "style guide generation instruction must not be empty", status=400)
    provider = load_style_guide_provider(root, _provider_name(data.get("provider")))
    include_project_context = _truthy(data["include_project_context"]) if "include_project_context" in data else True
    include_existing_style = _truthy(data["include_existing_style"]) if "include_existing_style" in data else True
    result = generate_style_guide(
        StyleGuideGenerationOptions(
            root=root,
            instruction=instruction,
            include_project_context=include_project_context,
            include_existing_style=include_existing_style,
        ),
        provider,
    )
    return {
        "path": STYLE_GUIDE_RELATIVE_PATH,
        "content": result.content,
        "warnings": list(result.warnings),
    }


def _save_chapter_file(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    _require_workspace(root)
    chapter_number = _chapter_number(data)
    target = str(data.get("target") or "")
    if target not in {"draft", "polished"}:
        raise WebAPIError("invalid_request", "target must be draft or polished", status=400)
    content = str(data.get("content") or "")
    if not content.strip():
        raise WebAPIError("invalid_request", "content must not be empty", status=400)
    chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
    source_name = str(data.get("source_file") or f"{target}.md")
    if not _is_allowed_chapter_version_name(source_name, target):
        raise WebAPIError("forbidden_file", "source_file is not an editable chapter version", status=403)
    source_path = chapter_dir / source_name
    if not source_path.exists():
        raise FileNotFoundError(f"{source_name} does not exist")
    if _is_archived_chapter(root, chapter_number):
        raise WebAPIError(
            "archived_content_read_only",
            "archived chapter content is read-only; create a new revision session instead",
            status=409,
        )

    output_ref = ArtifactStore(root).create(
        chapter_number=chapter_number,
        kind=ArtifactKind.CANDIDATE,
        content=(content.rstrip() + "\n").encode("utf-8"),
        suffix=".md",
    )
    output_path = root / output_ref.path
    record = RevisionRecord(
        id=_new_revision_id(),
        chapter_number=chapter_number,
        target=target,  # type: ignore[arg-type]
        source_file=source_name,
        output_file=output_ref.path,
        instruction=_optional_string(data.get("instruction")) or "Web editor save as version",
        from_audit=False,
        audit_file="audit.json" if (chapter_dir / "audit.json").exists() else None,
        audit_issue_ids=[],
        created_at=utc_now(),
        provider="web_editor",
    )
    log_path = chapter_dir / "revision_log.json"
    _append_web_revision_log(log_path, chapter_number, record)
    return {
        "output_path": str(output_path),
        "relative_path": _relative(root, output_path),
        "revision_log_path": str(log_path),
        "record": record.model_dump(mode="json"),
    }


def _save_provider_config(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    _require_workspace(root)
    config_path = root / "config" / "agents.yaml"
    raw_config = load_yaml(config_path)
    if not isinstance(raw_config, dict):
        raise WebAPIError("invalid_config", "config/agents.yaml must be a YAML mapping", status=400)
    profiles_update = data.get("profiles")
    tasks_update = data.get("tasks")
    default_update = data.get("default")
    clear_profiles = data.get("clear_profiles")
    clear_tasks = data.get("clear_tasks")
    if profiles_update is None and tasks_update is None and default_update is None and clear_profiles is None and clear_tasks is None:
        raise WebAPIError("invalid_request", "default, profiles, tasks, clear_profiles, or clear_tasks must be provided", status=400)
    if profiles_update is None:
        profiles_update = {}
    if tasks_update is None:
        tasks_update = {}
    if not isinstance(profiles_update, dict):
        raise WebAPIError("invalid_request", "profiles must be a mapping", status=400)
    if not isinstance(tasks_update, dict):
        raise WebAPIError("invalid_request", "tasks must be a mapping", status=400)
    if clear_profiles is None:
        clear_profiles = []
    if clear_tasks is None:
        clear_tasks = []
    if not isinstance(clear_profiles, list) or any(not isinstance(name, str) for name in clear_profiles):
        raise WebAPIError("invalid_request", "clear_profiles must be a list of profile names", status=400)
    if not isinstance(clear_tasks, list) or any(not isinstance(name, str) for name in clear_tasks):
        raise WebAPIError("invalid_request", "clear_tasks must be a list of task names", status=400)
    updated = dict(raw_config)
    if default_update is not None:
        if not isinstance(default_update, dict):
            raise WebAPIError("invalid_request", "default must be a mapping", status=400)
        current_default = updated.get("default")
        if current_default is not None and not isinstance(current_default, dict):
            raise WebAPIError("invalid_config", "default config must be a mapping", status=400)
        cleaned_default = _clean_agent_config_patch(default_update, allow_task_only_fields=False)
        if "inherit_default" in cleaned_default:
            raise WebAPIError("invalid_request", "default config cannot inherit default", status=400)
        updated["default"] = {**(current_default or {}), **cleaned_default}
    profiles = dict(updated.get("profiles") or {})
    tasks = dict(updated.get("tasks") or {})
    cleared_profiles: list[str] = []
    cleared_tasks: list[str] = []
    for profile_name in clear_profiles:
        if profile_name == "default":
            raise WebAPIError("invalid_request", "default config cannot be cleared", status=400)
        if profile_name not in EDITABLE_PROFILE_NAMES and profile_name not in profiles:
            raise WebAPIError("invalid_request", f"unknown profile: {profile_name}", status=400)
        if profile_name in profiles:
            profiles.pop(profile_name, None)
            cleared_profiles.append(profile_name)
    for task_name in clear_tasks:
        if task_name not in EDITABLE_TASK_NAMES and task_name not in tasks:
            raise WebAPIError("invalid_request", f"unknown task: {task_name}", status=400)
        if task_name in tasks:
            tasks.pop(task_name, None)
            cleared_tasks.append(task_name)
    for profile_name, patch in profiles_update.items():
        if not isinstance(profile_name, str) or not isinstance(patch, dict):
            raise WebAPIError("invalid_request", "profile updates must be mappings", status=400)
        if profile_name not in EDITABLE_PROFILE_NAMES and profile_name not in profiles:
            raise WebAPIError("invalid_request", f"unknown profile: {profile_name}", status=400)
        cleaned = _clean_agent_config_patch(patch, allow_task_only_fields=False)
        inherit_default = cleaned.get("inherit_default")
        if inherit_default is True:
            current_profile = profiles.get(profile_name)
            current_mapping = current_profile if isinstance(current_profile, dict) else None
            current_mapping = drop_legacy_profile_default_patch(profile_name, current_mapping)
            cleaned_mapping = drop_legacy_profile_default_patch(profile_name, cleaned) or {}
            profiles[profile_name] = {
                **inherited_profile_config_patch(profile_name, current_mapping),
                **profile_inherited_patch_fields(cleaned_mapping),
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
            base = _validated_default_agent_snapshot(updated)
            profiles[profile_name] = {**base, **cleaned, "inherit_default": False}
        else:
            profiles[profile_name] = {**profiles[profile_name], **cleaned}
    for task_name, patch in tasks_update.items():
        if not isinstance(task_name, str) or not isinstance(patch, dict):
            raise WebAPIError("invalid_request", "task updates must be mappings", status=400)
        if task_name not in EDITABLE_TASK_NAMES and task_name not in tasks:
            raise WebAPIError("invalid_request", f"unknown task: {task_name}", status=400)
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
    backup_path = backup_if_exists(config_path, reason="web_provider_config")
    atomic_write_yaml(config_path, updated)
    findings = validate_secret_config_file(config_path)
    if findings:
        if backup_path:
            atomic_write_text(config_path, backup_path.read_text(encoding="utf-8"))
        raise WebAPIError("unsafe_config_secret", "provider config contains unsafe secret-like values", status=400)
    from .inspection import _provider_config_summary

    summary = _provider_config_summary(root)
    return {
        "path": str(config_path),
        "backup_path": str(backup_path) if backup_path else None,
        "cleared_profiles": cleared_profiles,
        "cleared_tasks": cleared_tasks,
        "config": summary["agents"],
        "effective_profiles": summary["effective_profiles"],
        "effective_tasks": summary["effective_tasks"],
    }


def _validated_default_agent_snapshot(config: dict[str, object]) -> dict[str, object]:
    default_config = config.get("default")
    if not isinstance(default_config, dict):
        raise WebAPIError("invalid_config", "default config must be configured before agents can inherit it", status=400)
    if default_config.get("inherit_default") is True:
        raise WebAPIError("invalid_config", "default config cannot inherit default", status=400)
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
            cleaned = drop_legacy_profile_default_patch(profile_name, current_mapping)
            profiles[profile_name] = inherited_profile_config_patch(profile_name, cleaned)
    config["profiles"] = profiles


def _validate_provider_payload_config(config: AgentsConfig) -> None:
    resolver = ProviderFactory(env={})
    if config.default is not None:
        _validate_json_response_format_for_provider("default", config.default)
    for profile_name in config.profiles:
        try:
            resolved = resolver.resolve_profile_config(config, profile_name)
        except Exception as exc:
            raise WebAPIError("invalid_provider_config", _safe_error(str(exc)), status=400) from exc
        _validate_json_response_format_for_provider(f"profile {profile_name}", resolved)
    for task_name in config.tasks:
        try:
            resolved = resolver.resolve_agent_config(config, task_name)
        except Exception as exc:
            raise WebAPIError("invalid_provider_config", _safe_error(str(exc)), status=400) from exc
        _validate_json_response_format_for_provider(f"task {task_name}", resolved)


def _validate_json_response_format_for_provider(config_name: str, config: AgentConfig) -> None:
    try:
        resolve_json_response_format(config.provider.lower(), config.json_response_format)
    except ProviderError as exc:
        raise WebAPIError(
            "invalid_provider_config_field",
            f"provider config {config_name} json_response_format is not supported by provider {config.provider}: {_safe_error(str(exc))}",
            status=400,
        ) from exc


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
    root = _root_from_body(data)
    result = refresh_search_index(
        root,
        embedding_provider_name=str(data.get("embedding_provider") or "config"),
        with_embeddings=bool(data.get("with_embeddings")),
    )
    return {
        "index_path": str(result.index_path),
        "sqlite_path": str(result.sqlite_path),
        "manifest_path": str(result.manifest_path),
        "document_count": result.document_count,
        "refreshed_count": result.refreshed_count,
        "deleted_count": result.deleted_count,
        "embedding_document_count": result.embedding_document_count,
        "with_embeddings": result.with_embeddings,
        "search": search_index_status(root).as_dict(),
    }


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
    root = _root_from_body(data)
    blocked_fields = (set(TASK_ONLY_CONFIG_FIELDS) | {"thinking_type"}) & set(data)
    if blocked_fields:
        fields = ", ".join(sorted(blocked_fields))
        raise WebAPIError(
            "invalid_provider_config_field",
            f"default provider setup field is task-only: {fields}; use tasks.<task> overrides",
            status=400,
        )
    result = configure_default_provider(
        root,
        provider=_optional_string(data.get("provider")) or "openai_compatible",
        base_url=_required_string(data.get("base_url"), "base_url"),
        api_key=_required_string(data.get("api_key"), "api_key"),
        model=_required_string(data.get("model"), "model"),
        max_context_tokens=_optional_int(data.get("max_context_tokens")) or DEFAULT_AGENT_MAX_CONTEXT_TOKENS,
        max_tokens=_optional_int(data.get("max_tokens")) or DEFAULT_AGENT_MAX_TOKENS,
        timeout_seconds=_optional_float(data.get("timeout_seconds"), DEFAULT_AGENT_TIMEOUT_SECONDS),
        max_retries=_optional_int(data.get("max_retries")) or 1,
        ping=bool(data.get("ping", True)),
    )
    return {
        "config_path": str(result.config_path),
        "env_path": str(result.env_path),
        "provider": result.provider,
        "model": result.model,
        "api_key_env": result.api_key_env,
        "base_url_env": result.base_url_env,
        "ping_ok": result.ping_ok,
        "ping_message": result.ping_message,
        "message": "这组 API 配置已作为所有 profile 的默认配置。可在 config/agents.yaml 中覆盖 profile 的模型能力参数，或在 tasks 中覆盖单个 task 的思考模式、温度等业务参数。",
    }


def _setup_embedding(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    if bool(data.get("skip")):
        return {"skipped": True, "message": "已跳过 embedding API 配置；关键词/FTS 检索仍可用。"}
    dimensions = _optional_int(data.get("dimensions"))
    batch_size = _optional_int(data.get("batch_size"))
    result = configure_embedding_provider(
        root,
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
    )
    return {
        "config_path": str(result.config_path),
        "env_path": str(result.env_path),
        "active_provider": result.active_provider,
        "provider": result.provider,
        "model": result.model,
        "dimensions": result.dimensions,
        "batch_size": result.batch_size,
        "api_key_env": result.api_key_env,
        "base_url_env": result.base_url_env,
        "ping_ok": result.ping_ok,
        "ping_message": result.ping_message,
        "embedding_api": _embedding_api_config_summary_for_response(root),
    }


def _setup_web_port(data: dict[str, object]) -> dict[str, object]:
    current_host, current_port = _current_web_endpoint(data)
    host = _optional_string(data.get("host")) or current_host or "127.0.0.1"
    requested = _optional_int(data.get("port")) or 8765
    config_path = (
        Path(_required_string(data.get("launcher_config_path"), "launcher_config_path")).expanduser().resolve()
        if _optional_string(data.get("launcher_config_path"))
        else web_launcher.launcher_config_path_from_env()
    )
    result = web_launcher.save_web_launcher_port_config(
        config_path,
        host=host,
        requested_port=requested,
        current_host=current_host,
        current_port=current_port,
    )
    launcher_path = web_launcher.launcher_path_from_env()
    try:
        web_launcher.write_web_launcher_command(
            launcher_path,
            config_path=result.config_path,
            cwd=Path.cwd(),
        )
    except Exception:
        launcher_path = None
    return {
        "launcher_config_path": str(result.config_path),
        "launcher_path": str(launcher_path) if launcher_path else "",
        "host": result.host,
        "requested_port": result.requested_port,
        "selected_port": result.selected_port,
        "available": result.requested_port == result.selected_port,
        "url": result.url,
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
