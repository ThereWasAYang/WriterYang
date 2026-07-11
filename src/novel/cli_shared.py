from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
import re
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import cast

from novel.core.agent_defaults import PROFILE_NAMES
from novel.core.audit_localization import localize_audit_issue_for_author
from novel.core.env import load_project_env
from novel.core.management import load_management_events
from novel.core.io import load_yaml, load_yaml_model
from novel.core.locking import ProjectLock
from novel.core.provider_config import ProviderOverrides, describe_agent_provider, default_agent_config_path
from novel.core.security import redact_secret_text, scan_security
from novel.core.setup_guide import (
    SetupGuideError,
    configure_default_provider,
    configure_embedding_provider,
    configure_web_port,
    find_available_port,
    is_port_available,
)
from novel.core.schemas import (
    AgentsConfig,
    AuditReport,
    PolishMode,
    ProjectConfig,
    VectorContextMode,
)
from novel.core.validation import validate_project
from novel.core.command_bus import command_result_payload, dispatch_command, new_command_envelope
from novel.core.contracts import PublicCommand, Surface

ERROR_CODES = {
    "audit_error": "Audit generation or validation failed.",
    "canon_error": "Canon operation failed.",
    "drafting_error": "Chapter drafting failed.",
    "export_error": "Export operation failed.",
    "inspiration_error": "Inspiration generation failed.",
    "migration_error": "Schema migration failed.",
    "orchestrator_error": "Orchestrator request failed.",
    "memory_repair_error": "Memory repair proposal or apply failed.",
    "chapter_memory_error": "Chapter memory generation or loading failed.",
    "planning_error": "Chapter planning failed.",
    "polishing_error": "Chapter polishing failed.",
    "project_read_error": "Project data could not be read.",
    "revision_error": "Chapter revision failed.",
    "search_error": "Search index or query failed.",
    "session_error": "Creation session operation failed.",
    "setup_guide_error": "Project initial setup guide failed.",
    "state_update_error": "State/timeline update failed.",
    "usage_error": "Provider usage statistics could not be read.",
    "validation_failed": "Project validation failed.",
    "web_error": "Web UI could not start.",
    "workflow_error": "Chapter workflow failed.",
    "workspace_exists": "Workspace initialization would overwrite data.",
    "doctor_failed": "Doctor checks found blocking errors.",
    "secret_detected": "Secret scanner found a raw secret-looking value.",
    "invalid_env_example": ".env.example contains a non-empty or invalid value.",
    "unsafe_config_secret": "Config contains a likely literal secret instead of an env var name.",
    "project_locked": "Project workspace is locked by another writer process.",
    "atomic_write_failed": "Atomic file write failed.",
    "backup_failed": "File backup failed.",
    "error": "Generic command error.",
}


def _add_agent_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent-config",
        type=Path,
        default=None,
        help="Agent model config file. Defaults to config/agents.yaml in the workspace.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Temporarily override the configured model name.",
    )
    parser.add_argument(
        "--dry-run-provider",
        action="store_true",
        help="Show the provider configuration that would be used without calling the provider.",
    )

def _add_search_context_args(parser: argparse.ArgumentParser, *, default_enabled: bool = False) -> None:
    if default_enabled:
        parser.add_argument(
            "--no-search-context",
            dest="use_search_context",
            action="store_false",
            default=True,
            help="Disable automatic FTS memory context for this workflow.",
        )
    else:
        parser.add_argument(
            "--use-search-context",
            action="store_true",
            help="Add explainable FTS memory context to the agent prompt.",
        )
    parser.add_argument(
        "--vector-context",
        choices=("auto", "on", "off"),
        default="auto",
        help="Embedding semantic context mode for agent memory retrieval. Defaults to auto.",
    )
    parser.add_argument(
        "--use-vector-context",
        action="store_true",
        help="Compatibility alias for --vector-context on.",
    )

def _add_polish_mode_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--polish-mode",
        choices=("single-pass", "auto", "review-gate"),
        default=None,
        help="Finalization mode. Defaults to project polish.mode or single-pass.",
    )

def _vector_context_mode_from_args(args: argparse.Namespace) -> VectorContextMode:
    if getattr(args, "use_vector_context", False):
        return "on"
    value = str(getattr(args, "vector_context", "auto") or "auto")
    if value in {"auto", "on", "off"}:
        return cast(VectorContextMode, value)
    return "auto"

def _polish_mode_from_arg(value: str | None) -> PolishMode | None:
    if not value:
        return None
    normalized = value.replace("-", "_")
    if normalized in {"single_pass", "auto", "review_gate"}:
        return cast(PolishMode, normalized)
    return None

def _audit_issue_lines(report: AuditReport) -> list[str]:
    if not report.issues:
        return []
    lines = ["Audit 问题："]
    for issue in sorted(report.issues, key=lambda item: _severity_rank(item.severity), reverse=True):
        localized = localize_audit_issue_for_author(issue)
        lines.append(f"- [{localized.severity}/{localized.type}] {localized.id}: {localized.description}")
        if localized.suggested_fix:
            lines.append(f"  建议修复：{localized.suggested_fix}")
    if all(issue.severity == "low" for issue in report.issues):
        lines.append("低级别问题不会自动修复；可按需使用 revision-session 或 session revise-content 生成修订版。")
    return lines

def _management_event_payload(root: Path) -> list[dict[str, object]]:
    return [event.model_dump(mode="json") for event in load_management_events(root, limit=5)]

def _management_event_lines(root: Path) -> list[str]:
    events = load_management_events(root, limit=5)
    if not events:
        return []
    lines = ["Recent background management events:"]
    for event in events:
        targets = ", ".join(event.target_files) if event.target_files else "none"
        lines.append(f"- [{event.status}/{event.event_type}] {event.message} targets={targets}")
    return lines

def _severity_rank(severity: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(severity, 0)

def _extract_chapter_from_text(text: str) -> int | None:
    match = re.search(r"第\s*(\d+)\s*章", text)
    if match:
        return int(match.group(1))
    match = re.search(r"chapter\s*(\d+)", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def _extract_repair_id(text: str) -> str | None:
    match = re.search(r"\brepair_[0-9]{8}_[0-9]{6}_[0-9]{6}\b", text)
    return match.group(0) if match else None

def _resolve_memory_repair_proposal_arg(value: str) -> Path:
    repair_id = _extract_repair_id(value)
    if repair_id and value.strip() == repair_id:
        return Path("memory") / "repairs" / repair_id / "proposal.json"
    return Path(value)

def _print_dry_run_provider(
    root: Path,
    agent_config: Path | None,
    provider_name: str,
    model_name: str | None,
    tasks: tuple[str, ...],
) -> None:
    path = agent_config or default_agent_config_path(root)
    overrides = ProviderOverrides(provider_name=provider_name, model_name=model_name)
    for index, task_name in enumerate(tasks):
        if index:
            print("")
        print(
            describe_agent_provider(
                path,
                task_name,
                overrides=overrides,
            ).format()
        )

def _add_integration_args(parser: argparse.ArgumentParser) -> None:
    option_strings = {
        option
        for action in parser._actions
        for option in getattr(action, "option_strings", ())
    }
    if "--project" not in option_strings:
        parser.add_argument(
            "--project",
            default=None,
            help="Stable alias for --path, intended for external agent integrations.",
        )
    if "--quiet" not in option_strings:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress human-readable success output.",
        )
    if "--json" not in option_strings:
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output machine-readable JSON.",
        )

def _add_integration_args_recursive(parser: argparse.ArgumentParser) -> None:
    _add_integration_args(parser)
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for subparser in choices.values():
                _add_integration_args_recursive(subparser)

def _apply_project_alias(args: argparse.Namespace) -> None:
    project = getattr(args, "project", None)
    if project is not None and hasattr(args, "path"):
        args.path = project

def _wants_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))

def _quiet(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "quiet", False))

def _success(args: argparse.Namespace, payload: dict[str, object], lines: list[str] | None = None) -> int:
    if _wants_json(args):
        response = {"ok": True, **payload}
        if lines:
            response["messages"] = lines
        _print_json(response)
    elif not _quiet(args):
        for line in lines or []:
            print(line)
    return 0

def _failure(args: argparse.Namespace, message: str, *, code: int = 1, error_type: str = "error") -> int:
    safe = _safe_message(message)
    if _wants_json(args):
        _print_json(
            {
                "ok": False,
                "error": {
                    "type": error_type,
                    "code": error_type,
                    "message": safe,
                    "exit_code": code,
                },
            }
        )
    else:
        print(f"error: {safe}", file=sys.stderr)
    return code

def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))

def _safe_message(message: str) -> str:
    env_secrets = tuple(
        value
        for key, value in os.environ.items()
        if value and ("KEY" in key or "TOKEN" in key or "SECRET" in key)
    )
    return redact_secret_text(message, extra_secrets=env_secrets)

def _command_lock(args: argparse.Namespace, root: Path, task: str, *, enabled: bool = True):
    if not enabled:
        return nullcontext()
    return ProjectLock(root, task=task)


def _dispatch_cli_command(
    args: argparse.Namespace,
    root: Path,
    command: PublicCommand,
    *,
    confirmed: bool = False,
) -> dict[str, object]:
    result = dispatch_command(
        new_command_envelope(
            surface=Surface.CLI,
            project_root=root,
            command=command,
            confirmed=confirmed,
        )
    )
    return command_result_payload(result)

def _validation_payload(report) -> dict[str, object]:
    return {
        "root": str(report.root),
        "ok": report.ok,
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "messages": [
            {
                "level": message.level,
                "path": str(message.path),
                "message": message.message,
            }
            for message in report.messages
        ],
    }

def _status_payload(status) -> dict[str, object]:
    return {
        "title": status.title,
        "latest_chapter": status.latest_chapter,
        "inspiration_exists": status.inspiration_exists,
        "character_count": status.character_count,
        "location_count": status.location_count,
        "item_count": status.item_count,
        "timeline_event_count": status.timeline_event_count,
        "latest_run_log": str(status.latest_run_log) if status.latest_run_log else None,
        "latest_run_summary": status.latest_run_summary,
        "accepted_chapter_count": status.accepted_chapter_count,
    }

def _format_usage_summary(summary: dict[str, object]) -> list[str]:
    total = summary.get("total")
    total = total if isinstance(total, dict) else {}
    last_call = summary.get("last_call")
    lines = [
        "Provider usage:",
        f"Calls: {total.get('call_count', 0)} "
        f"(success: {total.get('success_count', 0)}, failed: {total.get('failed_count', 0)})",
        f"Tokens: total={total.get('total_tokens', 0)}, "
        f"prompt={total.get('prompt_tokens', 0)}, completion={total.get('completion_tokens', 0)}",
        f"Unknown token calls: {total.get('unknown_token_call_count', 0)}",
    ]
    if isinstance(last_call, dict):
        lines.append(
            "Last call: "
            f"{last_call.get('provider', 'unknown')} / {last_call.get('model', 'unknown')} / "
            f"{last_call.get('status', 'unknown')}"
        )
    return lines

def _resolve_web_port(path: str, explicit_port: int | None) -> int:
    if explicit_port is not None:
        return explicit_port
    project_path = Path(path) / "project.yaml"
    if not project_path.exists():
        return 8765
    project = load_yaml_model(project_path, ProjectConfig)
    if project.web:
        return project.web.default_port
    return 8765

def _should_run_init_guide(args: argparse.Namespace) -> bool:
    if getattr(args, "no_guide", False):
        return False
    if getattr(args, "guide", False):
        return True
    if _wants_json(args) or _quiet(args):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()

def _run_init_setup_guide(root: Path) -> tuple[list[str], bool, int]:
    lines = [
        "",
        "项目初始引导",
        "默认 API 需要使用 OpenAI-compatible /chat/completions 格式。",
        "API Key 会写入项目根目录 .env；config/agents.yaml 只保存环境变量名。",
    ]
    print("\n".join(lines))
    output_lines: list[str] = []

    base_url = _prompt_text("默认 API base URL", "https://api.openai.com/v1")
    api_key = getpass.getpass("默认 API Key（输入时不会显示，留空跳过默认 API 配置）: ").strip()
    model = ""
    if api_key:
        while not model:
            model = _prompt_text("默认模型名", "")
            if not model:
                print("模型名必填；没有模型名无法进行连通性测试。")
        try:
            result = configure_default_provider(
                root,
                base_url=base_url,
                api_key=api_key,
                model=model,
                provider="openai_compatible",
                ping=True,
            )
        except SetupGuideError as exc:
            raise SetupGuideError(
                f"默认 API 配置未保存，连通性测试失败：{exc}"
            ) from exc
        output_lines.append(f"默认 API 连通性测试通过：{result.provider} / {result.model}")
        output_lines.append(
            "这组 API 配置已作为所有 profile 的默认配置；"
            "后续可编辑 config/agents.yaml 为 profile 覆盖模型能力参数，或为少数 task 覆盖思考模式、温度等业务参数。"
        )
    else:
        output_lines.append("已跳过默认 API 配置；运行真实 Agent 前需要先配置 config/agents.yaml 和 .env。")

    if _prompt_yes_no("是否配置 embedding API？", default=False):
        embedding_base_url = _prompt_text("Embedding API base URL（OpenAI-compatible /embeddings 格式）", base_url)
        embedding_api_key = getpass.getpass("Embedding API Key（输入时不会显示）: ").strip()
        embedding_model = ""
        while not embedding_model:
            embedding_model = _prompt_text("Embedding 模型名", "")
            if not embedding_model:
                print("Embedding 模型名必填；如暂不配置，请按 Ctrl+C 中止后重新 init --no-guide。")
        if not embedding_api_key:
            raise SetupGuideError("embedding API Key must not be empty")
        try:
            embedding_result = configure_embedding_provider(
                root,
                base_url=embedding_base_url,
                api_key=embedding_api_key,
                model=embedding_model,
                provider="openai_compatible",
                provider_name="configured",
                ping=True,
            )
        except SetupGuideError as exc:
            raise SetupGuideError(f"Embedding 配置未保存，连通性测试失败：{exc}") from exc
        output_lines.append(
            f"Embedding 连通性测试通过：{embedding_result.provider} / {embedding_result.model}"
        )
    else:
        output_lines.append("已跳过 embedding API 配置；关键词/FTS 检索仍可用。")

    recommended_port = find_available_port(8765)
    while True:
        port_text = _prompt_text("CLI Web UI 默认端口", str(recommended_port))
        try:
            requested_port = int(port_text)
        except ValueError:
            print("端口号必须是 1-65535 之间的整数。")
            continue
        if not is_port_available(requested_port):
            replacement = find_available_port(requested_port + 1 if requested_port < 65535 else 8765)
            print(f"端口 {requested_port} 已被占用，将改用 {replacement}。")
            requested_port = replacement
        port_result = configure_web_port(root, requested_port=requested_port)
        output_lines.append(f"CLI Web UI 默认端口已写入 project.yaml：{port_result.selected_port}")
        open_web = _prompt_yes_no("是否现在打开 Web UI？", default=True)
        return output_lines, open_web, port_result.selected_port

def _prompt_text(label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default

def _prompt_yes_no(label: str, *, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{label} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "是", "好", "打开"}

def completion_script(shell: str) -> str:
    commands = (
        "init validate migrate schema index search ask memory-repair setting-change chapter-memory session revision-session "
        "status usage show inspire canon export preview web doctor completion"
    )
    common_options = "--help --json --quiet --project --path"
    if shell == "bash":
        return (
            "_novel_completion() {\n"
            "  local cur prev\n"
            "  COMPREPLY=()\n"
            "  cur=\"${COMP_WORDS[COMP_CWORD]}\"\n"
            f"  local commands=\"{commands}\"\n"
            f"  local options=\"{common_options} --version\"\n"
            "  if [[ ${COMP_CWORD} -eq 1 ]]; then\n"
            "    COMPREPLY=( $(compgen -W \"$commands $options\" -- \"$cur\") )\n"
            "  else\n"
            "    COMPREPLY=( $(compgen -W \"$commands $options\" -- \"$cur\") )\n"
            "  fi\n"
            "}\n"
            "complete -F _novel_completion novel\n"
        )
    if shell == "zsh":
        return (
            "#compdef novel\n"
            "_novel() {\n"
            "  local -a commands options\n"
            f"  commands=({commands})\n"
            f"  options=({common_options} --version)\n"
            "  _describe 'command' commands || _describe 'option' options\n"
            "}\n"
            "compdef _novel novel\n"
        )
    if shell == "fish":
        lines = ["complete -c novel -f"]
        for command in commands.split():
            lines.append(f"complete -c novel -n '__fish_use_subcommand' -a {command}")
        for option in (common_options + " --version").split():
            lines.append(f"complete -c novel -l {option.removeprefix('--')}")
        return "\n".join(lines) + "\n"
    raise ValueError(f"unsupported shell: {shell}")

def run_doctor(root: Path) -> dict[str, object]:
    root = root.expanduser().resolve()
    checks: list[dict[str, object]] = []
    _doctor_check(checks, "python", "ok", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    for module_name, label, required in (
        ("pydantic", "dependency:pydantic", True),
        ("yaml", "dependency:PyYAML", True),
        ("docx", "dependency:python-docx", True),
        ("playwright", "dependency:playwright", False),
    ):
        exists = importlib.util.find_spec(module_name) is not None
        status = "ok" if exists else ("error" if required else "warning")
        message = "installed" if exists else ("missing required dependency" if required else "missing optional dependency")
        _doctor_check(checks, label, status, message)

    if (root / "project.yaml").exists():
        _doctor_check(checks, "project", "ok", str(root))
        for rel_path in (
            "project.yaml",
            "config/agents.yaml",
            "config/embeddings.yaml",
            "memory/canon/characters.json",
            "memory/state/current_state.json",
            "memory/state/timeline.json",
        ):
            path = root / rel_path
            _doctor_check(
                checks,
                f"file:{rel_path}",
                "ok" if path.exists() else "error",
                "present" if path.exists() else "missing",
            )
        report = validate_project(root)
        _doctor_check(
            checks,
            "validation",
            "ok" if report.ok else "error",
            f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)",
        )
        for config_rel in ("config/agents.yaml", "config/embeddings.yaml"):
            checks.extend(_doctor_env_checks(root / config_rel))
        checks.extend(_doctor_agent_config_checks(root / "config" / "agents.yaml"))
    else:
        _doctor_check(checks, "project", "warning", f"{root} does not look like a novel workspace")

    security_root = _repo_root()
    if security_root:
        security = scan_security(security_root)
        if security.ok:
            _doctor_check(checks, "security", "ok", "no tracked secrets detected")
        else:
            _doctor_check(
                checks,
                "security",
                "error",
                f"{len(security.findings)} security finding(s); run tests for details",
            )

    error_count = sum(1 for check in checks if check["status"] == "error")
    warning_count = sum(1 for check in checks if check["status"] == "warning")
    return {
        "root": str(root),
        "ok": error_count == 0,
        "error_count": error_count,
        "warning_count": warning_count,
        "checks": checks,
        "error_codes": ERROR_CODES,
    }

def format_doctor_result(result: dict[str, object]) -> list[str]:
    lines = [
        f"Doctor: {'passed' if result['ok'] else 'failed'}",
        f"Root: {result['root']}",
        f"Errors: {result['error_count']}; warnings: {result['warning_count']}",
    ]
    checks = result.get("checks", [])
    if not isinstance(checks, list):
        checks = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        lines.append(f"{check['status']}: {check['name']}: {check['message']}")
    return lines

def _doctor_check(checks: list[dict[str, object]], name: str, status: str, message: str) -> None:
    checks.append({"name": name, "status": status, "message": message})

def _doctor_env_checks(path: Path) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    if not path.exists():
        return checks
    try:
        config = load_yaml(path)
    except Exception as exc:
        _doctor_check(checks, f"env:{path.name}", "error", f"could not read config: {exc}")
        return checks
    env = load_project_env(path.parent.parent)
    for env_name in sorted(_collect_env_names(config)):
        _doctor_check(
            checks,
            f"env:{env_name}",
            "ok" if env.get(env_name) else "warning",
            "set" if env.get(env_name) else "not set",
        )
    return checks

def _doctor_agent_config_checks(path: Path) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    if not path.exists():
        return checks
    try:
        config = load_yaml_model(path, AgentsConfig)
    except Exception as exc:
        _doctor_check(checks, "agent-config", "error", f"could not read config/agents.yaml: {exc}")
        return checks
    if config.default is None:
        _doctor_check(
            checks,
            "agent-config:default",
            "warning",
            "default API config is missing; unconfigured profiles cannot use provider config",
        )
    else:
        status = "warning" if config.default.provider.lower() == "mock" else "ok"
        message = (
            "default provider uses mock; mock is intended for tests only"
            if status == "warning"
            else f"default provider is {config.default.provider}"
        )
        _doctor_check(checks, "agent-config:default", status, message)
    for name in PROFILE_NAMES:
        if name not in config.profiles:
            _doctor_check(checks, f"agent-profile:{name}", "warning", "profile config is missing; default will be used")
    for name, config_item in sorted(config.profiles.items()):
        provider = config_item.provider
        if provider and provider.lower() == "mock":
            _doctor_check(
                checks,
                f"agent-profile:{name}",
                "warning",
                "profile uses mock provider; mock is intended for tests only",
            )
    for name, config_item in sorted(config.tasks.items()):
        provider = config_item.provider
        if provider and provider.lower() == "mock":
            _doctor_check(
                checks,
                f"agent-task:{name}",
                "warning",
                "task uses mock provider; mock is intended for tests only",
            )
    return checks

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

def _repo_root() -> Path | None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / ".git").exists():
            return parent
    return None
