from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import ValidationError

from novel.core.agent_output import (
    AgentInvocationContext,
    AgentOutputContract,
)
from novel.core.canon import format_canon_summary, load_canon_files
from novel.core.consistency import check_chapter_consistency
from novel.core.context_budget import render_state_prompt_text, render_timeline_prompt_text
from novel.core.io import atomic_write_json, atomic_write_model_json, backup_if_exists, load_json_model, load_yaml_model
from novel.core.json_extract import JsonExtractionError, extract_json_object
from novel.core.polishing import DraftDocument, PolishingError, read_markdown_with_front_matter
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.prompts import load_prompt_template
from novel.core.search import retrieve_context_bundle, write_context_report
from novel.core.schemas import (
    AuditEvidence,
    AuditIssue,
    AuditReport,
    AuditRecallConfig,
    ChapterPlan,
    ContextBundle,
    EntityState,
    ProjectConfig,
    TimelineFile,
    VectorContextMode,
)
from novel.core.structured_generation import JsonRepairExhaustedError, generate_json_with_repair
from novel.core.validation import validate_canon


AuditedFile = Literal["draft.md", "polished.md"]
FocusArea = Literal[
    "canon",
    "state",
    "timeline",
    "style",
    "plot",
    "character_voice",
    "premature_reveal",
]


class AuditError(RuntimeError):
    """Raised when consistency audit cannot proceed safely."""


@dataclass(frozen=True)
class ChapterAuditOptions:
    root: Path
    chapter_number: int
    instruction: str | None = None
    force: bool = False
    strict: bool = False
    focus: tuple[FocusArea, ...] = ()
    audited_file: AuditedFile = "polished.md"
    use_search_context: bool = False
    use_vector_context: bool | VectorContextMode = "auto"
    max_recall_rounds: int | None = None


@dataclass(frozen=True)
class ChapterAuditResult:
    audit_path: Path
    report: AuditReport
    warnings: tuple[str, ...] = ()
    context_report_path: Path | None = None
    deterministic_findings: tuple[AuditIssue, ...] = ()
    deterministic_highest_severity: str | None = None


@dataclass(frozen=True)
class AuditContext:
    project: ProjectConfig
    plan: ChapterPlan
    audited_document: DraftDocument
    audited_body: str
    draft_body: str
    polished_body: str
    inspiration_md: str
    style_guide: str
    canon_summary: str
    state_json: str
    timeline_json: str
    deterministic_summary: str = ""
    search_context: str = ""
    context_bundle: ContextBundle | None = None


@dataclass(frozen=True)
class PrecheckResult:
    issues: tuple[AuditIssue, ...]
    passed_checks: tuple[str, ...]
    warnings: tuple[str, ...]
    deterministic_issues: tuple[AuditIssue, ...] = ()
    deterministic_highest_severity: str | None = None


def audit_chapter(options: ChapterAuditOptions, provider: ModelProvider) -> ChapterAuditResult:
    root = options.root.resolve()
    if options.chapter_number < 1:
        raise AuditError("chapter_number must be a positive integer")
    if options.audited_file not in {"draft.md", "polished.md"}:
        raise AuditError("--audited-file must be draft.md or polished.md")

    chapter_dir = root / "memory" / "chapters" / f"{options.chapter_number:03d}"
    audit_path = chapter_dir / "audit.json"
    plan_path = chapter_dir / "plan.json"
    audited_path = chapter_dir / options.audited_file
    if not plan_path.exists():
        raise AuditError(f"{plan_path} is missing; run novel plan-chapter first")
    if not audited_path.exists():
        raise AuditError(f"{audited_path} is missing; run novel write-chapter or polish-chapter first")
    _refuse_existing(audit_path, options.force)

    context = load_audit_context(root, options)
    precheck = run_deterministic_prechecks(root, options, context)
    context = _with_deterministic_summary(context, precheck)

    user_prompt = build_audit_user_prompt(
        context=context,
        instruction=options.instruction,
        strict=options.strict,
        focus=options.focus,
    )
    provider_report = _generate_audit_report_with_repair(provider, context, user_prompt, root)
    provider_report = _maybe_rerun_audit_with_recalled_context(
        root=root,
        options=options,
        provider=provider,
        context=context,
        report=provider_report,
        strict=options.strict,
        focus=options.focus,
    )
    provider_report = _coerce_unrecognized_audited_file(provider_report, options.audited_file)
    if provider_report.chapter_number != options.chapter_number:
        precheck = _append_precheck_issue(
            precheck,
            _issue(
                issue_id="audit_precheck_provider_chapter_number",
                severity="critical",
                issue_type="continuity_issue",
                description=(
                    f"Provider returned chapter_number {provider_report.chapter_number}, "
                    f"expected {options.chapter_number}."
                ),
                source="provider_response",
                quote=f"chapter_number={provider_report.chapter_number}",
                suggested_fix="Regenerate the audit with the requested chapter number.",
            ),
        )
    if provider_report.audited_file != options.audited_file:
        precheck = _append_precheck_issue(
            precheck,
            _issue(
                issue_id="audit_precheck_provider_audited_file",
                severity="high",
                issue_type="continuity_issue",
                description=(
                    f"Provider returned audited_file {provider_report.audited_file}, "
                    f"expected {options.audited_file}."
                ),
                source="provider_response",
                quote=f"audited_file={provider_report.audited_file}",
                suggested_fix="Use the requested audited_file value in audit.json.",
            ),
        )

    report = combine_audit_reports(
        provider_report,
        precheck_issues=precheck.issues,
        passed_checks=precheck.passed_checks,
        chapter_number=options.chapter_number,
        audited_file=options.audited_file,
    )
    if options.force:
        backup_if_exists(audit_path, reason="force")
    atomic_write_model_json(audit_path, report)
    context_report_path = (
        write_context_report(root, context.context_bundle, force=options.force)
        if context.context_bundle
        else None
    )
    return ChapterAuditResult(
        audit_path=audit_path,
        report=report,
        warnings=precheck.warnings,
        context_report_path=context_report_path,
        deterministic_findings=precheck.deterministic_issues,
        deterministic_highest_severity=precheck.deterministic_highest_severity,
    )


def load_audit_context(root: Path, options: ChapterAuditOptions) -> AuditContext:
    chapter_dir = root / "memory" / "chapters" / f"{options.chapter_number:03d}"
    project = load_yaml_model(root / "project.yaml", ProjectConfig)
    plan = load_json_model(chapter_dir / "plan.json", ChapterPlan)
    audited_document = _read_front_matter(chapter_dir / options.audited_file)
    draft_body = _read_optional_document_body(chapter_dir / "draft.md")
    polished_body = _read_optional_document_body(chapter_dir / "polished.md")
    warnings: list[str] = []
    style_guide = _read_style_guide(root, warnings)
    canon = load_canon_files(root)
    state_json = _budgeted_state_or_raw(root, project=project, chapter_number=options.chapter_number, plan=plan)
    timeline_json = _budgeted_timeline_or_raw(root, project=project, chapter_number=options.chapter_number, plan=plan)
    context_bundle = (
        retrieve_context_bundle(
            root,
            chapter_number=options.chapter_number,
            task="audit",
            instruction=options.instruction,
            plan=plan,
            use_vector=options.use_vector_context,
        )
        if options.use_search_context
        else None
    )

    return AuditContext(
        project=project,
        plan=plan,
        audited_document=audited_document,
        audited_body=audited_document.body,
        draft_body=draft_body,
        polished_body=polished_body,
        inspiration_md=_read_optional_text(root / "memory" / "inspiration.md"),
        style_guide=style_guide,
        canon_summary=format_canon_summary(canon),
        state_json=state_json,
        timeline_json=timeline_json,
        search_context=context_bundle.render_for_prompt() if context_bundle else "",
        context_bundle=context_bundle,
    )


def _budgeted_state_or_raw(root: Path, *, project: ProjectConfig, chapter_number: int, plan: ChapterPlan) -> str:
    path = root / "memory" / "state" / "current_state.json"
    try:
        state = load_json_model(path, EntityState)
        return render_state_prompt_text(state, project=project, chapter_number=chapter_number, plan=plan)
    except Exception:
        return _read_required_json_text(path)


def _budgeted_timeline_or_raw(root: Path, *, project: ProjectConfig, chapter_number: int, plan: ChapterPlan) -> str:
    path = root / "memory" / "state" / "timeline.json"
    try:
        timeline = load_json_model(path, TimelineFile)
        return render_timeline_prompt_text(timeline, project=project, chapter_number=chapter_number, task="audit", plan=plan)
    except Exception:
        return _read_required_json_text(path)


def run_deterministic_prechecks(
    root: Path,
    options: ChapterAuditOptions,
    context: AuditContext,
) -> PrecheckResult:
    chapter_dir = root / "memory" / "chapters" / f"{options.chapter_number:03d}"
    issues: list[AuditIssue] = []
    passed_checks: list[str] = [
        f"{options.audited_file}_exists",
        "plan_json_exists",
        "plan_schema_valid",
    ]
    warnings: list[str] = []

    if context.plan.chapter_number == options.chapter_number:
        passed_checks.append("plan_chapter_number_matches")
    else:
        issues.append(
            _issue(
                issue_id="audit_precheck_plan_chapter_number",
                severity="critical",
                issue_type="continuity_issue",
                description=(
                    f"plan.json chapter_number {context.plan.chapter_number} does not match "
                    f"requested chapter {options.chapter_number}."
                ),
                source=str(chapter_dir / "plan.json"),
                quote=f"chapter_number={context.plan.chapter_number}",
                suggested_fix="Update plan.json or audit the matching chapter directory.",
            )
        )

    front_chapter = context.audited_document.metadata.get("chapter_number")
    if front_chapter == options.chapter_number:
        passed_checks.append("front_matter_chapter_number_matches")
    else:
        issues.append(
            _issue(
                issue_id="audit_precheck_front_matter_chapter_number",
                severity="critical",
                issue_type="continuity_issue",
                description=(
                    f"{options.audited_file} front matter chapter_number {front_chapter} "
                    f"does not match requested chapter {options.chapter_number}."
                ),
                source=str(chapter_dir / options.audited_file),
                quote=f"chapter_number={front_chapter}",
                suggested_fix="Regenerate or correct the chapter front matter before export.",
            )
        )

    if context.audited_document.metadata:
        passed_checks.append("front_matter_valid")

    _validate_state_file(root, issues, passed_checks)
    _validate_timeline_file(root, issues, passed_checks)
    _validate_canon_files(root, issues, passed_checks)
    _validate_audited_body_against_plan(chapter_dir, options, context, issues, passed_checks)

    consistency = check_chapter_consistency(
        root,
        options.chapter_number,
        audited_body=context.audited_body,
        audited_file=options.audited_file,
        include_existing_audit=False,
    )
    deterministic_issues = tuple(finding.to_audit_issue() for finding in consistency.findings)
    issues.extend(deterministic_issues)
    passed_checks.extend(consistency.passed_checks)

    if not (root / "memory" / "style_guide.md").exists():
        warnings.append("memory/style_guide.md is missing; using default style guidance")

    return PrecheckResult(
        issues=tuple(issues),
        passed_checks=tuple(passed_checks),
        warnings=tuple(warnings),
        deterministic_issues=deterministic_issues,
        deterministic_highest_severity=consistency.highest_severity,
    )


def _with_deterministic_summary(context: AuditContext, precheck: PrecheckResult) -> AuditContext:
    return replace(context, deterministic_summary=_deterministic_summary_from_issues(precheck.deterministic_issues))


def _deterministic_summary_from_issues(issues: tuple[AuditIssue, ...]) -> str:
    if not issues:
        return "Deterministic consistency checks: no blocking issues found.\n"
    lines = ["Deterministic consistency checks:"]
    for issue in issues:
        evidence = issue.evidence[0] if issue.evidence else None
        source = evidence.source if evidence else ""
        quote = evidence.quote if evidence else ""
        lines.append(
            f"- {issue.id} [{issue.severity}/{issue.type}] {issue.description} "
            f"source={source} evidence={quote}"
        )
    return "\n".join(lines) + "\n"


def _validate_audited_body_against_plan(
    chapter_dir: Path,
    options: ChapterAuditOptions,
    context: AuditContext,
    issues: list[AuditIssue],
    passed_checks: list[str],
) -> None:
    title_tokens = _significant_tokens(context.plan.title)
    goal_tokens = _significant_tokens(context.plan.goal)
    summary_tokens = _significant_tokens(context.plan.summary)
    required_tokens = [token for token in [*title_tokens, *goal_tokens, *summary_tokens] if token]
    if not required_tokens:
        passed_checks.append("plan_keyword_precheck_skipped")
        return
    body = context.audited_body
    hits = [token for token in required_tokens if token in body]
    if hits:
        passed_checks.append("audited_body_contains_plan_keywords")
        return
    issues.append(
        _issue(
            issue_id="audit_precheck_plan_keywords_missing",
            severity="medium",
            issue_type="plot_logic_issue",
            description=f"{options.audited_file} does not contain obvious keywords from plan title/goal/summary.",
            source=str(chapter_dir / options.audited_file),
            quote=context.plan.goal[:120],
            suggested_fix="Review whether the chapter drifted away from plan.json; revise or update the plan if intentional.",
        )
    )


def load_audit_provider(
    root: Path,
    provider_name: str,
    *,
    chapter_number: int = 1,
    audited_file: AuditedFile = "polished.md",
    agent_config_path: Path | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    return create_agent_provider(
        agent_config_path or default_agent_config_path(root),
        "audit",
        overrides=ProviderOverrides(provider_name=provider_name, model_name=model_name),
        mock_response=default_mock_audit_report_json(chapter_number, audited_file),
    )


def read_audit_instruction(instruction: str | None, input_path: Path | None) -> str | None:
    if instruction and input_path:
        raise AuditError("provide either --instruction or --input, not both")
    if input_path:
        if not input_path.exists():
            raise AuditError(f"audit instruction input file is missing: {input_path}")
        return input_path.read_text(encoding="utf-8").strip() or None
    return instruction.strip() if instruction and instruction.strip() else None


def build_audit_system_prompt() -> str:
    return load_prompt_template("audit_system")


def build_audit_user_prompt(
    *,
    context: AuditContext,
    instruction: str | None,
    strict: bool,
    focus: tuple[FocusArea, ...],
    recalled_context: str = "",
) -> str:
    recalled_context_text = f"Additional recalled context：\n{recalled_context}\n\n" if recalled_context else ""
    return (
        f"项目：{context.project.title}\n"
        f"语言：{context.project.language}\n"
        f"类型：{', '.join(context.project.genre)}\n"
        f"章节：{context.plan.chapter_number} - {context.plan.title}\n"
        f"严格审核：{'是' if strict else '否'}\n"
        f"审核重点：{', '.join(focus) if focus else '全部'}\n"
        f"用户额外审核要求：{instruction or '无'}\n\n"
        "请输出严格 JSON，符合 AuditReport schema，至少包含：\n"
        "chapter_number, audited_file, overall_status, summary, issues, passed_checks, created_at, need_context。\n"
        "issues 每项至少包含 id, severity, type, description, evidence, suggested_fix。\n\n"
        "字段约束：\n"
        "- issue.id 必须使用小写字母、数字和下划线，例如 audit_001_001，不要使用连字符。\n"
        "- issue.evidence 必须是数组，每项包含 source 和 quote；不要把 evidence 写成字符串。\n"
        "- severity 只能是 low, medium, high, critical。\n"
        "- overall_status 只能是 passed, needs_revision, blocked。\n\n"
        "Severity policy：\n"
        "- critical：会导致章节无法继续使用的问题，例如重大设定矛盾、主角死亡但后文当作未死亡、章节编号错乱。\n"
        "- high：明显影响连续性的问题，例如物品位置错误、角色知道了不该知道的信息、提前揭示重大隐藏真相。\n"
        "- medium：影响阅读或逻辑但可轻微修改的问题，例如动机解释不足、场景转场不清楚。\n"
        "- low：轻微风格或表述问题，例如语气略偏、局部重复。\n\n"
        "Status policy：存在 critical 时 overall_status 必须是 blocked；"
        "存在 medium/high 且无 critical 时必须是 needs_revision；"
        "只有 low issues 时 overall_status 应为 passed，交给用户决定是否修复；"
        "passed 不得包含 medium/high/critical issues。\n\n"
        "Deterministic checks 已经由程序完成。请不要机械重复这些结论；"
        "请在此基础上补充语义层审核，例如人物是否知道不该知道的信息、动机因果是否合理、"
        "hidden truth 是否被暗示过度。\n"
        "Timeline 审核必须区分 narrative_position（正文呈现顺序）和 story_position（故事世界顺序）。"
        "倒序、插叙、回忆、旧事揭示本身不是 timeline_conflict；只有 narrative_position 倒退、"
        "或同一 story_position.thread_id 内已明确 story_position.order 的 causes/effects 反转，"
        "才应作为时间线硬冲突。\n"
        "如果缺少某段历史正文、实体或查询上下文，且该缺口会影响 medium/high/critical 问题判断，"
        "可在 need_context 中列出 kind=chapter_prose/entity/query、ref 和 reason；否则 need_context 置空数组。\n"
        f"{context.deterministic_summary}\n"
        f"ChapterPlan：\n{context.plan.model_dump_json(indent=2)}\n\n"
        f"Audited file metadata：\n{json.dumps(context.audited_document.metadata, ensure_ascii=False, indent=2, default=str)}\n\n"
        f"Audited file body：\n{context.audited_body}\n\n"
        f"Draft body：\n{context.draft_body}\n\n"
        f"Polished body：\n{context.polished_body}\n\n"
        f"{context.search_context}\n"
        f"{recalled_context_text}"
        f"Style guide：\n{context.style_guide}\n\n"
        f"Canon 摘要：\n{context.canon_summary}\n\n"
        f"Current state：\n{context.state_json}\n\n"
        f"Timeline：\n{context.timeline_json}\n\n"
        f"Inspiration.md：\n{context.inspiration_md}\n"
    )


def _maybe_rerun_audit_with_recalled_context(
    *,
    root: Path,
    options: ChapterAuditOptions,
    provider: ModelProvider,
    context: AuditContext,
    report: AuditReport,
    strict: bool,
    focus: tuple[FocusArea, ...],
) -> AuditReport:
    if not report.need_context:
        return report
    recall_config = context.project.audit_recall or AuditRecallConfig()
    configured_rounds = recall_config.max_recall_rounds if recall_config.enabled else 0
    max_rounds = options.max_recall_rounds if options.max_recall_rounds is not None else configured_rounds
    if max_rounds <= 0:
        return report
    max_requests = recall_config.max_requests_per_round
    recalled_context, log_entries = _resolve_audit_context_requests(
        root,
        options=options,
        context=context,
        report=report,
        max_requests=max_requests,
    )
    _write_audit_recall_log(root, options.chapter_number, log_entries)
    if not recalled_context.strip():
        return report
    rerun_prompt = build_audit_user_prompt(
        context=context,
        instruction=options.instruction,
        strict=strict,
        focus=focus,
        recalled_context=recalled_context,
    )
    return _generate_audit_report_with_repair(provider, context, rerun_prompt, root)


def _resolve_audit_context_requests(
    root: Path,
    *,
    options: ChapterAuditOptions,
    context: AuditContext,
    report: AuditReport,
    max_requests: int,
) -> tuple[str, list[dict[str, object]]]:
    sections: list[str] = []
    log_entries: list[dict[str, object]] = []
    for request in report.need_context[:max_requests]:
        title = f"{request.kind}:{request.ref}"
        content = ""
        if request.kind == "chapter_prose":
            content = _recall_chapter_prose(root, request.ref)
        elif request.kind in {"entity", "query"}:
            bundle = retrieve_context_bundle(
                root,
                chapter_number=options.chapter_number,
                task="audit",
                instruction=request.ref,
                plan=context.plan,
                use_vector=options.use_vector_context,
            )
            content = bundle.render_for_prompt()
        if content.strip():
            sections.append(f"## {title}\nReason: {request.reason}\n{content.strip()}")
        log_entries.append(
            {
                "kind": request.kind,
                "ref": request.ref,
                "reason": request.reason,
                "found": bool(content.strip()),
            }
        )
    return "\n\n".join(sections), log_entries


def _recall_chapter_prose(root: Path, ref: str) -> str:
    match = re.search(r"\d+", ref)
    if not match:
        return ""
    chapter_number = int(match.group(0))
    chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
    for name in ("polished.md", "draft.md"):
        path = chapter_dir / name
        if path.exists():
            return f"{path.relative_to(root)}:\n{path.read_text(encoding='utf-8')}"
    return ""


def _write_audit_recall_log(root: Path, chapter_number: int, entries: list[dict[str, object]]) -> None:
    chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        chapter_dir / "audit_recall.json",
        {"chapter_number": chapter_number, "created_at": _utc_now(), "requests": entries},
    )


def parse_audit_report(content: str) -> AuditReport:
    try:
        json_text = extract_json_object(content)
    except JsonExtractionError as exc:
        raise AuditError("provider response does not contain a JSON object") from exc
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise AuditError(f"provider did not return valid AuditReport JSON: {exc}") from exc
    try:
        return AuditReport.model_validate(_normalize_audit_report_data(data))
    except ValidationError as exc:
        raise AuditError(f"provider returned invalid AuditReport: {exc}") from exc


def _generate_audit_report_with_repair(
    provider: ModelProvider,
    context: AuditContext,
    user_prompt: str,
    root: Path,
) -> AuditReport:
    request = ModelRequest(
        system_prompt=build_audit_system_prompt(),
        user_prompt=user_prompt,
        context=context.canon_summary,
        json_schema_name="AuditReport",
    )
    contract = AgentOutputContract(
        output_kind="json",
        target_name="AuditReport",
        json_schema_name="AuditReport",
    )
    try:
        return generate_json_with_repair(
            provider,
            request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="audit",
                caller="cli",
                interaction_mode="internal_task",
                task="audit_chapter",
                chapter_number=context.plan.chapter_number,
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="audit",
                caller="cli",
                interaction_mode="internal_task",
                task="audit_chapter_repair",
                chapter_number=context.plan.chapter_number,
            ),
            contract=contract,
            parse=parse_audit_report,
            repair_prompt=lambda invalid_output, error: _repair_prompt(
                schema_name="AuditReport",
                original_prompt=user_prompt,
                invalid_output=invalid_output,
                error=error,
            ),
        )
    except JsonRepairExhaustedError as exc:
        raise AuditError(str(exc)) from exc.second_error


def _normalize_audit_report_data(data: object) -> object:
    if not isinstance(data, dict):
        return data
    normalized = dict(data)
    if "audited_file" in normalized:
        normalized["audited_file"] = _normalize_audited_file(str(normalized.get("audited_file") or ""))
    audited_file = str(normalized.get("audited_file") or "audited_file")
    issues = normalized.get("issues")
    if isinstance(issues, list):
        normalized_issues = []
        for index, issue in enumerate(issues, start=1):
            if not isinstance(issue, dict):
                normalized_issues.append(issue)
                continue
            item = dict(issue)
            if "id" in item:
                item["id"] = _normalize_issue_id(str(item["id"]), index)
            evidence = item.get("evidence")
            if isinstance(evidence, str):
                item["evidence"] = [{"source": audited_file, "quote": evidence}]
            elif isinstance(evidence, dict):
                item["evidence"] = [evidence]
            normalized_issues.append(item)
        normalized["issues"] = normalized_issues
        _normalize_provider_issue_severities(normalized)
    return normalized


def _normalize_audited_file(value: str) -> str:
    text = value.strip()
    if text in {"draft.md", "polished.md"}:
        return text
    lowered = text.lower().replace("\\", "/")
    basename = lowered.rsplit("/", 1)[-1]
    if basename in {"draft.md", "polished.md"}:
        return basename
    if "draft" in lowered or "初稿" in lowered:
        return "draft.md"
    if "polished" in lowered or "polish" in lowered or "润色" in lowered or "正文" in lowered:
        return "polished.md"
    return text


def _coerce_unrecognized_audited_file(report: AuditReport, requested: AuditedFile) -> AuditReport:
    if report.audited_file in {"draft.md", "polished.md"}:
        return report
    return report.model_copy(update={"audited_file": requested})


def _normalize_issue_id(value: str, index: int) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
    return normalized or f"issue_{index}"


def _normalize_provider_issue_severities(report_data: dict[str, object]) -> None:
    issues = report_data.get("issues")
    if not isinstance(issues, list):
        return
    for issue in issues:
        if isinstance(issue, dict) and issue.get("severity") == "medium":
            if _is_subjective_nonblocking_issue(issue):
                issue["severity"] = "low"
    severities = [issue.get("severity") for issue in issues if isinstance(issue, dict)]
    if "critical" in severities:
        report_data["overall_status"] = "blocked"
    elif "high" in severities or "medium" in severities:
        report_data["overall_status"] = "needs_revision"
    else:
        report_data["overall_status"] = "passed"


def _is_subjective_nonblocking_issue(issue: dict[str, object]) -> bool:
    if issue.get("is_hard_blocker") is True:
        return False
    source_layer = str(issue.get("source_layer") or "").strip().lower()
    if source_layer in {"plan", "state", "timeline", "canon"}:
        return False
    evidence_strength = str(issue.get("evidence_strength") or "").strip().lower()
    if evidence_strength == "strong":
        return False
    confidence_value = issue.get("confidence")
    if isinstance(confidence_value, (int, float, str)):
        try:
            confidence = float(confidence_value)
        except ValueError:
            confidence = None
    else:
        confidence = None
    has_specific_evidence = _has_specific_audit_evidence(issue)
    if issue.get("is_hard_blocker") is False:
        return not has_specific_evidence
    if evidence_strength == "weak":
        return not has_specific_evidence
    if confidence is not None and confidence < 0.55:
        return not has_specific_evidence
    return False


def _has_specific_audit_evidence(issue: dict[str, object]) -> bool:
    evidence = issue.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return False
    for item in evidence:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if source and quote:
            return True
    return False


def _repair_prompt(
    *,
    schema_name: str,
    original_prompt: str,
    invalid_output: str,
    error: str,
) -> str:
    return (
        f"你上一次输出的 {schema_name} JSON 无法通过解析或 schema 校验。\n"
        "请只输出修正后的 JSON，不要解释，不要 Markdown 包装。\n\n"
        f"校验错误摘要：\n{error[:2400]}\n\n"
        f"上一次输出：\n{invalid_output[:6000]}\n\n"
        f"原始任务要求：\n{original_prompt[:6000]}\n"
    )


def combine_audit_reports(
    provider_report: AuditReport,
    *,
    precheck_issues: tuple[AuditIssue, ...],
    passed_checks: tuple[str, ...],
    chapter_number: int,
    audited_file: AuditedFile,
) -> AuditReport:
    issues = list(precheck_issues) + list(provider_report.issues)
    status = _status_for_issues(issues, provider_report.overall_status)
    checks = _unique_preserve_order([*passed_checks, *provider_report.passed_checks])
    summary = provider_report.summary
    if precheck_issues:
        summary = f"Deterministic pre-checks found {len(precheck_issues)} issue(s). {summary}"

    return AuditReport(
        chapter_number=chapter_number,
        audited_file=audited_file,
        overall_status=status,
        summary=summary,
        issues=issues,
        passed_checks=checks,
        created_at=datetime.now(timezone.utc),
    )


def default_mock_audit_report_json(
    chapter_number: int = 1,
    audited_file: AuditedFile = "polished.md",
) -> str:
    return json.dumps(
        {
            "chapter_number": chapter_number,
            "audited_file": audited_file,
            "overall_status": "passed",
            "summary": "Mock audit found no blocking consistency issues.",
            "issues": [],
            "passed_checks": [
                "canon_consistency_reviewed",
                "state_consistency_reviewed",
                "timeline_consistency_reviewed",
                "style_reviewed",
                "premature_reveal_reviewed",
            ],
            "created_at": _utc_now(),
        },
        ensure_ascii=False,
    )


def _validate_state_file(root: Path, issues: list[AuditIssue], passed_checks: list[str]) -> None:
    path = root / "memory" / "state" / "current_state.json"
    try:
        load_json_model(path, EntityState)
    except Exception as exc:
        issues.append(
            _issue(
                issue_id="audit_precheck_current_state_schema",
                severity="high",
                issue_type="state_conflict",
                description=f"current_state.json cannot be validated as EntityState: {exc}",
                source=str(path),
                quote=exc.__class__.__name__,
                suggested_fix="Fix memory/state/current_state.json before accepting this chapter.",
            )
        )
    else:
        passed_checks.append("current_state_schema_valid")


def _validate_timeline_file(root: Path, issues: list[AuditIssue], passed_checks: list[str]) -> None:
    path = root / "memory" / "state" / "timeline.json"
    try:
        load_json_model(path, TimelineFile)
    except Exception as exc:
        issues.append(
            _issue(
                issue_id="audit_precheck_timeline_schema",
                severity="high",
                issue_type="timeline_conflict",
                description=f"timeline.json cannot be validated as TimelineFile: {exc}",
                source=str(path),
                quote=exc.__class__.__name__,
                suggested_fix="Fix memory/state/timeline.json before accepting this chapter.",
            )
        )
    else:
        passed_checks.append("timeline_schema_valid")


def _validate_canon_files(root: Path, issues: list[AuditIssue], passed_checks: list[str]) -> None:
    report = validate_canon(root)
    if report.ok:
        passed_checks.append("canon_validation_passed")
    for index, message in enumerate(report.errors, start=1):
        issues.append(
            _issue(
                issue_id=f"audit_precheck_canon_error_{index}",
                severity="high",
                issue_type="canon_conflict",
                description=message.message,
                source=str(message.path),
                quote="canon validation error",
                suggested_fix="Fix canon validation errors before accepting this chapter.",
            )
        )
    for index, message in enumerate(report.warnings, start=1):
        issues.append(
            _issue(
                issue_id=f"audit_precheck_canon_warning_{index}",
                severity="low",
                issue_type="canon_conflict",
                description=message.message,
                source=str(message.path),
                quote="canon validation warning",
                suggested_fix="Review the referenced canon relationship and update missing IDs if needed.",
            )
        )


def _issue(
    *,
    issue_id: str,
    severity: Literal["low", "medium", "high", "critical"],
    issue_type: str,
    description: str,
    source: str,
    quote: str,
    suggested_fix: str,
) -> AuditIssue:
    return AuditIssue(
        id=issue_id,
        severity=severity,
        type=issue_type,
        description=description,
        evidence=[AuditEvidence(source=source, quote=quote)],
        suggested_fix=suggested_fix,
    )


def _append_precheck_issue(precheck: PrecheckResult, issue: AuditIssue) -> PrecheckResult:
    return PrecheckResult(
        issues=(*precheck.issues, issue),
        passed_checks=precheck.passed_checks,
        warnings=precheck.warnings,
    )


def _status_for_issues(
    issues: list[AuditIssue],
    preferred: Literal["passed", "needs_revision", "blocked"],
) -> Literal["passed", "needs_revision", "blocked"]:
    if any(issue.severity == "critical" for issue in issues):
        return "blocked"
    if any(issue.severity in {"medium", "high"} for issue in issues):
        return "needs_revision"
    if issues:
        return "passed"
    return preferred


def _significant_tokens(text: str) -> list[str]:
    candidates: list[str] = []
    for chunk in re_split_tokens(text):
        chunk = chunk.strip()
        if len(chunk) >= 2 and chunk not in {"本章", "目标", "摘要", "一个", "一次"}:
            candidates.append(chunk)
    return candidates[:8]


def re_split_tokens(text: str) -> list[str]:
    import re

    return [item for item in re.split(r"[\s,，。；;：:、！？!?（）()\[\]【】\"']+", text) if item]


def _read_front_matter(path: Path) -> DraftDocument:
    try:
        return read_markdown_with_front_matter(path)
    except PolishingError as exc:
        raise AuditError(str(exc)) from exc


def _read_optional_document_body(path: Path) -> str:
    if not path.exists():
        return ""
    return _read_front_matter(path).body


def _read_style_guide(root: Path, warnings: list[str]) -> str:
    path = root / "memory" / "style_guide.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    warnings.append("memory/style_guide.md is missing; using default style guidance")
    return "# Style Guide\n\n## Overall Style\n\n保持清晰、克制、连贯，避免过度解释。\n"


def _read_optional_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _read_required_json_text(path: Path) -> str:
    if not path.exists():
        raise AuditError(f"{path} is missing")
    return path.read_text(encoding="utf-8")


def _refuse_existing(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise AuditError(f"{path} already exists; use --force to overwrite it")


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
