from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

import pytest

from novel.cli import main
from novel.core.auditing import (
    AuditError,
    ChapterAuditOptions,
    audit_chapter,
    default_mock_audit_report_json,
    parse_audit_report,
)
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.drafting import ChapterDraftingOptions, write_chapter_draft
from novel.core.planning import ChapterPlanningOptions, default_mock_chapter_plan_json, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, polish_chapter
from novel.core.providers import MockProvider
from novel.core.schemas import AuditReport
from novel.core.workspace import InitOptions, init_workspace


def test_mock_provider_can_generate_audit_report(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    provider = MockProvider(fake_response=default_mock_audit_report_json(1, "polished.md"))

    result = audit_chapter(
        ChapterAuditOptions(
            root=root,
            chapter_number=1,
            instruction="重点检查人物是否知道了不该知道的信息",
            strict=True,
            focus=("canon", "state", "premature_reveal"),
        ),
        provider,
    )

    assert result.report.overall_status == "passed"
    assert result.report.audited_file == "polished.md"
    assert "严格审核：是" in provider.requests[0].user_prompt
    assert "审核重点：canon, state, premature_reveal" in provider.requests[0].user_prompt
    assert "重点检查人物是否知道了不该知道的信息" in provider.requests[0].user_prompt
    assert "只输出 AuditReport JSON" in provider.requests[0].system_prompt


def test_audit_chapter_cli_creates_audit_json(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)

    code, stdout, stderr = _run_cli(["audit-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert "Wrote chapter audit:" in stdout
    assert "Audit status: passed" in stdout
    audit_path = root / "memory" / "chapters" / "001" / "audit.json"
    report = AuditReport.model_validate(json.loads(audit_path.read_text(encoding="utf-8")))
    assert report.chapter_number == 1
    assert report.audited_file == "polished.md"
    assert report.overall_status == "passed"
    assert "plan_schema_valid" in report.passed_checks


def test_audit_chapter_can_audit_draft_file(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "audit-chapter",
            "1",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--audited-file",
            "draft.md",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote chapter audit:" in stdout
    report = AuditReport.model_validate(
        json.loads((root / "memory" / "chapters" / "001" / "audit.json").read_text(encoding="utf-8"))
    )
    assert report.audited_file == "draft.md"


def test_audit_chapter_refuses_to_overwrite_existing_by_default(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    first, _, _ = _run_cli(["audit-chapter", "1", "--path", str(root), "--provider", "mock"])
    audit_path = root / "memory" / "chapters" / "001" / "audit.json"
    original = audit_path.read_text(encoding="utf-8")

    second, stdout, stderr = _run_cli(["audit-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert first == 0
    assert second == 1
    assert stdout == ""
    assert "already exists" in stderr
    assert audit_path.read_text(encoding="utf-8") == original


def test_audit_chapter_force_overwrites_existing(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    _run_cli(["audit-chapter", "1", "--path", str(root), "--provider", "mock"])
    audit_path = root / "memory" / "chapters" / "001" / "audit.json"
    audit_path.write_text('{"manual": true}\n', encoding="utf-8")

    code, stdout, stderr = _run_cli(
        ["audit-chapter", "1", "--path", str(root), "--provider", "mock", "--force"]
    )

    assert code == 0
    assert stderr == ""
    assert "manual" not in audit_path.read_text(encoding="utf-8")
    assert "Wrote chapter audit:" in stdout


def test_audit_chapter_input_instruction(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    input_path = tmp_path / "audit_request.txt"
    input_path.write_text("重点检查人物是否知道了不该知道的信息", encoding="utf-8")

    code, stdout, stderr = _run_cli(
        [
            "audit-chapter",
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
    assert "Wrote chapter audit:" in stdout


def test_audit_chapter_flags_enter_prompt(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    provider = MockProvider(fake_response=default_mock_audit_report_json(1, "draft.md"))

    audit_chapter(
        ChapterAuditOptions(
            root=root,
            chapter_number=1,
            instruction="检查对白是否偏离角色",
            strict=True,
            focus=("style", "plot", "character_voice"),
            audited_file="draft.md",
        ),
        provider,
    )

    prompt = provider.requests[0].user_prompt
    assert "严格审核：是" in prompt
    assert "审核重点：style, plot, character_voice" in prompt
    assert "检查对白是否偏离角色" in prompt
    assert "Draft body" in prompt


def test_audit_chapter_missing_polished_has_clear_error(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    (root / "memory" / "chapters" / "001" / "polished.md").unlink()

    code, stdout, stderr = _run_cli(["audit-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 1
    assert stdout == ""
    assert "polished.md" in stderr
    assert "missing" in stderr


def test_audit_chapter_missing_plan_has_clear_error(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    (root / "memory" / "chapters" / "001" / "plan.json").unlink()

    code, stdout, stderr = _run_cli(["audit-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 1
    assert stdout == ""
    assert "plan.json" in stderr
    assert "missing" in stderr


def test_audit_chapter_plan_chapter_mismatch_becomes_critical_issue(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    plan_path = root / "memory" / "chapters" / "001" / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["chapter_number"] = 2
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    code, stdout, stderr = _run_cli(["audit-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert "Audit status: blocked" in stdout
    report = AuditReport.model_validate(
        json.loads((root / "memory" / "chapters" / "001" / "audit.json").read_text(encoding="utf-8"))
    )
    assert report.overall_status == "blocked"
    assert any(issue.id == "audit_precheck_plan_chapter_number" for issue in report.issues)
    assert any(issue.severity == "critical" for issue in report.issues)


def test_passed_audit_report_cannot_have_high_or_critical_issues() -> None:
    bad_report = json.dumps(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "overall_status": "passed",
            "summary": "Invalid mock report.",
            "issues": [
                {
                    "id": "issue_high",
                    "severity": "high",
                    "type": "continuity_issue",
                    "description": "A high issue cannot appear in a passed report.",
                    "evidence": [{"source": "polished.md", "quote": "example"}],
                    "suggested_fix": "Change status to needs_revision.",
                }
            ],
            "passed_checks": [],
            "created_at": "2026-05-22T00:00:00Z",
        },
        ensure_ascii=False,
    )

    with pytest.raises(AuditError):
        parse_audit_report(bad_report)


def _workspace_with_polished(tmp_path: Path) -> Path:
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
    return root


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
