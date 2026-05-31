from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Literal

from novel.core.auditing import ChapterAuditOptions, audit_chapter, load_audit_provider
from novel.core.canon import CanonSuggestOptions, load_canon_provider, suggest_canon
from novel.core.drafting import ChapterDraftingOptions, load_drafting_provider, write_chapter_draft
from novel.core.exporting import MarkdownExportOptions, export_markdown
from novel.core.inspiration import InspirationOptions, load_inspiration_provider, run_inspiration_agent
from novel.core.io import atomic_write_model_json
from novel.core.memory_repair import suggest_memory_repair
from novel.core.planning import ChapterPlanningOptions, load_planning_provider, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, load_polishing_provider, polish_chapter
from novel.core.revision import ChapterRevisionOptions, load_revision_provider, revise_chapter
from novel.core.schemas import AgentRunLog, AgentRunStep
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

    plan = plan_orchestration(request)
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


def plan_orchestration(request: str) -> OrchestratorPlan:
    task = classify_request(request)
    chapter_number = _extract_chapter_number(request)
    if task in {"plan", "write", "polish", "audit", "revision", "state_update"}:
        chapter_number = chapter_number or 1
    target = TASK_TO_AGENT[task]
    trace = (
        HandoffTraceEntry(
            step=1,
            source="orchestrator",
            target=target,
            reason=f"request classified as {task}",
        ),
    )
    _validate_handoff_trace(trace)
    return OrchestratorPlan(
        task=task,
        chapter_number=chapter_number,
        instruction=request,
        handoff_trace=trace,
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
            "execution_plan": format_orchestrator_plan(plan),
        }
    )
    atomic_write_model_json(path, enriched)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


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
