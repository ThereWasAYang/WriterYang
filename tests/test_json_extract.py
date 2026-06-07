from __future__ import annotations

import pytest

from novel.core.json_extract import JsonExtractionError, extract_json_object, strip_code_fence


def test_extract_json_object_from_fenced_payload() -> None:
    content = '```json\n{"ok": true, "items": [{"id": "x"}]}\n```'

    assert extract_json_object(content) == '{"ok": true, "items": [{"id": "x"}]}'


def test_extract_json_object_from_text_wrapped_payload() -> None:
    content = '说明文字\n{"ok": true}\n后续说明'

    assert extract_json_object(content) == '{"ok": true}'


def test_extract_json_object_raises_when_missing_object() -> None:
    with pytest.raises(JsonExtractionError):
        extract_json_object("[]")


def test_strip_code_fence_handles_plain_text() -> None:
    assert strip_code_fence("  plain  ") == "plain"
