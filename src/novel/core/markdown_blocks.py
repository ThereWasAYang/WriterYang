from __future__ import annotations

from dataclasses import dataclass
import re
import uuid

from novel.core.artifact_store import sha256_bytes
from novel.core.contracts import (
    ArtifactRef,
    MarkdownBlockKind,
    SegmentPatch,
    SegmentSelection,
)
from novel.core.timeutil import utc_now


class MarkdownBlockError(RuntimeError):
    """Raised when a Markdown selection or patch is unsafe."""


@dataclass(frozen=True)
class MarkdownBlock:
    index: int
    kind: MarkdownBlockKind
    start: int
    end: int
    text: str
    sha256: str


@dataclass(frozen=True)
class ParsedMarkdown:
    source: str
    front_matter_end: int
    blocks: tuple[MarkdownBlock, ...]


@dataclass(frozen=True)
class AppliedSegmentPatch:
    markdown: str
    prefix_sha256: str
    suffix_sha256: str


_THEMATIC_BREAK = re.compile(r"^[ \t]{0,3}((\*[ \t]*){3,}|(-[ \t]*){3,}|(_[ \t]*){3,})$")
_LIST_ITEM = re.compile(r"^[ \t]{0,3}(?:[-+*]|[0-9]+[.)])[ \t]+")


def parse_markdown_blocks(markdown: str) -> ParsedMarkdown:
    if not markdown:
        raise MarkdownBlockError("Markdown source is empty")
    front_matter_end = _front_matter_end(markdown)
    body = markdown[front_matter_end:]
    lines = body.splitlines(keepends=True)
    blocks: list[MarkdownBlock] = []
    offset = front_matter_end
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            offset += len(line)
            index += 1
            continue
        start = offset
        collected = [line]
        kind = _block_kind(line)
        offset += len(line)
        index += 1
        in_fenced_code = kind == MarkdownBlockKind.CODE and _is_fence(line)
        fence_marker = line.lstrip()[:3] if in_fenced_code else ""
        while index < len(lines):
            current = lines[index]
            if in_fenced_code:
                collected.append(current)
                offset += len(current)
                index += 1
                if current.lstrip().startswith(fence_marker):
                    in_fenced_code = False
                    break
                continue
            if not current.strip():
                break
            current_kind = _block_kind(current)
            if _starts_new_block(kind, current_kind):
                break
            collected.append(current)
            offset += len(current)
            index += 1
        if in_fenced_code:
            raise MarkdownBlockError("Markdown fenced code block is not closed")
        text = "".join(collected)
        blocks.append(
            MarkdownBlock(
                index=len(blocks) + 1,
                kind=kind,
                start=start,
                end=offset,
                text=text,
                sha256=sha256_bytes(text.encode("utf-8")),
            )
        )
    if not blocks:
        raise MarkdownBlockError("Markdown body has no selectable blocks")
    return ParsedMarkdown(source=markdown, front_matter_end=front_matter_end, blocks=tuple(blocks))


def create_segment_selection(
    *,
    chapter_number: int,
    source_candidate: ArtifactRef,
    markdown: str,
    start_block: int,
    end_block: int,
) -> SegmentSelection:
    parsed = parse_markdown_blocks(markdown)
    start, end = _selection_offsets(parsed, start_block, end_block)
    return SegmentSelection(
        selection_id=f"selection_{uuid.uuid4().hex}",
        chapter_number=chapter_number,
        source_candidate=source_candidate,
        start_block=start_block,
        end_block=end_block,
        selected_sha256=sha256_bytes(markdown[start:end].encode("utf-8")),
        prefix_sha256=sha256_bytes(markdown[:start].encode("utf-8")),
        suffix_sha256=sha256_bytes(markdown[end:].encode("utf-8")),
        created_at=utc_now(),
    )


def apply_segment_patch(
    markdown: str,
    selection: SegmentSelection,
    patch: SegmentPatch,
) -> AppliedSegmentPatch:
    source_sha = sha256_bytes(markdown.encode("utf-8"))
    if source_sha != selection.source_candidate.sha256 or source_sha != patch.source_sha256:
        raise MarkdownBlockError("segment patch source hash is stale")
    if patch.selection_id != selection.selection_id:
        raise MarkdownBlockError("segment patch selection_id does not match authorization")
    if patch.start_block != selection.start_block or patch.end_block != selection.end_block:
        raise MarkdownBlockError("segment patch range exceeds authorized selection")
    replacement = patch.replacement_markdown
    if replacement.startswith("---\n") or replacement.startswith("---\r\n"):
        raise MarkdownBlockError("segment replacement cannot contain front matter")
    if not replacement.strip():
        raise MarkdownBlockError("segment replacement cannot be blank")
    parsed = parse_markdown_blocks(markdown)
    start, end = _selection_offsets(parsed, selection.start_block, selection.end_block)
    selected = markdown[start:end]
    prefix = markdown[:start]
    suffix = markdown[end:]
    if sha256_bytes(selected.encode("utf-8")) != selection.selected_sha256:
        raise MarkdownBlockError("selected block hash is stale")
    if sha256_bytes(prefix.encode("utf-8")) != selection.prefix_sha256:
        raise MarkdownBlockError("content before selected blocks changed")
    if sha256_bytes(suffix.encode("utf-8")) != selection.suffix_sha256:
        raise MarkdownBlockError("content after selected blocks changed")
    revised = prefix + replacement + suffix
    parse_markdown_blocks(revised)
    revised_prefix = revised[:start]
    revised_suffix = revised[start + len(replacement):]
    if revised_prefix.encode("utf-8") != prefix.encode("utf-8"):
        raise MarkdownBlockError("segment patch changed bytes before authorized range")
    if revised_suffix.encode("utf-8") != suffix.encode("utf-8"):
        raise MarkdownBlockError("segment patch changed bytes after authorized range")
    return AppliedSegmentPatch(
        markdown=revised,
        prefix_sha256=sha256_bytes(revised_prefix.encode("utf-8")),
        suffix_sha256=sha256_bytes(revised_suffix.encode("utf-8")),
    )


def render_block_preview(parsed: ParsedMarkdown) -> list[dict[str, object]]:
    return [
        {
            "index": block.index,
            "kind": block.kind.value,
            "sha256": block.sha256,
            "preview": " ".join(block.text.strip().split())[:160],
        }
        for block in parsed.blocks
    ]


def _selection_offsets(parsed: ParsedMarkdown, start_block: int, end_block: int) -> tuple[int, int]:
    if start_block < 1 or end_block < start_block or end_block > len(parsed.blocks):
        raise MarkdownBlockError(
            f"invalid block range {start_block}-{end_block}; document has {len(parsed.blocks)} blocks"
        )
    return parsed.blocks[start_block - 1].start, parsed.blocks[end_block - 1].end


def _front_matter_end(markdown: str) -> int:
    if not (markdown.startswith("---\n") or markdown.startswith("---\r\n")):
        return 0
    lines = markdown.splitlines(keepends=True)
    offset = len(lines[0])
    for line in lines[1:]:
        offset += len(line)
        if line.strip() == "---":
            return offset
    raise MarkdownBlockError("Markdown front matter is not closed")


def _block_kind(line: str) -> MarkdownBlockKind:
    stripped = line.lstrip()
    if _is_fence(line):
        return MarkdownBlockKind.CODE
    if stripped.startswith("#") and re.match(r"^#{1,6}[ \t]+", stripped):
        return MarkdownBlockKind.HEADING
    if stripped.startswith(">"):
        return MarkdownBlockKind.QUOTE
    if _LIST_ITEM.match(line):
        return MarkdownBlockKind.LIST
    if _THEMATIC_BREAK.match(line.rstrip("\r\n")):
        return MarkdownBlockKind.THEMATIC_BREAK
    return MarkdownBlockKind.PARAGRAPH


def _starts_new_block(current: MarkdownBlockKind, incoming: MarkdownBlockKind) -> bool:
    if incoming in {MarkdownBlockKind.HEADING, MarkdownBlockKind.THEMATIC_BREAK, MarkdownBlockKind.CODE}:
        return True
    if current in {MarkdownBlockKind.HEADING, MarkdownBlockKind.THEMATIC_BREAK}:
        return True
    if incoming != current and incoming in {MarkdownBlockKind.QUOTE, MarkdownBlockKind.LIST}:
        return True
    return False


def _is_fence(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")
