from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

import pytest

from novel.cli import main
from novel.core import transactions as transaction_module
from novel.core.artifact_store import ArtifactStore, load_lifecycle
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.lifecycle import accepted_chapter_commit
from novel.core.contracts import RevisionSessionPhase, ensure_revision_phase_transition
from novel.core.revision_workflow import (
    RevisionActionOptions,
    RevisionRunOptions,
    RevisionStartOptions,
    RevisionWorkflowError,
    accept_revision_session,
    cancel_revision_session,
    list_revision_blocks,
    run_revision_session,
    show_revision_session,
    start_revision_session,
)
from novel.core.workspace import InitOptions, init_workspace
from novel.web_api import handle_api_request


def test_scoped_revision_reaudits_and_commits_same_candidate(tmp_path: Path) -> None:
    root = _accepted_workspace(tmp_path, chapters="1")
    before = accepted_chapter_commit(root, 1)
    blocks = list_revision_blocks(root, 1)
    assert len(blocks) >= 2
    started = start_revision_session(
        RevisionStartOptions(
            root=root,
            chapter_number=1,
            start_block=2,
            end_block=2,
            instruction="让这一段更紧张。",
        )
    )

    run = run_revision_session(
        RevisionRunOptions(root=root, revision_session_id=started.session.revision_session_id, provider_name="mock")
    )

    assert run.session.phase.value == "awaiting_review"
    assert run.session.candidate is not None
    assert run.session.audit is not None
    assert run.session.state_proposal is not None
    assert run.session.audit.candidate == run.session.candidate
    assert run.session.state_proposal.candidate == run.session.candidate
    assert run.session.state_proposal.audit == run.session.audit.audit
    old_accepted = (root / "memory" / "chapters" / "001" / "accepted.md").read_bytes()
    assert b"\xef\xbc\x88\xe5\xb7\xb2\xe6\x8c\x89" not in old_accepted

    accepted = accept_revision_session(
        RevisionActionOptions(root=root, revision_session_id=run.session.revision_session_id)
    )

    assert accepted.session.phase.value == "committed"
    current = accepted_chapter_commit(root, 1)
    assert current.commit_id != before.commit_id
    assert current.candidate == run.session.candidate
    assert "已按局部修订要求调整" in (root / "memory" / "chapters" / "001" / "accepted.md").read_text(encoding="utf-8")
    assert _run_cli(["export", "markdown", "--path", str(root), "--force"])[0] == 0


def test_revision_phase_transition_rejects_skipping_review() -> None:
    ensure_revision_phase_transition(RevisionSessionPhase.AWAITING_PATCH, RevisionSessionPhase.RUNNING)
    with pytest.raises(ValueError, match="illegal revision phase transition"):
        ensure_revision_phase_transition(RevisionSessionPhase.AWAITING_PATCH, RevisionSessionPhase.COMMITTED)


def test_revision_session_id_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(RevisionWorkflowError, match="invalid revision session id"):
        show_revision_session(tmp_path, "../../outside")


def test_revision_selection_becomes_stale_when_source_artifact_changes(tmp_path: Path) -> None:
    root = _accepted_workspace(tmp_path, chapters="1")
    started = start_revision_session(
        RevisionStartOptions(root=root, chapter_number=1, start_block=1, end_block=1, instruction="修改标题。")
    )
    source_ref = started.session.selection.source_candidate
    source_path = root / source_ref.path
    source_path.write_text(source_path.read_text(encoding="utf-8") + "\n篡改。\n", encoding="utf-8")

    with pytest.raises(RevisionWorkflowError, match="stale artifact"):
        run_revision_session(
            RevisionRunOptions(root=root, revision_session_id=started.session.revision_session_id, provider_name="mock")
        )


def test_segment_revision_rejects_non_latest_accepted_chapter(tmp_path: Path) -> None:
    root = _accepted_workspace(tmp_path, chapters="1,2")

    with pytest.raises(RevisionWorkflowError, match="latest accepted chapter"):
        start_revision_session(
            RevisionStartOptions(root=root, chapter_number=1, start_block=1, end_block=1, instruction="修改第一章。")
        )


def test_revision_acceptance_rolls_back_old_acceptance_on_failure(tmp_path: Path, monkeypatch) -> None:
    root = _accepted_workspace(tmp_path, chapters="1")
    before_commit = accepted_chapter_commit(root, 1)
    accepted_path = root / "memory" / "chapters" / "001" / "accepted.md"
    before_content = accepted_path.read_bytes()
    before_lifecycle = load_lifecycle(root, 1)
    started = start_revision_session(
        RevisionStartOptions(root=root, chapter_number=1, start_block=2, end_block=2, instruction="局部修改。")
    )
    ready = run_revision_session(
        RevisionRunOptions(root=root, revision_session_id=started.session.revision_session_id, provider_name="mock")
    )
    original_write = transaction_module.atomic_write_bytes
    failed = False

    def fail_once(path: Path, content: bytes) -> None:
        nonlocal failed
        if path.resolve() == accepted_path.resolve() and not failed:
            failed = True
            raise OSError("injected revision commit failure")
        original_write(path, content)

    monkeypatch.setattr(transaction_module, "atomic_write_bytes", fail_once)

    with pytest.raises(RevisionWorkflowError, match="rolled back"):
        accept_revision_session(RevisionActionOptions(root=root, revision_session_id=ready.session.revision_session_id))

    assert accepted_path.read_bytes() == before_content
    assert accepted_chapter_commit(root, 1).commit_id == before_commit.commit_id
    assert load_lifecycle(root, 1) == before_lifecycle


def test_revision_session_cli_exposes_blocks_run_and_accept(tmp_path: Path) -> None:
    root = _accepted_workspace(tmp_path, chapters="1")
    code, stdout, stderr = _run_cli(["revision-session", "blocks", "1", "--path", str(root)])
    assert code == 0
    assert stderr == ""
    assert "Markdown blocks" in stdout

    code, stdout, stderr = _run_cli(
        [
            "revision-session",
            "start",
            "1",
            "--blocks",
            "2",
            "--instruction",
            "增强紧张感。",
            "--path",
            str(root),
        ]
    )
    assert code == 0
    assert stderr == ""
    revision_session_id = stdout.split("Revision session: ", 1)[1].splitlines()[0]
    assert _run_cli(["revision-session", "run", revision_session_id, "--path", str(root), "--provider", "mock"])[0] == 0
    assert _run_cli(["revision-session", "accept", revision_session_id, "--path", str(root)])[0] == 0
    assert accepted_chapter_commit(root, 1).session_id == revision_session_id


def test_revision_cancel_restores_accepted_working_files_atomically(tmp_path: Path) -> None:
    root = _accepted_workspace(tmp_path, chapters="1")
    accepted = accepted_chapter_commit(root, 1)
    store = ArtifactStore(root)
    expected = {
        "polished.md": store.read_bytes(accepted.candidate),
        "audit.json": store.read_bytes(accepted.audit),
        "state_update_proposal.json": store.read_bytes(accepted.state_proposal),
    }
    started = start_revision_session(
        RevisionStartOptions(
            root=root,
            chapter_number=1,
            start_block=2,
            end_block=2,
            instruction="增强紧张感。",
        )
    )
    ready = run_revision_session(
        RevisionRunOptions(root=root, revision_session_id=started.session.revision_session_id, provider_name="mock")
    )
    chapter_dir = root / "memory" / "chapters" / "001"
    assert (chapter_dir / "polished.md").read_bytes() != expected["polished.md"]

    cancelled = cancel_revision_session(
        RevisionActionOptions(root=root, revision_session_id=ready.session.revision_session_id)
    )

    assert cancelled.session.phase is RevisionSessionPhase.CANCELLED
    for name, content in expected.items():
        assert (chapter_dir / name).read_bytes() == content
    with pytest.raises(RevisionWorkflowError, match="cannot be cancelled"):
        cancel_revision_session(
            RevisionActionOptions(root=root, revision_session_id=ready.session.revision_session_id)
        )


def test_revision_cancel_is_available_through_cli_and_web(tmp_path: Path) -> None:
    root = _accepted_workspace(tmp_path, chapters="1")
    first = start_revision_session(
        RevisionStartOptions(root=root, chapter_number=1, start_block=2, end_block=2, instruction="局部修改。")
    )
    code, stdout, stderr = _run_cli(
        ["revision-session", "cancel", first.session.revision_session_id, "--path", str(root)]
    )
    assert code == 0
    assert stderr == ""
    assert "Phase: cancelled" in stdout

    second = start_revision_session(
        RevisionStartOptions(root=root, chapter_number=1, start_block=2, end_block=2, instruction="再次修改。")
    )
    status, payload = handle_api_request(
        "POST",
        "/api/revision-session/cancel",
        "",
        json.dumps({"path": str(root), "revision_session_id": second.session.revision_session_id}),
    )
    assert status == 200
    assert payload["data"]["revision_session"]["phase"] == "cancelled"  # type: ignore[index]


def test_revision_session_web_api_returns_structured_selection(tmp_path: Path) -> None:
    root = _accepted_workspace(tmp_path, chapters="1")
    status, payload = handle_api_request(
        "GET",
        "/api/revision-session/blocks",
        f"path={root}&chapter=1",
    )
    assert status == 200
    blocks = payload["data"]["blocks"]  # type: ignore[index]
    assert blocks[0]["index"] == 1

    status, payload = handle_api_request(
        "POST",
        "/api/revision-session/start",
        "",
        json.dumps(
            {
                "path": str(root),
                "chapter": 1,
                "start_block": 2,
                "end_block": 2,
                "instruction": "压缩节奏。",
            }
        ),
    )
    assert status == 200
    session = payload["data"]["revision_session"]  # type: ignore[index]
    assert session["phase"] == "awaiting_patch"
    assert session["selection"]["start_block"] == 2


def _accepted_workspace(tmp_path: Path, *, chapters: str) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# Inspiration\n\n## Weak Outline\n\n雨夜旧车站传来停播多年的广播声。\n",
        encoding="utf-8",
    )
    proposal = tmp_path / "canon.json"
    proposal.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal).validation_report.ok
    assert (
        _run_cli(["session", "start", "连续创作", "--path", str(root), "--chapters", chapters, "--provider", "mock"])[0]
        == 0
    )
    session_dirs = sorted((root / "memory" / "sessions").iterdir())
    session_id = session_dirs[-1].name
    assert _run_cli(["session", "approve-outline", session_id, "--path", str(root)])[0] == 0
    assert _run_cli(["session", "run", session_id, "--path", str(root), "--provider", "mock"])[0] == 0
    assert _run_cli(["session", "accept", session_id, "--path", str(root), "--provider", "mock"])[0] == 0
    return root


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
