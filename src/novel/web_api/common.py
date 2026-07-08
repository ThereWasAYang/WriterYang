from __future__ import annotations

from typing import Literal

from .deps import (
    Callable,
    json,
    os,
    Path,
    re,
    sys,
    Mapping,
    cast,
    BaseModel,
    Field,
    __version__,
    PROFILE_NAMES,
    TASK_ONLY_CONFIG_FIELDS,
    TASK_TO_PROFILE,
    is_allowed_chapter_version_name,
    next_chapter_version_path,
    load_project_env,
    atomic_write_model_json,
    backup_if_exists,
    load_json,
    load_json_model,
    load_yaml,
    new_request_id,
    utc_timestamp,
    web_launcher,
    AgentsConfig,
    PolishMode,
    RevisionLog,
    RevisionRecord,
    VectorContextMode,
    MemoryChangeStage,
    redact_secret_text,
    ProviderName,
)


APIResponse = tuple[int, dict[str, object]]
WebPostHandler = Callable[[dict[str, object]], dict[str, object]]
RootResolver = Callable[[dict[str, object]], Path]
PostRoute = tuple[str, WebPostHandler, bool] | tuple[str, WebPostHandler, bool, RootResolver]
SAFE_FILE_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".yaml", ".yml"}
EXCLUDED_DIRS = {".git", ".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache"}
EXCLUDED_FILENAMES = {
    "search_index.json",
    "search_index.sqlite",
    ".DS_Store",
}
EDITABLE_PROFILE_NAMES = set(PROFILE_NAMES)
EDITABLE_TASK_NAMES = set(TASK_TO_PROFILE)
STYLE_GUIDE_RELATIVE_PATH = "memory/style_guide.md"


class WebErrorPayload(BaseModel):
    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)
    request_id: str


class WebResponsePayload(BaseModel):
    ok: bool
    data: dict[str, object] | None = None
    error: WebErrorPayload | None = None


class WebAPIError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or {}

def _safe_config_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"path": str(path), "exists": False, "content": None, "env": []}
    data = load_yaml(path)
    env_names = sorted(_collect_env_names(data))
    env = load_project_env(path.parent.parent)
    return {
        "path": str(path),
        "exists": True,
        "content": _sanitize_config(data),
        "env": [{"name": name, "exists": bool(env.get(name))} for name in env_names],
    }


def _agent_config_warnings(path: Path) -> list[str]:
    if not path.exists():
        return ["config/agents.yaml does not exist"]
    try:
        config = AgentsConfig.model_validate(load_yaml(path))
    except Exception as exc:
        return [f"config/agents.yaml is invalid: {_safe_error(str(exc))}"]
    warnings: list[str] = []
    if config.default is None:
        warnings.append("default API config is missing; unconfigured profiles cannot use provider config")
    elif config.default.provider.lower() == "mock":
        warnings.append("default provider uses mock; mock is intended for tests only")
    for name, item in sorted(config.profiles.items()):
        if item.provider and item.provider.lower() == "mock":
            warnings.append(f"profile {name} uses mock provider; mock is intended for tests only")
    for name, item in sorted(config.tasks.items()):
        if item.provider and item.provider.lower() == "mock":
            warnings.append(f"task {name} uses mock provider; mock is intended for tests only")
    return warnings


def _safe_json(path: Path) -> object:
    if not path.exists():
        return {}
    try:
        return load_json(path)
    except Exception:
        return {}


def _collect_env_names(value: object) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"api_key_env", "base_url_env"} and isinstance(item, str):
                names.add(item)
            names.update(_collect_env_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(_collect_env_names(item))
    return names


def _sanitize_config(value: object) -> object:
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            if key in {"api_key", "token", "secret"}:
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = _sanitize_config(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_config(item) for item in value]
    if isinstance(value, str):
        return _safe_error(value)
    return value


def _safe_workspace_file(root: Path, rel_path: str) -> Path:
    _require_workspace(root)
    if not rel_path or Path(rel_path).is_absolute():
        raise PermissionError("file must be a relative workspace path")
    path = (root / rel_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PermissionError("file must stay inside the workspace") from exc
    rel = _relative(root, path)
    if not _is_safe_file_rel_path(rel, path):
        raise PermissionError(f"file is not readable through the Web API: {rel_path}")
    return path


def _locate_quote(content: str, quote: str) -> dict[str, int] | None:
    if not quote:
        return None
    index = content.find(quote)
    if index < 0:
        compact_content = _compact_text(content)
        compact_quote = _compact_text(quote)
        if not compact_quote:
            return None
        compact_index = compact_content.find(compact_quote)
        if compact_index < 0:
            return None
        return {"line": 1, "column": compact_index + 1, "start_offset": compact_index, "end_offset": compact_index + len(compact_quote)}
    line = content.count("\n", 0, index) + 1
    line_start = content.rfind("\n", 0, index) + 1
    return {
        "line": line,
        "column": index - line_start + 1,
        "start_offset": index,
        "end_offset": index + len(quote),
    }


def _compact_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value).lower()


def _is_allowed_chapter_version_name(file_name: str, target: str) -> bool:
    return is_allowed_chapter_version_name(file_name, target)


def _next_version_path(chapter_dir: Path, target: str) -> Path:
    return next_chapter_version_path(chapter_dir, target)


def _new_revision_id() -> str:
    return new_request_id("revision")


def _append_web_revision_log(path: Path, chapter_number: int, record: RevisionRecord) -> None:
    if path.exists():
        log = load_json_model(path, RevisionLog)
        if log.chapter_number != chapter_number:
            raise WebAPIError("invalid_revision_log", "revision_log chapter_number does not match", status=400)
    else:
        log = RevisionLog(chapter_number=chapter_number, revisions=[])
    updated = log.model_copy(update={"revisions": [*log.revisions, record]})
    backup_if_exists(path, reason="web_revision_log")
    atomic_write_model_json(path, updated)


def _is_archived_chapter(root: Path, chapter_number: int) -> bool:
    archive_dir = root / "memory" / "archive"
    if not archive_dir.exists():
        return False
    chapter_fragment = f"chapters/{chapter_number:03d}/"
    for manifest_path in archive_dir.glob("session_*/manifest.json"):
        try:
            data = load_json(manifest_path)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        entries = data.get("entries")
        if isinstance(entries, list) and any(chapter_fragment in str(item) for item in entries):
            return True
        if chapter_fragment in json.dumps(data, ensure_ascii=False):
            return True
    return False


def _clean_agent_config_patch(
    patch: dict[object, object],
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
        key_text = str(key)
        if key_text not in allowed:
            raise WebAPIError("invalid_provider_config_field", f"field is not editable: {key_text}", status=400)
        if not allow_task_only_fields and key_text in TASK_ONLY_CONFIG_FIELDS:
            raise WebAPIError(
                "invalid_provider_config_field",
                f"default/profile config field is task-only: {key_text}; use tasks.<task> overrides",
                status=400,
            )
        if key_text in {"api_key", "token", "secret"}:
            raise WebAPIError("unsafe_config_secret", "raw secret fields are not allowed", status=400)
        if key_text == "inherit_default" and not isinstance(value, bool):
            raise WebAPIError("invalid_provider_config_field", "inherit_default must be a boolean", status=400)
        cleaned[key_text] = value
    return cleaned


def _is_safe_tree_path(rel_path: str, path: Path) -> bool:
    parts = Path(rel_path).parts
    if any(part in EXCLUDED_DIRS for part in parts):
        return False
    if any(part.startswith(".env") for part in parts):
        return False
    if path.name in EXCLUDED_FILENAMES:
        return False
    if path.name.startswith(".") and path.is_file():
        return False
    if path.is_file() and not _is_safe_file_rel_path(rel_path, path):
        return False
    return True


def _is_safe_file_rel_path(rel_path: str, path: Path) -> bool:
    parts = Path(rel_path).parts
    if any(part in EXCLUDED_DIRS for part in parts):
        return False
    if any(part.startswith(".env") for part in parts):
        return False
    if path.name in EXCLUDED_FILENAMES:
        return False
    if ".bak_" in path.name:
        return False
    return path.suffix in SAFE_FILE_SUFFIXES


def _require_workspace(root: Path) -> None:
    if not (root / "project.yaml").exists():
        raise WebAPIError("invalid_project", f"{root} does not look like a novel workspace", status=400)


def _json_body(body: bytes | str | None) -> dict[str, object]:
    if not body:
        return {}
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("request body must be a JSON object")
    return data


def _root_from_query(query: dict[str, str]) -> Path:
    return Path(query.get("path") or ".").expanduser().resolve()


def _root_from_body(data: dict[str, object]) -> Path:
    return Path(str(data.get("path") or ".")).expanduser().resolve()


def _init_project_root_from_body(data: dict[str, object]) -> Path:
    raw_path = str(data.get("path") or "").strip()
    path = Path(raw_path or "未命名小说").expanduser()
    if not path.is_absolute():
        path = _default_project_parent() / path
    return path.resolve()


def _chapter_number(data: dict[str, object]) -> int:
    raw_value = data.get("chapter_number") or 0
    if not isinstance(raw_value, (int, str)):
        raise ValueError("chapter_number must be a positive integer")
    value = int(raw_value)
    if value < 1:
        raise ValueError("chapter_number must be a positive integer")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _memory_change_stage(value: object) -> MemoryChangeStage:
    text = _optional_string(value)
    if text in {"pre_creation", "outline_discussion", "content_review", "post_chapter", "unknown"}:
        return cast(MemoryChangeStage, text)
    return "unknown"


def _vector_context_mode(data: dict[str, object]) -> VectorContextMode:
    if bool(data.get("use_vector_context")):
        return "on"
    value = _optional_string(data.get("vector_context"))
    if value in {"auto", "on", "off"}:
        return cast(VectorContextMode, value)
    return "auto"


def _polish_mode(data: dict[str, object]) -> PolishMode | None:
    value = _optional_string(data.get("polish_mode"))
    if not value:
        return None
    normalized = value.replace("-", "_")
    if normalized in {"single_pass", "auto", "review_gate"}:
        return cast(PolishMode, normalized)
    return None


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    if not isinstance(value, (int, str)):
        raise ValueError(f"expected integer-compatible value, got {type(value).__name__}")
    return int(value)


def _optional_float(value: object, default: float) -> float:
    if value in (None, ""):
        return default
    if not isinstance(value, (int, float, str)):
        raise ValueError(f"expected float-compatible value, got {type(value).__name__}")
    return float(value)


def _provider_name(value: object) -> ProviderName:
    provider = str(value or "config")
    if provider not in {"config", "mock", "openai", "openai_compatible", "deepseek", "zai"}:
        raise ValueError(f"unsupported provider: {provider}")
    return cast(ProviderName, provider)


def _audit_focus(value: object) -> tuple[
    Literal["canon", "state", "timeline", "style", "plot", "character_voice", "premature_reveal"],
    ...,
]:
    allowed = {"canon", "state", "timeline", "style", "plot", "character_voice", "premature_reveal"}
    if not isinstance(value, list):
        return ()
    focus: list[Literal["canon", "state", "timeline", "style", "plot", "character_voice", "premature_reveal"]] = []
    for item in value:
        text = str(item)
        if text in allowed:
            focus.append(cast(Literal["canon", "state", "timeline", "style", "plot", "character_voice", "premature_reveal"], text))
    return tuple(focus)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _required_string(value: object, field_name: str) -> str:
    text = _optional_string(value)
    if text is None:
        raise WebAPIError("invalid_request", f"{field_name} is required", status=400)
    return text


def _configured_web_port(root: Path) -> int:
    project_path = root / "project.yaml"
    if not project_path.exists():
        return 8765
    try:
        data = load_yaml(project_path)
    except Exception:
        return 8765
    web = data.get("web") if isinstance(data, dict) else None
    if isinstance(web, dict):
        try:
            return int(web.get("default_port") or 8765)
        except (TypeError, ValueError):
            return 8765
    return 8765


def _current_web_endpoint(values: Mapping[str, object]) -> tuple[str | None, int | None]:
    env_host, env_port = web_launcher.current_web_endpoint_from_env()
    current_host = _optional_string(values.get("current_host")) or env_host
    current_port = _optional_int(values.get("current_port")) or env_port
    return current_host, current_port


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _default_canon_proposal_path(root: Path) -> Path:
    return root / "runs" / f"canon_proposal_{utc_timestamp()}.json"


def _runtime_summary() -> dict[str, object]:
    conda_env = os.environ.get("CONDA_DEFAULT_ENV")
    virtual_env = os.environ.get("VIRTUAL_ENV")
    prefix_name = Path(sys.prefix).name
    environment = conda_env or (Path(virtual_env).name if virtual_env else prefix_name)
    managed = bool(re.fullmatch(r"WriterYang_\d{6}(?:\d{2})?", environment or ""))
    source = "conda" if conda_env else ("venv" if virtual_env else "python")
    current_host, current_port = web_launcher.current_web_endpoint_from_env()
    launcher_config_path = web_launcher.launcher_config_path_from_env()
    default_port = current_port or 8765
    try:
        launcher_config = web_launcher.load_web_launcher_config(
            launcher_config_path,
            default_host=current_host or "127.0.0.1",
            default_port=default_port,
        )
        launcher_host = launcher_config.host
        launcher_port = launcher_config.port
        launcher_config_valid = True
    except Exception:
        launcher_host = current_host or "127.0.0.1"
        launcher_port = default_port
        launcher_config_valid = False
    return {
        "python": sys.executable,
        "python_prefix": sys.prefix,
        "environment": environment,
        "environment_source": source,
        "version": __version__,
        "managed_install": managed,
        "default_project_parent": str(_default_project_parent()),
        "current_web_host": current_host,
        "current_web_port": current_port,
        "launcher_config_path": str(launcher_config_path),
        "launcher_config_host": launcher_host,
        "launcher_config_port": launcher_port,
        "launcher_config_valid": launcher_config_valid,
        "launcher_port_matches_current": bool(
            current_port == launcher_port and current_host and current_host == launcher_host
        ),
        "launcher_port_fallback": os.environ.get(web_launcher.WEB_PORT_FALLBACK_ENV) == "1",
        "warning": "" if managed else "当前 Web UI 可能不是从 WriterYang 专用环境启动的，建议使用安装脚本生成的 WriterYang_WebUI.command 启动。",
    }


def _default_project_parent() -> Path:
    return (Path.home() / "WriterYang").expanduser()


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _safe_error(exc: Exception | str) -> str:
    env_secrets = tuple(
        value
        for key, value in os.environ.items()
        if value and ("KEY" in key or "TOKEN" in key or "SECRET" in key)
    )
    return redact_secret_text(str(exc), extra_secrets=env_secrets)


__all__ = [
    "APIResponse",
    "WebPostHandler",
    "RootResolver",
    "PostRoute",
    "SAFE_FILE_SUFFIXES",
    "EXCLUDED_DIRS",
    "EXCLUDED_FILENAMES",
    "EDITABLE_PROFILE_NAMES",
    "EDITABLE_TASK_NAMES",
    "STYLE_GUIDE_RELATIVE_PATH",
    "WebErrorPayload",
    "WebResponsePayload",
    "WebAPIError",
    "_safe_config_file",
    "_agent_config_warnings",
    "_safe_json",
    "_collect_env_names",
    "_sanitize_config",
    "_safe_workspace_file",
    "_locate_quote",
    "_compact_text",
    "_is_allowed_chapter_version_name",
    "_next_version_path",
    "_new_revision_id",
    "_append_web_revision_log",
    "_is_archived_chapter",
    "_clean_agent_config_patch",
    "_is_safe_tree_path",
    "_is_safe_file_rel_path",
    "_require_workspace",
    "_json_body",
    "_root_from_query",
    "_root_from_body",
    "_init_project_root_from_body",
    "_chapter_number",
    "_optional_string",
    "_string_list",
    "_memory_change_stage",
    "_vector_context_mode",
    "_polish_mode",
    "_optional_int",
    "_optional_float",
    "_provider_name",
    "_audit_focus",
    "_truthy",
    "_required_string",
    "_configured_web_port",
    "_current_web_endpoint",
    "_split_csv",
    "_default_canon_proposal_path",
    "_runtime_summary",
    "_default_project_parent",
    "_relative",
    "_safe_error",
]
