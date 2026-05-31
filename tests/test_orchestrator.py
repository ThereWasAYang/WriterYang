from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.orchestrator import decide_ask_intent, route_audit_repair, route_revision_request
from novel.core.providers import MockProvider
from novel.core.schemas import AuditIssue, AuditReport
from novel.core.workspace import InitOptions, init_workspace


def test_ask_creates_creation_session_and_outline(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(
        ["ask", "请为第1章生成章节计划", "--path", str(root), "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    assert "Session:" in stdout
    assert "outline proposal generated" in stdout
    assert list((root / "memory" / "sessions").glob("session_*/session.json"))
    assert (root / "memory" / "chapters" / "001" / "plan.json").is_file()


def test_ask_json_returns_session_id(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(
        ["ask", "请写第1章初稿", "--path", str(root), "--provider", "mock", "--json", "--quiet"]
    )

    assert code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["task"] == "creation_session"
    assert payload["session_id"].startswith("session_")


def test_ask_dry_run_keeps_legacy_orchestrator_plan_without_writing(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "ask",
            "请为第1章生成章节计划",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--dry-run",
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "Dry run complete" in stdout
    assert not (root / "memory" / "chapters" / "001" / "plan.json").exists()
    assert not list((root / "runs").glob("run_*.json"))


def test_ask_stops_when_max_steps_exceeded(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, stdout, stderr = _run_cli(
        [
            "ask",
            "请为第1章生成章节计划",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--max-steps",
            "0",
        ]
    )

    assert code == 1
    assert stdout == ""
    assert "max_steps" in stderr
    assert not (root / "memory" / "chapters" / "001" / "plan.json").exists()


def test_ask_no_longer_writes_run_log_for_session_start(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    code, _, stderr = _run_cli(
        ["ask", "请为第1章生成章节计划", "--path", str(root), "--provider", "mock"]
    )

    assert code == 0
    assert stderr == ""
    assert not list((root / "runs").glob("run_*.json"))


def test_revision_route_decision_classifies_plot_replan(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    provider = MockProvider(
        fake_response=json.dumps(
            {
                "route": "plot_replan",
                "reason": "改变结尾和人物选择，属于剧情结构变化。",
                "chapter_numbers": [1],
                "instruction_for_plot": "把结尾改成主角主动背叛师门。",
                "instruction_for_writer": None,
                "instruction_for_revision": None,
                "risk_level": "high",
            },
            ensure_ascii=False,
        )
    )

    decision = route_revision_request(
        root,
        "把结尾改成主角主动背叛师门",
        provider_name="mock",
        provider=provider,
        chapter_numbers=[1],
    )

    assert decision.route == "plot_replan"
    assert decision.instruction_for_plot


def test_revision_route_decision_repair_retry(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    provider = MockProvider(
        fake_response=[
            "这需要再确认一下。",
            json.dumps(
                {
                    "route": "writer_rewrite",
                    "reason": "只影响压迫感和铺垫方式。",
                    "chapter_numbers": [1],
                    "instruction_for_plot": None,
                    "instruction_for_writer": "加强压迫感，增加铺垫，减少解释。",
                    "instruction_for_revision": None,
                    "risk_level": "medium",
                },
                ensure_ascii=False,
            ),
        ]
    )

    decision = route_revision_request(
        root,
        "人物压迫感不够，增加铺垫，减少解释",
        provider_name="mock",
        provider=provider,
        chapter_numbers=[1],
    )

    assert decision.route == "writer_rewrite"
    assert len(provider.requests) == 2


def test_revision_route_fallback_avoids_free_revision(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    provider = MockProvider(fake_response="{bad json")

    decision = route_revision_request(
        root,
        "人物压迫感不够，增加铺垫，减少解释",
        provider_name="mock",
        provider=provider,
        chapter_numbers=[1],
    )

    assert decision.route == "writer_rewrite"
    assert "fallback" in decision.reason


def test_ask_intent_decision_handles_noisy_session_request(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    provider = MockProvider(
        fake_response=json.dumps(
            {
                "task": "session_start",
                "reason": "用户想写第二章。",
                "chapter_range": [2],
                "confidence": 0.86,
                "source": "model",
            },
            ensure_ascii=False,
        )
    )

    decision = decide_ask_intent(root, "帮我搞下第2章，先整一点氛围", provider=provider)

    assert decision.task == "session_start"
    assert decision.chapter_range == [2]


def test_ask_intent_decision_repair_retry(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    provider = MockProvider(
        fake_response=[
            "需要再确认。",
            json.dumps(
                {
                    "task": "memory_repair_suggest",
                    "reason": "用户指出时间线事件类型错误。",
                    "chapter_range": [],
                    "confidence": 0.78,
                    "source": "model",
                },
                ensure_ascii=False,
            ),
        ]
    )

    decision = decide_ask_intent(root, "第2章这个事件其实是回忆", provider=provider)

    assert decision.task == "memory_repair_suggest"
    assert len(provider.requests) == 2


def test_ask_intent_fallback_does_not_apply_repair(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)

    decision = decide_ask_intent(root, "确认应用 repair_20260530_010101_000001", provider_name="mock")

    assert decision.task == "unknown"
    assert decision.repair_id == "repair_20260530_010101_000001"
    assert "memory-repair apply" in (decision.user_message or "")


def test_audit_repair_route_uses_structured_plan_source(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    report = _audit_report(
        issue=AuditIssue(
            id="issue_plan",
            severity="high",
            type="plot_logic_issue",
            description="计划层矛盾。",
            evidence=[],
            suggested_fix="重写计划。",
            source_layer="plan",
        )
    )

    decision = route_audit_repair(root, report, provider_name="mock")

    assert decision.route == "plot_replan"


def test_audit_repair_route_does_not_replan_from_natural_language_only(tmp_path: Path) -> None:
    root = _workspace_ready(tmp_path)
    report = _audit_report(
        issue=AuditIssue(
            id="issue_text_only",
            severity="high",
            type="continuity_issue",
            description="这里提到了真相和伏笔，但没有结构化来源。",
            evidence=[],
            suggested_fix="人工检查。",
        )
    )

    decision = route_audit_repair(root, report, provider_name="mock")

    assert decision.route == "manual_review"


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


def _audit_report(*, issue: AuditIssue) -> AuditReport:
    return AuditReport(
        chapter_number=1,
        audited_file="polished.md",
        overall_status="needs_revision",
        summary="blocked",
        issues=[issue],
        passed_checks=[],
        created_at="2026-05-22T00:00:00Z",
    )


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
