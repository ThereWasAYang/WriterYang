from __future__ import annotations

import argparse
from pathlib import Path

from novel.cli_shared import _command_lock, _failure, _success
from novel.core.exporting import parse_chapter_selector
from novel.core.locking import ProjectLockError
from novel.core.previewing import PreviewError, PreviewPackageOptions, build_preview_package


def _cmd_preview(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    if args.preview_command != "package":
        return _failure(args, f"unknown preview command: {args.preview_command}", code=2)
    try:
        chapters = parse_chapter_selector(args.chapters)
        with _command_lock(args, root, "preview package"):
            result = build_preview_package(
                PreviewPackageOptions(
                    root=root,
                    chapters=chapters,
                    from_chapter=args.from_chapter,
                    to_chapter=args.to_chapter,
                    source_kind=args.source,
                    title=args.title,
                )
            )
    except ProjectLockError as exc:
        return _failure(args, str(exc), error_type="project_locked")
    except (PreviewError, ValueError) as exc:
        return _failure(args, str(exc), error_type="export_error")
    payload: dict[str, object] = {
        "command": "preview package",
        "preview_id": result.manifest.preview_id,
        "package_dir": str(result.package_dir),
        "content_path": str(result.content_path),
        "manifest_path": str(result.manifest_path),
        "chapters": list(result.chapters),
        "production_eligible": result.manifest.production_eligible,
    }
    return _success(
        args,
        payload,
        [
            f"Wrote preview package: {result.package_dir}",
            f"Preview content: {result.content_path}",
            "This package is not production eligible.",
        ],
    )
