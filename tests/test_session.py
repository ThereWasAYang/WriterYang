from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.io import load_json_model
from novel.core.schemas import CreationArchiveManifest, CreationSession
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
