#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import webbrowser
from typing import Mapping, Optional, Sequence


PYTHON_VERSION = "3.12"
ENV_PREFIX = "WriterYang"
WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8765


@dataclass(frozen=True)
class InstallPlan:
    mode: str
    env_name: str
    commands: list[list[str]]
    activation_command: str
    verify_commands: list[str]
    web_url: str | None = None
    web_command: list[str] | None = None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create a fresh environment and install WriterYang.")
    parser.add_argument("--dev", action="store_true", help='Install development dependencies with ".[dev]".')
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands without creating an environment.")
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT, help="Starting port for Web UI. Defaults to 8765.")
    parser.add_argument("--no-open-web", action="store_true", help="Start Web UI without opening a browser.")
    parser.add_argument("--no-web", action="store_true", help="Do not start Web UI after installation.")
    parser.add_argument(
        "--venv-root",
        default=".venv",
        help="Directory for venv fallback environments when conda is not available. Defaults to .venv.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    try:
        plan = build_install_plan(
            repo_root=repo_root,
            dev=args.dev,
            venv_root=repo_root / args.venv_root,
            env=os.environ,
            web_port=args.web_port,
            start_web=not args.no_web,
            check_web_port=not args.dry_run,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _print_plan(plan, dry_run=args.dry_run)
    if not args.dry_run:
        for command in plan.commands:
            _run(command, cwd=repo_root)
        if plan.web_url and not args.no_open_web:
            webbrowser.open(plan.web_url)
        if plan.web_command:
            print("\nStarting WriterYang Web UI. Press Ctrl+C to stop.")
            _run(plan.web_command, cwd=repo_root)
    _print_next_steps(plan)
    return 0


def build_install_plan(
    *,
    repo_root: Path,
    dev: bool = False,
    venv_root: Path,
    env: Mapping[str, str],
    now: Optional[datetime] = None,
    web_port: int = DEFAULT_WEB_PORT,
    start_web: bool = True,
    check_web_port: bool = True,
) -> InstallPlan:
    conda = find_conda(env)
    install_target = ".[dev]" if dev else "."
    base_name = dated_env_base_name(now)
    if conda:
        env_name = unique_name(base_name, existing_conda_env_names(conda))
        web = (
            build_web_launch(conda=conda, env_name=env_name, start_port=web_port, check_port=check_web_port)
            if start_web
            else None
        )
        commands = [
            [conda, "create", "-n", env_name, f"python={PYTHON_VERSION}", "-y"],
            [conda, "run", "-n", env_name, "python", "-m", "pip", "install", "--upgrade", "pip"],
            [conda, "run", "-n", env_name, "python", "-m", "pip", "install", install_target],
        ]
        return InstallPlan(
            mode="conda",
            env_name=env_name,
            commands=commands,
            activation_command=f"conda activate {env_name}",
            verify_commands=["novel --version", "novel doctor"],
            web_url=web.url if web else None,
            web_command=web.command if web else None,
        )

    python = find_python312()
    if not python:
        raise RuntimeError("conda was not found, and python3.12 is not available for venv fallback")
    venv_root = venv_root.resolve()
    env_name = unique_name(base_name, existing_venv_names(venv_root))
    venv_path = venv_root / env_name
    venv_python = venv_python_path(venv_path)
    web = build_web_launch(venv_path=venv_path, start_port=web_port, check_port=check_web_port) if start_web else None
    commands = [
        [python, "-m", "venv", str(venv_path)],
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
        [str(venv_python), "-m", "pip", "install", install_target],
    ]
    return InstallPlan(
        mode="venv",
        env_name=env_name,
        commands=commands,
        activation_command=venv_activation_command(venv_path),
        verify_commands=["novel --version", "novel doctor"],
        web_url=web.url if web else None,
        web_command=web.command if web else None,
    )


@dataclass(frozen=True)
class WebLaunch:
    url: str
    command: list[str]


def build_web_launch(
    *,
    start_port: int,
    host: str = WEB_HOST,
    conda: str | None = None,
    env_name: str | None = None,
    venv_path: Path | None = None,
    check_port: bool = True,
) -> WebLaunch:
    port = find_available_port(host, start_port) if check_port else validate_port(start_port)
    url = f"http://{host}:{port}"
    if conda and env_name:
        command = [conda, "run", "-n", env_name, "novel", "web", "--host", host, "--port", str(port)]
    elif venv_path:
        command = [str(venv_novel_path(venv_path)), "web", "--host", host, "--port", str(port)]
    else:
        raise RuntimeError("web launch requires either conda/env_name or venv_path")
    return WebLaunch(url=url, command=command)


def find_available_port(host: str, start_port: int) -> int:
    validate_port(start_port)
    for port in range(start_port, 65536):
        if is_port_available(host, port):
            return port
    raise RuntimeError(f"no available Web UI port from {start_port} to 65535")


def validate_port(port: int) -> int:
    if port < 1 or port > 65535:
        raise RuntimeError(f"web port must be between 1 and 65535: {port}")
    return port


def is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def find_conda(env: Mapping[str, str]) -> Optional[str]:
    conda_exe = env.get("CONDA_EXE")
    if conda_exe and Path(conda_exe).exists():
        return conda_exe
    return shutil.which("conda")


def existing_conda_env_names(conda: str) -> set[str]:
    completed = subprocess.run(
        [conda, "env", "list", "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"could not list conda environments: {completed.stderr.strip() or completed.stdout.strip()}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("could not parse conda env list output") from exc
    envs = payload.get("envs")
    if not isinstance(envs, list):
        raise RuntimeError("conda env list output does not contain an envs list")
    return {Path(str(path)).name for path in envs}


def find_python312() -> Optional[str]:
    return shutil.which("python3.12")


def existing_venv_names(venv_root: Path) -> set[str]:
    if not venv_root.exists():
        return set()
    return {path.name for path in venv_root.iterdir() if path.is_dir()}


def dated_env_base_name(now: Optional[datetime] = None) -> str:
    value = now or datetime.now()
    return f"{ENV_PREFIX}_{value:%y%m%d}"


def unique_name(base_name: str, existing_names: set[str]) -> str:
    if base_name not in existing_names:
        return base_name
    for index in range(1, 100):
        candidate = f"{base_name}{index:02d}"
        if candidate not in existing_names:
            return candidate
    raise RuntimeError(f"could not find available environment name for {base_name}")


def venv_python_path(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def venv_novel_path(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / "novel.exe"
    return venv_path / "bin" / "novel"


def venv_activation_command(venv_path: Path) -> str:
    if os.name == "nt":
        return str(venv_path / "Scripts" / "activate")
    return f"source {shlex.quote(str(venv_path / 'bin' / 'activate'))}"


def _print_plan(plan: InstallPlan, *, dry_run: bool) -> None:
    prefix = "[dry-run] " if dry_run else ""
    print(f"{prefix}mode: {plan.mode}")
    print(f"{prefix}environment: {plan.env_name}")
    for command in plan.commands:
        print(f"{prefix}$ {shlex.join(command)}")
    if plan.web_url and plan.web_command:
        print(f"{prefix}web_url: {plan.web_url}")
        print(f"{prefix}web_command: {shlex.join(plan.web_command)}")


def _print_next_steps(plan: InstallPlan) -> None:
    print("\nNext steps:")
    print(f"  {plan.activation_command}")
    for command in plan.verify_commands:
        print(f"  {command}")
    if plan.web_url:
        print(f"  Web UI: {plan.web_url}")
        print("  Stop Web UI with Ctrl+C")


def _run(command: list[str], *, cwd: Path) -> None:
    print(f"$ {shlex.join(command)}")
    subprocess.run(command, cwd=cwd, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
