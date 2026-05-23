from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
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
