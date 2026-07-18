from __future__ import annotations

import argparse
from collections.abc import Callable

from novel import __version__
from novel.cli_commands.generation import (
    _cmd_canon,
    _cmd_export,
    _cmd_inspire,
)
from novel.cli_commands.memory import (
    _cmd_chapter_memory,
    _cmd_memory_repair,
    _cmd_setting_change,
)
from novel.cli_commands.orchestrator import _cmd_ask
from novel.cli_commands.preview import _cmd_preview
from novel.cli_commands.project_system import (
    _cmd_completion,
    _cmd_doctor,
    _cmd_init,
    _cmd_schema,
    _cmd_show,
    _cmd_status,
    _cmd_usage,
    _cmd_validate,
    _cmd_web,
    _cmd_web_launch,
)
from novel.cli_commands.revision_session import _cmd_revision_session
from novel.cli_commands.search import _cmd_index, _cmd_search
from novel.cli_commands.session import _cmd_session
from novel.cli_parsers import (
    register_generation_parsers,
    register_memory_parsers,
    register_project_parsers,
    register_search_parsers,
    register_session_parsers,
)
from novel.cli_shared import (
    _add_integration_args_recursive,
    _apply_project_alias,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel", description="Novel workspace CLI")
    parser.add_argument("--version", action="version", version=f"novel {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_project_parsers(subparsers)
    register_search_parsers(subparsers)
    register_memory_parsers(subparsers)
    register_session_parsers(subparsers)
    register_generation_parsers(subparsers)
    _add_integration_args_recursive(parser)
    return parser


_COMMAND_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "init": _cmd_init,
    "validate": _cmd_validate,
    "schema": _cmd_schema,
    "completion": _cmd_completion,
    "doctor": _cmd_doctor,
    "index": _cmd_index,
    "search": _cmd_search,
    "memory-repair": _cmd_memory_repair,
    "setting-change": _cmd_setting_change,
    "chapter-memory": _cmd_chapter_memory,
    "ask": _cmd_ask,
    "session": _cmd_session,
    "revision-session": _cmd_revision_session,
    "status": _cmd_status,
    "usage": _cmd_usage,
    "show": _cmd_show,
    "inspire": _cmd_inspire,
    "canon": _cmd_canon,
    "export": _cmd_export,
    "preview": _cmd_preview,
    "web": _cmd_web,
    "web-launch": _cmd_web_launch,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_project_alias(args)

    handler = _COMMAND_HANDLERS.get(args.command)
    if handler is not None:
        return handler(args)

    parser.error(f"unknown command: {args.command}")
    return 2
