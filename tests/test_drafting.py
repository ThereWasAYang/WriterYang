from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import yaml

from novel.cli import main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.drafting import (
    ChapterDraftingOptions,
    write_chapter_draft,
)
from novel.core.planning import ChapterPlanningOptions, default_mock_chapter_plan_json, plan_chapter
from novel.core.providers import MockProvider
from novel.core.workspace import InitOptions, init_workspace


def test_mock_provider_can_generate_chapter_draft(tmp_path: Path) -> None:
    root = _workspace_with_plan(tmp_path)
    provider = MockProvider(fake_response="雨声压低了旧车站的轮廓。")

    result = write_chapter_draft(
        ChapterDraftingOptions(
            root=root,
            chapter_number=1,
            instruction="加强压抑感，减少解释性文字",
            target_words=3000,
            style_note="句子短一些",
        ),
        provider,
    )

    assert result.draft_path == root.resolve() / "memory" / "chapters" / "001" / "draft.md"
    assert "雨声压低了旧车站的轮廓。" in result.draft_markdown
    assert "加强压抑感" in provider.requests[0].user_prompt
    assert "目标字数：3000" in provider.requests[0].user_prompt
    assert "句子短一些" in provider.requests[0].user_prompt
    assert "不要输出大纲、解释、分析或 JSON" in provider.requests[0].system_prompt


def test_write_chapter_cli_creates_draft_with_front_matter(tmp_path: Path) -> None:
    root = _workspace_with_plan(tmp_path)

    code, stdout, stderr = _run_cli(["write-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert "Wrote chapter draft:" in stdout
    draft_path = root / "memory" / "chapters" / "001" / "draft.md"
    assert draft_path.is_file()
    metadata, body = _read_front_matter(draft_path)
    assert metadata["chapter_number"] == 1
    assert metadata["title"] == "雨夜旧车站"
    assert metadata["status"] == "draft"
    assert metadata["created_by"] == "writer_agent"
    assert metadata["based_on"] == "plan.json"
    assert "created_at" in metadata
    assert "# 第一章 雨夜旧车站" in body
    assert "雨落在旧车站" in body
    assert "raw_response" not in body
    assert "provider" not in body.lower()


def test_write_chapter_refuses_to_overwrite_existing_draft_by_default(tmp_path: Path) -> None:
    root = _workspace_with_plan(tmp_path)
    first, _, _ = _run_cli(["write-chapter", "1", "--path", str(root), "--provider", "mock"])
    draft_path = root / "memory" / "chapters" / "001" / "draft.md"
    original = draft_path.read_text(encoding="utf-8")

    second, stdout, stderr = _run_cli(["write-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert first == 0
    assert second == 1
    assert stdout == ""
    assert "already exists" in stderr
    assert draft_path.read_text(encoding="utf-8") == original


def test_write_chapter_force_overwrites_existing_draft(tmp_path: Path) -> None:
    root = _workspace_with_plan(tmp_path)
    _run_cli(["write-chapter", "1", "--path", str(root), "--provider", "mock"])
    draft_path = root / "memory" / "chapters" / "001" / "draft.md"
    draft_path.write_text("manual edit\n", encoding="utf-8")

    code, stdout, stderr = _run_cli(
        ["write-chapter", "1", "--path", str(root), "--provider", "mock", "--force"]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote chapter draft:" in stdout
    assert "manual edit" not in draft_path.read_text(encoding="utf-8")


def test_write_chapter_input_instruction(tmp_path: Path) -> None:
    root = _workspace_with_plan(tmp_path)
    input_path = tmp_path / "chapter_request.txt"
    input_path.write_text("加强压抑感，减少解释性文字", encoding="utf-8")

    code, stdout, stderr = _run_cli(
        [
            "write-chapter",
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
    assert "Wrote chapter draft:" in stdout


def test_write_chapter_missing_plan_has_clear_error(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))

    code, stdout, stderr = _run_cli(["write-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 1
    assert stdout == ""
    assert "plan.json is missing" in stderr


def test_write_chapter_missing_style_guide_warns_and_uses_default(tmp_path: Path) -> None:
    root = _workspace_with_plan(tmp_path)
    (root / "memory" / "style_guide.md").unlink()

    code, stdout, stderr = _run_cli(["write-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert "warning: memory/style_guide.md is missing" in stdout
    assert (root / "memory" / "chapters" / "001" / "draft.md").is_file()


def test_write_chapter_target_words_and_style_note_cli(tmp_path: Path) -> None:
    root = _workspace_with_plan(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "write-chapter",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--target-words",
            "3000",
            "--style-note",
            "加强潮湿感",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote chapter draft:" in stdout


def test_write_chapter_search_context_writes_report_and_protects_hidden_truth(tmp_path: Path) -> None:
    root = _workspace_with_plan(tmp_path)
    provider = MockProvider(fake_response="雨声压低了旧车站的轮廓。")

    result = write_chapter_draft(
        ChapterDraftingOptions(
            root=root,
            chapter_number=1,
            instruction="揭示隐藏真相",
            use_search_context=True,
        ),
        provider,
    )

    assert result.context_report_path is not None
    assert result.context_report_path.is_file()
    prompt = provider.requests[0].user_prompt
    assert "Context bundle" in prompt
    assert "旧车站在特定雨夜会短暂连接过去的时间层" not in prompt
    assert "广播来自过去的时间层" not in prompt
    report = result.context_report_path.read_text(encoding="utf-8")
    assert "truth_station_overlap" in report
    assert "protected from drafting output" in report


def _workspace_with_plan(tmp_path: Path) -> Path:
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
