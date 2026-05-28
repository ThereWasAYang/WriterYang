from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.io import load_json_model
from novel.core.schemas import AuditReport, CreationArchiveManifest, CreationSession
from novel.core.session import SessionRunOptions, _has_hard_issues
from novel.core import session as session_module
from novel.core.workspace import InitOptions, init_workspace


def test_session_start_creates_single_chapter_outline(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(
        ["session", "start", "写第1章，突出雨夜旧车站", "--path", str(root), "--chapters", "1", "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    assert "outline proposal generated" in stdout
    session = _latest_session(root)
    assert session.status == "outline_proposed"
    assert session.outline_status == "proposed"
    assert session.chapter_range == [1]
    session_dir = root / "memory" / "sessions" / session.session_id
    assert (session_dir / "outline_proposal.json").is_file()
    assert (session_dir / "outline_proposal.md").is_file()


def test_session_start_supports_multi_chapter_and_segments(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, _, stderr = _run_cli(
        [
            "session",
            "start",
            "重写第2章后三段",
            "--path",
            str(root),
            "--chapter",
            "2",
            "--segments",
            "8-10",
            "--provider",
            "mock",
        ]
    )

    assert code == 0
    assert stderr == ""
    session = _latest_session(root)
    assert session.scope_type == "segments"
    assert session.chapter_range == [2]
    assert session.segment_range == [8, 9, 10]


def test_session_run_requires_approved_outline(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(
        ["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"]
    )
    session = _latest_session(root)

    code, stdout, stderr = _run_cli(
        ["session", "run", session.session_id, "--path", str(root), "--provider", "mock"]
    )

    assert code == 1
    assert stdout == ""
    assert "approve the outline" in stderr


def test_session_auto_repair_treats_medium_issues_as_blocking() -> None:
    report = AuditReport.model_validate(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "overall_status": "needs_revision",
            "summary": "中等级别问题需要自动修复。",
            "issues": [
                {
                    "id": "audit_001_medium",
                    "severity": "medium",
                    "type": "plot_logic_issue",
                    "description": "转场逻辑不足。",
                    "evidence": [{"source": "polished.md", "quote": "忽然"}],
                    "suggested_fix": "补足因果过渡。",
                }
            ],
            "created_at": "2026-05-22T00:00:00Z",
        }
    )

    assert _has_hard_issues(report)


def test_session_full_mock_flow_accepts_and_archives(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(
        ["session", "start", "写第1章，突出雨夜旧车站", "--path", str(root), "--chapters", "1", "--provider", "mock"]
    )
    session = _latest_session(root)
    assert _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])[0] == 0

    run_code, run_stdout, run_stderr = _run_cli(
        ["session", "run", session.session_id, "--path", str(root), "--provider", "mock"]
    )
    assert run_code == 0
    assert run_stderr == ""
    assert "ready for user review" in run_stdout

    accept_code, _, accept_stderr = _run_cli(
        ["session", "accept", session.session_id, "--path", str(root), "--provider", "mock"]
    )
    assert accept_code == 0
    assert accept_stderr == ""

    archive_code, _, archive_stderr = _run_cli(["session", "archive", session.session_id, "--path", str(root)])
    assert archive_code == 0
    assert archive_stderr == ""
    archived = load_json_model(root / "memory" / "sessions" / session.session_id / "session.json", CreationSession)
    assert archived.status == "archived"
    manifest = load_json_model(root / "memory" / "archive" / session.session_id / "manifest.json", CreationArchiveManifest)
    assert manifest.entries
    assert all(len(entry.sha256) == 64 for entry in manifest.entries)


def test_session_auto_repair_promotes_revision_before_reaudit(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])

    hard = AuditReport.model_validate(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "overall_status": "needs_revision",
            "summary": "需要修复正文。",
            "issues": [
                {
                    "id": "audit_001_medium",
                    "severity": "medium",
                    "type": "plot_logic_issue",
                    "description": "正文实现偏离计划。",
                    "evidence": [{"source": "polished.md", "quote": "旧正文"}],
                    "suggested_fix": "修订正文。",
                }
            ],
            "created_at": "2026-05-22T00:00:00Z",
        }
    )
    passed = AuditReport.model_validate(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "overall_status": "passed",
            "summary": "已通过。",
            "issues": [],
            "created_at": "2026-05-22T00:00:00Z",
        }
    )
    audits = iter([hard, passed])
    chapter_dir = root / "memory" / "chapters" / "001"

    def fake_generate(*args: object, **kwargs: object) -> None:
        chapter_dir.mkdir(parents=True, exist_ok=True)
        (chapter_dir / "polished.md").write_text(
            "---\nchapter_number: 1\ntitle: 测试\nstatus: polished\n---\n\n旧正文\n",
            encoding="utf-8",
        )

    def fake_repair(*args: object, **kwargs: object) -> Path:
        path = chapter_dir / "polished.v2.md"
        path.write_text(
            "---\nchapter_number: 1\ntitle: 测试\nstatus: polished_revision\nbased_on: polished.md\nrevision_id: revision_test\n---\n\n修订后正文\n",
            encoding="utf-8",
        )
        return path

    monkeypatch.setattr(session_module, "_generate_chapter_content", fake_generate)
    monkeypatch.setattr(session_module, "_load_audit", lambda *args: next(audits))
    monkeypatch.setattr(session_module, "_auto_repair_chapter", fake_repair)
    monkeypatch.setattr(session_module, "_audit_chapter_content", lambda *args, **kwargs: None)
    monkeypatch.setattr(session_module, "_propose_state", lambda *args, **kwargs: None)

    result = session_module.run_session(
        SessionRunOptions(root=root, session_id=session.session_id, provider_name="mock", max_auto_revision_rounds=1)
    )

    assert result.session.status == "needs_user_review"
    assert "修订后正文" in (chapter_dir / "polished.md").read_text(encoding="utf-8")
    assert "status: polished\n" in (chapter_dir / "polished.md").read_text(encoding="utf-8")
    assert result.session.revision_history[-1].endswith("polished.v2.md")


def test_session_stops_as_needs_revision_after_unresolved_audit(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    hard = AuditReport.model_validate(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "overall_status": "needs_revision",
            "summary": "仍有问题。",
            "issues": [
                {
                    "id": "audit_001_medium",
                    "severity": "medium",
                    "type": "plot_logic_issue",
                    "description": "需要人工处理。",
                    "evidence": [{"source": "polished.md", "quote": "example"}],
                    "suggested_fix": "修复后重跑。",
                }
            ],
            "created_at": "2026-05-22T00:00:00Z",
        }
    )

    monkeypatch.setattr(session_module, "_generate_chapter_content", lambda *args, **kwargs: None)
    monkeypatch.setattr(session_module, "_load_audit", lambda *args: hard)

    result = session_module.run_session(
        SessionRunOptions(root=root, session_id=session.session_id, provider_name="mock", max_auto_revision_rounds=0)
    )

    assert result.session.status == "needs_revision"
    assert result.session.content_status == "needs_revision"


def test_session_revise_content_can_use_low_audit_issues_when_user_chooses(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    _run_cli(["session", "run", session.session_id, "--path", str(root), "--provider", "mock"])
    audit_path = root / "memory" / "chapters" / "001" / "audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["overall_status"] = "passed"
    audit["issues"] = [
        {
            "id": "audit_001_low",
            "severity": "low",
            "type": "style_mismatch",
            "description": "局部称呼略显突兀。",
            "evidence": [{"source": "polished.md", "quote": "姑娘"}],
            "suggested_fix": "用户选择后再统一称呼。",
        }
    ]
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    code, stdout, stderr = _run_cli(
        ["session", "revise-content", session.session_id, "--path", str(root), "--provider", "mock", "--from-audit"]
    )

    assert code == 0
    assert stderr == ""
    assert "Content revised for user review" in stdout
    assert (root / "memory" / "chapters" / "001" / "revision_log.json").is_file()
    revised = _latest_session(root)
    assert revised.final_output_paths
    assert revised.final_output_paths[-1].endswith(".md")


def test_archived_session_is_immutable(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    _run_cli(["session", "run", session.session_id, "--path", str(root), "--provider", "mock"])
    _run_cli(["session", "accept", session.session_id, "--path", str(root), "--provider", "mock"])
    _run_cli(["session", "archive", session.session_id, "--path", str(root)])

    code, stdout, stderr = _run_cli(
        [
            "session",
            "revise-outline",
            session.session_id,
            "--path",
            str(root),
            "--instruction",
            "改成更紧张",
            "--provider",
            "mock",
        ]
    )

    assert code == 1
    assert stdout == ""
    assert "archived sessions are immutable" in stderr


def _workspace_ready(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# Inspiration\n\n## Weak Outline\n\n雨夜旧车站传来停播多年的广播声。\n",
        encoding="utf-8",
    )
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    return root


def _latest_session(root: Path) -> CreationSession:
    paths = sorted((root / "memory" / "sessions").glob("session_*/session.json"))
    assert paths
    return CreationSession.model_validate(json.loads(paths[-1].read_text(encoding="utf-8")))


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
