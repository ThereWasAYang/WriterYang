from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path

from docx import Document

from novel.cli import main
from novel.core.schemas import ExportManifest
from novel.core.workspace import InitOptions, init_workspace


def test_export_markdown_multiple_accepted_chapters(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    code, stdout, stderr = _run_cli(["export", "markdown", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "Wrote Markdown export:" in stdout
    output = (root / "exports" / "novel.md").read_text(encoding="utf-8")
    assert output.startswith("# 雨夜旧车站\n")
    assert "## 第一章 雨夜旧车站" in output
    assert "## 第二章 桥下的回声" in output
    assert "第三章" not in output
    assert "第一章正文" in output
    assert "第二章正文" in output


def test_export_markdown_chapter_order_is_sorted(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    _run_cli(["export", "markdown", "--path", str(root)])

    output = (root / "exports" / "novel.md").read_text(encoding="utf-8")
    assert output.index("## 第一章 雨夜旧车站") < output.index("## 第二章 桥下的回声")


def test_export_markdown_skips_unaccepted_by_default(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    code, stdout, stderr = _run_cli(["export", "markdown", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "warning: chapter 3 is not accepted; skipped" in stdout
    output = (root / "exports" / "novel.md").read_text(encoding="utf-8")
    assert "第三章正文" not in output


def test_export_markdown_include_unaccepted(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    code, stdout, stderr = _run_cli(
        ["export", "markdown", "--path", str(root), "--include-unaccepted"]
    )

    assert code == 0
    assert stderr == ""
    output = (root / "exports" / "novel.md").read_text(encoding="utf-8")
    assert "## 第三章 未验收章节" in output
    assert "第三章正文" in output


def test_export_markdown_chapters_selector(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    code, stdout, stderr = _run_cli(
        ["export", "markdown", "--path", str(root), "--chapters", "2,1"]
    )

    assert code == 0
    assert stderr == ""
    assert "Chapters: 1, 2" in stdout
    output = (root / "exports" / "novel.md").read_text(encoding="utf-8")
    assert "第一章正文" in output
    assert "第二章正文" in output
    assert "第三章正文" not in output


def test_export_markdown_from_to_selector(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    code, stdout, stderr = _run_cli(
        ["export", "markdown", "--path", str(root), "--from", "2", "--to", "3", "--include-unaccepted"]
    )

    assert code == 0
    assert stderr == ""
    output = (root / "exports" / "novel.md").read_text(encoding="utf-8")
    assert "第一章正文" not in output
    assert "第二章正文" in output
    assert "第三章正文" in output


def test_export_markdown_refuses_to_overwrite_existing_by_default(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)
    first, _, _ = _run_cli(["export", "markdown", "--path", str(root)])
    output_path = root / "exports" / "novel.md"
    original = output_path.read_text(encoding="utf-8")

    second, stdout, stderr = _run_cli(["export", "markdown", "--path", str(root)])

    assert first == 0
    assert second == 1
    assert stdout == ""
    assert "already exists" in stderr
    assert output_path.read_text(encoding="utf-8") == original


def test_export_markdown_force_overwrites_existing(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)
    _run_cli(["export", "markdown", "--path", str(root)])
    output_path = root / "exports" / "novel.md"
    output_path.write_text("manual edit\n", encoding="utf-8")

    code, stdout, stderr = _run_cli(["export", "markdown", "--path", str(root), "--force"])

    assert code == 0
    assert stderr == ""
    assert "manual edit" not in output_path.read_text(encoding="utf-8")
    assert "Wrote Markdown export:" in stdout


def test_export_markdown_updates_manifest(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    _run_cli(["export", "markdown", "--path", str(root), "--title", "导出标题"])

    manifest_path = root / "exports" / "export_manifest.json"
    manifest = ExportManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    assert len(manifest.exports) == 1
    record = manifest.exports[0]
    assert record.type == "markdown"
    assert record.source_chapters == [1, 2]
    assert record.output_path == "exports/novel.md"
    assert record.title == "导出标题"
    assert len(record.source_chapter_details) == 2
    first_detail = record.source_chapter_details[0]
    first_path = root / first_detail.path
    assert first_detail.chapter_number == 1
    assert first_detail.accepted is True
    assert first_detail.sha256 == hashlib.sha256(first_path.read_bytes()).hexdigest()


def test_export_markdown_can_include_toc_volume_and_arabic_chapter_numbers(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "export",
            "markdown",
            "--path",
            str(root),
            "--toc",
            "--volume-title",
            "第一卷 雨声",
            "--chapter-number-style",
            "arabic",
        ]
    )

    assert code == 0
    assert stderr == ""
    output = (root / "exports" / "novel.md").read_text(encoding="utf-8")
    assert "## 目录" in output
    assert "- 第一卷 雨声" in output
    assert "  - [第1章 雨夜旧车站](#第1章-雨夜旧车站)" in output
    assert "## 第一卷 雨声" in output
    assert "## 第1章 雨夜旧车站" in output
    assert "## 第2章 桥下的回声" in output


def test_export_markdown_supports_plain_chapter_number_style(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    code, _, stderr = _run_cli(
        ["export", "markdown", "--path", str(root), "--chapter-number-style", "plain"]
    )

    assert code == 0
    assert stderr == ""
    output = (root / "exports" / "novel.md").read_text(encoding="utf-8")
    assert "## 1. 雨夜旧车站" in output
    assert "## 2. 桥下的回声" in output


def test_export_docx_generates_nonempty_file(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    code, stdout, stderr = _run_cli(["export", "docx", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "Wrote DOCX export:" in stdout
    output_path = root / "exports" / "novel.docx"
    assert output_path.is_file()
    assert output_path.stat().st_size > 0


def test_export_docx_multiple_accepted_chapters(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    _run_cli(["export", "docx", "--path", str(root)])

    text = _docx_text(root / "exports" / "novel.docx")
    assert "雨夜旧车站" in text
    assert "第一章 雨夜旧车站" in text
    assert "第一章正文" in text
    assert "第二章 桥下的回声" in text
    assert "第二章正文" in text


def test_export_docx_skips_unaccepted_by_default(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    code, stdout, stderr = _run_cli(["export", "docx", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "warning: chapter 3 is not accepted; skipped" in stdout
    text = _docx_text(root / "exports" / "novel.docx")
    assert "第三章正文" not in text


def test_export_docx_include_unaccepted(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    code, stdout, stderr = _run_cli(
        ["export", "docx", "--path", str(root), "--include-unaccepted"]
    )

    assert code == 0
    assert stderr == ""
    text = _docx_text(root / "exports" / "novel.docx")
    assert "第三章 未验收章节" in text
    assert "第三章正文" in text


def test_export_docx_refuses_to_overwrite_existing_by_default(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)
    first, _, _ = _run_cli(["export", "docx", "--path", str(root)])
    output_path = root / "exports" / "novel.docx"
    original_size = output_path.stat().st_size

    second, stdout, stderr = _run_cli(["export", "docx", "--path", str(root)])

    assert first == 0
    assert second == 1
    assert stdout == ""
    assert "already exists" in stderr
    assert output_path.stat().st_size == original_size


def test_export_docx_force_overwrites_existing(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)
    _run_cli(["export", "docx", "--path", str(root)])
    output_path = root / "exports" / "novel.docx"
    output_path.write_bytes(b"manual edit")

    code, stdout, stderr = _run_cli(["export", "docx", "--path", str(root), "--force"])

    assert code == 0
    assert stderr == ""
    assert output_path.read_bytes() != b"manual edit"
    assert "Wrote DOCX export:" in stdout


def test_export_docx_updates_manifest(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)

    _run_cli(["export", "docx", "--path", str(root), "--title", "导出标题"])

    manifest = ExportManifest.model_validate(
        json.loads((root / "exports" / "export_manifest.json").read_text(encoding="utf-8"))
    )
    assert len(manifest.exports) == 1
    record = manifest.exports[0]
    assert record.type == "docx"
    assert record.source_chapters == [1, 2]
    assert record.output_path == "exports/novel.docx"
    assert record.title == "导出标题"
    assert len(record.source_chapter_details) == 2
    assert record.source_chapter_details[0].sha256 == hashlib.sha256(
        (root / record.source_chapter_details[0].path).read_bytes()
    ).hexdigest()


def _workspace_with_chapters(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    _write_polished(root, 2, "桥下的回声", "accepted", "第二章正文")
    _write_polished(root, 1, "雨夜旧车站", "accepted", "第一章正文")
    _write_polished(root, 3, "未验收章节", "polished", "第三章正文")
    return root


def _write_polished(root: Path, chapter_number: int, title: str, status: str, body: str) -> None:
    chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    chapter_dir.joinpath("polished.md").write_text(
        "---\n"
        f"chapter_number: {chapter_number}\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        f"status: {status}\n"
        "created_by: polish_agent\n"
        "based_on: draft.md\n"
        "created_at: 2026-05-23T00:00:00Z\n"
        "---\n\n"
        f"# 第{chapter_number}章 {title}\n\n"
        f"{body}\n",
        encoding="utf-8",
    )


def _docx_text(path: Path) -> str:
    document = Document(path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
