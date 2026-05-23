from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

import yaml

from novel.cli import main
from novel.core.auditing import ChapterAuditOptions, audit_chapter, default_mock_audit_report_json
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.drafting import ChapterDraftingOptions, write_chapter_draft
from novel.core.planning import ChapterPlanningOptions, default_mock_chapter_plan_json, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, polish_chapter
from novel.core.providers import MockProvider
from novel.core.schemas import ChapterMetadata, EntityState, StateUpdateApplyLog, StateUpdateProposal, TimelineFile
from novel.core.state_update import (
    StateUpdateApplyOptions,
    StateUpdateProposeOptions,
    default_mock_state_update_proposal_json,
    apply_state_update,
    propose_state_update,
)
from novel.core.workspace import InitOptions, init_workspace


def test_mock_provider_can_generate_state_update_proposal(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    provider = MockProvider(fake_response=default_mock_state_update_proposal_json(1))

    result = propose_state_update(
        StateUpdateProposeOptions(
            root=root,
            chapter_number=1,
            instruction="只记录正文中实际发生的变化",
        ),
        provider,
    )

    assert result.proposal.chapter_number == 1
    assert result.proposal_path.is_file()
    assert len(result.proposal.state_changes) == 3
    assert len(result.proposal.timeline_events) == 1
    assert "只记录正文中实际发生的变化" in provider.requests[0].user_prompt
    assert "只输出结构化 JSON" in provider.requests[0].system_prompt


def test_propose_state_update_does_not_modify_state_or_timeline(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    state_path = root / "memory" / "state" / "current_state.json"
    timeline_path = root / "memory" / "state" / "timeline.json"
    original_state = state_path.read_text(encoding="utf-8")
    original_timeline = timeline_path.read_text(encoding="utf-8")

    code, stdout, stderr = _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert "Wrote state update proposal:" in stdout
    assert state_path.read_text(encoding="utf-8") == original_state
    assert timeline_path.read_text(encoding="utf-8") == original_timeline
    proposal = StateUpdateProposal.model_validate(
        json.loads((root / "memory" / "chapters" / "001" / "state_update_proposal.json").read_text(encoding="utf-8"))
    )
    assert proposal.timeline_events[0].id == "event_001_001"


def test_apply_state_update_applies_legal_proposal_and_creates_backups(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    code, stdout, stderr = _run_cli(["apply-state-update", "1", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "Backed up current state:" in stdout
    assert "Backed up timeline:" in stdout
    assert list((root / "memory" / "state").glob("current_state.json.bak_*"))
    assert list((root / "memory" / "state").glob("timeline.json.bak_*"))
    apply_log_path = root / "memory" / "chapters" / "001" / "state_update_apply_log.json"
    assert apply_log_path.is_file()
    apply_log = StateUpdateApplyLog.model_validate(json.loads(apply_log_path.read_text(encoding="utf-8")))
    assert apply_log.status == "applied"
    state = EntityState.model_validate(
        json.loads((root / "memory" / "state" / "current_state.json").read_text(encoding="utf-8"))
    )
    timeline = TimelineFile.model_validate(
        json.loads((root / "memory" / "state" / "timeline.json").read_text(encoding="utf-8"))
    )
    assert state.story_position.latest_chapter == 1
    assert state.item_states[0].holder_id == "char_lin_che"
    assert timeline.events[0].id == "event_001_001"


def test_apply_state_update_rolls_back_when_timeline_write_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])
    state_path = root / "memory" / "state" / "current_state.json"
    timeline_path = root / "memory" / "state" / "timeline.json"
    original_state = state_path.read_text(encoding="utf-8")
    original_timeline = timeline_path.read_text(encoding="utf-8")
    original_write_text = Path.write_text

    def flaky_write_text(self: Path, data: str, *args, **kwargs):
        if self == timeline_path:
            raise OSError("simulated timeline write failure")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    try:
        apply_state_update(StateUpdateApplyOptions(root=root, chapter_number=1))
    except Exception as exc:
        assert "rolled back" in str(exc)
    else:
        raise AssertionError("expected rollback failure")

    assert state_path.read_text(encoding="utf-8") == original_state
    assert timeline_path.read_text(encoding="utf-8") == original_timeline
    apply_log_path = root / "memory" / "chapters" / "001" / "state_update_apply_log.json"
    assert apply_log_path.is_file()
    apply_log = StateUpdateApplyLog.model_validate(json.loads(apply_log_path.read_text(encoding="utf-8")))
    assert apply_log.status == "rolled_back"


def test_apply_state_update_fails_on_duplicate_timeline_event_id(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    timeline_path = root / "memory" / "state" / "timeline.json"
    timeline_path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "event_001_001",
                        "chapter": 1,
                        "in_story_time": "第1天，雨夜",
                        "summary": "已存在事件。",
                        "reader_visible": True,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    code, stdout, stderr = _run_cli(["apply-state-update", "1", "--path", str(root)])

    assert code == 1
    assert stdout == ""
    assert "timeline event id conflict" in stderr


def test_apply_state_update_fails_on_old_value_mismatch(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])
    proposal_path = root / "memory" / "chapters" / "001" / "state_update_proposal.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    proposal["state_changes"][2]["old_value"] = 99
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    code, stdout, stderr = _run_cli(["apply-state-update", "1", "--path", str(root)])

    assert code == 1
    assert stdout == ""
    assert "old_value mismatch" in stderr


def test_apply_state_update_fails_on_item_holder_location_conflict(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    state_path = root / "memory" / "state" / "current_state.json"
    state_path.write_text(
        json.dumps(
            {
                "story_position": {"latest_chapter": 0, "in_story_time": None, "summary": None},
                "character_states": [],
                "item_states": [
                    {
                        "entity_id": "item_broken_ticket",
                        "holder_id": None,
                        "location_id": "loc_old_station",
                        "condition": None,
                        "known_properties": [],
                        "last_updated_chapter": 0,
                    }
                ],
                "location_states": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    code, stdout, stderr = _run_cli(["apply-state-update", "1", "--path", str(root)])

    assert code == 1
    assert stdout == ""
    assert "has both holder_id and location_id" in stderr


def test_accept_chapter_passed_audit_applies_update_and_marks_accepted(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    code, stdout, stderr = _run_cli(["accept-chapter", "1", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "Accepted chapter:" in stdout
    metadata = _read_front_matter(root / "memory" / "chapters" / "001" / "polished.md")
    assert metadata["status"] == "accepted"
    assert "accepted_at" in metadata
    structured = ChapterMetadata.model_validate(
        json.loads((root / "memory" / "chapters" / "001" / "metadata.json").read_text(encoding="utf-8"))
    )
    assert structured.status == "accepted"
    assert structured.accepted_at is not None
    assert structured.state_update_apply_log_path == "memory/chapters/001/state_update_apply_log.json"


def test_propose_state_update_blocked_audit_fails_by_default(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _write_blocked_audit(root)

    code, stdout, stderr = _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    assert code == 1
    assert stdout == ""
    assert "unresolved high or critical issues" in stderr


def test_accept_chapter_can_auto_propose_when_missing(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)

    code, stdout, stderr = _run_cli(
        ["accept-chapter", "1", "--path", str(root), "--propose", "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote state update proposal:" in stdout
    assert "Accepted chapter:" in stdout
    assert (root / "memory" / "chapters" / "001" / "state_update_proposal.json").is_file()


def test_accept_chapter_blocked_audit_fails_by_default(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _write_blocked_audit(root)
    _run_cli(
        [
            "propose-state-update",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--allow-unresolved-audit",
            "--force",
        ]
    )

    code, stdout, stderr = _run_cli(["accept-chapter", "1", "--path", str(root)])

    assert code == 1
    assert stdout == ""
    assert "unresolved high or critical issues" in stderr


def test_accept_chapter_allow_issues_can_continue_with_blocked_audit(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _write_blocked_audit(root)
    _run_cli(
        [
            "propose-state-update",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--allow-unresolved-audit",
            "--force",
        ]
    )

    code, stdout, stderr = _run_cli(["accept-chapter", "1", "--path", str(root), "--allow-issues"])

    assert code == 0
    assert stderr == ""
    assert "Accepted chapter:" in stdout


def _workspace_with_audit(tmp_path: Path) -> Path:
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
    polish_chapter(
        ChapterPolishingOptions(root=root, chapter_number=1),
        MockProvider(fake_response="雨声更深，旧车站像在夜里醒来。林澈收起车票。"),
    )
    audit_chapter(
        ChapterAuditOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_audit_report_json(1, "polished.md")),
    )
    return root


def _write_blocked_audit(root: Path) -> None:
    audit_path = root / "memory" / "chapters" / "001" / "audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "audited_file": "polished.md",
                "overall_status": "blocked",
                "summary": "存在重大连续性问题。",
                "issues": [
                    {
                        "id": "issue_blocking",
                        "severity": "critical",
                        "type": "continuity_issue",
                        "description": "章节编号或事实存在阻断问题。",
                        "evidence": [{"source": "polished.md", "quote": "example"}],
                        "suggested_fix": "先修订章节再接受。",
                    }
                ],
                "passed_checks": [],
                "created_at": "2026-05-23T00:00:00Z",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _read_front_matter(path: Path) -> dict[str, object]:
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    _, metadata_text, _ = content.split("---\n", 2)
    return yaml.safe_load(metadata_text)


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
