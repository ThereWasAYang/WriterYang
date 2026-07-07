#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import shlex
import shutil
import socket
import subprocess
import sys
from typing import Mapping, Optional, Sequence


SUPPORTED_PYTHON_VERSIONS = ("3.12", "3.11", "3.13")
MIN_PYTHON_VERSION = (3, 11)
MAX_PYTHON_VERSION_EXCLUSIVE = (3, 14)
CONDA_PYTHON_SPEC = "python>=3.11,<3.14"
ENV_PREFIX = "WriterYang"
WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8765
WEB_LAUNCHER_CONFIG_FILENAME = "WriterYang_WebUI.config.json"
WEB_LAUNCHER_CONFIG_ENV = "WRITERYANG_WEB_LAUNCHER_CONFIG"
WEB_LAUNCHER_PATH_ENV = "WRITERYANG_WEB_LAUNCHER_PATH"


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
    launcher_config_path: Path | None = None
    launcher_command: list[str] | None = None
    launcher_host: str = WEB_HOST
    launcher_port: int = DEFAULT_WEB_PORT
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
        default=None,
        help="Path for the generated Web UI launcher. Defaults to WriterYang_WebUI.command on macOS/Linux and WriterYang_WebUI.cmd on Windows.",
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
            launcher_path=Path(args.launcher_path) if args.launcher_path else None,
            activate_shell=not args.no_activate_shell,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _print_plan(plan, dry_run=args.dry_run)
    if not args.dry_run:
        for command in plan.commands:
            _run(command, cwd=repo_root)
        if plan.launcher_config_path:
            _write_web_launcher_config(
                plan.launcher_config_path,
                host=plan.launcher_host,
                port=plan.launcher_port,
            )
        if plan.launcher_path and plan.launcher_command and plan.launcher_config_path:
            _write_web_launcher(
                plan.launcher_path,
                plan.launcher_command,
                cwd=repo_root,
                config_path=plan.launcher_config_path,
            )
        if plan.web_command:
            print("\nStarting WriterYang Web UI. Press Ctrl+C to stop.")
            web_command = [*plan.web_command, "--no-open"] if args.no_open_web else plan.web_command
            _run_web_command(
                web_command,
                cwd=repo_root,
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
    install_args = editable_install_args(dev=dev)
    base_name = dated_env_base_name(now)
    resolved_launcher_path = _resolve_launcher_path(repo_root, launcher_path)
    launcher_config_path = _launcher_config_path(resolved_launcher_path)
    if conda:
        env_name = unique_name(base_name, existing_conda_env_names(conda))
        env_prefix = conda_env_prefix(conda, env_name)
        env_python = conda_env_python_path(env_prefix)
        web = build_web_launch(
            conda_env_prefix=env_prefix,
            launcher_config_path=launcher_config_path,
            start_port=web_port,
            check_port=check_web_port,
        )
        commands = [
            [conda, "create", "-n", env_name, CONDA_PYTHON_SPEC, "-y"],
            [str(env_python), "-m", "pip", "install", "--upgrade", "pip"],
            [str(env_python), "-m", "pip", "install", *install_args],
        ]
        return InstallPlan(
            mode="conda",
            env_name=env_name,
            commands=commands,
            activation_command=f"conda activate {env_name}",
            verify_commands=["novel --version", "novel doctor"],
            web_url=web.url,
            web_command=web.command if start_web else None,
            launcher_path=resolved_launcher_path,
            launcher_config_path=launcher_config_path,
            launcher_command=web.launcher_command,
            launcher_host=web.host,
            launcher_port=web.port,
            activate_shell=build_activate_shell(env=env, env_name=env_name, conda_env_prefix=env_prefix)
            if activate_shell
            else None,
        )

    python_command = find_supported_python()
    if not python_command:
        accepted = ", ".join(SUPPORTED_PYTHON_VERSIONS)
        raise RuntimeError(
            "conda was not found, and no supported Python was found for venv fallback "
            f"(accepted: {accepted}; recommended: 3.12)"
        )
    venv_root = venv_root.resolve()
    env_name = unique_name(base_name, existing_venv_names(venv_root))
    venv_path = venv_root / env_name
    venv_python = venv_python_path(venv_path)
    web = build_web_launch(
        venv_path=venv_path,
        launcher_config_path=launcher_config_path,
        start_port=web_port,
        check_port=check_web_port,
    )
    commands = [
        [*python_command, "-m", "venv", str(venv_path)],
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
        [str(venv_python), "-m", "pip", "install", *install_args],
    ]
    return InstallPlan(
        mode="venv",
        env_name=env_name,
        commands=commands,
        activation_command=venv_activation_command(venv_path),
        verify_commands=["novel --version", "novel doctor"],
        web_url=web.url,
        web_command=web.command if start_web else None,
        launcher_path=resolved_launcher_path,
        launcher_config_path=launcher_config_path,
        launcher_command=web.launcher_command,
        launcher_host=web.host,
        launcher_port=web.port,
        activate_shell=build_activate_shell(venv_path=venv_path, env=env) if activate_shell else None,
    )


@dataclass(frozen=True)
class WebLaunch:
    url: str
    command: list[str]
    launcher_command: list[str]
    host: str
    port: int


def editable_install_args(*, dev: bool) -> list[str]:
    target = ".[dev]" if dev else "."
    return ["-e", target]


def build_web_launch(
    *,
    start_port: int,
    launcher_config_path: Path,
    host: str = WEB_HOST,
    conda_env_prefix: Path | None = None,
    venv_path: Path | None = None,
    check_port: bool = True,
) -> WebLaunch:
    port = find_available_port(host, start_port) if check_port else validate_port(start_port)
    url = f"http://{host}:{port}"
    if conda_env_prefix:
        novel_path = str(conda_env_novel_path(conda_env_prefix))
    elif venv_path:
        novel_path = str(venv_novel_path(venv_path))
    else:
        raise RuntimeError("web launch requires either conda_env_prefix or venv_path")
    command = [novel_path, "web-launch", "--config", str(launcher_config_path)]
    launcher_command = [*command, "--open"]
    return WebLaunch(url=url, command=command, launcher_command=launcher_command, host=host, port=port)


def build_activate_shell(
    *,
    env: Mapping[str, str],
    env_name: str | None = None,
    conda_env_prefix: Path | None = None,
    venv_path: Path | None = None,
) -> ShellLaunch:
    if os.name == "nt":
        shell = env.get("COMSPEC") or shutil.which("cmd.exe") or "cmd.exe"
        overrides: dict[str, str] = {}
        if conda_env_prefix and env_name:
            bin_dir = conda_env_prefix / "Scripts"
            existing_path = env.get("PATH", "")
            overrides = {
                "CONDA_DEFAULT_ENV": env_name,
                "CONDA_PREFIX": str(conda_env_prefix),
                "PATH": f"{bin_dir}{os.pathsep}{existing_path}" if existing_path else str(bin_dir),
            }
        elif venv_path:
            bin_dir = venv_path / "Scripts"
            existing_path = env.get("PATH", "")
            overrides = {
                "VIRTUAL_ENV": str(venv_path),
                "PATH": f"{bin_dir}{os.pathsep}{existing_path}" if existing_path else str(bin_dir),
            }
        else:
            raise RuntimeError("activate shell requires either conda/env_name or venv_path")
        return ShellLaunch(command=[shell, "/K"], env=overrides)

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
    if conda_path.parent.name in {"bin", "Scripts", "condabin"}:
        return conda_path.parent.parent / "envs" / env_name
    raise RuntimeError(
        f"could not infer conda environment prefix from {conda}; "
        "run the installer from a shell where CONDA_EXE points to conda's executable"
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


def find_supported_python() -> list[str] | None:
    for candidate in (*SUPPORTED_PYTHON_VERSIONS, "python3", "python"):
        executable = shutil.which(f"python{candidate}" if candidate[0].isdigit() else candidate)
        command = [executable] if executable else None
        if command and is_supported_python(command):
            return command
    py_launcher = shutil.which("py")
    if py_launcher:
        for version in SUPPORTED_PYTHON_VERSIONS:
            command = [py_launcher, f"-{version}"]
            if is_supported_python(command):
                return command
    return None


def is_supported_python(command: Sequence[str]) -> bool:
    version = python_version_info(command)
    if version is None:
        return False
    return MIN_PYTHON_VERSION <= version < MAX_PYTHON_VERSION_EXCLUSIVE


def python_version_info(command: Sequence[str]) -> tuple[int, int] | None:
    completed = subprocess.run(
        [
            *command,
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        major, minor = completed.stdout.strip().split(".", 1)
        return int(major), int(minor)
    except ValueError:
        return None


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
    path = launcher_path or Path(_default_launcher_filename())
    if not path.is_absolute():
        path = repo_root / path
    return path


def _default_launcher_filename() -> str:
    return "WriterYang_WebUI.cmd" if os.name == "nt" else "WriterYang_WebUI.command"


def _launcher_config_path(launcher_path: Path) -> Path:
    return launcher_path.with_name(WEB_LAUNCHER_CONFIG_FILENAME)


def _write_web_launcher_config(path: Path, *, host: str, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host": host,
        "port": validate_port(port),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_web_launcher(path: Path, command: list[str], *, cwd: Path, config_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix in {".cmd", ".bat"}:
        content = _windows_cmd_launcher_content(path, command, cwd=cwd, config_path=config_path)
    elif suffix == ".ps1":
        content = _powershell_launcher_content(path, command, cwd=cwd, config_path=config_path)
    else:
        content = _bash_launcher_content(path, command, cwd=cwd, config_path=config_path)
    path.write_text(content, encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o755)


def _bash_launcher_content(path: Path, command: list[str], *, cwd: Path, config_path: Path) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"cd {shlex.quote(str(cwd))}\n"
        f"export {WEB_LAUNCHER_PATH_ENV}={shlex.quote(str(path))}\n"
        f"export {WEB_LAUNCHER_CONFIG_ENV}={shlex.quote(str(config_path))}\n"
        f"exec {shlex.join(command)}\n"
    )


def _windows_cmd_launcher_content(path: Path, command: list[str], *, cwd: Path, config_path: Path) -> str:
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        f'cd /d "{cwd}"\r\n'
        f'set "{WEB_LAUNCHER_PATH_ENV}={path}"\r\n'
        f'set "{WEB_LAUNCHER_CONFIG_ENV}={config_path}"\r\n'
        f"{subprocess.list2cmdline(command)}\r\n"
        "exit /b %ERRORLEVEL%\r\n"
    )


def _powershell_launcher_content(path: Path, command: list[str], *, cwd: Path, config_path: Path) -> str:
    return (
        "Set-StrictMode -Version Latest\n"
        f"Set-Location -LiteralPath {_powershell_literal(str(cwd))}\n"
        f"$env:{WEB_LAUNCHER_PATH_ENV} = {_powershell_literal(str(path))}\n"
        f"$env:{WEB_LAUNCHER_CONFIG_ENV} = {_powershell_literal(str(config_path))}\n"
        f"{_powershell_command(command)}\n"
        "exit $LASTEXITCODE\n"
    )


def _powershell_command(command: list[str]) -> str:
    if not command:
        raise RuntimeError("launcher command must not be empty")
    executable = _powershell_literal(command[0])
    args = ", ".join(_powershell_literal(arg) for arg in command[1:])
    return f"& {executable} @({args})"


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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
        if plan.launcher_config_path:
            print(f"{prefix}launcher_config_path: {plan.launcher_config_path}")
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
    if plan.launcher_config_path:
        print(f"  Web UI launcher config: {plan.launcher_config_path}")
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


def _run_web_command(command: list[str], *, cwd: Path) -> None:
    print(f"$ {shlex.join(command)}")
    process = subprocess.Popen(command, cwd=cwd)
    try:
        returncode = process.wait()
        if returncode not in {0, -signal.SIGINT, 130}:
            raise subprocess.CalledProcessError(returncode, command)
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        process.wait()
        print("\nWeb UI stopped.")


if __name__ == "__main__":
    raise SystemExit(main())
