from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from novel.core.io import atomic_write_text, backup_if_exists


def project_env_path(root: Path) -> Path:
    return root.expanduser().resolve() / ".env"


def read_project_env_file(root: Path) -> dict[str, str]:
    path = project_env_path(root)
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed:
            key, value = parsed
            values[key] = value
    return values


def load_project_env(root: Path, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(read_project_env_file(root))
    env.update(dict(os.environ if base_env is None else base_env))
    return env


def write_project_env_values(root: Path, values: Mapping[str, str]) -> Path:
    root = root.expanduser().resolve()
    env_path = project_env_path(root)
    current = read_project_env_file(root)
    current.update({key: value for key, value in values.items() if value is not None})
    lines = [
        "# WriterYang local secrets. Do not commit this file.\n",
        "# Config files should reference these names through *_env fields.\n",
    ]
    for key in sorted(current):
        lines.append(f"{key}={_quote_env_value(current[key])}\n")
    backup_if_exists(env_path, reason="setup_guide_env")
    atomic_write_text(env_path, "".join(lines))
    _chmod_user_only(env_path)
    return env_path


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return key, _unquote_env_value(value.strip())


def _quote_env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.replace('\\"', '"').replace("\\\\", "\\")


def _chmod_user_only(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        path.chmod(0o600)
    except OSError:
        return
