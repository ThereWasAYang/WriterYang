from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import shlex
import socket
import sys
from typing import Sequence

from pydantic import BaseModel, Field

from novel.core.io import atomic_write_model_json, atomic_write_text, load_json_model


WEB_LAUNCHER_FILENAME = "WriterYang_WebUI.command"
WEB_LAUNCHER_CONFIG_FILENAME = "WriterYang_WebUI.config.json"
WEB_LAUNCHER_CONFIG_ENV = "WRITERYANG_WEB_LAUNCHER_CONFIG"
WEB_LAUNCHER_PATH_ENV = "WRITERYANG_WEB_LAUNCHER_PATH"
WEB_HOST_ENV = "WRITERYANG_WEB_HOST"
WEB_PORT_ENV = "WRITERYANG_WEB_PORT"
WEB_URL_ENV = "WRITERYANG_WEB_URL"
WEB_PORT_FALLBACK_ENV = "WRITERYANG_WEB_PORT_FALLBACK"


class WebLauncherError(RuntimeError):
    """Raised when the Web UI launcher cannot be configured or started."""


class PortUnavailableError(WebLauncherError):
    """Raised when the requested launcher port is already occupied."""


class WebLauncherConfig(BaseModel):
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8765, ge=1, le=65535)
    updated_at: str | None = None


@dataclass(frozen=True)
class WebLauncherPortResult:
    config_path: Path
    host: str
    requested_port: int
    selected_port: int

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.selected_port}"


def launcher_config_path_from_env(*, default_root: Path | None = None) -> Path:
    configured = os.environ.get(WEB_LAUNCHER_CONFIG_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return ((default_root or Path.cwd()) / WEB_LAUNCHER_CONFIG_FILENAME).expanduser().resolve()


def launcher_path_from_env(*, default_root: Path | None = None) -> Path:
    configured = os.environ.get(WEB_LAUNCHER_PATH_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return ((default_root or Path.cwd()) / WEB_LAUNCHER_FILENAME).expanduser().resolve()


def current_web_endpoint_from_env() -> tuple[str | None, int | None]:
    host = os.environ.get(WEB_HOST_ENV)
    port_text = os.environ.get(WEB_PORT_ENV)
    try:
        port = int(port_text) if port_text else None
    except ValueError:
        port = None
    return host, port


def load_web_launcher_config(
    path: Path,
    *,
    default_host: str = "127.0.0.1",
    default_port: int = 8765,
) -> WebLauncherConfig:
    path = path.expanduser().resolve()
    if not path.exists():
        return WebLauncherConfig(host=default_host, port=default_port)
    return load_json_model(path, WebLauncherConfig)


def write_web_launcher_config(path: Path, config: WebLauncherConfig) -> Path:
    path = path.expanduser().resolve()
    atomic_write_model_json(path, config)
    return path


def save_web_launcher_port_config(
    config_path: Path,
    *,
    host: str,
    requested_port: int,
    current_host: str | None = None,
    current_port: int | None = None,
) -> WebLauncherPortResult:
    config = WebLauncherConfig(
        host=host,
        port=requested_port,
        updated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    if not _is_current_endpoint(config.host, config.port, current_host=current_host, current_port=current_port):
        if not is_port_available(config.host, config.port):
            raise PortUnavailableError(
                f"端口 {config.port} 已被占用，未保存启动器端口配置。请选择其他可用端口。"
            )
    written = write_web_launcher_config(config_path, config)
    return WebLauncherPortResult(
        config_path=written,
        host=config.host,
        requested_port=requested_port,
        selected_port=config.port,
    )


def recommend_web_launcher_port(
    start_port: int,
    *,
    host: str = "127.0.0.1",
    current_host: str | None = None,
    current_port: int | None = None,
) -> int:
    WebLauncherConfig(host=host, port=start_port)
    if _is_current_endpoint(host, start_port, current_host=current_host, current_port=current_port):
        return start_port
    return find_available_port(host=host, start_port=start_port)


def is_port_available(host: str, port: int) -> bool:
    WebLauncherConfig(host=host, port=port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def find_available_port(*, host: str, start_port: int) -> int:
    WebLauncherConfig(host=host, port=start_port)
    for port in range(start_port, 65536):
        if is_port_available(host, port):
            return port
    raise WebLauncherError(f"no available Web UI port from {start_port} to 65535")


def write_web_launcher_command(
    path: Path,
    *,
    config_path: Path,
    cwd: Path,
    command: Sequence[str] | None = None,
) -> Path:
    path = path.expanduser().resolve()
    config_path = config_path.expanduser().resolve()
    cwd = cwd.expanduser().resolve()
    launch_command = list(command) if command is not None else [
        sys.executable,
        "-m",
        "novel",
        "web-launch",
        "--config",
        str(config_path),
        "--open",
    ]
    content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"cd {shlex.quote(str(cwd))}\n"
        f"export {WEB_LAUNCHER_PATH_ENV}={shlex.quote(str(path))}\n"
        f"export {WEB_LAUNCHER_CONFIG_ENV}={shlex.quote(str(config_path))}\n"
        f"exec {shlex.join(launch_command)}\n"
    )
    atomic_write_text(path, content)
    path.chmod(0o755)
    return path


def _is_current_endpoint(
    host: str,
    port: int,
    *,
    current_host: str | None,
    current_port: int | None,
) -> bool:
    return current_port == port and current_host is not None and _same_host(host, current_host)


def _same_host(left: str, right: str) -> bool:
    left_value = left.strip().lower()
    right_value = right.strip().lower()
    loopbacks = {"127.0.0.1", "localhost", "::1"}
    if left_value in loopbacks and right_value in loopbacks:
        return True
    return left_value == right_value
