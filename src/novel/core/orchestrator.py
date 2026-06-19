from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import ValidationError

from novel.core.agent_output import (
    AgentInvocationContext,
    AgentOutputContract,
    AgentOutputContractError,
)
from novel.core.auditing import ChapterAuditOptions, audit_chapter, load_audit_provider
from novel.core.canon import CanonSuggestOptions, load_canon_provider, suggest_canon
from novel.core.drafting import ChapterDraftingOptions, load_drafting_provider, write_chapter_draft
from novel.core.exporting import MarkdownExportOptions, export_markdown
from novel.core.inspiration import InspirationOptions, load_inspiration_provider, run_inspiration_agent
from novel.core.io import atomic_write_model_json
from novel.core.json_extract import JsonExtractionError, extract_json_object
from novel.core.memory_repair import suggest_memory_repair
from novel.core.planning import ChapterPlanningOptions, load_planning_provider, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, load_polishing_provider, polish_chapter
from novel.core.prompts import load_prompt_template, prompt_template_version
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.revision import ChapterRevisionOptions, load_revision_provider, revise_chapter
from novel.core.schemas import (
    AgentRunLog,
    AgentRunStep,
    AskIntentDecision,
    AskIntentTask,
    AuditRepairRouteDecision,
    AuditReport,
    RevisionRouteDecision,
    VectorContextMode,
)
from novel.core.state_update import (
    StateUpdateProposeOptions,
    load_state_update_provider,
    propose_state_update,
)
from novel.core.structured_generation import (
    REPAIR_ERROR_LIMIT,
    REPAIR_INVALID_OUTPUT_LIMIT,
    JsonRepairExhaustedError,
    generate_json_with_repair,
)
from novel.core.timeutil import utc_now, utc_timestamp


OrchestratorTask = Literal[
    "inspiration",
    "canon",
    "plan",
    "write",
    "polish",
    "audit",
    "revision",
    "plot_replan",
    "writer_rewrite",
    "revision_patch",
    "state_update",
    "export_markdown",
    "memory_repair",
]


ALLOWED_HANDOFFS: dict[str, tuple[str, ...]] = {
    "orchestrator": (
        "inspiration",
        "canon",
        "plot",
        "writer",
        "polish",
        "audit",
        "state_update",
        "export",
        "memory",
        "revision",
    ),
    "inspiration": ("canon", "plot"),
    "canon": ("plot",),
    "plot": ("writer",),
    "writer": ("polish",),
    "polish": ("audit",),
    "audit": ("writer", "revision", "state_update"),
    "revision": ("audit",),
    "state_update": ("export",),
}


TASK_TO_AGENT: dict[OrchestratorTask, str] = {
    "inspiration": "inspiration",
    "canon": "canon",
    "plan": "plot",
    "write": "writer",
    "polish": "polish",
    "audit": "audit",
    "revision": "revision",
    "plot_replan": "plot",
    "writer_rewrite": "writer",
    "revision_patch": "revision",
    "state_update": "state_update",
    "export_markdown": "export",
    "memory_repair": "memory",
}


class OrchestratorError(RuntimeError):
    """Raised when controlled orchestration cannot proceed safely."""


@dataclass(frozen=True)
class HandoffTraceEntry:
    step: int
    source: str
    target: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "step": self.step,
            "source": self.source,
            "target": self.target,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class OrchestratorPlan:
    task: OrchestratorTask
    chapter_number: int | None
    instruction: str
    handoff_trace: tuple[HandoffTraceEntry, ...]
    revision_route: RevisionRouteDecision | None = None


@dataclass(frozen=True)
class OrchestratorOptions:
    root: Path
    request: str
    provider_name: str = "config"
    dry_run: bool = False
    force: bool = False
    max_steps: int = 8
    max_retries: int = 0
    max_agent_calls: int = 8
    use_search_context: bool = True
    use_vector_context: bool | VectorContextMode = "auto"


@dataclass(frozen=True)
class OrchestratorResult:
    plan: OrchestratorPlan
    run_log: AgentRunLog | None
    run_log_path: Path | None
    message: str


def orchestrate(options: OrchestratorOptions) -> OrchestratorResult:
    root = options.root.resolve()
    request = options.request.strip()
    if not request:
        raise OrchestratorError("request must not be empty")
    _check_limits(options)

    revision_route = None
    if _is_revision_feedback_request(request):
        revision_route = (
            _fallback_revision_route_decision(request, chapter_numbers=_chapters_from_request(request))
            if options.dry_run
            else route_revision_request(
                root,
                request,
                provider_name=options.provider_name,
                chapter_numbers=_chapters_from_request(request),
            )
        )
    plan = plan_orchestration(request, revision_route=revision_route)
    if len(plan.handoff_trace) > options.max_steps:
        raise OrchestratorError(
            f"orchestration requires {len(plan.handoff_trace)} step(s), exceeds max_steps={options.max_steps}"
        )
    if len(plan.handoff_trace) > options.max_agent_calls:
        raise OrchestratorError(
            "orchestration requires "
            f"{len(plan.handoff_trace)} agent call(s), exceeds max_agent_calls={options.max_agent_calls}"
        )
    if options.dry_run:
        return OrchestratorResult(
            plan=plan,
            run_log=None,
            run_log_path=None,
            message="Dry run complete. No files were written.",
        )

    run_log = _new_run_log(plan)
    run_log_path = _run_log_path(root, run_log.run_id)
    root.joinpath("runs").mkdir(parents=True, exist_ok=True)

    try:
        _execute_plan(root, options, plan, run_log)
    except Exception as exc:
        run_log.status = "failed"
        run_log.ended_at = utc_now()
        run_log.errors.append(str(exc))
        run_log.output_files = _unique_outputs(run_log.steps)
        _write_run_log(run_log_path, run_log, plan)
        raise OrchestratorError(str(exc)) from exc

    run_log.status = "completed"
    run_log.ended_at = utc_now()
    run_log.output_files = _unique_outputs(run_log.steps)
    _write_run_log(run_log_path, run_log, plan)
    return OrchestratorResult(
        plan=plan,
        run_log=run_log,
        run_log_path=run_log_path,
        message=f"Orchestrated task completed: {plan.task}",
    )


def plan_orchestration(
    request: str,
    *,
    revision_route: RevisionRouteDecision | None = None,
) -> OrchestratorPlan:
    task = classify_request(request)
    chapter_number = _extract_chapter_number(request)
    if task == "revision" and revision_route is not None:
        task = revision_route.route
        if revision_route.chapter_numbers:
            chapter_number = revision_route.chapter_numbers[0]
    if task in {"plan", "write", "polish", "audit", "revision", "state_update"}:
        chapter_number = chapter_number or 1
    if task in {"plot_replan", "writer_rewrite", "revision_patch"}:
        chapter_number = chapter_number or 1
    target = TASK_TO_AGENT[task]
    reason = f"request classified as {task}"
    if revision_route is not None and task in {"plot_replan", "writer_rewrite", "revision_patch"}:
        reason = f"revision feedback routed as {revision_route.route}: {revision_route.reason}"
    trace = (
        HandoffTraceEntry(
            step=1,
            source="orchestrator",
            target=target,
            reason=reason,
        ),
    )
    _validate_handoff_trace(trace)
    return OrchestratorPlan(
        task=task,
        chapter_number=chapter_number,
        instruction=request,
        handoff_trace=trace,
        revision_route=revision_route,
    )


def classify_request(request: str) -> OrchestratorTask:
    text = request.lower()
    if re.search(r"\brepair_[0-9]{8}_[0-9]{6}_[0-9]{6}\b", text):
        return "memory_repair"
    if _contains_any(text, ("导出", "export", "markdown")):
        return "export_markdown"
    if _contains_any(
        text,
        (
            "修复记忆",
            "纠正记忆",
            "项目管家",
            "timeline",
            "时间线错",
            "状态错",
            "记忆错",
            "其实是回忆",
            "不是当前行动",
            "事件其实",
        ),
    ):
        return "memory_repair"
    if _contains_any(text, ("状态更新", "state update", "更新状态", "时间线更新")):
        return "state_update"
    if _contains_any(text, ("修订", "修改", "revision", "revise")):
        return "revision"
    if _contains_any(text, ("审核", "审查", "检查一致", "audit", "consistency")):
        return "audit"
    if _contains_any(text, ("润色", "polish")):
        return "polish"
    if _contains_any(text, ("写章节", "写第", "初稿", "正文", "draft", "write")):
        return "write"
    if _contains_any(text, ("章节计划", "章节大纲", "大纲", "计划", "plan", "outline")):
        return "plan"
    if _contains_any(text, ("canon", "设定", "角色设定", "世界观")):
        return "canon"
    if _contains_any(text, ("灵感", "inspiration", "弱总纲")):
        return "inspiration"
    return "plan"


def load_intent_router_provider(
    root: Path,
    provider_name: str,
    *,
    agent_config_path: Path | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    return create_agent_provider(
        agent_config_path or default_agent_config_path(root),
        "intent_router",
        overrides=ProviderOverrides(provider_name=provider_name, model_name=model_name),
        mock_response=default_mock_revision_route_decision_json(),
    )


def decide_ask_intent(
    root: Path,
    request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
) -> AskIntentDecision:
    instruction = request.strip()
    if not instruction:
        raise OrchestratorError("ask intent requires a non-empty request")
    if provider is None and provider_name.lower() == "mock":
        return _fallback_ask_intent_decision(instruction)
    route_provider = provider or load_intent_router_provider(root, provider_name)
    user_prompt = build_ask_intent_user_prompt(instruction)
    model_request = ModelRequest(
        system_prompt=load_prompt_template("intent_router_ask_intent_system"),
        user_prompt=user_prompt,
        json_schema_name="AskIntentDecision",
        prompt_version=prompt_template_version("intent_router_ask_intent_system"),
    )
    contract = AgentOutputContract(
        output_kind="json",
        target_name="AskIntentDecision",
        json_schema_name="AskIntentDecision",
        allow_user_questions=False,
    )
    try:
        return generate_json_with_repair(
            route_provider,
            model_request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="intent_router",
                caller="orchestrator",
                interaction_mode="internal_task",
                task="ask_intent",
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="intent_router",
                caller="orchestrator",
                interaction_mode="internal_task",
                task="ask_intent_repair",
            ),
            contract=contract,
            parse=lambda content: parse_ask_intent_decision(content, fallback_request=instruction),
            repair_prompt=lambda invalid_output, error: _ask_intent_repair_prompt(
                invalid_output=invalid_output,
                error=error,
            ),
        )
    except AgentOutputContractError:
        fallback = _fallback_ask_intent_decision(instruction)
        return fallback.model_copy(update={"reason": f"provider intent contract failed; fallback used. {fallback.reason}"})
    except JsonRepairExhaustedError:
        fallback = _fallback_ask_intent_decision(instruction)
        return fallback.model_copy(update={"reason": f"provider intent parse failed; fallback used. {fallback.reason}"})


def build_ask_intent_user_prompt(request: str) -> str:
    return (
        "请把下面用户请求分类为 AskIntentDecision JSON。\n"
        "注意：用户可能口语化、有错别字或混合中英文，请根据整体意图判断，不要只看关键词。\n"
        "如果用户请求应用 repair，请必须识别 repair_id；否则不要输出 memory_repair_apply。\n\n"
        f"用户请求：\n{request.strip()}\n"
    )


def parse_ask_intent_decision(content: str, *, fallback_request: str) -> AskIntentDecision:
    try:
        raw = extract_json_object(content)
    except JsonExtractionError as exc:
        raise OrchestratorError("provider response did not contain a JSON object") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OrchestratorError(f"provider returned invalid AskIntentDecision JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise OrchestratorError("provider returned AskIntentDecision as a non-object JSON value")
    data = _normalize_ask_intent_payload(data, fallback_request=fallback_request)
    try:
        return AskIntentDecision.model_validate(data)
    except ValidationError as exc:
        raise OrchestratorError(f"provider returned invalid AskIntentDecision: {exc}") from exc


def _normalize_ask_intent_payload(data: dict[str, object], *, fallback_request: str) -> dict[str, object]:
    normalized = dict(data)
    task = str(normalized.get("task") or "").strip().lower()
    aliases: dict[str, AskIntentTask] = {
        "start_session": "session_start",
        "create_session": "session_start",
        "session": "session_start",
        "memory_repair": "memory_repair_suggest",
        "repair": "memory_repair_suggest",
        "repair_suggest": "memory_repair_suggest",
        "apply_repair": "memory_repair_apply",
        "memory_apply": "memory_repair_apply",
        "export_markdown": "export",
        "markdown_export": "export",
        "project_status": "status",
        "display": "show",
    }
    allowed: set[AskIntentTask] = {
        "session_start",
        "memory_repair_suggest",
        "memory_repair_apply",
        "export",
        "status",
        "show",
        "unknown",
    }
    task = aliases.get(task, task)  # type: ignore[assignment]
    if task not in allowed:
        task = "unknown"
    normalized["task"] = task
    normalized["reason"] = str(normalized.get("reason") or "orchestrator ask intent decision")
    normalized["chapter_range"] = _normalize_chapter_numbers(normalized.get("chapter_range"), list(_chapters_from_request(fallback_request)))
    repair_id = str(normalized.get("repair_id") or "").strip()
    normalized["repair_id"] = repair_id or None
    raw_confidence = normalized.get("confidence")
    if isinstance(raw_confidence, (int, float, str)):
        try:
            confidence = float(raw_confidence)
        except ValueError:
            confidence = 0.5
    else:
        confidence = 0.5
    normalized["confidence"] = min(1.0, max(0.0, confidence))
    normalized["source"] = "model"
    if normalized["task"] == "memory_repair_apply":
        request_repair_id = _extract_repair_id(fallback_request)
        if not request_repair_id:
            normalized["task"] = "memory_repair_suggest"
            normalized["repair_id"] = None
            normalized["reason"] = (
                f"{normalized['reason']} Downgraded from memory_repair_apply because the user request "
                "does not include an explicit repair_id."
            )
            normalized.setdefault(
                "user_message",
                "我会先生成 memory repair proposal；应用 repair 需要明确的 repair_id。",
            )
        elif not normalized["repair_id"]:
            raise OrchestratorError("memory_repair_apply decision is missing repair_id")
        elif normalized["repair_id"] != request_repair_id:
            raise OrchestratorError("memory_repair_apply decision repair_id does not match user request")
    if normalized.get("user_message") is not None:
        normalized["user_message"] = str(normalized["user_message"])
    return normalized


def _ask_intent_repair_prompt(*, invalid_output: str, error: str) -> str:
    return (
        "上一次输出不能被解析为 AskIntentDecision。\n"
        f"错误：{error[:REPAIR_ERROR_LIMIT]}\n\n"
        "请重新只输出一个 JSON object，不要 Markdown 或解释。"
        "task 只能是 session_start / memory_repair_suggest / memory_repair_apply / export / status / show / unknown。\n"
        f"上一次输出：\n{invalid_output[:REPAIR_INVALID_OUTPUT_LIMIT]}\n"
    )


def _fallback_ask_intent_decision(request: str) -> AskIntentDecision:
    text = request.lower()
    if _contains_any(text, ("status", "项目状态", "当前状态", "状态面板")):
        return AskIntentDecision(task="status", reason="fallback recognized read-only status request", confidence=0.4, source="fallback")
    if _contains_any(text, ("show", "查看", "显示", "列出")):
        return AskIntentDecision(task="show", reason="fallback recognized read-only show request", confidence=0.35, source="fallback")
    task = classify_request(request)
    if task == "memory_repair":
        repair_id = _extract_repair_id(request)
        if repair_id:
            return AskIntentDecision(
                task="unknown",
                reason="fallback refused to infer memory repair apply intent from natural language",
                repair_id=repair_id,
                confidence=0.2,
                user_message=f"请使用显式命令应用 repair：novel memory-repair apply {repair_id}",
                source="fallback",
            )
        return AskIntentDecision(
            task="memory_repair_suggest",
            reason="fallback recognized a memory repair request; proposal only, no apply",
            chapter_range=list(_chapters_from_request(request)),
            confidence=0.35,
            source="fallback",
        )
    if task == "export_markdown":
        return AskIntentDecision(task="export", reason="fallback recognized export request", confidence=0.4, source="fallback")
    if task in {"plan", "write", "polish", "audit", "revision", "inspiration", "canon", "state_update"}:
        return AskIntentDecision(
            task="session_start",
            reason=f"fallback maps {task} request to creation session",
            chapter_range=list(_chapters_from_request(request)),
            confidence=0.3,
            source="fallback",
        )
    return AskIntentDecision(
        task="unknown",
        reason="fallback could not safely classify request",
        confidence=0.1,
        user_message="无法安全判断请求类型，请补充要创作、修复记忆、导出还是查看状态。",
        source="fallback",
    )


def route_revision_request(
    root: Path,
    user_instruction: str,
    *,
    provider_name: str = "config",
    chapter_numbers: tuple[int, ...] | list[int] | None = None,
    session_summary: str | None = None,
    provider: ModelProvider | None = None,
) -> RevisionRouteDecision:
    instruction = user_instruction.strip()
    if not instruction:
        raise OrchestratorError("revision routing requires a non-empty instruction")
    chapters = list(chapter_numbers or _chapters_from_request(instruction))
    if not chapters:
        chapters = [1]
    if provider is None and provider_name.lower() == "mock":
        return _fallback_revision_route_decision(instruction, chapter_numbers=chapters)
    route_provider = provider or load_intent_router_provider(root, provider_name)
    user_prompt = build_revision_route_user_prompt(
        instruction,
        chapter_numbers=chapters,
        session_summary=session_summary,
    )
    request = ModelRequest(
        system_prompt=load_prompt_template("intent_router_revision_route_system"),
        user_prompt=user_prompt,
        context=session_summary,
        json_schema_name="RevisionRouteDecision",
        prompt_version=prompt_template_version("intent_router_revision_route_system"),
    )
    contract = AgentOutputContract(
        output_kind="json",
        target_name="RevisionRouteDecision",
        json_schema_name="RevisionRouteDecision",
        allow_user_questions=False,
    )
    try:
        return generate_json_with_repair(
            route_provider,
            request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="intent_router",
                caller="orchestrator",
                interaction_mode="internal_task",
                task="revision_route",
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="intent_router",
                caller="orchestrator",
                interaction_mode="internal_task",
                task="revision_route_repair",
            ),
            contract=contract,
            parse=lambda content: parse_revision_route_decision(
                content,
                fallback_instruction=instruction,
                chapter_numbers=chapters,
            ),
            repair_prompt=lambda invalid_output, error: _revision_route_repair_prompt(
                invalid_output=invalid_output,
                error=error,
            ),
        )
    except AgentOutputContractError:
        fallback = _fallback_revision_route_decision(instruction, chapter_numbers=chapters)
        return fallback.model_copy(update={"reason": f"provider route contract failed; fallback used. {fallback.reason}"})
    except JsonRepairExhaustedError:
        fallback = _fallback_revision_route_decision(instruction, chapter_numbers=chapters)
        return fallback.model_copy(update={"reason": f"provider route parse failed; fallback used. {fallback.reason}"})


def route_audit_repair(
    root: Path,
    audit_report: AuditReport,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    plan_summary: str | None = None,
    state_summary: str | None = None,
) -> AuditRepairRouteDecision:
    blocking = [issue for issue in audit_report.issues if issue.severity in {"medium", "high", "critical"}]
    if not blocking:
        return AuditRepairRouteDecision(
            route="manual_review",
            reason="audit has no blocking issues",
            chapter_number=audit_report.chapter_number,
            issue_ids=[],
            risk_level="low",
            source="deterministic",
        )
    deterministic = _deterministic_audit_repair_route(audit_report)
    if provider is None and provider_name.lower() == "mock":
        return deterministic
    route_provider = provider or load_intent_router_provider(root, provider_name)
    user_prompt = build_audit_repair_route_user_prompt(
        audit_report,
        plan_summary=plan_summary,
        state_summary=state_summary,
    )
    request = ModelRequest(
        system_prompt=load_prompt_template("audit_repair_route_system"),
        user_prompt=user_prompt,
        json_schema_name="AuditRepairRouteDecision",
        prompt_version=prompt_template_version("audit_repair_route_system"),
    )
    contract = AgentOutputContract(
        output_kind="json",
        target_name="AuditRepairRouteDecision",
        json_schema_name="AuditRepairRouteDecision",
        allow_user_questions=False,
    )
    try:
        return generate_json_with_repair(
            route_provider,
            request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="intent_router",
                caller="session",
                interaction_mode="internal_task",
                task="audit_repair_route",
                chapter_number=audit_report.chapter_number,
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="intent_router",
                caller="session",
                interaction_mode="internal_task",
                task="audit_repair_route_repair",
                chapter_number=audit_report.chapter_number,
            ),
            contract=contract,
            parse=lambda content: parse_audit_repair_route_decision(content, audit_report=audit_report),
            repair_prompt=lambda invalid_output, error: _audit_repair_route_repair_prompt(
                invalid_output=invalid_output,
                error=error,
            ),
        )
    except AgentOutputContractError:
        return deterministic.model_copy(update={"reason": f"provider audit route contract failed; {deterministic.reason}"})
    except JsonRepairExhaustedError:
        return deterministic.model_copy(update={"reason": f"provider audit route parse failed; {deterministic.reason}"})


def build_audit_repair_route_user_prompt(
    audit_report: AuditReport,
    *,
    plan_summary: str | None = None,
    state_summary: str | None = None,
) -> str:
    blocking = [
        issue.model_dump(mode="json")
        for issue in audit_report.issues
        if issue.severity in {"medium", "high", "critical"}
    ]
    return (
        "请根据 AuditReport 的阻断问题，判断自动修复应回退到哪个工作流节点。\n"
        "不要根据单个自然语言关键词机械判断；优先使用 source_layer、evidence.source、issue 类型和上下文。\n\n"
        f"chapter_number: {audit_report.chapter_number}\n"
        f"audited_file: {audit_report.audited_file}\n"
        f"overall_status: {audit_report.overall_status}\n"
        f"blocking_issues JSON:\n{json.dumps(blocking, ensure_ascii=False, indent=2)}\n\n"
        f"plan_summary:\n{plan_summary or '未提供'}\n\n"
        f"state_summary:\n{state_summary or '未提供'}\n"
    )


def parse_audit_repair_route_decision(
    content: str,
    *,
    audit_report: AuditReport,
) -> AuditRepairRouteDecision:
    try:
        raw = extract_json_object(content)
    except JsonExtractionError as exc:
        raise OrchestratorError("provider response did not contain a JSON object") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OrchestratorError(f"provider returned invalid AuditRepairRouteDecision JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise OrchestratorError("provider returned AuditRepairRouteDecision as a non-object JSON value")
    normalized = _normalize_audit_repair_route_payload(data, audit_report=audit_report)
    try:
        return AuditRepairRouteDecision.model_validate(normalized)
    except ValidationError as exc:
        raise OrchestratorError(f"provider returned invalid AuditRepairRouteDecision: {exc}") from exc


def _normalize_audit_repair_route_payload(data: dict[str, object], *, audit_report: AuditReport) -> dict[str, object]:
    normalized = dict(data)
    route = str(normalized.get("route") or "").strip().lower()
    aliases = {
        "replan": "plot_replan",
        "plot": "plot_replan",
        "writer": "writer_rewrite",
        "rewrite": "writer_rewrite",
        "revision": "revision_rewrite",
        "revise": "revision_rewrite",
        "manual": "manual_review",
    }
    route = aliases.get(route, route)
    if route not in {"plot_replan", "writer_rewrite", "revision_rewrite", "manual_review"}:
        raise OrchestratorError(f"unknown audit repair route: {route or '<empty>'}")
    normalized["route"] = route
    normalized["reason"] = str(normalized.get("reason") or "audit repair route decision")
    raw_chapter_number = normalized.get("chapter_number")
    if isinstance(raw_chapter_number, (int, str)):
        try:
            normalized["chapter_number"] = int(raw_chapter_number)
        except ValueError:
            normalized["chapter_number"] = audit_report.chapter_number
    else:
        normalized["chapter_number"] = audit_report.chapter_number
    issue_ids = normalized.get("issue_ids")
    if not isinstance(issue_ids, list) or not issue_ids:
        normalized["issue_ids"] = [issue.id for issue in audit_report.issues if issue.severity in {"medium", "high", "critical"}]
    source_layer = str(normalized.get("source_layer") or "").strip().lower()
    normalized["source_layer"] = source_layer if source_layer in {"plan", "draft", "polished", "state", "timeline", "canon", "style", "unknown"} else None
    risk = str(normalized.get("risk_level") or "").strip().lower()
    normalized["risk_level"] = risk if risk in {"low", "medium", "high"} else "medium"
    normalized["source"] = "model"
    return normalized


def _audit_repair_route_repair_prompt(*, invalid_output: str, error: str) -> str:
    return (
        "上一次输出不能被解析为 AuditRepairRouteDecision。\n"
        f"错误：{error[:REPAIR_ERROR_LIMIT]}\n\n"
        "请重新只输出 JSON object。route 只能是 plot_replan / writer_rewrite / revision_rewrite / manual_review。\n"
        f"上一次输出：\n{invalid_output[:REPAIR_INVALID_OUTPUT_LIMIT]}\n"
    )


def _deterministic_audit_repair_route(audit_report: AuditReport) -> AuditRepairRouteDecision:
    blocking = [issue for issue in audit_report.issues if issue.severity in {"medium", "high", "critical"}]
    issue_ids = [issue.id for issue in blocking]
    layers = {issue.source_layer for issue in blocking if issue.source_layer}
    evidence_sources = {item.source for issue in blocking for item in issue.evidence}
    if "plan" in layers or any(Path(source).name == "plan.json" for source in evidence_sources):
        return AuditRepairRouteDecision(
            route="plot_replan",
            reason="structured audit evidence points to plan-level conflict",
            chapter_number=audit_report.chapter_number,
            issue_ids=issue_ids,
            source_layer="plan",
            risk_level="high",
            source="deterministic",
        )
    if layers & {"draft", "polished", "style"} or any(
        Path(source).name in {"draft.md", "polished.md"} for source in evidence_sources
    ):
        return AuditRepairRouteDecision(
            route="revision_rewrite",
            reason="structured audit evidence points to generated text",
            chapter_number=audit_report.chapter_number,
            issue_ids=issue_ids,
            source_layer=next(iter(layers & {"draft", "polished", "style"}), "polished"),
            risk_level="medium",
            source="deterministic",
        )
    return AuditRepairRouteDecision(
        route="manual_review",
        reason="blocking audit issues do not provide enough structured routing evidence",
        chapter_number=audit_report.chapter_number,
        issue_ids=issue_ids,
        source_layer=next(iter(layers), "unknown") if layers else "unknown",
        risk_level="medium",
        source="deterministic",
    )


def build_revision_route_user_prompt(
    user_instruction: str,
    *,
    chapter_numbers: list[int],
    session_summary: str | None = None,
) -> str:
    return (
        "请判断用户这次对已生成内容的修改意见应该由哪条工作流处理。\n"
        "只能三选一：\n"
        "1. plot_replan：核心剧情、章节目标、场景结构、人物动机、关键设定揭示发生变化。\n"
        "2. writer_rewrite：不改核心剧情，但涉及人物刻画、铺垫、详略取舍、叙事风格、节奏、对白、描写。\n"
        "3. revision_patch：只改指定局部语句或表达，不影响剧情、canon、state、timeline。\n\n"
        "请输出 JSON，字段必须包含：route, reason, chapter_numbers, "
        "instruction_for_plot, instruction_for_writer, instruction_for_revision, risk_level。\n"
        "只填写被选 route 对应的 instruction 字段，其他 instruction 字段可为 null。\n"
        "risk_level 只能是 low/medium/high。\n\n"
        f"涉及章节：{chapter_numbers}\n"
        f"Session 摘要：\n{session_summary or '无'}\n\n"
        f"用户修改意见：\n{user_instruction.strip()}\n"
    )


def parse_revision_route_decision(
    content: str,
    *,
    fallback_instruction: str,
    chapter_numbers: list[int],
) -> RevisionRouteDecision:
    try:
        raw = extract_json_object(content)
    except JsonExtractionError as exc:
        raise OrchestratorError("provider response did not contain a JSON object") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OrchestratorError(f"provider returned invalid RevisionRouteDecision JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise OrchestratorError("provider returned RevisionRouteDecision as a non-object JSON value")
    data = _normalize_revision_route_payload(data, fallback_instruction=fallback_instruction, chapter_numbers=chapter_numbers)
    try:
        return RevisionRouteDecision.model_validate(data)
    except ValidationError as exc:
        raise OrchestratorError(f"provider returned invalid RevisionRouteDecision: {exc}") from exc


def _revision_route_repair_prompt(*, invalid_output: str, error: str) -> str:
    return (
        "上一次输出不能被解析为 RevisionRouteDecision。\n"
        f"错误：{error[:REPAIR_ERROR_LIMIT]}\n\n"
        "请重新只输出一个 JSON object，不要 Markdown 或解释。"
        "route 只能是 plot_replan / writer_rewrite / revision_patch；"
        "必须填写被选 route 对应的 instruction 字段。\n"
        f"上一次输出：\n{invalid_output[:REPAIR_INVALID_OUTPUT_LIMIT]}\n"
    )


def default_mock_revision_route_decision_json(route: str = "revision_patch") -> str:
    instruction_key = {
        "plot_replan": "instruction_for_plot",
        "writer_rewrite": "instruction_for_writer",
        "revision_patch": "instruction_for_revision",
    }.get(route, "instruction_for_revision")
    payload: dict[str, object] = {
        "route": route,
        "reason": "mock route decision",
        "chapter_numbers": [1],
        "instruction_for_plot": None,
        "instruction_for_writer": None,
        "instruction_for_revision": None,
        "risk_level": "low" if route == "revision_patch" else "medium",
    }
    payload[instruction_key] = "按用户要求修订。"
    return json.dumps(payload, ensure_ascii=False)


def _normalize_revision_route_payload(
    data: dict[str, object],
    *,
    fallback_instruction: str,
    chapter_numbers: list[int],
) -> dict[str, object]:
    route = str(data.get("route") or "").strip().lower()
    aliases = {
        "plot": "plot_replan",
        "replan": "plot_replan",
        "outline": "plot_replan",
        "writer": "writer_rewrite",
        "rewrite": "writer_rewrite",
        "polish": "writer_rewrite",
        "revision": "revision_patch",
        "patch": "revision_patch",
        "local_patch": "revision_patch",
    }
    route = aliases.get(route, route)
    if route not in {"plot_replan", "writer_rewrite", "revision_patch"}:
        raise OrchestratorError(f"unknown revision route: {route or '<empty>'}")
    normalized = dict(data)
    normalized["route"] = route
    normalized["reason"] = str(normalized.get("reason") or "orchestrator route decision")
    normalized["chapter_numbers"] = _normalize_chapter_numbers(normalized.get("chapter_numbers"), chapter_numbers)
    risk = str(normalized.get("risk_level") or "").strip().lower()
    normalized["risk_level"] = risk if risk in {"low", "medium", "high"} else ("low" if route == "revision_patch" else "medium")
    instruction_key = {
        "plot_replan": "instruction_for_plot",
        "writer_rewrite": "instruction_for_writer",
        "revision_patch": "instruction_for_revision",
    }[route]
    if not str(normalized.get(instruction_key) or "").strip():
        normalized[instruction_key] = fallback_instruction
    for key in ("instruction_for_plot", "instruction_for_writer", "instruction_for_revision"):
        if key != instruction_key and normalized.get(key) == "":
            normalized[key] = None
    return normalized


def _fallback_revision_route_decision(
    user_instruction: str,
    *,
    chapter_numbers: tuple[int, ...] | list[int] | None = None,
) -> RevisionRouteDecision:
    text = user_instruction.lower()
    chapters = list(chapter_numbers or _chapters_from_request(user_instruction) or (1,))
    if _looks_like_local_expression_patch(text):
        return RevisionRouteDecision(
            route="revision_patch",
            reason="fallback: request looks like a local wording replacement",
            chapter_numbers=chapters,
            instruction_for_revision=user_instruction.strip(),
            risk_level="low",
        )
    if _contains_any(
        text,
        (
            "核心剧情",
            "剧情走向",
            "结尾",
            "大纲",
            "场景结构",
            "人物动机",
            "背叛",
            "死亡",
            "真相",
            "身份",
            "揭示",
            "改成主角",
        ),
    ):
        return RevisionRouteDecision(
            route="plot_replan",
            reason="fallback: request appears to change plot structure or core story facts",
            chapter_numbers=chapters,
            instruction_for_plot=user_instruction.strip(),
            risk_level="high",
        )
    return RevisionRouteDecision(
        route="writer_rewrite",
        reason="fallback: request affects execution, prose, pacing, characterization, or emphasis",
        chapter_numbers=chapters,
        instruction_for_writer=user_instruction.strip(),
        risk_level="medium",
    )


def handoff_rules_text() -> str:
    lines = ["Allowed handoffs:"]
    for source, targets in ALLOWED_HANDOFFS.items():
        lines.append(f"- {source} -> {', '.join(targets)}")
    return "\n".join(lines)


def format_orchestrator_plan(plan: OrchestratorPlan) -> str:
    lines = [
        f"Task: {plan.task}",
        f"Chapter: {plan.chapter_number if plan.chapter_number is not None else 'none'}",
        "Handoff trace:",
    ]
    for entry in plan.handoff_trace:
        lines.append(f"- {entry.step}: {entry.source} -> {entry.target} ({entry.reason})")
    if plan.revision_route is not None:
        lines.extend(
            [
                "Revision route:",
                f"- route: {plan.revision_route.route}",
                f"- reason: {plan.revision_route.reason}",
                f"- risk: {plan.revision_route.risk_level}",
            ]
        )
    return "\n".join(lines)


def _execute_plan(
    root: Path,
    options: OrchestratorOptions,
    plan: OrchestratorPlan,
    run_log: AgentRunLog,
) -> None:
    call_count = 0
    for index, handoff in enumerate(plan.handoff_trace, start=1):
        call_count += 1
        if call_count > options.max_agent_calls:
            raise OrchestratorError(f"max_agent_calls exceeded: {options.max_agent_calls}")
        step = AgentRunStep(
            step_id=f"step_{index:03d}",
            agent=f"{handoff.target}_agent" if handoff.target != "export" else "export_service",
            input_files=["project.yaml"],
            output_files=[],
            status="running",
        )
        run_log.steps.append(step)
        try:
            step.output_files = _execute_task(root, options, plan)
            step.status = "completed"
        except Exception as exc:
            step.status = "failed"
            step.error = str(exc)
            raise


def _execute_task(root: Path, options: OrchestratorOptions, plan: OrchestratorPlan) -> list[str]:
    provider_name = options.provider_name
    chapter = plan.chapter_number
    if plan.task == "inspiration":
        provider = load_inspiration_provider(root, provider_name)
        inspiration_result = run_inspiration_agent(
            InspirationOptions(
                root=root,
                source_text=plan.instruction,
                source_type="ask",
                overwrite=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            provider,
        )
        outputs = [_rel(root, inspiration_result.markdown_path)]
        if inspiration_result.json_path:
            outputs.append(_rel(root, inspiration_result.json_path))
        return outputs
    if plan.task == "canon":
        provider = load_canon_provider(root, provider_name)
        output_path = root / "runs" / f"canon_proposal_{utc_timestamp()}.json"
        canon_result = suggest_canon(
            CanonSuggestOptions(
                root=root,
                output_path=output_path,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            provider,
        )
        return [_rel(root, canon_result.output_path)] if canon_result.output_path else []
    if plan.task == "plan":
        assert chapter is not None
        provider = load_planning_provider(root, provider_name, chapter_number=chapter)
        planning_result = plan_chapter(
            ChapterPlanningOptions(
                root=root,
                chapter_number=chapter,
                instruction=plan.instruction,
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            provider,
        )
        return [_rel(root, planning_result.plan_json_path), _rel(root, planning_result.plan_markdown_path)]
    if plan.task == "write":
        assert chapter is not None
        provider = load_drafting_provider(root, provider_name)
        drafting_result = write_chapter_draft(
            ChapterDraftingOptions(
                root=root,
                chapter_number=chapter,
                instruction=plan.instruction,
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            provider,
        )
        return [_rel(root, drafting_result.draft_path)]
    if plan.task == "polish":
        assert chapter is not None
        provider = load_polishing_provider(root, provider_name)
        polishing_result = polish_chapter(
            ChapterPolishingOptions(
                root=root,
                chapter_number=chapter,
                instruction=plan.instruction,
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            provider,
        )
        return [_rel(root, polishing_result.polished_path)]
    if plan.task == "audit":
        assert chapter is not None
        provider = load_audit_provider(root, provider_name, chapter_number=chapter)
        audit_result = audit_chapter(
            ChapterAuditOptions(
                root=root,
                chapter_number=chapter,
                instruction=plan.instruction,
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            provider,
        )
        return [_rel(root, audit_result.audit_path)]
    if plan.task == "revision":
        assert chapter is not None
        provider = load_revision_provider(root, provider_name, target="polished")
        revision_result = revise_chapter(
            ChapterRevisionOptions(
                root=root,
                chapter_number=chapter,
                instruction=plan.instruction,
                from_audit=True,
                target="polished",
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            provider,
            provider_name=provider_name,
        )
        return [_rel(root, revision_result.output_path), _rel(root, revision_result.revision_log_path)]
    if plan.task == "plot_replan":
        assert chapter is not None
        provider = load_planning_provider(root, provider_name, chapter_number=chapter)
        replan_result = plan_chapter(
            ChapterPlanningOptions(
                root=root,
                chapter_number=chapter,
                instruction=plan.revision_route.instruction_for_plot if plan.revision_route else plan.instruction,
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            provider,
        )
        return [_rel(root, replan_result.plan_json_path), _rel(root, replan_result.plan_markdown_path)]
    if plan.task == "writer_rewrite":
        assert chapter is not None
        provider = load_drafting_provider(root, provider_name)
        draft = write_chapter_draft(
            ChapterDraftingOptions(
                root=root,
                chapter_number=chapter,
                instruction=plan.revision_route.instruction_for_writer if plan.revision_route else plan.instruction,
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            provider,
        )
        return [_rel(root, draft.draft_path)]
    if plan.task == "revision_patch":
        assert chapter is not None
        provider = load_revision_provider(root, provider_name, target="polished")
        patch_result = revise_chapter(
            ChapterRevisionOptions(
                root=root,
                chapter_number=chapter,
                instruction=plan.revision_route.instruction_for_revision if plan.revision_route else plan.instruction,
                from_audit=False,
                target="polished",
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            provider,
            provider_name=provider_name,
        )
        return [_rel(root, patch_result.output_path), _rel(root, patch_result.revision_log_path)]
    if plan.task == "state_update":
        assert chapter is not None
        provider = load_state_update_provider(root, provider_name, chapter_number=chapter)
        state_update_result = propose_state_update(
            StateUpdateProposeOptions(
                root=root,
                chapter_number=chapter,
                instruction=plan.instruction,
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            provider,
        )
        return [_rel(root, state_update_result.proposal_path)]
    if plan.task == "export_markdown":
        export_result = export_markdown(
            MarkdownExportOptions(root=root, include_unaccepted=True, force=options.force)
        )
        return [_rel(root, export_result.output_path), _rel(root, export_result.manifest_path)]
    if plan.task == "memory_repair":
        memory_repair_result = suggest_memory_repair(root, plan.instruction, provider_name=provider_name)
        return [_rel(root, memory_repair_result.proposal_path), _rel(root, memory_repair_result.markdown_path)]
    raise OrchestratorError(f"unsupported orchestrator task: {plan.task}")


def _validate_handoff_trace(trace: tuple[HandoffTraceEntry, ...]) -> None:
    seen: set[tuple[str, str]] = set()
    for entry in trace:
        if entry.target not in ALLOWED_HANDOFFS.get(entry.source, ()):
            raise OrchestratorError(f"handoff not allowed: {entry.source} -> {entry.target}")
        key = (entry.source, entry.target)
        if key in seen:
            raise OrchestratorError(f"repeated handoff is not allowed: {entry.source} -> {entry.target}")
        seen.add(key)


def _check_limits(options: OrchestratorOptions) -> None:
    if options.max_steps < 1:
        raise OrchestratorError("max_steps must be at least 1")
    if options.max_retries < 0:
        raise OrchestratorError("max_retries must be zero or greater")
    if options.max_agent_calls < 1:
        raise OrchestratorError("max_agent_calls must be at least 1")


def _new_run_log(plan: OrchestratorPlan) -> AgentRunLog:
    now = utc_now()
    return AgentRunLog(
        run_id=f"run_{now.strftime('%Y%m%d_%H%M%S_%f')}",
        task="ask",
        chapter_number=plan.chapter_number,
        started_at=now,
        status="running",
        steps=[],
        input_files=["project.yaml"],
        output_files=[],
        errors=[],
    )


def _write_run_log(path: Path, run_log: AgentRunLog, plan: OrchestratorPlan) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    enriched = run_log.model_copy(
        update={
            "handoff_trace": [entry.as_dict() for entry in plan.handoff_trace],
            "orchestrator_task": plan.task,
            "revision_route": plan.revision_route.model_dump(mode="json") if plan.revision_route else None,
            "execution_plan": format_orchestrator_plan(plan),
        }
    )
    atomic_write_model_json(path, enriched)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _is_revision_feedback_request(request: str) -> bool:
    return classify_request(request) == "revision"


def _chapters_from_request(request: str) -> tuple[int, ...]:
    chapter = _extract_chapter_number(request)
    return (chapter,) if chapter else ()


def _extract_repair_id(text: str) -> str | None:
    match = re.search(r"\brepair_[0-9]{8}_[0-9]{6}_[0-9]{6}\b", text)
    return match.group(0) if match else None


def _extract_chapter_number(request: str) -> int | None:
    patterns = (
        r"第\s*([0-9]+)\s*章",
        r"chapter\s*([0-9]+)",
        r"章节\s*([0-9]+)",
        r"\b([0-9]+)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, request, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _normalize_chapter_numbers(value: object, fallback: list[int]) -> list[int]:
    values: list[int] = []
    raw_values = value if isinstance(value, list) else [value] if value is not None else fallback
    for item in raw_values:
        try:
            number = int(item)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in values:
            values.append(number)
    return values or fallback or [1]


def _looks_like_local_expression_patch(text: str) -> bool:
    has_local_marker = _contains_any(
        text,
        (
            "这句",
            "这句话",
            "这段话",
            "第三段",
            "第二段",
            "第一段",
            "某一句",
            "个别语句",
            "局部",
            "表达方式",
        ),
    )
    has_replace_marker = _contains_any(text, ("改成", "改为", "替换", "换成", "用", "改写为"))
    has_quote = any(marker in text for marker in ("“", "”", "\"", "'", "「", "」"))
    plot_markers = (
        "核心剧情",
        "剧情走向",
        "大纲",
        "人物动机",
        "场景结构",
        "真相",
        "身份",
        "伏笔",
    )
    return (has_local_marker or has_quote) and has_replace_marker and not _contains_any(text, plot_markers)


def _unique_outputs(steps: list[AgentRunStep]) -> list[str]:
    seen: set[str] = set()
    outputs: list[str] = []
    for step in steps:
        for path in step.output_files:
            if path and path not in seen:
                outputs.append(path)
                seen.add(path)
    return outputs


def _run_log_path(root: Path, run_id: str) -> Path:
    return root / "runs" / f"{run_id}.json"


def _rel(root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
