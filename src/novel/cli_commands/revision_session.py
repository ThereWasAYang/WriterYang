from __future__ import annotations

import argparse
from pathlib import Path

from novel.cli_shared import (
    _dispatch_cli_command,
    _failure,
    _success,
    _vector_context_mode_from_args,
)
from novel.core.command_bus import DomainError
from novel.core.contracts import PublicCommand, RevisionBlocksCommand, RevisionCommand, RevisionStartCommand
from novel.core.session import parse_range


def _cmd_revision_session(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    action = args.revision_session_command
    try:
        if action == "blocks":
            payload = _dispatch_cli_command(args, root, RevisionBlocksCommand(chapter_number=args.chapter))
            blocks = payload.get("blocks")
            if not isinstance(blocks, list):
                raise DomainError("internal_error", "command result is missing blocks")
            return _success(
                args,
                {**payload, "command": "revision-session blocks"},
                [
                    f"第 {args.chapter} 章 Markdown blocks：",
                    *[
                        f"{item['index']}. [{item['kind']}] {item['preview']}"
                        for item in blocks
                        if isinstance(item, dict)
                    ],
                ],
            )

        if action == "start":
            block_range = parse_range(args.blocks)
            if tuple(range(block_range[0], block_range[-1] + 1)) != block_range:
                raise DomainError("invalid_command", "--blocks must be one contiguous range", recoverable=True)
            command: PublicCommand = RevisionStartCommand(
                chapter_number=args.chapter,
                start_block=block_range[0],
                end_block=block_range[-1],
                instruction=args.instruction,
            )
        elif action == "show":
            command = RevisionCommand(
                type="revision.show",
                revision_session_id=args.revision_session_id,
            )
        elif action == "run":
            command = RevisionCommand(
                type="revision.run",
                revision_session_id=args.revision_session_id,
                provider_name=getattr(args, "provider", "config"),
                use_search_context=getattr(args, "use_search_context", True),
                use_vector_context=_vector_context_mode_from_args(args),
            )
        elif action == "accept":
            command = RevisionCommand(
                type="revision.accept",
                revision_session_id=args.revision_session_id,
            )
        else:
            raise DomainError("unknown_command", f"unknown revision-session command: {action}")
        payload = _dispatch_cli_command(args, root, command, confirmed=action == "accept")
    except (DomainError, ValueError) as exc:
        if isinstance(exc, DomainError):
            return _failure(args, exc.message, error_type=exc.code)
        return _failure(args, str(exc), error_type="revision_error")

    session = payload.get("revision_session")
    if not isinstance(session, dict):
        return _failure(args, "command result is missing revision_session", error_type="internal_error")
    return _success(
        args,
        {**payload, "command": f"revision-session {action}"},
        [
            f"Revision session: {session.get('revision_session_id')}",
            str(payload.get("message") or ""),
            f"Phase: {session.get('phase')}",
            f"Session file: {payload.get('session_path')}",
        ],
    )
