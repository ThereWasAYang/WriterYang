from __future__ import annotations

from typing import Literal, cast

from novel.core.exporting import parse_chapter_selector
from novel.core.previewing import PreviewPackageOptions, build_preview_package

from .common import _optional_int, _optional_string, _root_from_body


def _preview_package(data: dict[str, object]) -> dict[str, object]:
    result = build_preview_package(
        PreviewPackageOptions(
            root=_root_from_body(data),
            chapters=parse_chapter_selector(_optional_string(data.get("chapters"))),
            from_chapter=_optional_int(data.get("from_chapter")),
            to_chapter=_optional_int(data.get("to_chapter")),
            source_kind=_preview_source(data.get("source")),
            title=_optional_string(data.get("title")),
        )
    )
    return {
        "preview_id": result.manifest.preview_id,
        "package_dir": str(result.package_dir),
        "content_path": str(result.content_path),
        "manifest_path": str(result.manifest_path),
        "chapters": list(result.chapters),
        "production_eligible": result.manifest.production_eligible,
    }


def _preview_source(value: object) -> Literal["draft", "polished"]:
    source = str(value or "polished")
    if source not in {"draft", "polished"}:
        raise ValueError("preview source must be draft or polished")
    return cast(Literal["draft", "polished"], source)
