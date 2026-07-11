from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

from novel.core.artifact_store import load_lifecycle, sha256_bytes, sha256_file
from novel.core.contracts import PreviewManifest, PreviewSourceChapter
from novel.core.io import atomic_write_model_json, atomic_write_text, load_yaml_model
from novel.core.polishing import PolishingError, read_markdown_with_front_matter
from novel.core.schemas import ProjectConfig
from novel.core.timeutil import utc_now, utc_timestamp


class PreviewError(RuntimeError):
    """Raised when a preview package cannot be built safely."""


@dataclass(frozen=True)
class PreviewPackageOptions:
    root: Path
    chapters: tuple[int, ...] = ()
    from_chapter: int | None = None
    to_chapter: int | None = None
    source_kind: Literal["draft", "polished"] = "polished"
    title: str | None = None


@dataclass(frozen=True)
class PreviewPackageResult:
    package_dir: Path
    content_path: Path
    manifest_path: Path
    manifest: PreviewManifest
    chapters: tuple[int, ...]


@dataclass(frozen=True)
class _PreviewChapter:
    source: PreviewSourceChapter
    body: str


PREVIEW_WARNING = "预览包不是正式出版物，不能替代通过 AcceptanceCommit 授权的 Production Export。"


def build_preview_package(options: PreviewPackageOptions) -> PreviewPackageResult:
    root = options.root.resolve()
    project = load_yaml_model(root / "project.yaml", ProjectConfig)
    title = options.title.strip() if options.title and options.title.strip() else project.title
    numbers = _selected_chapter_numbers(options, root)
    if not numbers:
        raise PreviewError("no chapters selected for preview")
    chapters = [_load_preview_chapter(root, number, options.source_kind) for number in numbers]
    preview_id = f"preview_{utc_timestamp()}"
    package_dir = root / "exports" / "previews" / preview_id
    content_path = package_dir / "preview.md"
    manifest_path = package_dir / "manifest.json"
    if package_dir.exists():
        raise PreviewError(f"preview package already exists: {preview_id}")
    content = _render_preview(title, chapters)
    manifest = PreviewManifest(
        preview_id=preview_id,
        title=title,
        source_kind=options.source_kind,
        source_chapters=[chapter.source for chapter in chapters],
        content_path=content_path.relative_to(root).as_posix(),
        content_sha256=sha256_bytes(content.encode("utf-8")),
        warning=PREVIEW_WARNING,
        created_at=utc_now(),
    )
    package_dir.mkdir(parents=True, exist_ok=False)
    atomic_write_text(content_path, content)
    atomic_write_model_json(manifest_path, manifest)
    return PreviewPackageResult(
        package_dir=package_dir,
        content_path=content_path,
        manifest_path=manifest_path,
        manifest=manifest,
        chapters=tuple(numbers),
    )


def _selected_chapter_numbers(options: PreviewPackageOptions, root: Path) -> list[int]:
    if options.chapters:
        numbers = sorted(set(options.chapters))
    else:
        chapters_dir = root / "memory" / "chapters"
        numbers = sorted(
            int(child.name)
            for child in chapters_dir.iterdir()
            if child.is_dir() and child.name.isdigit()
        ) if chapters_dir.exists() else []
    if any(number < 1 for number in numbers):
        raise PreviewError("chapter numbers must be positive integers")
    if options.from_chapter is not None:
        if options.from_chapter < 1:
            raise PreviewError("--from must be a positive integer")
        numbers = [number for number in numbers if number >= options.from_chapter]
    if options.to_chapter is not None:
        if options.to_chapter < 1:
            raise PreviewError("--to must be a positive integer")
        numbers = [number for number in numbers if number <= options.to_chapter]
    if (
        options.from_chapter is not None
        and options.to_chapter is not None
        and options.from_chapter > options.to_chapter
    ):
        raise PreviewError("--from must be less than or equal to --to")
    return numbers


def _load_preview_chapter(
    root: Path,
    chapter_number: int,
    source_kind: Literal["draft", "polished"],
) -> _PreviewChapter:
    path = root / "memory" / "chapters" / f"{chapter_number:03d}" / f"{source_kind}.md"
    if not path.is_file():
        raise PreviewError(f"chapter {chapter_number} has no working {source_kind}.md")
    try:
        document = read_markdown_with_front_matter(path)
    except PolishingError as exc:
        raise PreviewError(str(exc)) from exc
    metadata_number = document.metadata.get("chapter_number")
    if metadata_number != chapter_number:
        raise PreviewError(
            f"{path} chapter_number {metadata_number} does not match directory chapter {chapter_number}"
        )
    artifact_ref = None
    lifecycle = load_lifecycle(root, chapter_number)
    if (
        source_kind == "polished"
        and lifecycle
        and lifecycle.active_candidate
        and lifecycle.active_candidate.sha256 == sha256_file(path)
    ):
        artifact_ref = lifecycle.active_candidate
    source = PreviewSourceChapter(
        chapter_number=chapter_number,
        title=str(document.metadata.get("title") or f"Chapter {chapter_number}"),
        source_kind=source_kind,
        path=path.relative_to(root).as_posix(),
        sha256=sha256_file(path),
        artifact_ref=artifact_ref,
    )
    return _PreviewChapter(source=source, body=document.body)


def _render_preview(title: str, chapters: list[_PreviewChapter]) -> str:
    lines = [
        f"# [PREVIEW] {title}",
        "",
        "> **PREVIEW / 非正式导出**",
        ">",
        f"> {PREVIEW_WARNING}",
        "",
    ]
    for chapter in chapters:
        lines.extend(
            [
                f"## 第{chapter.source.chapter_number}章 {chapter.source.title}",
                "",
                _strip_leading_heading(chapter.body),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _strip_leading_heading(body: str) -> str:
    lines = body.strip().splitlines()
    if lines and re.match(r"^#\s+", lines[0]):
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines).strip()
