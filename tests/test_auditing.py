from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import main
from novel.cli_shared import _audit_issue_lines
from novel.core.auditing import (
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


def test_audit_chapter_coerces_provider_title_audited_file_to_requested_file(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    provider_payload = json.loads(default_mock_audit_report_json(1, "polished.md"))
    provider_payload["audited_file"] = "第一章 雨夜广播"
    provider_payload["overall_status"] = "needs_revision"
    provider_payload["issues"] = [
        {
            "id": "audit_001_low",
            "severity": "low",
            "type": "style",
            "description": "标题略显重复。",
            "evidence": [{"source": "polished.md", "quote": "第一章"}],
            "suggested_fix": "保留一个标题即可。",
        }
    ]
    provider = MockProvider(fake_response=json.dumps(provider_payload, ensure_ascii=False))

    result = audit_chapter(ChapterAuditOptions(root=root, chapter_number=1), provider)

    assert result.report.audited_file == "polished.md"
    assert result.report.overall_status == "passed"
    assert {issue.id for issue in result.report.issues} == {"audit_001_low"}


def test_audit_agent_question_repairs_once(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    provider = MockProvider(fake_response=["是否需要重点检查时间线？", default_mock_audit_report_json(1, "polished.md")])

    result = audit_chapter(ChapterAuditOptions(root=root, chapter_number=1), provider)

    assert result.report.overall_status == "passed"
    assert len(provider.requests) == 2
    assert "不要向用户或上游 Agent 提问" in provider.requests[1].user_prompt


def test_audit_prompt_allows_unrevealed_timeline_background_without_narrative_position(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    provider = MockProvider(fake_response=default_mock_audit_report_json(1, "polished.md"))

    audit_chapter(ChapterAuditOptions(root=root, chapter_number=1), provider)

    assert "未在正文揭示的背景/前史事件可以没有 narrative_position" in provider.requests[0].user_prompt
    assert "这本身不是 timeline_conflict" in provider.requests[0].user_prompt


def test_audit_recall_reruns_with_requested_chapter_context(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    first = json.loads(default_mock_audit_report_json(1, "polished.md"))
    first["need_context"] = [
        {"kind": "chapter_prose", "ref": "1", "reason": "核对旧车站广播原文"}
    ]
    second = json.loads(default_mock_audit_report_json(1, "polished.md"))
    second["summary"] = "复审后通过"
    provider = MockProvider(fake_response=[json.dumps(first, ensure_ascii=False), json.dumps(second, ensure_ascii=False)])

    result = audit_chapter(ChapterAuditOptions(root=root, chapter_number=1), provider)

    assert result.report.summary == "复审后通过"
    assert len(provider.requests) == 2
    assert "Additional recalled context" in provider.requests[1].user_prompt
    assert (root / "memory" / "chapters" / "001" / "audit_recall.json").is_file()


def test_audit_search_context_writes_report_and_includes_hidden_truth(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    provider = MockProvider(fake_response=default_mock_audit_report_json(1, "polished.md"))

    result = audit_chapter(
        ChapterAuditOptions(
            root=root,
            chapter_number=1,
            instruction="检查是否提前揭示隐藏真相",
            use_search_context=True,
        ),
        provider,
    )

    assert result.context_report_path is not None
    assert result.context_report_path.is_file()
    prompt = provider.requests[0].user_prompt
    assert "Context bundle" in prompt
    assert "旧车站在特定雨夜会短暂连接过去的时间层" in prompt
    report = result.context_report_path.read_text(encoding="utf-8")
    assert "truth_station_overlap" in report


def test_audit_chapter_cli_creates_audit_json(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)

    code, stdout, stderr = _run_cli(["audit-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert "Wrote chapter audit:" in stdout
    assert "Audit status: passed" in stdout
    assert "Deterministic issues: 0" in stdout
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


def test_audit_precheck_flags_hidden_truth_text_in_polished_body(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    polished_path = root / "memory" / "chapters" / "001" / "polished.md"
    text = polished_path.read_text(encoding="utf-8")
    polished_path.write_text(text + "\n旧车站在特定雨夜会短暂连接过去的时间层。\n", encoding="utf-8")

    result = audit_chapter(
        ChapterAuditOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_audit_report_json(1, "polished.md")),
    )

    assert result.report.overall_status == "needs_revision"
    assert any(issue.type == "premature_reveal" for issue in result.report.issues)


def test_audit_precheck_flags_character_knows_unrevealed_hidden_truth(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    _write_json(
        root / "memory" / "state" / "current_state.json",
        {
            "story_position": {"latest_chapter": 0},
            "character_states": [
                {
                    "entity_id": "char_lin_che",
                    "knowledge": ["truth_station_overlap"],
                    "possessions": [],
                    "last_updated_chapter": 0,
                }
            ],
            "item_states": [],
            "location_states": [],
        },
    )

    result = audit_chapter(
        ChapterAuditOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_audit_report_json(1, "polished.md")),
    )

    assert result.report.overall_status == "needs_revision"
    assert any(issue.type == "premature_reveal" and "knows hidden truth" in issue.description for issue in result.report.issues)
    assert result.deterministic_highest_severity == "high"


def test_audit_precheck_flags_item_holder_location_conflict(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    _write_json(
        root / "memory" / "state" / "current_state.json",
        {
            "story_position": {"latest_chapter": 0},
            "character_states": [
                {"entity_id": "char_lin_che", "possessions": ["item_broken_ticket"], "last_updated_chapter": 0}
            ],
            "item_states": [
                {
                    "entity_id": "item_broken_ticket",
                    "holder_id": "char_lin_che",
                    "location_id": "loc_old_station",
                    "last_updated_chapter": 0,
                }
            ],
            "location_states": [],
        },
    )

    result = audit_chapter(
        ChapterAuditOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_audit_report_json(1, "polished.md")),
    )

    assert result.report.overall_status == "needs_revision"
    assert any(issue.id == "cons_item_holder_location_item_broken_ticket" for issue in result.report.issues)


def test_audit_precheck_flags_timeline_reversed_cause(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    _write_json(
        root / "memory" / "state" / "timeline.json",
        {
            "events": [
                {
                    "id": "event_effect",
                    "narrative_position": {"chapter": 1, "scene": 1},
                    "story_position": {"time_label": "第1天", "order": 1, "thread_id": "main"},
                    "summary": "结果先被呈现。",
                    "reader_visible": True,
                },
                {
                    "id": "event_cause",
                    "narrative_position": {"chapter": 1, "scene": 2},
                    "story_position": {"time_label": "第1天稍晚", "order": 2, "thread_id": "main"},
                    "summary": "被错误记录为发生在结果之后的原因。",
                    "reader_visible": True,
                    "effects": ["event_effect"],
                },
            ]
        },
    )

    result = audit_chapter(
        ChapterAuditOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_audit_report_json(1, "polished.md")),
    )

    assert result.report.overall_status == "needs_revision"
    assert any(issue.type == "timeline_conflict" for issue in result.report.issues)


def test_audit_precheck_allows_flashback_without_story_order(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    _write_json(
        root / "memory" / "state" / "timeline.json",
        {
            "events": [
                {
                    "id": "event_current",
                    "narrative_position": {"chapter": 1, "scene": 1},
                    "story_position": {"time_label": "第1天"},
                    "summary": "当前事件。",
                    "reader_visible": True,
                },
                {
                    "id": "event_memory",
                    "narrative_position": {"chapter": 1, "scene": 2},
                    "story_position": {"time_label": "十年前"},
                    "summary": "角色回忆旧事。",
                    "reader_visible": True,
                    "event_role": "flashback",
                    "causes": ["event_current"],
                },
            ]
        },
    )

    result = audit_chapter(
        ChapterAuditOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_audit_report_json(1, "polished.md")),
    )

    assert not any(issue.id.startswith("cons_timeline_reversed") for issue in result.report.issues)


def test_audit_prompt_includes_deterministic_summary(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    provider = MockProvider(fake_response=default_mock_audit_report_json(1, "polished.md"))

    audit_chapter(ChapterAuditOptions(root=root, chapter_number=1), provider)

    assert "Deterministic consistency checks" in provider.requests[0].user_prompt
    assert "请不要机械重复这些结论" in provider.requests[0].user_prompt


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


def test_parse_audit_report_normalizes_issue_id_and_string_evidence() -> None:
    payload = {
        "chapter_number": 1,
        "audited_file": "polished.md",
        "overall_status": "needs_revision",
        "summary": "发现一处连续性问题。",
        "issues": [
            {
                "id": "ISS-2-1",
                "severity": "medium",
                "type": "continuity_issue",
                "description": "证据字段被模型输出为字符串。",
                "evidence": "角色突然知道了隐藏信息。",
                "suggested_fix": "改成角色尚未得知的信息范围。",
            }
        ],
        "passed_checks": [],
        "created_at": "2026-05-22T00:00:00Z",
    }

    report = parse_audit_report(json.dumps(payload, ensure_ascii=False))

    assert report.issues[0].id == "iss_2_1"
    assert report.issues[0].evidence[0].source == "polished.md"
    assert report.issues[0].evidence[0].quote == "角色突然知道了隐藏信息。"


def test_parse_audit_report_normalizes_audited_file_aliases() -> None:
    payload = {
        "chapter_number": 2,
        "audited_file": "chapter_02_polished.md",
        "overall_status": "passed",
        "summary": "别名文件名应归一化。",
        "issues": [],
        "passed_checks": [],
        "created_at": "2026-05-22T00:00:00Z",
    }

    report = parse_audit_report(json.dumps(payload, ensure_ascii=False))

    assert report.audited_file == "polished.md"


def test_parse_audit_report_does_not_downgrade_subjective_words_alone() -> None:
    payload = {
        "chapter_number": 2,
        "audited_file": "polished.md",
        "overall_status": "needs_revision",
        "summary": "模型把低确定性建议标成 medium。",
        "issues": [
            {
                "id": "audit_subjective_medium",
                "severity": "medium",
                "type": "knowledge_conflict",
                "description": "角色认出对方的依据不足，可能造成知识冲突。",
                "evidence": [{"source": "polished.md", "quote": "她觉得这个背影有些熟"}],
                "suggested_fix": "补一句依据，或改成不确定判断。",
            }
        ],
        "passed_checks": [],
        "created_at": "2026-05-22T00:00:00Z",
    }

    report = parse_audit_report(json.dumps(payload, ensure_ascii=False))

    assert report.overall_status == "needs_revision"
    assert report.issues[0].severity == "medium"


def test_parse_audit_report_downgrades_weak_low_confidence_issue_to_low() -> None:
    payload = {
        "chapter_number": 2,
        "audited_file": "polished.md",
        "overall_status": "needs_revision",
        "summary": "模型把低确定性建议标成 medium。",
        "issues": [
            {
                "id": "audit_subjective_medium",
                "severity": "medium",
                "type": "character_voice_issue",
                "description": "角色语气可能略显跳脱。",
                "evidence": [],
                "suggested_fix": "用户可按偏好微调。",
                "evidence_strength": "weak",
                "confidence": 0.4,
                "is_hard_blocker": False,
            }
        ],
        "passed_checks": [],
        "created_at": "2026-05-22T00:00:00Z",
    }

    report = parse_audit_report(json.dumps(payload, ensure_ascii=False))

    assert report.overall_status == "passed"
    assert report.issues[0].severity == "low"


def test_parse_audit_report_keeps_hard_medium_issue_blocking() -> None:
    payload = {
        "chapter_number": 2,
        "audited_file": "polished.md",
        "overall_status": "needs_revision",
        "summary": "具体状态冲突。",
        "issues": [
            {
                "id": "audit_hard_medium",
                "severity": "medium",
                "type": "state_conflict",
                "description": "current_state 中 item_a 的 location_id 在 loc_a，正文写到 loc_b。",
                "evidence": [{"source": "polished.md", "quote": "物品在 loc_b 出现"}],
                "suggested_fix": "修正文或先用 state_update 移动物品。",
            }
        ],
        "passed_checks": [],
        "created_at": "2026-05-22T00:00:00Z",
    }

    report = parse_audit_report(json.dumps(payload, ensure_ascii=False))

    assert report.overall_status == "needs_revision"
    assert report.issues[0].severity == "medium"


def test_low_only_audit_issues_are_passed_and_displayed(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    payload = {
        "chapter_number": 1,
        "audited_file": "polished.md",
        "overall_status": "needs_revision",
        "summary": "只有轻微风格建议。",
        "issues": [
            {
                "id": "audit_001_low",
                "severity": "low",
                "type": "style_mismatch",
                "description": "称呼略显老派。",
                "evidence": [{"source": "polished.md", "quote": "姑娘"}],
                "suggested_fix": "由用户决定是否统一口癖。",
            }
        ],
        "passed_checks": [],
        "created_at": "2026-05-22T00:00:00Z",
    }

    result = audit_chapter(
        ChapterAuditOptions(root=root, chapter_number=1),
        MockProvider(fake_response=json.dumps(payload, ensure_ascii=False)),
    )
    report = result.report
    lines = _audit_issue_lines(report)

    assert report.overall_status == "passed"
    assert any("称呼略显老派" in line for line in lines)
    assert any("not auto-fixed" in line for line in lines)


def test_audit_chapter_repairs_invalid_provider_report_once(tmp_path: Path) -> None:
    root = _workspace_with_polished(tmp_path)
    bad = json.dumps(
        {
            "chapter_number": 1,
            "audited_file": "polished.md",
            "overall_status": "passed",
            "summary": "状态不合法。",
            "issues": [
                {
                    "id": "ISS-2-1",
                    "severity": "high",
                    "type": "continuity_issue",
                    "description": "缺少 suggested_fix。",
                    "evidence": "example",
                }
            ],
            "passed_checks": [],
            "created_at": "2026-05-22T00:00:00Z",
        },
        ensure_ascii=False,
    )
    provider = MockProvider(fake_response=[bad, default_mock_audit_report_json(1, "polished.md")])

    result = audit_chapter(ChapterAuditOptions(root=root, chapter_number=1), provider)

    assert result.report.overall_status == "passed"
    assert len(provider.requests) == 2


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


def test_parse_audit_report_normalizes_passed_with_high_to_needs_revision() -> None:
    report_json = json.dumps(
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

    report = parse_audit_report(report_json)

    assert report.overall_status == "needs_revision"
    assert report.issues[0].severity == "high"


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


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
