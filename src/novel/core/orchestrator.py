from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from novel.core.agent_output import (
    AgentInvocationContext,
    AgentOutputContract,
    AgentOutputContractError,
)
from novel.core.audit_policy import auto_fixable_issues, manual_review_blockers
from novel.core.budget import WorkflowBudgetExceeded, workflow_budget_scope
from novel.core.context_policy import render_untrusted_workspace_data
from novel.core.contracts import (
    BudgetUsage,
    CommandProposal,
    DecisionRisk,
    MemoryRepairApplyCommand,
    MemoryRepairSuggestCommand,
    ProductionExportCommand,
    ProjectStatusCommand,
    PublicCommand,
    SessionStartCommand,
    Surface,
    TaskId,
    WorkflowBudget,
    WorkflowRun,
)
from novel.core.io import atomic_write_model_json, load_json_model
from novel.core.json_extract import JsonExtractionError, extract_json_object
from novel.core.prompts import load_prompt_template, prompt_template_version
from novel.core.provider_config import (
    ProviderOverrides,
    create_agent_provider,
    default_agent_config_path,
    resolve_agent_config,
)
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.schemas import (
    AskIntentDecision,
    AskIntentTask,
    AuditRepairRouteDecision,
    AuditReport,
    RevisionRouteDecision,
    VectorContextMode,
)
from novel.core.structured_generation import (
    REPAIR_ERROR_LIMIT,
    REPAIR_INVALID_OUTPUT_LIMIT,
    JsonRepairExhaustedError,
    generate_json_with_repair,
)
from novel.core.workflow_runtime import record_workflow_decision, workflow_runtime_scope


class OrchestratorError(RuntimeError):
    """Raised when controlled orchestration cannot proceed safely."""


MIN_EXECUTABLE_INTENT_CONFIDENCE = 0.65


@dataclass(frozen=True)
class AskCommandProposalResult:
    proposal: CommandProposal
    workflow_run_id: str
    budget_usage: BudgetUsage
    intent: AskIntentDecision
    request_id: str


def load_ask_command_proposal(root: Path, workflow_run_id: str) -> tuple[CommandProposal, WorkflowRun]:
    """Load the exact persisted ask proposal selected for confirmation."""

    if not re.fullmatch(r"run_[0-9a-f]{32}", workflow_run_id):
        raise OrchestratorError("invalid workflow_run_id for ask confirmation")
    run_dir = root.resolve() / "runs" / workflow_run_id
    proposal_path = run_dir / "proposal.json"
    run_path = run_dir / "run.json"
    if not proposal_path.is_file() or not run_path.is_file():
        raise OrchestratorError(f"ask proposal not found for workflow run: {workflow_run_id}")
    proposal = load_json_model(proposal_path, CommandProposal)
    run = load_json_model(run_path, WorkflowRun)
    if run.workflow_run_id != workflow_run_id or run.surface is not Surface.ASK:
        raise OrchestratorError("workflow run is not an ask proposal")
    if proposal.command is None:
        raise OrchestratorError("ask proposal is not executable; answer its clarification request first")
    if proposal.budget != run.budget:
        raise OrchestratorError("ask proposal budget does not match its workflow run")
    return proposal, run


def propose_ask_command(
    root: Path,
    request: str,
    *,
    provider_name: str,
    budget: WorkflowBudget,
    force: bool = False,
    use_search_context: bool = True,
    use_vector_context: bool | VectorContextMode = "auto",
    intent_provider: ModelProvider | None = None,
) -> AskCommandProposalResult:
    workflow_run_id = f"run_{uuid.uuid4().hex}"
    proposal_command_id = f"cmd_{uuid.uuid4().hex}"
    request_id = f"req_{uuid.uuid4().hex}"
    try:
        with workflow_budget_scope(budget) as tracker:

            def build_proposal() -> tuple[AskIntentDecision, CommandProposal]:
                intent = decide_ask_intent(
                    root,
                    request,
                    provider_name=provider_name,
                    provider=intent_provider,
                )
                record_workflow_decision(
                    name="ask_intent",
                    task_id=TaskId.INTENT_ROUTER,
                    payload=intent.model_dump(mode="json"),
                )
                command = _ask_intent_command(
                    intent,
                    request=request,
                    provider_name=provider_name,
                    force=force,
                    use_search_context=use_search_context,
                    use_vector_context=use_vector_context,
                )
                estimated_calls = _estimate_command_model_calls(command)
                clarification = intent.user_message if command is None else None
                if (
                    command is not None
                    and not isinstance(command, ProjectStatusCommand)
                    and intent.confidence < MIN_EXECUTABLE_INTENT_CONFIDENCE
                ):
                    clarification = (
                        f"意图置信度 {intent.confidence:.2f} 低于执行阈值 "
                        f"{MIN_EXECUTABLE_INTENT_CONFIDENCE:.2f}；请明确操作类型和范围。"
                    )
                    command = None
                if command is not None and tracker.usage.model_calls + estimated_calls > budget.max_model_calls:
                    clarification = (
                        f"预计还需 {estimated_calls} 次模型调用，但 workflow 剩余预算不足；"
                        "请提高 --max-agent-calls 或缩小章节范围。"
                    )
                    command = None
                risk = _command_risk(command)
                proposal = CommandProposal(
                    command=command,
                    reason=intent.reason,
                    confidence=intent.confidence,
                    risk=risk,
                    estimated_model_calls=estimated_calls,
                    requires_confirmation=command is not None and risk is not DecisionRisk.LOW,
                    clarification_question=clarification
                    or ("请说明要创作、查看、修复记忆还是导出。" if command is None else None),
                    budget=budget,
                )
                atomic_write_model_json(root / "runs" / workflow_run_id / "proposal.json", proposal)
                record_workflow_decision(
                    name="command_proposal",
                    task_id=TaskId.INTENT_ROUTER,
                    payload=proposal.model_dump(mode="json"),
                )
                return intent, proposal

            if (root / "project.yaml").is_file():
                with workflow_runtime_scope(
                    root=root,
                    workflow_run_id=workflow_run_id,
                    command_id=proposal_command_id,
                    surface=Surface.ASK,
                    budget=budget,
                    request_id=request_id,
                ) as runtime:
                    intent, proposal = runtime.execute_node(
                        name="proposal:ask",
                        node_type="deterministic",
                        function=build_proposal,
                        input_paths=["project.yaml"],
                        output_details=lambda _: ([], [f"runs/{workflow_run_id}/proposal.json"]),
                    )
            else:
                intent, proposal = build_proposal()
            usage = tracker.snapshot()
    except WorkflowBudgetExceeded as exc:
        raise OrchestratorError(str(exc)) from exc
    return AskCommandProposalResult(
        proposal=proposal,
        workflow_run_id=workflow_run_id,
        budget_usage=usage,
        intent=intent,
        request_id=request_id,
    )


def _ask_intent_command(
    intent: AskIntentDecision,
    *,
    request: str,
    provider_name: str,
    force: bool,
    use_search_context: bool,
    use_vector_context: bool | VectorContextMode,
) -> PublicCommand | None:
    vector_mode: VectorContextMode = (
        "on" if use_vector_context is True else "off" if use_vector_context is False else use_vector_context
    )
    if intent.task == "session_start":
        chapters = intent.chapter_range or list(_chapters_from_request(request)) or [1]
        return SessionStartCommand(
            user_intent=request,
            chapter_range=chapters,
            provider_name=provider_name,
            force=force,
            use_search_context=use_search_context,
            use_vector_context=vector_mode,
        )
    if intent.task == "memory_repair_suggest":
        return MemoryRepairSuggestCommand(request=request, provider_name=provider_name)
    if intent.task == "memory_repair_apply" and intent.repair_id:
        return MemoryRepairApplyCommand(proposal_path=f"memory/repairs/{intent.repair_id}/proposal.json")
    if intent.task == "export":
        return ProductionExportCommand(type="export.markdown", force=force)
    if intent.task in {"status", "show"}:
        return ProjectStatusCommand()
    return None


def _estimate_command_model_calls(command: PublicCommand | None) -> int:
    if isinstance(command, SessionStartCommand):
        return max(1, len(command.chapter_range))
    if isinstance(command, MemoryRepairSuggestCommand):
        return 1
    return 0


def _command_risk(command: PublicCommand | None) -> DecisionRisk:
    if command is None or isinstance(command, ProjectStatusCommand):
        return DecisionRisk.LOW
    if isinstance(command, (MemoryRepairApplyCommand, ProductionExportCommand)):
        return DecisionRisk.HIGH
    return DecisionRisk.MEDIUM


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
    if provider is None and _intent_router_uses_mock(root, provider_name):
        fallback = _fallback_ask_intent_decision(instruction)
        if fallback.task != "unknown" or _extract_repair_id(instruction):
            return fallback
        return AskIntentDecision(
            task="session_start",
            reason="explicit mock provider emits a deterministic structured creation decision",
            chapter_range=list(_chapters_from_request(instruction) or (1,)),
            confidence=1.0,
            source="mock",
        )
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
        decision = generate_json_with_repair(
            route_provider,
            model_request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="intent_router",
                interaction_mode="internal_task",
                task="ask_intent",
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="intent_router",
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
        return decision
    except AgentOutputContractError:
        fallback = _fallback_ask_intent_decision(instruction)
        return fallback.model_copy(
            update={"reason": f"provider intent contract failed; fallback used. {fallback.reason}"}
        )
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
    task = aliases.get(task, task)
    if task not in allowed:
        task = "unknown"
    normalized["task"] = task
    normalized["reason"] = str(normalized.get("reason") or "intent router ask intent decision")
    normalized["chapter_range"] = _normalize_chapter_numbers(
        normalized.get("chapter_range"), list(_chapters_from_request(fallback_request))
    )
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
        return AskIntentDecision(
            task="status", reason="fallback recognized read-only status request", confidence=0.4, source="fallback"
        )
    if _contains_any(text, ("show", "查看", "显示", "列出")):
        return AskIntentDecision(
            task="show", reason="fallback recognized read-only show request", confidence=0.35, source="fallback"
        )
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
        task="unknown",
        reason="fallback does not classify write or mutation requests without a structured model decision",
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
        return _trace_revision_decision(
            RevisionRouteDecision(
                route="revision_patch",
                reason="mock structured route uses the narrowest authorized revision executor",
                chapter_numbers=chapters,
                instruction_for_revision=instruction,
                risk_level="low",
            )
        )
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
        decision = generate_json_with_repair(
            route_provider,
            request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="intent_router",
                interaction_mode="internal_task",
                task="revision_route",
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="intent_router",
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
        return _trace_revision_decision(decision)
    except AgentOutputContractError:
        fallback = _fallback_revision_route_decision(instruction, chapter_numbers=chapters)
        return _trace_revision_decision(
            fallback.model_copy(update={"reason": f"provider route contract failed; fallback used. {fallback.reason}"})
        )
    except JsonRepairExhaustedError:
        fallback = _fallback_revision_route_decision(instruction, chapter_numbers=chapters)
        return _trace_revision_decision(
            fallback.model_copy(update={"reason": f"provider route parse failed; fallback used. {fallback.reason}"})
        )


def _intent_router_uses_mock(root: Path, provider_name: str) -> bool:
    config = resolve_agent_config(
        default_agent_config_path(root),
        "intent_router",
        overrides=ProviderOverrides(provider_name=provider_name),
    )
    return config.provider == "mock"


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
        return _trace_audit_decision(AuditRepairRouteDecision(
            route="manual_review",
            reason="audit has no blocking issues",
            chapter_number=audit_report.chapter_number,
            issue_ids=[],
            risk_level="low",
            source="deterministic",
        ))
    manual_blockers = manual_review_blockers(audit_report)
    if manual_blockers:
        return _trace_audit_decision(AuditRepairRouteDecision(
            route="manual_review",
            reason="automatic repair denied by audit policy: "
            + "; ".join(f"{item.issue_id}: {item.reason}" for item in manual_blockers),
            chapter_number=audit_report.chapter_number,
            issue_ids=[item.issue_id for item in manual_blockers],
            risk_level="medium",
            source="deterministic",
        ))
    deterministic = _deterministic_audit_repair_route(audit_report)
    if provider is None and provider_name.lower() == "mock":
        return _trace_audit_decision(deterministic)
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
        decision = generate_json_with_repair(
            route_provider,
            request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="intent_router",
                interaction_mode="internal_task",
                task="audit_repair_route",
                chapter_number=audit_report.chapter_number,
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="intent_router",
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
        if decision.route != deterministic.route:
            return _trace_audit_decision(AuditRepairRouteDecision(
                route="manual_review",
                reason=(
                    "provider route conflicts with deterministic audit policy: "
                    f"provider={decision.route}, policy={deterministic.route}"
                ),
                chapter_number=audit_report.chapter_number,
                issue_ids=deterministic.issue_ids,
                source_layer=deterministic.source_layer,
                risk_level="high",
                source="deterministic",
            ))
        return _trace_audit_decision(decision)
    except AgentOutputContractError:
        return _trace_audit_decision(
            deterministic.model_copy(update={"reason": f"provider audit route contract failed; {deterministic.reason}"})
        )
    except JsonRepairExhaustedError:
        return _trace_audit_decision(
            deterministic.model_copy(update={"reason": f"provider audit route parse failed; {deterministic.reason}"})
        )


def _trace_revision_decision(decision: RevisionRouteDecision) -> RevisionRouteDecision:
    record_workflow_decision(
        name="revision_route",
        task_id=TaskId.INTENT_ROUTER,
        payload=decision.model_dump(mode="json"),
    )
    return decision


def _trace_audit_decision(decision: AuditRepairRouteDecision) -> AuditRepairRouteDecision:
    record_workflow_decision(
        name="audit_repair_route",
        task_id=TaskId.INTENT_ROUTER,
        payload=decision.model_dump(mode="json"),
    )
    return decision


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
        f"{render_untrusted_workspace_data('blocking_audit_issues', json.dumps(blocking, ensure_ascii=False, indent=2))}\n"
        f"{render_untrusted_workspace_data('plan_summary', plan_summary or '未提供')}\n"
        f"{render_untrusted_workspace_data('state_summary', state_summary or '未提供')}\n"
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
        decision = AuditRepairRouteDecision.model_validate(normalized)
    except ValidationError as exc:
        raise OrchestratorError(f"provider returned invalid AuditRepairRouteDecision: {exc}") from exc
    if decision.chapter_number != audit_report.chapter_number:
        raise OrchestratorError("audit repair route chapter does not match audited chapter")
    known_issue_ids = {issue.id for issue in audit_report.issues}
    unknown_issue_ids = sorted(set(decision.issue_ids) - known_issue_ids)
    if unknown_issue_ids:
        raise OrchestratorError(f"audit repair route referenced unknown issue ids: {unknown_issue_ids}")
    return decision


def _normalize_audit_repair_route_payload(data: dict[str, object], *, audit_report: AuditReport) -> dict[str, object]:
    normalized = dict(data)
    route = str(normalized.get("route") or "").strip().lower()
    aliases = {
        "replan": "plot_replan",
        "plot": "plot_replan",
        "writer": "revision_rewrite",
        "rewrite": "revision_rewrite",
        "revision": "revision_rewrite",
        "revise": "revision_rewrite",
        "manual": "manual_review",
    }
    route = aliases.get(route, route)
    if route not in {"plot_replan", "revision_rewrite", "manual_review"}:
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
        normalized["issue_ids"] = [
            issue.id for issue in audit_report.issues if issue.severity in {"medium", "high", "critical"}
        ]
    source_layer = str(normalized.get("source_layer") or "").strip().lower()
    normalized["source_layer"] = (
        source_layer
        if source_layer in {"plan", "draft", "polished", "state", "timeline", "canon", "style", "unknown"}
        else None
    )
    risk = str(normalized.get("risk_level") or "").strip().lower()
    normalized["risk_level"] = risk if risk in {"low", "medium", "high"} else "medium"
    normalized["source"] = "model"
    return normalized


def _audit_repair_route_repair_prompt(*, invalid_output: str, error: str) -> str:
    return (
        "上一次输出不能被解析为 AuditRepairRouteDecision。\n"
        f"错误：{error[:REPAIR_ERROR_LIMIT]}\n\n"
        "请重新只输出 JSON object。route 只能是 plot_replan / revision_rewrite / manual_review。\n"
        f"上一次输出：\n{invalid_output[:REPAIR_INVALID_OUTPUT_LIMIT]}\n"
    )


def _deterministic_audit_repair_route(audit_report: AuditReport) -> AuditRepairRouteDecision:
    blocking = auto_fixable_issues(audit_report)
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
        f"{render_untrusted_workspace_data('session_summary', session_summary or '无')}\n"
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
    data = _normalize_revision_route_payload(
        data, fallback_instruction=fallback_instruction, chapter_numbers=chapter_numbers
    )
    try:
        decision = RevisionRouteDecision.model_validate(data)
    except ValidationError as exc:
        raise OrchestratorError(f"provider returned invalid RevisionRouteDecision: {exc}") from exc
    unauthorized = sorted(set(decision.chapter_numbers) - set(chapter_numbers))
    if unauthorized:
        raise OrchestratorError(f"revision route escaped authorized chapters: {unauthorized}")
    return decision


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
    if route not in {"plot_replan", "writer_rewrite", "revision_patch", "manual_review"}:
        raise OrchestratorError(f"unknown revision route: {route or '<empty>'}")
    normalized = dict(data)
    normalized["route"] = route
    normalized["reason"] = str(normalized.get("reason") or "intent router route decision")
    normalized["chapter_numbers"] = _normalize_chapter_numbers(normalized.get("chapter_numbers"), chapter_numbers)
    risk = str(normalized.get("risk_level") or "").strip().lower()
    normalized["risk_level"] = (
        risk if risk in {"low", "medium", "high"} else ("low" if route == "revision_patch" else "medium")
    )
    instruction_key = {
        "plot_replan": "instruction_for_plot",
        "writer_rewrite": "instruction_for_writer",
        "revision_patch": "instruction_for_revision",
    }.get(route)
    if instruction_key and not str(normalized.get(instruction_key) or "").strip():
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
    chapters = list(chapter_numbers or _chapters_from_request(user_instruction) or (1,))
    return RevisionRouteDecision(
        route="manual_review",
        reason="structured revision routing failed; keyword heuristics are not authorized to select a write executor",
        chapter_numbers=chapters,
        risk_level="high",
    )


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


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
