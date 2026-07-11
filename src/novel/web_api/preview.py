from __future__ import annotations

from typing import Literal, cast

from novel.core.contracts import PreviewPackageCommand
from novel.core.exporting import parse_chapter_selector

from .common import _dispatch_web_command, _optional_int, _optional_string


def _preview_package(data: dict[str, object]) -> dict[str, object]:
    return _dispatch_web_command(
        data,
        PreviewPackageCommand(
            chapters=list(parse_chapter_selector(_optional_string(data.get("chapters")))),
            from_chapter=_optional_int(data.get("from_chapter")),
            to_chapter=_optional_int(data.get("to_chapter")),
            source_kind=_preview_source(data.get("source")),
            title=_optional_string(data.get("title")),
        ),
    )


def _preview_source(value: object) -> Literal["draft", "polished"]:
    source = str(value or "polished")
    if source not in {"draft", "polished"}:
        raise ValueError("preview source must be draft or polished")
    return cast(Literal["draft", "polished"], source)
