from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.providers import MockProvider
from novel.core.revision import ChapterRevisionOptions, revise_chapter
from novel.core.schemas import RevisionLog
from novel.core.workflow import GenerateChapterOptions, generate_chapter
from novel.core.workspace import InitOptions, init_workspace


def test_revise_chapter_from_instruction_creates_polished_version(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)
    original = (root / "memory" / "chapters" / "001" / "polished.md").read_text(encoding="utf-8")

    code, stdout, stderr = _run_cli(
        [
            "revise-chapter",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--instruction",
            "加强悬疑感，但不要改变结尾事件",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote chapter revision" in stdout
    version_path = root / "memory" / "chapters" / "001" / "polished.v2.md"
    assert version_path.is_file()
    assert "status: polished_revision" in version_path.read_text(encoding="utf-8")
    assert (root / "memory" / "chapters" / "001" / "polished.md").read_text(encoding="utf-8") == original


def test_revise_chapter_missing_style_guide_injects_chinese_fallback(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)
    (root / "memory" / "style_guide.md").unlink()
    provider = MockProvider(fake_response="修订后的正文保留广播与车票，并让语气更克制。")

    result = revise_chapter(
        ChapterRevisionOptions(root=root, chapter_number=1, instruction="压低解释性文字"),
        provider,
    )

    assert "memory/style_guide.md is missing" in result.warnings[0]
    prompt = provider.requests[0].user_prompt
    assert "# 文风设置" in prompt
    assert "## 整体风格" in prompt
    assert "# Style Guide" not in prompt
    assert "## Overall Style" not in prompt


def test_revise_chapter_from_audit_creates_revision(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)

    code, stdout, stderr = _run_cli(
        ["revise-chapter", "1", "--path", str(root), "--provider", "mock", "--from-audit"]
    )

    assert code == 0
    assert stderr == ""
    assert (root / "memory" / "chapters" / "001" / "polished.v2.md").is_file()
    log = _revision_log(root)
    assert log.revisions[0].from_audit is True
    assert log.revisions[0].audit_file == "audit.json"


def test_revise_chapter_requires_instruction_or_audit(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)

    code, stdout, stderr = _run_cli(["revise-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 1
    assert stdout == ""
    assert "provide --instruction" in stderr


def test_revise_chapter_save_as_version_can_create_draft_version(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)
    original = (root / "memory" / "chapters" / "001" / "draft.md").read_text(encoding="utf-8")

    code, stdout, stderr = _run_cli(
        [
            "revise-chapter",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--target",
            "draft",
            "--save-as-version",
            "--instruction",
            "压缩解释性文字",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "draft.v2.md" in stdout
    version_path = root / "memory" / "chapters" / "001" / "draft.v2.md"
    assert version_path.is_file()
    assert "status: draft_revision" in version_path.read_text(encoding="utf-8")
    assert (root / "memory" / "chapters" / "001" / "draft.md").read_text(encoding="utf-8") == original


def test_revise_chapter_revision_log_is_created_and_appended(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)

    assert _run_cli(
        [
            "revise-chapter",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--instruction",
            "第一轮修订",
        ]
    )[0] == 0
    assert _run_cli(
        [
            "revise-chapter",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--instruction",
            "第二轮修订",
        ]
    )[0] == 0

    log = _revision_log(root)
    assert len(log.revisions) == 2
    assert log.revisions[0].output_file == "polished.v2.md"
    assert log.revisions[1].output_file == "polished.v3.md"
    assert log.revisions[1].instruction == "第二轮修订"


def test_revise_chapter_loop_requires_explicit_confirmation(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "revise-chapter",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--instruction",
            "循环修订",
            "--max-rounds",
            "2",
        ]
    )

    assert code == 1
    assert stdout == ""
    assert "--confirm-loop" in stderr


def test_revise_chapter_search_context_protects_hidden_truth(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)
    provider = MockProvider(fake_response="修订后的正文仍保留广播与车票，只让林澈意识到雨夜还有未解之事。")

    result = revise_chapter(
        ChapterRevisionOptions(
            root=root,
            chapter_number=1,
            instruction="保持悬疑",
            use_search_context=True,
        ),
        provider,
    )

    prompt = provider.requests[0].user_prompt
    assert "Context bundle" in prompt
    assert "旧车站在特定雨夜会短暂连接过去的时间层" not in prompt
    assert result.context_report_path is not None
    assert result.context_report_path.is_file()


def test_revise_chapter_loop_writes_versions_and_run_log(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "revise-chapter",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--instruction",
            "循环修订",
            "--max-rounds",
            "2",
            "--confirm-loop",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote revision loop log" in stdout
    assert (root / "memory" / "chapters" / "001" / "polished.v2.md").is_file()
    assert (root / "memory" / "chapters" / "001" / "polished.v3.md").is_file()
    logs = list((root / "memory" / "chapters" / "001").glob("revision_loop_*.json"))
    assert logs
    payload = json.loads(logs[0].read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert len(payload["steps"]) == 2


def _workspace_with_generated_chapter(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# Inspiration\n\n## Weak Outline\n\n雨夜旧车站传来停播多年的广播声。\n",
        encoding="utf-8",
    )
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    result = generate_chapter(
        GenerateChapterOptions(root=root, chapter_number=1, provider_name="mock")
    )
    assert result.run_log.status == "completed"
    return root


def _revision_log(root: Path) -> RevisionLog:
    path = root / "memory" / "chapters" / "001" / "revision_log.json"
    assert path.is_file()
    return RevisionLog.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
