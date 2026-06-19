from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import yaml

from novel.cli import main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.drafting import ChapterDraftingOptions, write_chapter_draft
from novel.core.planning import ChapterPlanningOptions, default_mock_chapter_plan_json, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, PolishingError, polish_chapter
from novel.core.providers import MockProvider
from novel.core.workspace import InitOptions, init_workspace


def test_mock_provider_can_generate_polished_body(tmp_path: Path) -> None:
    root = _workspace_with_draft(tmp_path)
    provider = MockProvider(fake_response="雨声更深，旧车站像在夜里醒来。")

    result = polish_chapter(
        ChapterPolishingOptions(
            root=root,
            chapter_number=1,
            instruction="加强压抑感，减少解释性文字",
            style_note="更克制",
            keep_length=True,
            edit_mode="deep",
        ),
        provider,
    )

    assert "雨声更深" in result.polished_markdown
    assert "加强压抑感" in provider.requests[0].user_prompt
    assert "临时文风要求：更克制" in provider.requests[0].user_prompt
    assert "尽量保持长度：是" in provider.requests[0].user_prompt
    assert "编辑模式：deep" in provider.requests[0].user_prompt
    assert "不要输出解释、分析、修改说明、JSON 或大纲" in provider.requests[0].system_prompt


def test_truncated_polish_output_fails_without_writing_polished_and_records_event(tmp_path: Path) -> None:
    root = _workspace_with_draft(tmp_path)
    provider = MockProvider(fake_response={"content": "雨声更深", "finish_reason": "length"})

    try:
        polish_chapter(ChapterPolishingOptions(root=root, chapter_number=1), provider)
    except PolishingError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected truncation failure")

    assert "finish_reason=length" in message
    assert not (root / "memory" / "chapters" / "001" / "polished.md").exists()
    events = (root / "memory" / "management_events.jsonl").read_text(encoding="utf-8")
    assert "provider_output_truncated" in events
    assert "未写入 polished.md" in events


def test_polish_chapter_cli_creates_polished_markdown_with_front_matter(tmp_path: Path) -> None:
    root = _workspace_with_draft(tmp_path)

    code, stdout, stderr = _run_cli(["polish-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert "Wrote polished chapter:" in stdout
    polished_path = root / "memory" / "chapters" / "001" / "polished.md"
    assert polished_path.is_file()
    metadata, body = _read_front_matter(polished_path)
    assert metadata["chapter_number"] == 1
    assert metadata["title"] == "雨夜旧车站"
    assert metadata["status"] == "polished"
    assert metadata["created_by"] == "polish_agent"
    assert metadata["based_on"] == "draft.md"
    assert "created_at" in metadata
    assert "# 第一章 雨夜旧车站" in body
    assert "雨水敲在旧车站" in body
    assert "润色如下" not in body
    assert "raw_response" not in body


def test_polish_chapter_refuses_to_overwrite_existing_by_default(tmp_path: Path) -> None:
    root = _workspace_with_draft(tmp_path)
    first, _, _ = _run_cli(["polish-chapter", "1", "--path", str(root), "--provider", "mock"])
    polished_path = root / "memory" / "chapters" / "001" / "polished.md"
    original = polished_path.read_text(encoding="utf-8")

    second, stdout, stderr = _run_cli(["polish-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert first == 0
    assert second == 1
    assert stdout == ""
    assert "already exists" in stderr
    assert polished_path.read_text(encoding="utf-8") == original


def test_polish_chapter_force_overwrites_existing(tmp_path: Path) -> None:
    root = _workspace_with_draft(tmp_path)
    _run_cli(["polish-chapter", "1", "--path", str(root), "--provider", "mock"])
    polished_path = root / "memory" / "chapters" / "001" / "polished.md"
    polished_path.write_text("manual edit\n", encoding="utf-8")

    code, stdout, stderr = _run_cli(
        ["polish-chapter", "1", "--path", str(root), "--provider", "mock", "--force"]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote polished chapter:" in stdout
    assert "manual edit" not in polished_path.read_text(encoding="utf-8")


def test_polish_chapter_input_instruction(tmp_path: Path) -> None:
    root = _workspace_with_draft(tmp_path)
    input_path = tmp_path / "polish_request.txt"
    input_path.write_text("加强压抑感，减少解释性文字", encoding="utf-8")

    code, stdout, stderr = _run_cli(
        [
            "polish-chapter",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--input",
            str(input_path),
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote polished chapter:" in stdout


def test_polish_chapter_modes_and_flags_cli(tmp_path: Path) -> None:
    root = _workspace_with_draft(tmp_path)

    light, _, light_err = _run_cli(
        ["polish-chapter", "1", "--path", str(root), "--provider", "mock", "--light-edit"]
    )
    assert light == 0
    assert light_err == ""

    deep, _, deep_err = _run_cli(
        [
            "polish-chapter",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--deep-edit",
            "--keep-length",
            "--style-note",
            "更克制",
            "--force",
        ]
    )
    assert deep == 0
    assert deep_err == ""


def test_polish_chapter_search_context_protects_hidden_truth(tmp_path: Path) -> None:
    root = _workspace_with_draft(tmp_path)
    provider = MockProvider(fake_response="雨声更深，林澈仍只看见旧车站的空站台。")

    result = polish_chapter(
        ChapterPolishingOptions(root=root, chapter_number=1, use_search_context=True),
        provider,
    )

    prompt = provider.requests[0].user_prompt
    assert "Context bundle" in prompt
    assert "旧车站在特定雨夜会短暂连接过去的时间层" not in prompt
    assert result.context_report_path is not None
    assert result.context_report_path.is_file()


def test_polish_chapter_rejects_conflicting_edit_modes(tmp_path: Path) -> None:
    root = _workspace_with_draft(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "polish-chapter",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--light-edit",
            "--deep-edit",
        ]
    )

    assert code == 1
    assert stdout == ""
    assert "use only one of --light-edit or --deep-edit" in stderr


def test_polish_chapter_missing_draft_has_clear_error(tmp_path: Path) -> None:
    root = _workspace_with_draft(tmp_path)
    (root / "memory" / "chapters" / "001" / "draft.md").unlink()

    code, stdout, stderr = _run_cli(["polish-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 1
    assert stdout == ""
    assert "draft.md" in stderr
    assert "missing" in stderr


def test_polish_chapter_missing_plan_has_clear_error(tmp_path: Path) -> None:
    root = _workspace_with_draft(tmp_path)
    (root / "memory" / "chapters" / "001" / "plan.json").unlink()

    code, stdout, stderr = _run_cli(["polish-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 1
    assert stdout == ""
    assert "plan.json" in stderr
    assert "missing" in stderr


def test_polish_chapter_missing_style_guide_warns_and_uses_default(tmp_path: Path) -> None:
    root = _workspace_with_draft(tmp_path)
    (root / "memory" / "style_guide.md").unlink()

    code, stdout, stderr = _run_cli(["polish-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert "warning: memory/style_guide.md is missing" in stdout
    assert (root / "memory" / "chapters" / "001" / "polished.md").is_file()


def test_polish_chapter_missing_style_guide_injects_chinese_fallback(tmp_path: Path) -> None:
    root = _workspace_with_draft(tmp_path)
    (root / "memory" / "style_guide.md").unlink()
    provider = MockProvider(fake_response="雨声更深，旧车站像在夜里醒来。")

    result = polish_chapter(ChapterPolishingOptions(root=root, chapter_number=1), provider)

    assert "memory/style_guide.md is missing" in result.warnings[0]
    prompt = provider.requests[0].user_prompt
    assert "# 文风设置" in prompt
    assert "## 整体风格" in prompt
    assert "# Style Guide" not in prompt
    assert "## Overall Style" not in prompt


def _workspace_with_draft(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# Inspiration\n\n## Weak Outline\n\n雨夜旧车站传来停播多年的广播声。\n",
        encoding="utf-8",
    )
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    plan_chapter(
        ChapterPlanningOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_chapter_plan_json(1)),
    )
    write_chapter_draft(
        ChapterDraftingOptions(root=root, chapter_number=1),
        MockProvider(fake_response="雨落在旧车站。林澈听见广播，拾起半张车票。"),
    )
    return root


def _read_front_matter(path: Path) -> tuple[dict[str, object], str]:
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    _, metadata_text, body = content.split("---\n", 2)
    return yaml.safe_load(metadata_text), body


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
