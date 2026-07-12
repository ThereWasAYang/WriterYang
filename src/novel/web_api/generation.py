from __future__ import annotations

from .deps import (
    parse_chapter_selector,
)
from novel.core.contracts import (
    ChapterMemoryGenerateCommand,
    ChapterMemoryRebuildCommand,
    ProductionExportCommand,
)

from .common import (
    WebAPIError,
    _chapter_number,
    _optional_string,
    _optional_int,
    _truthy,
    _dispatch_web_command,
)


def _export_markdown(data: dict[str, object]) -> dict[str, object]:
    return _dispatch_web_command(
        data,
        ProductionExportCommand(
            type="export.markdown",
            chapters=list(parse_chapter_selector(_optional_string(data.get("chapters")))),
            from_chapter=_optional_int(data.get("from_chapter")),
            to_chapter=_optional_int(data.get("to_chapter")),
            output_path=str(data["output"]) if data.get("output") else None,
            title=_optional_string(data.get("title")),
            force=bool(data.get("force")),
        ),
        confirmed=True,
    )


def _export_docx(data: dict[str, object]) -> dict[str, object]:
    return _dispatch_web_command(
        data,
        ProductionExportCommand(
            type="export.docx",
            chapters=list(parse_chapter_selector(_optional_string(data.get("chapters")))),
            from_chapter=_optional_int(data.get("from_chapter")),
            to_chapter=_optional_int(data.get("to_chapter")),
            output_path=str(data["output"]) if data.get("output") else None,
            title=_optional_string(data.get("title")),
            force=bool(data.get("force")),
        ),
        confirmed=True,
    )


def _chapter_memory_generate(data: dict[str, object]) -> dict[str, object]:
    return _dispatch_web_command(
        data,
        ChapterMemoryGenerateCommand(
            chapter_number=_chapter_number(data),
            force=True if "force" not in data else _truthy(data.get("force")),
            provider_name=_optional_string(data.get("provider")) or "config",
        ),
    )


def _chapter_memory_rebuild(data: dict[str, object]) -> dict[str, object]:
    mode = _optional_string(data.get("mode")) or "missing_or_stale"
    if mode not in {"missing", "missing_or_stale", "all"}:
        raise WebAPIError("invalid_request", "mode must be missing, missing_or_stale, or all", status=400)
    return _dispatch_web_command(
        data,
        ChapterMemoryRebuildCommand(
            mode=mode,  # type: ignore[arg-type]
            provider_name=_optional_string(data.get("provider")) or "config",
        ),
    )
