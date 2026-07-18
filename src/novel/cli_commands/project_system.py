from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

import novel.core.web_launcher as web_launcher
from novel.cli_shared import (
    _dispatch_cli_command,
    _failure,
    _format_usage_summary,
    _print_json,
    _quiet,
    _resolve_web_port,
    _run_init_setup_guide,
    _should_run_init_guide,
    _status_payload,
    _success,
    _wants_json,
    completion_script,
    format_doctor_result,
    run_doctor,
)
from novel.core.command_bus import DomainError
from novel.core.contracts import (
    ProjectInitCommand,
    ProjectShowCommand,
    ProjectStatusCommand,
    ProjectValidateCommand,
    SchemaExportCommand,
)
from novel.core.inspection import (
    ProjectStatus,
    format_status,
)
from novel.core.setup_guide import (
    SetupGuideError,
)
from novel.core.usage import UsageError, summarize_provider_usage


def _cmd_init(args: argparse.Namespace) -> int:
    try:
        payload = _dispatch_cli_command(
            args,
            Path(args.path).expanduser().resolve(),
            ProjectInitCommand(
                title=args.title,
                project_id=args.project_id,
                language=args.language,
                genre=args.genre,
            ),
        )
        root = Path(str(payload["root"])).resolve()
    except DomainError as exc:
        return _failure(args, exc.message, error_type=exc.code)
    setup_lines: list[str] = []
    open_web = False
    web_port: int | None = None
    if _should_run_init_guide(args):
        try:
            setup_lines, open_web, web_port = _run_init_setup_guide(root)
        except SetupGuideError as exc:
            return _failure(
                args,
                f"Workspace created at {root}, but initial setup failed: {exc}",
                error_type="setup_guide_error",
            )
    elif not getattr(args, "no_guide", False) and not _wants_json(args) and not _quiet(args):
        setup_lines.append("Skipped initial setup guide because this command is not running in an interactive terminal.")

    if open_web and web_port is not None:
        from novel.web_server import WebServerError, run_web_server

        url = f"http://127.0.0.1:{web_port}"
        print(f"Created novel workspace: {root}")
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
            **payload,
            "root": str(root),
            "project_file": str(root / "project.yaml"),
            "setup_guide_ran": bool(setup_lines) and not setup_lines[0].startswith("Skipped"),
            "setup_messages": setup_lines,
            "web_port": web_port,
        },
        [
            f"Created novel workspace: {root}",
            f"Project file: {root / 'project.yaml'}",
            *setup_lines,
        ],
    )

def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        result = _dispatch_cli_command(
            args,
            Path(args.path).expanduser().resolve(),
            ProjectValidateCommand(),
        )
    except DomainError as exc:
        return _failure(args, exc.message, error_type=exc.code)
    valid = bool(result.get("valid"))
    messages_value = result.get("messages")
    messages: list[object] = messages_value if isinstance(messages_value, list) else []
    if _wants_json(args):
        _print_json({"ok": valid, "command": "validate", "validation": result})
        return 0 if valid else 1
    if _quiet(args):
        return 0 if valid else 1
    for message in messages:
        if isinstance(message, dict):
            print(f"{message.get('level')}: {message.get('path')}: {message.get('message')}")

    if valid:
        print(f"Validation passed: {result.get('warning_count', 0)} warning(s)")
        return 0

    print(
        f"Validation failed: {result.get('error_count', 0)} error(s), "
        f"{result.get('warning_count', 0)} warning(s)",
        file=sys.stderr,
    )
    return 1

def _cmd_schema(args: argparse.Namespace) -> int:
    if args.schema_command == "export":
        try:
            payload = _dispatch_cli_command(
                args,
                Path.cwd(),
                SchemaExportCommand(output_path=str(args.output)),
            )
        except DomainError as exc:
            return _failure(args, exc.message, error_type=exc.code)
        return _success(
            args,
            {
                "command": "schema export",
                **payload,
            },
            [f"Wrote {payload['schema_count']} JSON Schema file(s) to {payload['output']}"],
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
        result = _dispatch_cli_command(
            args,
            Path(args.path).expanduser().resolve(),
            ProjectStatusCommand(),
        )
        status_data = result.get("status")
        if not isinstance(status_data, dict):
            raise DomainError("internal_error", "command result is missing status")
        status = ProjectStatus(
            **{
                **status_data,
                "latest_run_log": Path(status_data["latest_run_log"])
                if status_data.get("latest_run_log")
                else None,
            }
        )
    except DomainError as exc:
        return _failure(args, exc.message, error_type=exc.code)
    return _success(
        args,
        {**result, "command": "status", "status": _status_payload(status)},
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
        result = _dispatch_cli_command(
            args,
            Path(args.path).expanduser().resolve(),
            ProjectShowCommand(target=args.target),
        )
        output = str(result.get("output") or "")
    except DomainError as exc:
        return _failure(args, exc.message, error_type=exc.code)
    return _success(
        args,
        {**result, "command": "show", "target": args.target, "output": output},
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
    webbrowser.open(url)


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
