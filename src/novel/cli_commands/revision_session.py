from __future__ import annotations

import argparse
from pathlib import Path

from novel.cli_shared import (
    _command_lock,
    _failure,
    _success,
    _vector_context_mode_from_args,
)
from novel.core.locking import ProjectLockError
from novel.core.revision_workflow import (
    RevisionActionOptions,
    RevisionRunOptions,
    RevisionStartOptions,
    RevisionWorkflowError,
    accept_revision_session,
    list_revision_blocks,
    run_revision_session,
    show_revision_session,
    start_revision_session,
)
from novel.core.session import parse_range


def _cmd_revision_session(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    command = args.revision_session_command
    try:
        if command == "blocks":
            blocks = list_revision_blocks(root, args.chapter)
            return _success(
                args,
                {"command": "revision-session blocks", "chapter_number": args.chapter, "blocks": blocks},
                [
                    f"第 {args.chapter} 章 Markdown blocks：",
                    *[
                        f"{item['index']}. [{item['kind']}] {item['preview']}"
                        for item in blocks
                    ],
                ],
            )
        with _command_lock(args, root, f"revision-session {command}"):
            if command == "start":
                block_range = parse_range(args.blocks)
                if tuple(range(block_range[0], block_range[-1] + 1)) != block_range:
                    raise RevisionWorkflowError("--blocks must be one contiguous range")
                result = start_revision_session(
                    RevisionStartOptions(
                        root=root,
                        chapter_number=args.chapter,
                        start_block=block_range[0],
                        end_block=block_range[-1],
                        instruction=args.instruction,
                    )
                )
            elif command == "show":
                result = show_revision_session(root, args.revision_session_id)
            elif command == "run":
                result = run_revision_session(
                    RevisionRunOptions(
                        root=root,
                        revision_session_id=args.revision_session_id,
                        provider_name=args.provider,
                        use_search_context=getattr(args, "use_search_context", True),
                        use_vector_context=_vector_context_mode_from_args(args),
                    )
                )
            elif command == "accept":
                result = accept_revision_session(
                    RevisionActionOptions(root=root, revision_session_id=args.revision_session_id)
                )
            else:
                raise RevisionWorkflowError(f"unknown revision-session command: {command}")
    except (RevisionWorkflowError, ProjectLockError, ValueError) as exc:
        return _failure(args, str(exc), error_type="revision_error")
    session = result.session
    payload: dict[str, object] = {
        "command": f"revision-session {command}",
        "revision_session": session.model_dump(mode="json"),
        "session_path": str(result.session_path),
        "message": result.message,
    }
    return _success(
        args,
        payload,
        [
            f"Revision session: {session.revision_session_id}",
            result.message,
            f"Phase: {session.phase.value}",
            f"Session file: {result.session_path}",
        ],
    )
