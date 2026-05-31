from __future__ import annotations

import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from novel.cli import main
from novel.core.inspiration import (
    InspirationError,
    InspirationOptions,
    read_inspiration_input,
    run_inspiration_agent,
)
from novel.core.providers import MockProvider
from novel.core.validation import validate_project
from novel.core.workspace import InitOptions, init_workspace


FAKE_MARKDOWN = """# Inspiration

## Source Summary

雨夜旧车站传来停播多年的广播。

## Themes

- 记忆
- 失踪

## Mood

- 潮湿
- 孤独

## Weak Outline

一个旧物修复师在雨夜旧车站发现异常广播，沿着声音和旧物线索追查一段被遮蔽的往事。

## Constraints

- 不要一开始解释超自然规则。
- 保留旧车站真相的开放性。

## Potential Characters

- 旧物修复师
- 熟悉车站旧事的人

## Potential Locations

- 雨夜旧车站
- 修复铺

## Potential Conflicts

- 主角想查清真相，但真相会改变他对过去的理解。
"""


def test_inspiration_agent_writes_markdown_and_json_with_mock_provider(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    provider = MockProvider(fake_response=FAKE_MARKDOWN)

    result = run_inspiration_agent(
        InspirationOptions(
            root=root,
            source_text="雨夜旧车站里传来已经停播多年的广播声",
            write_json=True,
            overwrite=True,
        ),
        provider,
    )

    assert result.markdown_path == root.resolve() / "memory" / "inspiration.md"
    assert result.json_path == root.resolve() / "memory" / "inspiration.json"
    assert "## Weak Outline" in result.markdown_path.read_text(encoding="utf-8")
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert data["id"] == "inspiration_001"
    assert data["themes"] == ["记忆", "失踪"]
    assert data["mood"] == ["潮湿", "孤独"]
    assert "旧物修复师" in data["weak_outline"]
    assert provider.requests[0].json_schema_name is None
    assert "弱总纲" in provider.requests[0].system_prompt


def test_inspiration_agent_accepts_json_wrapper_from_provider(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    wrapped = json.dumps({"outline": FAKE_MARKDOWN}, ensure_ascii=False)
    provider = MockProvider(fake_response=wrapped)

    result = run_inspiration_agent(
        InspirationOptions(
            root=root,
            source_text="雨夜旧车站里传来已经停播多年的广播声",
            write_json=True,
            overwrite=True,
        ),
        provider,
    )

    assert "## Weak Outline" in result.markdown_path.read_text(encoding="utf-8")
    assert result.json_path is not None
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert data["themes"] == ["记忆", "失踪"]


def test_inspiration_agent_can_receive_search_context(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "style_guide.md").write_text("保持悬疑。", encoding="utf-8")
    provider = MockProvider(fake_response=FAKE_MARKDOWN)

    result = run_inspiration_agent(
        InspirationOptions(
            root=root,
            source_text="继续发展旧车站广播的灵感",
            overwrite=True,
            use_search_context=True,
        ),
        provider,
    )

    assert "Context bundle" in provider.requests[0].user_prompt
    assert result.context_report_path is not None
    assert result.context_report_path.is_file()


def test_inspiration_agent_refuses_to_overwrite_existing_markdown(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    original = (root / "memory" / "inspiration.md").read_text(encoding="utf-8")

    try:
        run_inspiration_agent(
            InspirationOptions(
                root=root,
                source_text="一个雨夜旧车站的故事",
                overwrite=False,
            ),
            MockProvider(fake_response=FAKE_MARKDOWN),
        )
    except InspirationError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected InspirationError")

    assert (root / "memory" / "inspiration.md").read_text(encoding="utf-8") == original


def test_read_inspiration_input_from_file(tmp_path: Path) -> None:
    input_path = tmp_path / "input.txt"
    input_path.write_text("一个雨夜旧车站里传来广播声", encoding="utf-8")

    text, source_type = read_inspiration_input(None, input_path)

    assert text == "一个雨夜旧车站里传来广播声"
    assert source_type == "file"


def test_read_inspiration_input_rejects_text_and_file(tmp_path: Path) -> None:
    input_path = tmp_path / "input.txt"
    input_path.write_text("file", encoding="utf-8")

    try:
        read_inspiration_input("text", input_path)
    except InspirationError as exc:
        assert "either direct text or --input" in str(exc)
    else:
        raise AssertionError("expected InspirationError")


def test_inspire_cli_supports_direct_text_with_mock_provider(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))

    code, stdout, stderr = _run_cli(
        [
            "inspire",
            "雨夜旧车站里传来已经停播多年的广播声",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--json",
            "--overwrite",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote inspiration markdown:" in stdout
    assert (root / "memory" / "inspiration.md").is_file()
    assert (root / "memory" / "inspiration.json").is_file()
    assert "## Potential Characters" in (root / "memory" / "inspiration.md").read_text(
        encoding="utf-8"
    )


def test_inspire_cli_supports_input_file_with_mock_provider(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    input_path = tmp_path / "input.txt"
    input_path.write_text("旧车站广播声", encoding="utf-8")
    init_workspace(InitOptions(title="雨夜旧车站", root=root))

    code, stdout, stderr = _run_cli(
        [
            "inspire",
            "--input",
            str(input_path),
            "--path",
            str(root),
            "--provider",
            "mock",
            "--overwrite",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote inspiration markdown:" in stdout
    assert "## Weak Outline" in (root / "memory" / "inspiration.md").read_text(
        encoding="utf-8"
    )


def test_inspire_cli_refuses_silent_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    original = (root / "memory" / "inspiration.md").read_text(encoding="utf-8")

    code, stdout, stderr = _run_cli(
        [
            "inspire",
            "一个雨夜旧车站的故事",
            "--path",
            str(root),
            "--provider",
            "mock",
        ]
    )

    assert code == 1
    assert stdout == ""
    assert "already exists" in stderr
    assert (root / "memory" / "inspiration.md").read_text(encoding="utf-8") == original


def test_validate_checks_optional_inspiration_json(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.json").write_text(
        json.dumps({"id": "inspiration_001"}, ensure_ascii=False),
        encoding="utf-8",
    )

    report = validate_project(root)

    assert not report.ok
    assert any("source_type" in message.message for message in report.errors)


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
