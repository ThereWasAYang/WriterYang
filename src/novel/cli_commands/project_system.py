from __future__ import annotations

# ruff: noqa: F401

import argparse
import getpass
import importlib.util
import json
import os
import re
import sys
import webbrowser
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import cast

from novel.core.auditing import (
    AuditError,
    ChapterAuditOptions,
    audit_chapter,
    load_audit_provider,
    read_audit_instruction,
)
from novel.core.canon import (
    CanonError,
    CanonSuggestOptions,
    apply_canon_proposal,
    format_canon_validation_report,
    load_canon_provider,
    suggest_canon,
)
from novel.core.chapter_memory import (
    ChapterMemoryError,
    ChapterMemoryOptions,
    accepted_chapter_numbers,
    chapter_memory_path,
    generate_chapter_memory,
    load_chapter_memory_provider,
)
from novel.core.env import load_project_env
from novel.core.drafting import (
    ChapterDraftingOptions,
    DraftingError,
    load_drafting_provider,
    read_drafting_instruction,
    write_chapter_draft,
)
from novel.core.inspiration import (
    InspirationError,
    InspirationOptions,
    load_inspiration_provider,
    read_inspiration_input,
    run_inspiration_agent,
)
from novel.core.planning import (
    ChapterPlanningOptions,
    PlanningError,
    load_planning_provider,
    plan_chapter,
    read_planning_instruction,
)
from novel.core.polishing import (
    ChapterPolishingOptions,
    PolishingError,
    load_polishing_provider,
    polish_chapter,
    read_polishing_instruction,
    resolve_edit_mode,
)
from novel.core.revision import (
    ChapterRevisionOptions,
    RevisionError,
    RevisionLoopOptions,
    load_revision_provider,
    read_revision_instruction,
    revise_chapter,
    revise_chapter_loop,
)
from novel.core.management import load_management_events
from novel.core.memory_repair import (
    MemoryRepairError,
    SettingChangeSuggestionResult,
    answer_setting_change_clarification,
    apply_memory_repair,
    suggest_memory_repair,
    suggest_setting_change_interactive,
)
from novel.core.search import SearchError, rebuild_search_index, refresh_search_index, search_index_status, search_project
from novel.core.session import (
    CreationSessionError,
    SessionActionOptions,
    SessionInstructionOptions,
    SessionResult,
    SessionRunOptions,
    SessionStartOptions,
    SessionRewriteControlOptions,
    accept_session,
    approve_outline,
    archive_session,
    load_rewrite_events,
    parse_range,
    retry_rewrite,
    revise_audit,
    revise_content,
    revise_outline,
    run_session,
    show_session,
    start_session,
    undo_rewrite,
)
from novel.core.state_update import (
    AcceptChapterOptions,
    StateUpdateApplyOptions,
    StateUpdateError,
    StateUpdateProposeOptions,
    accept_chapter,
    apply_state_update,
    load_state_update_provider,
    propose_state_update,
    read_state_update_instruction,
)
from novel.core.exporting import (
    DocxExportOptions,
    ExportError,
    MarkdownExportOptions,
    export_docx,
    export_markdown,
    parse_chapter_selector,
)
from novel.core.inspection import (
    ProjectReadError,
    format_characters,
    format_canon,
    format_state,
    format_status,
    format_timeline,
    get_project_status,
)
from novel.core.json_schema import export_json_schemas
from novel.core.io import load_json_model, load_yaml, load_yaml_model
from novel.core.locking import ProjectLock, ProjectLockError
from novel.core.migration import MigrationError, migrate_project
from novel.core.orchestrator import (
    OrchestratorError,
    OrchestratorOptions,
    decide_ask_intent,
    format_orchestrator_plan,
    handoff_rules_text,
    orchestrate,
)
from novel.core.provider_config import ProviderOverrides, describe_agent_provider, default_agent_config_path
from novel.core.security import scan_security
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
    CreationSession,
    MemoryChangeStage,
    PolishMode,
    ProjectConfig,
    VectorContextMode,
)
from novel.core.usage import UsageError, summarize_provider_usage
import novel.core.web_launcher as web_launcher
from novel.core.workspace import InitOptions, WorkspaceExistsError, init_workspace
from novel.core.validation import validate_canon, validate_project
from novel.core.workflow import (
    GenerateChapterOptions,
    WorkflowError,
    generate_chapter,
    read_workflow_instruction,
)
from novel import __version__
from novel.cli_shared import (
    _add_agent_runtime_args,
    _add_search_context_args,
    _add_polish_mode_arg,
    _vector_context_mode_from_args,
    _polish_mode_from_arg,
    _audit_issue_lines,
    _management_event_payload,
    _management_event_lines,
    _severity_rank,
    _extract_chapter_from_text,
    _extract_repair_id,
    _resolve_memory_repair_proposal_arg,
    _print_dry_run_provider,
    _add_integration_args,
    _add_integration_args_recursive,
    _apply_project_alias,
    _wants_json,
    _quiet,
    _success,
    _failure,
    _print_json,
    _safe_message,
    _command_lock,
    _validation_payload,
    _status_payload,
    _format_usage_summary,
    _resolve_web_port,
    _should_run_init_guide,
    _run_init_setup_guide,
    _prompt_text,
    _prompt_yes_no,
    completion_script,
    run_doctor,
    format_doctor_result,
    _doctor_check,
    _doctor_env_checks,
    _doctor_agent_config_checks,
    _collect_env_names,
    _repo_root,
)

def _cmd_init(args: argparse.Namespace) -> int:
    options = InitOptions(
        title=args.title,
        root=Path(args.path),
        project_id=args.project_id,
        language=args.language,
        genre=args.genre,
    )
    try:
        result = init_workspace(options)
    except WorkspaceExistsError as exc:
        return _failure(args, str(exc), error_type="workspace_exists")
    setup_lines: list[str] = []
    open_web = False
    web_port: int | None = None
    if _should_run_init_guide(args):
        try:
            setup_lines, open_web, web_port = _run_init_setup_guide(result.root)
        except SetupGuideError as exc:
            return _failure(
                args,
                f"Workspace created at {result.root}, but initial setup failed: {exc}",
                error_type="setup_guide_error",
            )
    elif not getattr(args, "no_guide", False) and not _wants_json(args) and not _quiet(args):
        setup_lines.append("Skipped initial setup guide because this command is not running in an interactive terminal.")

    if open_web and web_port is not None:
        from novel.web_server import WebServerError, run_web_server

        url = f"http://127.0.0.1:{web_port}"
        print(f"Created novel workspace: {result.root}")
        for line in setup_lines:
            print(line)
        print(f"Web UI: {url}")
        _open_browser(url)
        try:
            run_web_server(host="127.0.0.1", port=web_port)
        except WebServerError as exc:
            return _failure(args, str(exc), error_type="web_error")
        return 0

    return _success(
        args,
        {
            "command": "init",
            "root": str(result.root),
            "project_file": str(result.root / "project.yaml"),
            "setup_guide_ran": bool(setup_lines) and not setup_lines[0].startswith("Skipped"),
            "setup_messages": setup_lines,
            "web_port": web_port,
        },
        [
            f"Created novel workspace: {result.root}",
            f"Project file: {result.root / 'project.yaml'}",
            *setup_lines,
        ],
    )

def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate_project(Path(args.path))
    payload = _validation_payload(report)
    if _wants_json(args):
        _print_json({"ok": report.ok, "command": "validate", "validation": payload})
        return 0 if report.ok else 1
    if _quiet(args):
        return 0 if report.ok else 1
    for message in report.messages:
        path = message.path
        try:
            path = path.relative_to(report.root)
        except ValueError:
            pass
        print(f"{message.level}: {path}: {message.message}")

    if report.ok:
        print(f"Validation passed: {len(report.warnings)} warning(s)")
        return 0

    print(
        f"Validation failed: {len(report.errors)} error(s), "
        f"{len(report.warnings)} warning(s)",
        file=sys.stderr,
    )
    return 1

def _cmd_migrate(args: argparse.Namespace) -> int:
    try:
        with _command_lock(args, Path(args.path), "migrate", enabled=not args.dry_run):
            result = migrate_project(Path(args.path), dry_run=args.dry_run)
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except MigrationError as exc:
        return _failure(args, str(exc), error_type="migration_error")
    payload = {
        "command": "migrate",
        "root": str(result.root),
        "changed": result.changed,
        "from_version": result.from_version,
        "to_version": result.to_version,
        "updated_files": [str(path) for path in result.updated_files],
        "dry_run": args.dry_run,
    }
    lines = [
        f"Schema version: {result.from_version or 'missing'} -> {result.to_version}",
        "Migration required." if result.changed else "Already up to date.",
    ]
    if result.changed:
        action = "Would update" if args.dry_run else "Updated"
        lines.extend(f"{action}: {path}" for path in result.updated_files)
    return _success(args, payload, lines)

def _cmd_schema(args: argparse.Namespace) -> int:
    if args.schema_command == "export":
        paths = export_json_schemas(args.output)
        return _success(
            args,
            {
                "command": "schema export",
                "output": str(args.output),
                "schema_count": len(paths),
                "files": [str(path) for path in paths],
            },
            [f"Wrote {len(paths)} JSON Schema file(s) to {args.output}"],
        )
    return _failure(args, f"unknown schema command: {args.schema_command}", code=2)

def _cmd_completion(args: argparse.Namespace) -> int:
    script = completion_script(args.shell)
    if _wants_json(args):
        _print_json({"ok": True, "command": "completion", "shell": args.shell, "script": script})
    elif not _quiet(args):
        print(script, end="" if script.endswith("\n") else "\n")
    return 0

def _cmd_doctor(args: argparse.Namespace) -> int:
    result = run_doctor(Path(args.path))
    payload = {"command": "doctor", **result}
    lines = format_doctor_result(result)
    if result["error_count"]:
        if _wants_json(args):
            _print_json({"ok": False, **payload})
            return 1
        if not _quiet(args):
            for line in lines:
                print(line)
        return 1
    return _success(args, payload, lines)

def _cmd_status(args: argparse.Namespace) -> int:
    try:
        status = get_project_status(Path(args.path))
    except ProjectReadError as exc:
        return _failure(args, str(exc), error_type="project_read_error")
    return _success(
        args,
        {"command": "status", "status": _status_payload(status)},
        [format_status(status, Path(args.path))],
    )

def _cmd_usage(args: argparse.Namespace) -> int:
    try:
        summary = summarize_provider_usage(Path(args.path))
    except UsageError as exc:
        return _failure(args, str(exc), error_type="usage_error")
    payload: dict[str, object] = {"command": "usage", "usage": summary.as_dict()}
    lines = _format_usage_summary(summary.as_dict())
    return _success(args, payload, lines)

def _cmd_show(args: argparse.Namespace) -> int:
    try:
        if args.target == "characters":
            output = format_characters(Path(args.path))
        elif args.target == "timeline":
            output = format_timeline(Path(args.path))
        elif args.target == "canon":
            output = format_canon(Path(args.path))
        else:
            output = format_state(Path(args.path))
    except ProjectReadError as exc:
        return _failure(args, str(exc), error_type="project_read_error")
    return _success(
        args,
        {"command": "show", "target": args.target, "output": output},
        [output],
    )

def _cmd_web(args: argparse.Namespace) -> int:
    from novel.web_server import WebServerError, run_web_server

    try:
        port = _resolve_web_port(args.path, args.port)
        if args.open_browser:
            _open_browser(f"http://{args.host}:{port}")
        run_web_server(host=args.host, port=port)
    except Exception as exc:
        error_type = "web_error"
        if isinstance(exc, WebServerError):
            return _failure(args, str(exc), error_type=error_type)
        return _failure(args, f"Web UI 启动失败：{exc}", error_type=error_type)
    return 0


def _cmd_web_launch(args: argparse.Namespace) -> int:
    from novel.web_server import WebServerError, run_web_server

    config_path = Path(args.config).expanduser().resolve()
    try:
        config = web_launcher.load_web_launcher_config(config_path)
        requested_port = config.port
        selected_port = requested_port
        fallback = False
        if not web_launcher.is_port_available(config.host, requested_port):
            start_port = requested_port + 1 if requested_port < 65535 else 8765
            selected_port = web_launcher.find_available_port(host=config.host, start_port=start_port)
            fallback = True
            print(
                f"端口 {requested_port} 已被占用，已临时改用 {selected_port}。"
                "建议在 Web UI 中重新保存端口配置。"
            )
        os.environ[web_launcher.WEB_LAUNCHER_CONFIG_ENV] = str(config_path)
        os.environ[web_launcher.WEB_PORT_FALLBACK_ENV] = "1" if fallback else "0"
        url = f"http://{config.host}:{selected_port}"
        if args.open_browser:
            _open_browser_when_ready(url)
        run_web_server(host=config.host, port=selected_port)
    except Exception as exc:
        error_type = "web_error"
        if isinstance(exc, (WebServerError, web_launcher.WebLauncherError)):
            return _failure(args, str(exc), error_type=error_type)
        return _failure(args, f"Web UI 启动失败：{exc}", error_type=error_type)
    return 0


def _open_browser(url: str) -> None:
    import novel.cli as cli_module

    cli_module.webbrowser.open(url)


def _open_browser_when_ready(url: str, timeout_seconds: float = 15.0) -> None:
    import socket
    import threading
    import time
    from urllib.parse import urlparse

    def worker() -> None:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=0.2):
                    _open_browser(url)
                    return
            except OSError:
                time.sleep(0.1)

    threading.Thread(target=worker, daemon=True).start()
