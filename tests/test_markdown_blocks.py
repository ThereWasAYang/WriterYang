from __future__ import annotations

from datetime import UTC, datetime

import pytest

from novel.core.artifact_store import sha256_bytes
from novel.core.contracts import ArtifactKind, ArtifactRef, SegmentPatch
from novel.core.markdown_blocks import (
    MarkdownBlockError,
    apply_segment_patch,
    create_segment_selection,
    parse_markdown_blocks,
)

MARKDOWN = """---
chapter_number: 1
title: 测试
---

# 第一章

第一段文字。

> 一段引用。

- 列表一
- 列表二

最后一段。
"""


def _candidate(markdown: str = MARKDOWN) -> ArtifactRef:
    return ArtifactRef(
        artifact_id="art_" + "1" * 32,
        kind=ArtifactKind.CANDIDATE,
        path="memory/chapters/001/candidates/candidate.md",
        sha256=sha256_bytes(markdown.encode("utf-8")),
        created_at=datetime.now(UTC),
    )


def test_markdown_parser_excludes_front_matter_and_classifies_blocks() -> None:
    parsed = parse_markdown_blocks(MARKDOWN)

    assert [block.kind.value for block in parsed.blocks] == [
        "heading",
        "paragraph",
        "quote",
        "list",
        "paragraph",
    ]
    assert parsed.blocks[0].text == "# 第一章\n"


def test_segment_patch_preserves_every_byte_outside_selection() -> None:
    selection = create_segment_selection(
        chapter_number=1,
        source_candidate=_candidate(),
        markdown=MARKDOWN,
        start_block=2,
        end_block=3,
    )
    patch = SegmentPatch(
        patch_id="patch_" + "2" * 32,
        selection_id=selection.selection_id,
        source_sha256=selection.source_candidate.sha256,
        start_block=2,
        end_block=3,
        replacement_markdown="修订后的第一段。\n\n> 修订后的引用。\n",
        created_at=datetime.now(UTC),
    )

    applied = apply_segment_patch(MARKDOWN, selection, patch)

    assert "修订后的第一段" in applied.markdown
    assert applied.prefix_sha256 == selection.prefix_sha256
    assert applied.suffix_sha256 == selection.suffix_sha256
    assert applied.markdown.startswith(MARKDOWN[:parse_markdown_blocks(MARKDOWN).blocks[1].start])
    assert applied.markdown.endswith(MARKDOWN[parse_markdown_blocks(MARKDOWN).blocks[2].end:])


def test_segment_patch_rejects_stale_source() -> None:
    selection = create_segment_selection(
        chapter_number=1,
        source_candidate=_candidate(),
        markdown=MARKDOWN,
        start_block=2,
        end_block=2,
    )
    patch = SegmentPatch(
        patch_id="patch_" + "3" * 32,
        selection_id=selection.selection_id,
        source_sha256=selection.source_candidate.sha256,
        start_block=2,
        end_block=2,
        replacement_markdown="新段落。\n",
        created_at=datetime.now(UTC),
    )

    with pytest.raises(MarkdownBlockError, match="source hash is stale"):
        apply_segment_patch(MARKDOWN + "篡改", selection, patch)


def test_segment_patch_rejects_range_expansion() -> None:
    selection = create_segment_selection(
        chapter_number=1,
        source_candidate=_candidate(),
        markdown=MARKDOWN,
        start_block=2,
        end_block=2,
    )
    patch = SegmentPatch(
        patch_id="patch_" + "4" * 32,
        selection_id=selection.selection_id,
        source_sha256=selection.source_candidate.sha256,
        start_block=2,
        end_block=3,
        replacement_markdown="越界修改。\n",
        created_at=datetime.now(UTC),
    )

    with pytest.raises(MarkdownBlockError, match="exceeds authorized"):
        apply_segment_patch(MARKDOWN, selection, patch)


def test_markdown_parser_rejects_unclosed_fenced_code() -> None:
    with pytest.raises(MarkdownBlockError, match="fenced code block is not closed"):
        parse_markdown_blocks("# 标题\n\n```text\n未闭合\n")
