from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import uuid

from docx import Document
from novel.cli import main
from novel.core.artifact_store import ArtifactStore, combined_sha256, write_lifecycle
from novel.core.contracts import (
    AcceptanceCommit,
    ArtifactKind,
    AuditBinding,
    ChapterLifecycle,
    StateProposalBinding,
    TaskId,
)
from novel.core.schemas import ExportManifest
from novel.core.timeutil import utc_now
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
    assert "warning: chapter 3 has no accepted.md; skipped" in stdout
    output = (root / "exports" / "novel.md").read_text(encoding="utf-8")
    assert "第三章正文" not in output


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
        ["export", "markdown", "--path", str(root), "--from", "2", "--to", "3"]
    )

    assert code == 0
    assert stderr == ""
    output = (root / "exports" / "novel.md").read_text(encoding="utf-8")
    assert "第一章正文" not in output
    assert "第二章正文" in output
    assert "第三章正文" not in output


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


def test_export_rejects_accepted_content_changed_after_commit(tmp_path: Path) -> None:
    root = _workspace_with_chapters(tmp_path)
    accepted = root / "memory" / "chapters" / "001" / "accepted.md"
    accepted.write_text(accepted.read_text(encoding="utf-8") + "\n手工篡改。\n", encoding="utf-8")

    code, stdout, stderr = _run_cli(["export", "markdown", "--path", str(root)])

    assert code == 1
    assert stdout == ""
    assert "accepted.md is stale" in stderr


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
    assert "warning: chapter 3 has no accepted.md; skipped" in stdout
    text = _docx_text(root / "exports" / "novel.docx")
    assert "第三章正文" not in text


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
    content = (
        "---\n"
        f"chapter_number: {chapter_number}\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        f"status: {status}\n"
        "created_by: polish_agent\n"
        "based_on: draft.md\n"
        "created_at: 2026-05-23T00:00:00Z\n"
        "---\n\n"
        f"# 第{chapter_number}章 {title}\n\n"
        f"{body}\n"
    )
    markdown = content
    chapter_dir.joinpath("polished.md").write_text(markdown, encoding="utf-8")
    if status != "accepted":
        return
    store = ArtifactStore(root)
    plan = store.create(
        chapter_number=chapter_number,
        kind=ArtifactKind.PLAN,
        content=b"{}\n",
        suffix=".json",
        producer_task_id=TaskId.PLAN,
    )
    candidate = store.create(
        chapter_number=chapter_number,
        kind=ArtifactKind.CANDIDATE,
        content=markdown.encode("utf-8"),
        suffix=".md",
        producer_task_id=TaskId.POLISH,
        inputs=[plan],
    )
    audit_content = json.dumps(
        {
            "schema_version": 3,
            "chapter_number": chapter_number,
            "audited_file": "polished.md",
            "overall_status": "passed",
            "summary": "通过。",
            "issues": [],
            "created_at": "2026-05-23T00:00:00Z",
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8") + b"\n"
    audit = store.create(
        chapter_number=chapter_number,
        kind=ArtifactKind.AUDIT,
        content=audit_content,
        suffix=".json",
        producer_task_id=TaskId.AUDIT,
        inputs=[candidate, plan],
    )
    proposal = store.create(
        chapter_number=chapter_number,
        kind=ArtifactKind.STATE_PROPOSAL,
        content=b"{}\n",
        suffix=".json",
        producer_task_id=TaskId.STATE_UPDATE,
        inputs=[candidate, audit],
    )
    memory = store.create(
        chapter_number=chapter_number,
        kind=ArtifactKind.CHAPTER_MEMORY,
        content=b"{}\n",
        suffix=".json",
        producer_task_id=TaskId.CHAPTER_MEMORY,
        inputs=[candidate, audit, proposal],
    )
    snapshot_hash = "0" * 64
    acceptance = AcceptanceCommit(
        commit_id=f"accept_{uuid.uuid4().hex}",
        transaction_id=f"tx_{uuid.uuid4().hex}",
        session_id="session_20260523_000000_000001",
        chapter_number=chapter_number,
        candidate=candidate,
        audit=audit,
        state_proposal=proposal,
        chapter_memory=memory,
        pre_state_sha256=snapshot_hash,
        pre_timeline_sha256=snapshot_hash,
        post_state_sha256=snapshot_hash,
        post_timeline_sha256=snapshot_hash,
        accepted_content_sha256=candidate.sha256,
        created_at=utc_now(),
    )
    acceptance_bytes = (acceptance.model_dump_json(indent=2) + "\n").encode("utf-8")
    acceptance_ref = store.create(
        chapter_number=chapter_number,
        kind=ArtifactKind.ACCEPTANCE,
        content=acceptance_bytes,
        suffix=".json",
        authority="deterministic",
        inputs=[candidate, audit, proposal, memory],
    )
    context_hash = combined_sha256(snapshot_hash, snapshot_hash, candidate.sha256)
    write_lifecycle(
        root,
        ChapterLifecycle(
            chapter_number=chapter_number,
            active_plan=plan,
            active_candidate=candidate,
            active_audit=AuditBinding(
                audit=audit,
                candidate=candidate,
                context_snapshot_hash=context_hash,
                policy_version="test",
            ),
            active_state_proposal=StateProposalBinding(
                proposal=proposal,
                candidate=candidate,
                audit=audit,
                base_state_sha256=snapshot_hash,
                base_timeline_sha256=snapshot_hash,
            ),
            active_acceptance=acceptance_ref,
            lineages=[store.load_lineage(ref) for ref in (plan, candidate, audit, proposal, memory, acceptance_ref)],
            updated_at=utc_now(),
        ),
    )
    (chapter_dir / "accepted.md").write_bytes(markdown.encode("utf-8"))
    (chapter_dir / "acceptance.json").write_bytes(acceptance_bytes)


def _docx_text(path: Path) -> str:
    document = Document(path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
