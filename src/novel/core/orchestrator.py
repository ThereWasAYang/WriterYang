from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import ValidationError

from novel.core.agent_output import (
    AgentInvocationContext,
    AgentOutputContract,
    AgentOutputContractError,
    generate_with_output_guard,
)
from novel.core.auditing import ChapterAuditOptions, audit_chapter, load_audit_provider
from novel.core.canon import CanonSuggestOptions, load_canon_provider, suggest_canon
from novel.core.drafting import ChapterDraftingOptions, load_drafting_provider, write_chapter_draft
from novel.core.exporting import MarkdownExportOptions, export_markdown
from novel.core.inspiration import InspirationOptions, load_inspiration_provider, run_inspiration_agent
from novel.core.io import atomic_write_model_json
from novel.core.memory_repair import suggest_memory_repair
from novel.core.planning import ChapterPlanningOptions, load_planning_provider, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, load_polishing_provider, polish_chapter
from novel.core.prompts import load_prompt_template
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.revision import ChapterRevisionOptions, load_revision_provider, revise_chapter
from novel.core.schemas import AgentRunLog, AgentRunStep, RevisionRouteDecision
from novel.core.state_update import (
    StateUpdateProposeOptions,
    load_state_update_provider,
    propose_state_update,
)


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
    use_vector_context: bool = False


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
        run_log.ended_at = _utc_now()
        run_log.errors.append(str(exc))
        run_log.output_files = _unique_outputs(run_log.steps)
        _write_run_log(run_log_path, run_log, plan)
        raise OrchestratorError(str(exc)) from exc

    run_log.status = "completed"
    run_log.ended_at = _utc_now()
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


def load_orchestrator_provider(
    root: Path,
    provider_name: str,
    *,
    agent_config_path: Path | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    return create_agent_provider(
        agent_config_path or default_agent_config_path(root),
        "orchestrator",
        overrides=ProviderOverrides(provider_name=provider_name, model_name=model_name),
        mock_response=default_mock_revision_route_decision_json(),
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
    route_provider = provider or load_orchestrator_provider(root, provider_name)
    user_prompt = build_revision_route_user_prompt(
        instruction,
        chapter_numbers=chapters,
        session_summary=session_summary,
    )
    try:
        content = generate_with_output_guard(
            route_provider,
            ModelRequest(
                system_prompt=load_prompt_template("orchestrator_revision_route_system"),
                user_prompt=user_prompt,
                context=session_summary,
                json_schema_name="RevisionRouteDecision",
            ),
            root=root,
            invocation=AgentInvocationContext(
                agent_name="orchestrator",
                caller="orchestrator",
                interaction_mode="internal_task",
                task="revision_route",
            ),
            contract=AgentOutputContract(
                output_kind="json",
                target_name="RevisionRouteDecision",
                json_schema_name="RevisionRouteDecision",
                allow_user_questions=False,
            ),
        )
    except AgentOutputContractError:
        fallback = _fallback_revision_route_decision(instruction, chapter_numbers=chapters)
        return fallback.model_copy(update={"reason": f"provider route output contract failed; fallback used. {fallback.reason}"})
    try:
        return parse_revision_route_decision(content, fallback_instruction=instruction, chapter_numbers=chapters)
    except OrchestratorError as first_error:
        try:
            repair_content = generate_with_output_guard(
                route_provider,
                ModelRequest(
                    system_prompt=load_prompt_template("orchestrator_revision_route_system"),
                    user_prompt=_revision_route_repair_prompt(
                        original_prompt=user_prompt,
                        invalid_output=content,
                        error=str(first_error),
                    ),
                    context=session_summary,
                    json_schema_name="RevisionRouteDecision",
                ),
                root=root,
                invocation=AgentInvocationContext(
                    agent_name="orchestrator",
                    caller="orchestrator",
                    interaction_mode="internal_task",
                    task="revision_route_repair",
                ),
                contract=AgentOutputContract(
                    output_kind="json",
                    target_name="RevisionRouteDecision",
                    json_schema_name="RevisionRouteDecision",
                    allow_user_questions=False,
                ),
            )
        except AgentOutputContractError:
            fallback = _fallback_revision_route_decision(instruction, chapter_numbers=chapters)
            return fallback.model_copy(update={"reason": f"provider route repair contract failed; fallback used. {fallback.reason}"})
        try:
            return parse_revision_route_decision(
                repair_content,
                fallback_instruction=instruction,
                chapter_numbers=chapters,
            )
        except OrchestratorError:
            pass
        fallback = _fallback_revision_route_decision(instruction, chapter_numbers=chapters)
        return fallback.model_copy(update={"reason": f"provider route parse failed; fallback used. {fallback.reason}"})


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
    raw = _extract_json_object(content)
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


def _revision_route_repair_prompt(*, original_prompt: str, invalid_output: str, error: str) -> str:
    return (
        f"{original_prompt}\n\n"
        "上一次输出不能被解析为 RevisionRouteDecision。\n"
        f"错误：{error}\n\n"
        "请重新只输出一个 JSON object，不要 Markdown 或解释。"
        "route 只能是 plot_replan / writer_rewrite / revision_patch；"
        "必须填写被选 route 对应的 instruction 字段。\n"
        f"上一次输出：\n{invalid_output[:3000]}\n"
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
        result = run_inspiration_agent(
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
        outputs = [_rel(root, result.markdown_path)]
        if result.json_path:
            outputs.append(_rel(root, result.json_path))
        return outputs
    if plan.task == "canon":
        provider = load_canon_provider(root, provider_name)
        output_path = root / "runs" / f"canon_proposal_{_timestamp()}.json"
        result = suggest_canon(
            CanonSuggestOptions(
                root=root,
                output_path=output_path,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            provider,
        )
        return [_rel(root, result.output_path)] if result.output_path else []
    if plan.task == "plan":
        assert chapter is not None
        provider = load_planning_provider(root, provider_name, chapter_number=chapter)
        result = plan_chapter(
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
        return [_rel(root, result.plan_json_path), _rel(root, result.plan_markdown_path)]
    if plan.task == "write":
        assert chapter is not None
        provider = load_drafting_provider(root, provider_name)
        result = write_chapter_draft(
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
        return [_rel(root, result.draft_path)]
    if plan.task == "polish":
        assert chapter is not None
        provider = load_polishing_provider(root, provider_name)
        result = polish_chapter(
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
        return [_rel(root, result.polished_path)]
    if plan.task == "audit":
        assert chapter is not None
        provider = load_audit_provider(root, provider_name, chapter_number=chapter)
        result = audit_chapter(
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
        return [_rel(root, result.audit_path)]
    if plan.task == "revision":
        assert chapter is not None
        provider = load_revision_provider(root, provider_name, target="polished")
        result = revise_chapter(
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
        return [_rel(root, result.output_path), _rel(root, result.revision_log_path)]
    if plan.task == "plot_replan":
        assert chapter is not None
        provider = load_planning_provider(root, provider_name, chapter_number=chapter)
        result = plan_chapter(
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
        return [_rel(root, result.plan_json_path), _rel(root, result.plan_markdown_path)]
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
        result = revise_chapter(
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
        return [_rel(root, result.output_path), _rel(root, result.revision_log_path)]
    if plan.task == "state_update":
        assert chapter is not None
        provider = load_state_update_provider(root, provider_name, chapter_number=chapter)
        result = propose_state_update(
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
        return [_rel(root, result.proposal_path)]
    if plan.task == "export_markdown":
        result = export_markdown(
            MarkdownExportOptions(root=root, include_unaccepted=True, force=options.force)
        )
        return [_rel(root, result.output_path), _rel(root, result.manifest_path)]
    if plan.task == "memory_repair":
        result = suggest_memory_repair(root, plan.instruction)
        return [_rel(root, result.proposal_path), _rel(root, result.markdown_path)]
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
    now = _utc_now()
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
        handoff_trace=[entry.as_dict() for entry in plan.handoff_trace],
        orchestrator_task=plan.task,
        max_loop_policy="single-pass handoff trace; repeated handoffs rejected",
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


def _extract_json_object(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise OrchestratorError("provider response did not contain a JSON object")
    return stripped[start : end + 1]


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


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
