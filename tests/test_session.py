from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.artifact_store import load_lifecycle, sha256_file
from novel.core.io import atomic_write_model_json, load_json_model
from novel.core.schemas import (
    AuditReport,
    CreationArchiveManifest,
    CreationSession,
    EntityState,
    RevisionRouteDecision,
    SessionRewriteEvent,
    SessionRewriteEvents,
)
from novel.core.session import (
    CreationSessionError,
    SessionActionOptions,
    SessionInstructionOptions,
    SessionRewriteControlOptions,
    SessionResult,
    SessionRunOptions,
    SessionStartOptions,
    _has_hard_issues,
    load_session_progress,
    load_rewrite_events,
    request_session_cancel,
)
from novel.core import session as session_module
from novel.core import transactions as transaction_module
from novel.core.projection import load_projection
from novel.core.workspace import InitOptions, init_workspace
import pytest


def test_session_start_creates_single_chapter_outline(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(
        ["session", "start", "写第1章，突出雨夜旧车站", "--path", str(root), "--chapters", "1", "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    assert "outline proposal generated" in stdout
    session = _latest_session(root)
    assert session.phase.value == "awaiting_outline_approval"
    assert session.chapter_range == [1]
    session_dir = root / "memory" / "sessions" / session.session_id
    assert (session_dir / "outline_proposal.json").is_file()
    assert (session_dir / "outline_proposal.md").is_file()


def test_session_start_requires_chapter_creation_scope(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "session",
            "start",
            "继续写第2章",
            "--path",
            str(root),
            "--chapters",
            "2",
            "--provider",
            "mock",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "outline proposal generated" in stdout
    session = _latest_session(root)
    assert session.chapter_range == [2]


def test_session_run_requires_approved_outline(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)

    code, stdout, stderr = _run_cli(["session", "run", session.session_id, "--path", str(root), "--provider", "mock"])

    assert code == 1
    assert stdout == ""
    assert "approve the outline" in stderr


def test_session_start_cleans_incomplete_session_dir_on_outline_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _workspace_ready(tmp_path)

    def fail_outline(root_arg: Path, session: CreationSession, *args: object, **kwargs: object) -> CreationSession:
        session_dir = root_arg / "memory" / "sessions" / session.session_id
        assert session_dir.is_dir()
        (session_dir / "partial.txt").write_text("partial\n", encoding="utf-8")
        raise CreationSessionError("simulated outline failure")

    monkeypatch.setattr(session_module, "_write_outline_proposal", fail_outline)

    with pytest.raises(CreationSessionError, match="simulated outline failure"):
        session_module.start_session(
            SessionStartOptions(
                root=root,
                user_intent="写第1章",
                chapter_range=(1,),
                provider_name="mock",
            )
        )

    assert not list((root / "memory" / "sessions").glob("session_*"))


def test_session_approve_outline_rolls_back_multi_chapter_plan_write_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _workspace_ready(tmp_path)
    result = session_module.start_session(
        SessionStartOptions(
            root=root,
            user_intent="写第1到2章",
            chapter_range=(1, 2),
            provider_name="mock",
        )
    )
    session = result.session
    fail_path = root.resolve() / "memory" / "chapters" / "002" / "plan.json"
    original_atomic_write_text = session_module.atomic_write_text

    def flaky_atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
        if path.resolve() == fail_path:
            raise OSError("simulated plan write failure")
        original_atomic_write_text(path, content, encoding=encoding)

    monkeypatch.setattr(session_module, "atomic_write_text", flaky_atomic_write_text)

    with pytest.raises(CreationSessionError, match="outline approval failed"):
        session_module.approve_outline(SessionActionOptions(root=root, session_id=session.session_id))

    session_dir = root / "memory" / "sessions" / session.session_id
    assert not (root / "memory" / "chapters" / "001" / "plan.json").exists()
    assert not (root / "memory" / "chapters" / "001" / "plan.md").exists()
    assert not (root / "memory" / "chapters" / "002" / "plan.json").exists()
    assert not (root / "memory" / "chapters" / "002" / "plan.md").exists()
    assert not (session_dir / "approved_outline.json").exists()
    assert not (session_dir / "approved_outline.md").exists()
    stored = load_json_model(session_dir / "session.json", CreationSession)
    assert stored.phase.value == "awaiting_outline_approval"


def test_session_approve_outline_force_rolls_back_overwritten_plan_and_cleans_backups(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _workspace_ready(tmp_path)
    chapter_dir = root / "memory" / "chapters" / "001"
    chapter_dir.mkdir(parents=True)
    original_plan_json = '{"original": true}\n'
    original_plan_md = "# 旧正式计划\n"
    (chapter_dir / "plan.json").write_text(original_plan_json, encoding="utf-8")
    (chapter_dir / "plan.md").write_text(original_plan_md, encoding="utf-8")
    result = session_module.start_session(
        SessionStartOptions(
            root=root,
            user_intent="重做第1章大纲",
            chapter_range=(1,),
            provider_name="mock",
        )
    )
    session = result.session
    fail_path = root.resolve() / "memory" / "sessions" / session.session_id / "approved_outline.json"
    original_atomic_write_text = session_module.atomic_write_text

    def flaky_atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
        if path.resolve() == fail_path:
            raise OSError("simulated approved outline write failure")
        original_atomic_write_text(path, content, encoding=encoding)

    monkeypatch.setattr(session_module, "atomic_write_text", flaky_atomic_write_text)

    with pytest.raises(CreationSessionError, match="outline approval failed"):
        session_module.approve_outline(SessionActionOptions(root=root, session_id=session.session_id, force=True))

    session_dir = root / "memory" / "sessions" / session.session_id
    assert (chapter_dir / "plan.json").read_text(encoding="utf-8") == original_plan_json
    assert (chapter_dir / "plan.md").read_text(encoding="utf-8") == original_plan_md
    assert not (session_dir / "approved_outline.json").exists()
    assert not (session_dir / "approved_outline.md").exists()
    assert not list(chapter_dir.glob("plan.json.bak_*"))
    assert not list(chapter_dir.glob("plan.md.bak_*"))
    stored = load_json_model(session_dir / "session.json", CreationSession)
    assert stored.phase.value == "awaiting_outline_approval"


def test_session_approve_outline_rolls_back_if_session_status_write_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _workspace_ready(tmp_path)
    result = session_module.start_session(
        SessionStartOptions(
            root=root,
            user_intent="写第1章",
            chapter_range=(1,),
            provider_name="mock",
        )
    )
    session = result.session
    session_dir = root / "memory" / "sessions" / session.session_id
    fail_path = (session_dir / "session.json").resolve()
    original_session_text = (session_dir / "session.json").read_text(encoding="utf-8")
    original_atomic_write_text = session_module.atomic_write_text
    failed_once = False

    def flaky_atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
        nonlocal failed_once
        if path.resolve() == fail_path and not failed_once:
            failed_once = True
            raise OSError("simulated session status write failure")
        original_atomic_write_text(path, content, encoding=encoding)

    monkeypatch.setattr(session_module, "atomic_write_text", flaky_atomic_write_text)

    with pytest.raises(CreationSessionError, match="outline approval failed"):
        session_module.approve_outline(SessionActionOptions(root=root, session_id=session.session_id))

    assert (session_dir / "session.json").read_text(encoding="utf-8") == original_session_text
    assert not (root / "memory" / "chapters" / "001" / "plan.json").exists()
    assert not (root / "memory" / "chapters" / "001" / "plan.md").exists()
    assert not (session_dir / "approved_outline.json").exists()
    assert not (session_dir / "approved_outline.md").exists()
    stored = load_json_model(session_dir / "session.json", CreationSession)
    assert stored.phase.value == "awaiting_outline_approval"


def test_session_auto_repair_treats_medium_issues_as_blocking() -> None:
    report = AuditReport.model_validate(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "audited_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
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
    assert archived.phase.value == "archived"
    manifest = load_json_model(
        root / "memory" / "archive" / session.session_id / "manifest.json", CreationArchiveManifest
    )
    assert manifest.entries
    assert all(len(entry.sha256) == 64 for entry in manifest.entries)
    assert load_rewrite_events(root, session.session_id) == []
    progress = load_session_progress(root, session.session_id)
    assert progress.status == "completed"
    assert progress.current_stage == "completed"
    assert progress.started_at is not None
    assert progress.completed_at is not None
    assert {event.stage for event in progress.events} >= {
        "session_start",
        "chapter_start",
        "draft",
        "single_pass_final",
        "audit",
        "completed",
    }


def test_multi_chapter_session_uses_projection_and_commits_atomically(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "连续写第1至2章", "--path", str(root), "--chapters", "1,2", "--provider", "mock"])
    session = _latest_session(root)
    assert _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])[0] == 0
    assert _run_cli(["session", "run", session.session_id, "--path", str(root), "--provider", "mock"])[0] == 0

    canonical = load_json_model(root / "memory" / "state" / "current_state.json", EntityState)
    projection = load_projection(root, session.session_id)
    chapter_two = load_lifecycle(root, 2)

    assert canonical.story_position.latest_chapter == 0
    assert projection.checkpoints[-1].chapter_number == 2
    assert chapter_two is not None and chapter_two.active_state_proposal is not None
    assert chapter_two.active_state_proposal.base_state_sha256 == projection.checkpoints[-1].before_state_sha256

    assert _run_cli(["session", "accept", session.session_id, "--path", str(root), "--provider", "mock"])[0] == 0
    committed = load_json_model(root / "memory" / "state" / "current_state.json", EntityState)
    assert committed.story_position.latest_chapter == 2
    for chapter_number in (1, 2):
        chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
        assert (chapter_dir / "accepted.md").is_file()
        assert (chapter_dir / "acceptance.json").is_file()


def test_multi_chapter_acceptance_rolls_back_every_chapter_on_late_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "连续写两章", "--path", str(root), "--chapters", "1,2", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    _run_cli(["session", "run", session.session_id, "--path", str(root), "--provider", "mock"])
    state_path = root / "memory" / "state" / "current_state.json"
    timeline_path = root / "memory" / "state" / "timeline.json"
    before_state = state_path.read_bytes()
    before_timeline = timeline_path.read_bytes()
    original_write = transaction_module.atomic_write_bytes
    failed = False

    def fail_chapter_two_acceptance(path: Path, content: bytes) -> None:
        nonlocal failed
        if path.resolve() == (root / "memory" / "chapters" / "002" / "accepted.md").resolve() and not failed:
            failed = True
            raise OSError("injected late commit failure")
        original_write(path, content)

    monkeypatch.setattr(transaction_module, "atomic_write_bytes", fail_chapter_two_acceptance)

    with pytest.raises(CreationSessionError, match="rolled back"):
        session_module.accept_session(SessionActionOptions(root=root, session_id=session.session_id))

    assert state_path.read_bytes() == before_state
    assert timeline_path.read_bytes() == before_timeline
    for chapter_number in (1, 2):
        chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
        assert not (chapter_dir / "accepted.md").exists()
        assert not (chapter_dir / "acceptance.json").exists()
    stored = load_json_model(root / "memory" / "sessions" / session.session_id / "session.json", CreationSession)
    assert stored.phase.value == "ready_to_commit"


def test_session_accept_rejects_working_content_changed_after_review(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第一章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    _run_cli(["session", "run", session.session_id, "--path", str(root), "--provider", "mock"])
    polished = root / "memory" / "chapters" / "001" / "polished.md"
    polished.write_text(polished.read_text(encoding="utf-8") + "\n审阅后手工改动。\n", encoding="utf-8")

    with pytest.raises(CreationSessionError, match="no longer matches reviewed artifact"):
        session_module.accept_session(SessionActionOptions(root=root, session_id=session.session_id))

    assert not (root / "memory" / "chapters" / "001" / "accepted.md").exists()


def test_find_latest_active_session_prefers_generated_content_over_newer_outline(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(
        ["session", "start", "写第1章，突出雨夜旧车站", "--path", str(root), "--chapters", "1", "--provider", "mock"]
    )
    generated_session = _latest_session(root)
    assert _run_cli(["session", "approve-outline", generated_session.session_id, "--path", str(root)])[0] == 0
    assert _run_cli(["session", "run", generated_session.session_id, "--path", str(root), "--provider", "mock"])[0] == 0
    _run_cli(["session", "start", "重做第1章大纲", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    outline_session = _latest_session(root)
    corrupt_session_dir = root / "memory" / "sessions" / "session_99991231_235959_999999"
    corrupt_session_dir.mkdir(parents=True)
    (corrupt_session_dir / "session.json").write_text("{not-json", encoding="utf-8")

    preferred = session_module.find_latest_active_session(root)
    latest = session_module.find_latest_active_session(root, prefer_generated=False)

    assert preferred is not None
    assert preferred.session.session_id == generated_session.session_id
    assert preferred.session.phase.value == "awaiting_content_review"
    assert latest is not None
    assert latest.session.session_id == outline_session.session_id


def test_find_latest_active_session_returns_none_without_valid_active_sessions(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    corrupt_session_dir = root / "memory" / "sessions" / "session_99991231_235959_999999"
    corrupt_session_dir.mkdir(parents=True)
    (corrupt_session_dir / "session.json").write_text("{not-json", encoding="utf-8")

    assert session_module.find_latest_active_session(root) is None


def test_session_run_writes_progress_and_honors_cancel_at_boundary(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    chapter_dir = root / "memory" / "chapters" / "001"

    def fake_generate(root_arg: Path, chapter_number: int, *args: object, **kwargs: object) -> None:
        chapter_dir.mkdir(parents=True, exist_ok=True)
        (chapter_dir / "polished.md").write_text(
            "---\nchapter_number: 1\ntitle: 测试\nstatus: polished\n---\n\n已完成的安全边界前输出\n",
            encoding="utf-8",
        )
        request_session_cancel(root_arg, session.session_id)

    def fail_load_audit(*args: object, **kwargs: object) -> AuditReport:
        raise AssertionError("cancel should stop before loading audit at the next boundary")

    monkeypatch.setattr(session_module, "_generate_chapter_content", fake_generate)
    monkeypatch.setattr(session_module, "_load_audit", fail_load_audit)

    result = session_module.run_session(
        SessionRunOptions(root=root, session_id=session.session_id, provider_name="mock")
    )

    progress = load_session_progress(root, session.session_id)
    assert result.session.phase.value == "cancelled"
    assert result.session.final_output_paths == []
    assert progress.status == "cancelled"
    assert progress.cancel_requested_at is not None
    assert progress.completed_at is not None
    assert (chapter_dir / "polished.md").is_file()
    assert "安全边界前输出" in (chapter_dir / "polished.md").read_text(encoding="utf-8")


def test_session_auto_repair_promotes_revision_before_reaudit(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])

    hard = AuditReport.model_validate(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "audited_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
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
                    "source_layer": "polished",
                    "blocking_reason": "正文明确偏离已批准计划",
                    "evidence_strength": "strong",
                    "is_hard_blocker": True,
                    "confidence": 0.95,
                }
            ],
            "created_at": "2026-05-22T00:00:00Z",
        }
    )
    passed = AuditReport.model_validate(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "audited_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
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
        path = chapter_dir / "candidates" / ("candidate_art_" + "1" * 32 + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
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
    monkeypatch.setattr(
        session_module, "_capture_and_advance_projection", lambda root, session, chapter, projection: projection
    )

    result = session_module.run_session(
        SessionRunOptions(root=root, session_id=session.session_id, provider_name="mock", max_auto_revision_rounds=1)
    )

    assert result.session.phase.value == "awaiting_content_review"
    assert "修订后正文" in (chapter_dir / "polished.md").read_text(encoding="utf-8")
    assert "status: polished\n" in (chapter_dir / "polished.md").read_text(encoding="utf-8")
    assert "/candidates/candidate_art_" in result.session.revision_history[-1]
    events = load_rewrite_events(root, session.session_id)
    assert len(events) == 1
    assert events[0].action == "revision_rewrite"
    assert events[0].status == "completed"
    assert events[0].rejected_text_snapshot_path
    assert "旧正文" in (root / events[0].rejected_text_snapshot_path).read_text(encoding="utf-8")
    assert events[0].blocking_issues[0].description == "正文实现偏离计划。"


def test_session_auto_replan_records_rewrite_event(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    hard = AuditReport.model_validate(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "audited_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            "overall_status": "needs_revision",
            "summary": "计划层问题。",
            "issues": [
                {
                    "id": "audit_001_high",
                    "severity": "high",
                    "type": "premature_reveal",
                    "description": "大纲导致隐藏真相过早暴露。",
                    "evidence": [{"source": "plan.json", "quote": "隐藏真相"}],
                    "suggested_fix": "重写大纲并弱化伏笔。",
                    "source_layer": "plan",
                    "blocking_reason": "已批准计划直接安排过早揭示",
                    "evidence_strength": "strong",
                    "is_hard_blocker": True,
                    "confidence": 0.96,
                }
            ],
            "created_at": "2026-05-22T00:00:00Z",
        }
    )
    passed = AuditReport.model_validate(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "audited_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
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
            "---\nchapter_number: 1\ntitle: 测试\nstatus: polished\n---\n\n重规划前正文\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(session_module, "_generate_chapter_content", fake_generate)
    monkeypatch.setattr(session_module, "_load_audit", lambda *args: next(audits))
    monkeypatch.setattr(session_module, "_should_replan_chapter", lambda *args: True)
    monkeypatch.setattr(session_module, "_auto_replan_chapter", lambda *args, **kwargs: None)
    monkeypatch.setattr(session_module, "_propose_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        session_module, "_capture_and_advance_projection", lambda root, session, chapter, projection: projection
    )

    result = session_module.run_session(
        SessionRunOptions(root=root, session_id=session.session_id, provider_name="mock", max_auto_revision_rounds=1)
    )

    assert result.session.phase.value == "awaiting_content_review"
    events = load_rewrite_events(root, session.session_id)
    assert len(events) == 1
    assert events[0].action == "plot_replan"
    assert events[0].status == "completed"
    assert events[0].rejected_text_snapshot_path
    assert "重规划前正文" in (root / events[0].rejected_text_snapshot_path).read_text(encoding="utf-8")


def test_session_unresolved_auto_repair_marks_rewrite_event_unresolved(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    hard = AuditReport.model_validate(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "audited_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            "overall_status": "needs_revision",
            "summary": "仍有问题。",
            "issues": [
                {
                    "id": "audit_001_medium",
                    "severity": "medium",
                    "type": "state_conflict",
                    "description": "修订后仍冲突。",
                    "evidence": [{"source": "polished.md", "quote": "冲突"}],
                    "suggested_fix": "继续修复。",
                    "source_layer": "polished",
                    "blocking_reason": "正文与当前状态存在明确冲突",
                    "evidence_strength": "strong",
                    "is_hard_blocker": True,
                    "confidence": 0.9,
                }
            ],
            "created_at": "2026-05-22T00:00:00Z",
        }
    )
    audits = iter([hard, hard])
    chapter_dir = root / "memory" / "chapters" / "001"

    def fake_generate(*args: object, **kwargs: object) -> None:
        chapter_dir.mkdir(parents=True, exist_ok=True)
        (chapter_dir / "polished.md").write_text(
            "---\nchapter_number: 1\ntitle: 测试\nstatus: polished\n---\n\n仍有冲突的原文\n",
            encoding="utf-8",
        )

    def fake_repair(*args: object, **kwargs: object) -> Path:
        path = chapter_dir / "candidates" / ("candidate_art_" + "2" * 32 + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nchapter_number: 1\ntitle: 测试\nstatus: polished_revision\nbased_on: polished.md\n---\n\n仍有冲突的修订\n",
            encoding="utf-8",
        )
        return path

    monkeypatch.setattr(session_module, "_generate_chapter_content", fake_generate)
    monkeypatch.setattr(session_module, "_load_audit", lambda *args: next(audits))
    monkeypatch.setattr(session_module, "_auto_repair_chapter", fake_repair)
    monkeypatch.setattr(session_module, "_audit_chapter_content", lambda *args, **kwargs: None)

    result = session_module.run_session(
        SessionRunOptions(root=root, session_id=session.session_id, provider_name="mock", max_auto_revision_rounds=1)
    )

    assert result.session.phase.value == "awaiting_content_review"
    events = load_rewrite_events(root, session.session_id)
    assert len(events) == 1
    assert events[0].status == "unresolved"
    assert events[0].rejected_text_snapshot_path
    assert "仍有冲突的原文" in (root / events[0].rejected_text_snapshot_path).read_text(encoding="utf-8")
    code, stdout, stderr = _run_cli(["session", "show", session.session_id, "--path", str(root), "--json"])
    assert code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["rewrite_events"][0]["status"] == "unresolved"


def test_session_stops_as_needs_revision_after_unresolved_audit(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    hard = AuditReport.model_validate(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "audited_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
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

    assert result.session.phase.value == "awaiting_content_review"


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
    assert "Content revised, audited, and ready for user review" in stdout
    assert (root / "memory" / "chapters" / "001" / "revision_log.json").is_file()
    revised = _latest_session(root)
    assert revised.phase.value == "awaiting_content_review"
    assert revised.final_output_paths[-1].endswith("polished.md")
    assert "/candidates/candidate_art_" in revised.revision_history[-1]
    assert (root / "memory" / "chapters" / "001" / "polished.md").is_file()
    assert (root / "memory" / "chapters" / "001" / "state_update_proposal.json").is_file()


def test_session_revise_content_keeps_needs_revision_when_reaudit_blocks(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    _run_cli(["session", "run", session.session_id, "--path", str(root), "--provider", "mock"])
    hard = AuditReport.model_validate(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "audited_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            "overall_status": "needs_revision",
            "summary": "仍有阻断问题。",
            "issues": [
                {
                    "id": "audit_001_medium",
                    "severity": "medium",
                    "type": "state_conflict",
                    "description": "物品位置仍与 current_state 冲突。",
                    "evidence": [{"source": "polished.md", "quote": "物品在错误地点"}],
                    "suggested_fix": "修正文或状态变化。",
                }
            ],
            "created_at": "2026-05-22T00:00:00Z",
        }
    )

    monkeypatch.setattr(session_module, "_audit_chapter_content", lambda *args, **kwargs: None)
    monkeypatch.setattr(session_module, "_load_audit", lambda *args: hard)

    def fail_propose(*args: object, **kwargs: object) -> None:
        raise AssertionError("state proposal must not be regenerated while audit blocks")

    monkeypatch.setattr(session_module, "_propose_state", fail_propose)

    result = session_module.revise_content(
        SessionInstructionOptions(
            root=root,
            session_id=session.session_id,
            instruction=None,
            provider_name="mock",
            from_audit=True,
        )
    )

    assert result.session.phase.value == "awaiting_content_review"
    assert result.session.final_output_paths[-1].endswith("polished.md")
    assert "/candidates/candidate_art_" in result.session.revision_history[-1]


def test_session_revise_content_routes_writer_rewrite(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    _run_cli(["session", "run", session.session_id, "--path", str(root), "--provider", "mock"])
    chapter_dir = root / "memory" / "chapters" / "001"
    seen: dict[str, str] = {}

    monkeypatch.setattr(
        session_module,
        "route_revision_request",
        lambda *args, **kwargs: RevisionRouteDecision(
            route="writer_rewrite",
            reason="写作实现层修改。",
            chapter_numbers=[1],
            instruction_for_writer="加强压迫感，增加铺垫。",
            risk_level="medium",
        ),
    )

    def fake_rewrite(*args: object, **kwargs: object) -> Path:
        seen["instruction"] = str(args[3])
        path = chapter_dir / "polished.md"
        path.write_text(
            "---\nchapter_number: 1\ntitle: 测试\nstatus: polished\n---\n\n重写正文\n",
            encoding="utf-8",
        )
        return path

    monkeypatch.setattr(session_module, "_rewrite_chapter_with_writer", fake_rewrite)
    monkeypatch.setattr(session_module, "_audit_chapter_content", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        session_module,
        "_load_audit",
        lambda *args: AuditReport.model_validate(
            {
                "chapter_number": 1,
                "audited_file": "polished.md",
                "audited_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                "overall_status": "passed",
                "summary": "通过。",
                "issues": [],
                "created_at": "2026-05-22T00:00:00Z",
            }
        ),
    )
    monkeypatch.setattr(session_module, "_propose_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        session_module, "_capture_and_advance_projection", lambda root, session, chapter, projection: projection
    )

    result = session_module.revise_content(
        SessionInstructionOptions(
            root=root,
            session_id=session.session_id,
            instruction="人物压迫感不够，增加铺垫。",
            provider_name="mock",
        )
    )

    assert result.session.phase.value == "awaiting_content_review"
    assert "加强压迫感" in seen["instruction"]
    assert result.session.revision_route_history[-1].decision.route == "writer_rewrite"
    assert result.session.final_output_paths[-1].endswith("polished.md")


def test_session_resume_reaudits_when_polished_changes_after_completed_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    original_propose = session_module._propose_state
    calls = 0

    def fail_first_proposal(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected state update outage")
        return original_propose(*args, **kwargs)

    monkeypatch.setattr(session_module, "_propose_state", fail_first_proposal)
    with pytest.raises(RuntimeError, match="injected state update outage"):
        session_module.run_session(
            SessionRunOptions(root=root, session_id=session.session_id, provider_name="mock")
        )
    failed = session_module.load_session(root, session.session_id)
    assert failed.chapter_runs[1].audit.value == "completed"

    polished_path = root / "memory" / "chapters" / "001" / "polished.md"
    polished_path.write_text(
        polished_path.read_text(encoding="utf-8") + "\n【已在恢复前人工修改】\n",
        encoding="utf-8",
    )

    resumed = session_module.run_session(
        SessionRunOptions(root=root, session_id=session.session_id, provider_name="mock")
    )

    assert resumed.session.phase.value == "awaiting_content_review"
    report = load_json_model(root / "memory" / "chapters" / "001" / "audit.json", AuditReport)
    assert report.audited_sha256 == sha256_file(polished_path)
    assert resumed.session.chapter_runs[1].audit.value == "completed"


def test_session_revision_uses_interleaved_projection_for_later_chapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1-2章", "--path", str(root), "--chapters", "1,2", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    _run_cli(["session", "run", session.session_id, "--path", str(root), "--provider", "mock"])
    chapter_one_lifecycle = load_lifecycle(root, 1)
    assert chapter_one_lifecycle and chapter_one_lifecycle.active_candidate
    chapter_one_lineage_path = root / f"{chapter_one_lifecycle.active_candidate.path}.lineage.json"
    chapter_one_lineage_before = chapter_one_lineage_path.read_bytes()
    seen: dict[str, tuple[int, list[str]]] = {}
    original_revision = session_module.revise_chapter
    original_audit = session_module._audit_chapter_content

    monkeypatch.setattr(
        session_module,
        "route_revision_request",
        lambda *args, **kwargs: RevisionRouteDecision(
            route="revision_patch",
            reason="只修订后序章节。",
            chapter_numbers=[2],
            instruction_for_revision="只调整第2章措辞。",
            risk_level="low",
        ),
    )

    def projection_snapshot(world_state_dir: Path) -> tuple[int, list[str]]:
        state = load_json_model(world_state_dir / "current_state.json", EntityState)
        character = next(item for item in state.character_states if item.entity_id == "char_lin_che")
        return state.story_position.latest_chapter, character.possessions

    def capture_revision(options, provider, **kwargs):
        assert options.world_state_dir is not None
        seen["revision"] = projection_snapshot(options.world_state_dir)
        return original_revision(options, provider, **kwargs)

    def capture_audit(*args, **kwargs):
        seen["audit"] = projection_snapshot(kwargs["world_state_dir"])
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(session_module, "revise_chapter", capture_revision)
    monkeypatch.setattr(session_module, "_audit_chapter_content", capture_audit)

    result = session_module.revise_content(
        SessionInstructionOptions(
            root=root,
            session_id=session.session_id,
            instruction="只调整第2章措辞。",
            provider_name="mock",
        )
    )

    assert result.session.phase.value == "awaiting_content_review"
    assert seen["revision"] == (1, ["item_broken_ticket"])
    assert seen["audit"] == (1, ["item_broken_ticket"])
    checkpoints = load_projection(root, session.session_id).checkpoints
    assert [item.chapter_number for item in checkpoints] == [1, 2]
    assert chapter_one_lineage_path.read_bytes() == chapter_one_lineage_before


def test_revision_failure_persists_recoverable_node_and_resumes(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    _run_cli(["session", "run", session.session_id, "--path", str(root), "--provider", "mock"])

    def fail_after_revision_started(options: SessionInstructionOptions) -> SessionResult:
        current = session_module.load_session(options.root, options.session_id)
        current = session_module._transition_session(current, session_module.SessionPhase.REVISING)
        session_module._write_session(options.root, current)
        raise RuntimeError("injected revision failure")

    monkeypatch.setattr(session_module, "_revise_content_impl", fail_after_revision_started)
    options = SessionInstructionOptions(
        root=root,
        session_id=session.session_id,
        instruction="收紧第一段。",
        provider_name="mock",
    )
    with pytest.raises(RuntimeError, match="injected revision failure"):
        session_module.revise_content(options)

    failed = session_module.load_session(root, session.session_id)
    assert failed.phase.value == "failed_recoverable"
    assert failed.failure_node == "revision.content"

    def resume_revision(options: SessionInstructionOptions) -> SessionResult:
        current = session_module.load_session(options.root, options.session_id)
        assert current.phase.value == "failed_recoverable"
        current = session_module._transition_session(current, session_module.SessionPhase.REVISING)
        current = session_module._transition_session(current, session_module.SessionPhase.AWAITING_CONTENT_REVIEW)
        session_module._write_session(options.root, current)
        return SessionResult(
            session=current,
            session_path=root / "memory" / "sessions" / current.session_id / "session.json",
            message="resumed",
        )

    monkeypatch.setattr(session_module, "_revise_content_impl", resume_revision)
    resumed = session_module.revise_content(options)
    assert resumed.session.phase.value == "awaiting_content_review"


def test_session_revise_content_routes_plot_replan(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    _run_cli(["session", "approve-outline", session.session_id, "--path", str(root)])
    _run_cli(["session", "run", session.session_id, "--path", str(root), "--provider", "mock"])
    chapter_dir = root / "memory" / "chapters" / "001"

    monkeypatch.setattr(
        session_module,
        "route_revision_request",
        lambda *args, **kwargs: RevisionRouteDecision(
            route="plot_replan",
            reason="改变结尾。",
            chapter_numbers=[1],
            instruction_for_plot="把结尾改成主角主动背叛师门。",
            risk_level="high",
        ),
    )

    def fake_replan(*args: object, **kwargs: object) -> Path:
        path = chapter_dir / "polished.md"
        path.write_text(
            "---\nchapter_number: 1\ntitle: 测试\nstatus: polished\n---\n\n重写大纲后的正文\n",
            encoding="utf-8",
        )
        return path

    monkeypatch.setattr(session_module, "_replan_and_rewrite_chapter", fake_replan)
    monkeypatch.setattr(session_module, "_audit_chapter_content", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        session_module,
        "_load_audit",
        lambda *args: AuditReport.model_validate(
            {
                "chapter_number": 1,
                "audited_file": "polished.md",
                "audited_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                "overall_status": "passed",
                "summary": "通过。",
                "issues": [],
                "created_at": "2026-05-22T00:00:00Z",
            }
        ),
    )
    monkeypatch.setattr(session_module, "_propose_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        session_module, "_capture_and_advance_projection", lambda root, session, chapter, projection: projection
    )

    result = session_module.revise_content(
        SessionInstructionOptions(
            root=root,
            session_id=session.session_id,
            instruction="把结尾改成主角主动背叛师门。",
            provider_name="mock",
        )
    )

    assert result.session.phase.value == "awaiting_content_review"
    assert result.session.revision_route_history[-1].decision.route == "plot_replan"
    assert "重写大纲后的正文" in (chapter_dir / "polished.md").read_text(encoding="utf-8")


def test_session_undo_rewrite_restores_snapshot_and_reaudits(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready(tmp_path)
    _run_cli(["session", "start", "写第1章", "--path", str(root), "--chapters", "1", "--provider", "mock"])
    session = _latest_session(root)
    approved_outline = root / "memory" / "sessions" / session.session_id / "approved_outline.md"
    approved_outline.write_text("# 已批准大纲\n", encoding="utf-8")
    session = session_module._validated_session_copy(
        session,
        phase="awaiting_content_review",
        approved_outline_path=approved_outline.relative_to(root).as_posix(),
    )
    atomic_write_model_json(
        root / "memory" / "sessions" / session.session_id / "session.json",
        session,
    )
    chapter_dir = root / "memory" / "chapters" / "001"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    (chapter_dir / "polished.md").write_text(
        "---\nchapter_number: 1\ntitle: 测试\nstatus: polished\n---\n\n重写后的正文\n",
        encoding="utf-8",
    )
    session_dir = root / "memory" / "sessions" / session.session_id
    snapshot = session_dir / "rejections" / "chapter_001_round_1_before.md"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(
        "---\nchapter_number: 1\ntitle: 测试\nstatus: polished\n---\n\n被打回的原文\n",
        encoding="utf-8",
    )
    now = session.created_at
    event_id = "rewrite_ch001_round1_revision_rewrite_20260530_010101_000001"
    atomic_write_model_json(
        session_dir / "rewrite_events.json",
        SessionRewriteEvents(
            events=[
                SessionRewriteEvent(
                    event_id=event_id,
                    session_id=session.session_id,
                    chapter_number=1,
                    round_number=1,
                    action="revision_rewrite",
                    status="unresolved",
                    trigger_audit_path="memory/chapters/001/audit.json",
                    rejected_text_snapshot_path=f"memory/sessions/{session.session_id}/rejections/chapter_001_round_1_before.md",
                    before_output_path="memory/chapters/001/polished.md",
                    blocking_issues=[
                        {
                            "id": "audit_001_medium",
                            "severity": "medium",
                            "type": "state_conflict",
                            "description": "错误打回。",
                            "evidence": [{"source": "polished.md", "quote": "原文"}],
                            "suggested_fix": "撤回。",
                        }
                    ],
                    created_at=now,
                )
            ]
        ),
    )
    passed = AuditReport.model_validate(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "audited_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            "overall_status": "passed",
            "summary": "复审通过。",
            "issues": [],
            "created_at": "2026-05-22T00:00:00Z",
        }
    )
    monkeypatch.setattr(session_module, "_audit_chapter_content", lambda *args, **kwargs: None)
    monkeypatch.setattr(session_module, "_load_audit", lambda *args: passed)
    monkeypatch.setattr(session_module, "_propose_state", lambda *args, **kwargs: None)

    result = session_module.undo_rewrite(
        SessionRewriteControlOptions(
            root=root,
            session_id=session.session_id,
            event_id=event_id,
            provider_name="mock",
        )
    )

    assert "被打回的原文" in (chapter_dir / "polished.md").read_text(encoding="utf-8")
    events = load_rewrite_events(root, session.session_id)
    assert events[0].undo_status == "restored"
    assert events[0].status == "completed"
    assert result.session.phase.value == "awaiting_content_review"


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
