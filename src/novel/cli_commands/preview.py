from __future__ import annotations

import argparse
from pathlib import Path

from novel.cli_shared import _dispatch_cli_command, _failure, _success
from novel.core.command_bus import DomainError
from novel.core.contracts import PreviewPackageCommand
from novel.core.exporting import parse_chapter_selector


def _cmd_preview(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    if args.preview_command != "package":
        return _failure(args, f"unknown preview command: {args.preview_command}", code=2)
    try:
        payload = _dispatch_cli_command(
            args,
            root,
            PreviewPackageCommand(
                chapters=list(parse_chapter_selector(args.chapters)),
                from_chapter=args.from_chapter,
                to_chapter=args.to_chapter,
                source_kind=args.source,
                title=args.title,
            ),
        )
    except DomainError as exc:
        return _failure(args, exc.message, error_type=exc.code)
    return _success(
        args,
        payload,
        [
            f"Wrote preview package: {payload['package_dir']}",
            f"Preview content: {payload['content_path']}",
            "This package is not production eligible.",
        ],
    )
