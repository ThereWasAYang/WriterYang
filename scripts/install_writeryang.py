#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import shlex
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse
import webbrowser
from typing import Mapping, Optional, Sequence


PYTHON_VERSION = "3.12"
ENV_PREFIX = "WriterYang"
WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8765


@dataclass(frozen=True)
class ShellLaunch:
    command: list[str]
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class InstallPlan:
    mode: str
    env_name: str
    commands: list[list[str]]
    activation_command: str
    verify_commands: list[str]
    web_url: str | None = None
    web_command: list[str] | None = None
    launcher_path: Path | None = None
    launcher_command: list[str] | None = None
    activate_shell: ShellLaunch | None = None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create a fresh environment and install WriterYang.")
    parser.add_argument("--dev", action="store_true", help='Install development dependencies with ".[dev]".')
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands without creating an environment.")
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT, help="Starting port for Web UI. Defaults to 8765.")
    parser.add_argument("--no-open-web", action="store_true", help="Start Web UI without opening a browser.")
    parser.add_argument("--no-web", action="store_true", help="Do not start Web UI after installation.")
    parser.add_argument("--no-activate-shell", action="store_true", help="Do not enter the new environment shell after installation.")
    parser.add_argument(
        "--launcher-path",
        default="WriterYang_WebUI.command",
        help="Path for the generated macOS/Linux Web UI launcher. Defaults to WriterYang_WebUI.command.",
    )
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
            launcher_path=Path(args.launcher_path),
            activate_shell=not args.no_activate_shell,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _print_plan(plan, dry_run=args.dry_run)
    if not args.dry_run:
        for command in plan.commands:
            _run(command, cwd=repo_root)
        if plan.launcher_path and plan.launcher_command:
            _write_web_launcher(plan.launcher_path, plan.launcher_command, cwd=repo_root, url=plan.web_url)
        if plan.web_command:
            print("\nStarting WriterYang Web UI. Press Ctrl+C to stop.")
            _run_web_command(
                plan.web_command,
                cwd=repo_root,
                url=plan.web_url,
                open_browser=not args.no_open_web,
            )
        if plan.activate_shell and is_interactive_terminal(os.environ):
            print("\nEntering the new WriterYang environment shell. Type exit to return.")
            _run(plan.activate_shell.command, cwd=repo_root, env=plan.activate_shell.env)
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
    launcher_path: Path | None = None,
    activate_shell: bool = True,
) -> InstallPlan:
    conda = find_conda(env)
    install_target = ".[dev]" if dev else "."
    base_name = dated_env_base_name(now)
    if conda:
        env_name = unique_name(base_name, existing_conda_env_names(conda))
        env_prefix = conda_env_prefix(conda, env_name)
        env_python = conda_env_python_path(env_prefix)
        web = build_web_launch(conda_env_prefix=env_prefix, start_port=web_port, check_port=check_web_port)
        commands = [
            [conda, "create", "-n", env_name, f"python={PYTHON_VERSION}", "-y"],
            [str(env_python), "-m", "pip", "install", "--upgrade", "pip"],
            [str(env_python), "-m", "pip", "install", install_target],
        ]
        return InstallPlan(
            mode="conda",
            env_name=env_name,
            commands=commands,
            activation_command=f"conda activate {env_name}",
            verify_commands=["novel --version", "novel doctor"],
            web_url=web.url,
            web_command=web.command if start_web else None,
            launcher_path=_resolve_launcher_path(repo_root, launcher_path),
            launcher_command=web.command,
            activate_shell=build_activate_shell(env=env, env_name=env_name, conda_env_prefix=env_prefix)
            if activate_shell
            else None,
        )

    python = find_python312()
    if not python:
        raise RuntimeError("conda was not found, and python3.12 is not available for venv fallback")
    venv_root = venv_root.resolve()
    env_name = unique_name(base_name, existing_venv_names(venv_root))
    venv_path = venv_root / env_name
    venv_python = venv_python_path(venv_path)
    web = build_web_launch(venv_path=venv_path, start_port=web_port, check_port=check_web_port)
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
        web_url=web.url,
        web_command=web.command if start_web else None,
        launcher_path=_resolve_launcher_path(repo_root, launcher_path),
        launcher_command=web.command,
        activate_shell=build_activate_shell(venv_path=venv_path, env=env) if activate_shell else None,
    )


@dataclass(frozen=True)
class WebLaunch:
    url: str
    command: list[str]


def build_web_launch(
    *,
    start_port: int,
    host: str = WEB_HOST,
    conda_env_prefix: Path | None = None,
    venv_path: Path | None = None,
    check_port: bool = True,
) -> WebLaunch:
    port = find_available_port(host, start_port) if check_port else validate_port(start_port)
    url = f"http://{host}:{port}"
    if conda_env_prefix:
        command = [str(conda_env_novel_path(conda_env_prefix)), "web", "--host", host, "--port", str(port)]
    elif venv_path:
        command = [str(venv_novel_path(venv_path)), "web", "--host", host, "--port", str(port)]
    else:
        raise RuntimeError("web launch requires either conda_env_prefix or venv_path")
    return WebLaunch(url=url, command=command)


def build_activate_shell(
    *,
    env: Mapping[str, str],
    env_name: str | None = None,
    conda_env_prefix: Path | None = None,
    venv_path: Path | None = None,
) -> ShellLaunch:
    shell = env.get("SHELL") or shutil.which("zsh") or shutil.which("bash") or "/bin/sh"
    if conda_env_prefix and env_name:
        bin_dir = conda_env_prefix / "bin"
        existing_path = env.get("PATH", "")
        overrides = {
            "CONDA_DEFAULT_ENV": env_name,
            "CONDA_PREFIX": str(conda_env_prefix),
            "PATH": f"{bin_dir}{os.pathsep}{existing_path}" if existing_path else str(bin_dir),
        }
        return ShellLaunch(command=shell_without_startup_files(shell), env=overrides)
    if venv_path:
        bin_dir = venv_path / ("Scripts" if os.name == "nt" else "bin")
        existing_path = env.get("PATH", "")
        overrides = {
            "VIRTUAL_ENV": str(venv_path),
            "PATH": f"{bin_dir}{os.pathsep}{existing_path}" if existing_path else str(bin_dir),
        }
        return ShellLaunch(command=[shell, "-i"], env=overrides)
    raise RuntimeError("activate shell requires either conda/env_name or venv_path")


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


def conda_env_prefix(conda: str, env_name: str) -> Path:
    conda_path = Path(conda).resolve()
    if conda_path.parent.name == "bin":
        return conda_path.parent.parent / "envs" / env_name
    raise RuntimeError(
        f"could not infer conda environment prefix from {conda}; "
        "run the installer from a shell where CONDA_EXE points to conda's bin/conda"
    )


def conda_env_python_path(env_prefix: Path) -> Path:
    if os.name == "nt":
        return env_prefix / "python.exe"
    return env_prefix / "bin" / "python"


def conda_env_novel_path(env_prefix: Path) -> Path:
    if os.name == "nt":
        return env_prefix / "Scripts" / "novel.exe"
    return env_prefix / "bin" / "novel"


def shell_without_startup_files(shell: str) -> list[str]:
    name = Path(shell).name
    if name == "zsh":
        return [shell, "-f", "-i"]
    if name == "bash":
        return [shell, "--noprofile", "--norc", "-i"]
    return [shell, "-i"]


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


def is_interactive_terminal(env: Mapping[str, str]) -> bool:
    if env.get("CI"):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def _resolve_launcher_path(repo_root: Path, launcher_path: Path | None) -> Path:
    path = launcher_path or Path("WriterYang_WebUI.command")
    if not path.is_absolute():
        path = repo_root / path
    return path


def _write_web_launcher(path: Path, command: list[str], *, cwd: Path, url: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    open_url = _launcher_open_after_ready_script(url)
    content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"cd {shlex.quote(str(cwd))}\n"
        f"echo 'WriterYang Web UI: {url or ''}'\n"
        f"{open_url}"
        f"exec {shlex.join(command)}\n"
    )
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _launcher_open_after_ready_script(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    host = parsed.hostname or WEB_HOST
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    quoted_url = shlex.quote(url)
    return (
        "WRITERYANG_WEB_HOST=" + shlex.quote(host) + "\n"
        "WRITERYANG_WEB_PORT=" + shlex.quote(str(port)) + "\n"
        "WRITERYANG_WEB_URL=" + quoted_url + "\n"
        "(\n"
        "  i=0\n"
        "  while [ \"$i\" -lt 150 ]; do\n"
        "    if (: >/dev/tcp/$WRITERYANG_WEB_HOST/$WRITERYANG_WEB_PORT) >/dev/null 2>&1; then\n"
        "      if command -v open >/dev/null 2>&1; then\n"
        "        open \"$WRITERYANG_WEB_URL\" >/dev/null 2>&1 || true\n"
        "      elif command -v xdg-open >/dev/null 2>&1; then\n"
        "        xdg-open \"$WRITERYANG_WEB_URL\" >/dev/null 2>&1 || true\n"
        "      fi\n"
        "      exit 0\n"
        "    fi\n"
        "    i=$((i + 1))\n"
        "    sleep 0.1\n"
        "  done\n"
        ") &\n"
    )


def _print_plan(plan: InstallPlan, *, dry_run: bool) -> None:
    prefix = "[dry-run] " if dry_run else ""
    print(f"{prefix}mode: {plan.mode}")
    print(f"{prefix}environment: {plan.env_name}")
    for command in plan.commands:
        print(f"{prefix}$ {shlex.join(command)}")
    if plan.web_url and plan.web_command:
        print(f"{prefix}web_url: {plan.web_url}")
        print(f"{prefix}web_command: {shlex.join(plan.web_command)}")
    elif plan.web_url:
        print(f"{prefix}web_url: {plan.web_url}")
    if plan.launcher_path and plan.launcher_command:
        print(f"{prefix}launcher_path: {plan.launcher_path}")
        print(f"{prefix}launcher_command: {shlex.join(plan.launcher_command)}")
    if plan.activate_shell:
        print(f"{prefix}activate_shell: {shlex.join(plan.activate_shell.command)}")


def _print_next_steps(plan: InstallPlan) -> None:
    print("\nNext steps:")
    print(f"  {plan.activation_command}")
    for command in plan.verify_commands:
        print(f"  {command}")
    if plan.launcher_path:
        print(f"  Web UI launcher: {plan.launcher_path}")
    if plan.web_url:
        print(f"  Web UI: {plan.web_url}")
        if plan.web_command:
            print("  Stop Web UI with Ctrl+C")
        else:
            print("  Start Web UI later with the launcher or the printed activation command.")
    if plan.activate_shell:
        print("  This installer will enter a new environment shell when running interactively.")


def _run(command: list[str], *, cwd: Path, env: Mapping[str, str] | None = None) -> None:
    print(f"$ {shlex.join(command)}")
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    subprocess.run(command, cwd=cwd, env=merged_env if env else None, check=True)


def _run_web_command(command: list[str], *, cwd: Path, url: str | None, open_browser: bool) -> None:
    print(f"$ {shlex.join(command)}")
    process = subprocess.Popen(command, cwd=cwd)
    try:
        if open_browser and url:
            if _wait_for_web_server(url):
                webbrowser.open(url)
            else:
                print(f"Web UI is still starting. If the browser does not open, visit: {url}", file=sys.stderr)
        returncode = process.wait()
        if returncode not in {0, -signal.SIGINT, 130}:
            raise subprocess.CalledProcessError(returncode, command)
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        process.wait()
        print("\nWeb UI stopped.")


def _wait_for_web_server(url: str, timeout_seconds: float = 15.0) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


if __name__ == "__main__":
    raise SystemExit(main())
