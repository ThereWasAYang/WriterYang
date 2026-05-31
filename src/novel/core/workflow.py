from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from novel.core.auditing import (
    ChapterAuditOptions,
    audit_chapter,
    load_audit_provider,
)
from novel.core.drafting import (
    ChapterDraftingOptions,
    write_chapter_draft,
)
from novel.core.planning import (
    ChapterPlanningOptions,
    load_planning_provider,
    plan_chapter,
)
from novel.core.polishing import (
    ChapterPolishingOptions,
    polish_chapter,
)
from novel.core.io import atomic_write_model_json
from novel.core.providers import ModelProvider
from novel.core.schemas import AgentRunLog, AgentRunStep


StopAfter = Literal["plan", "write", "polish", "audit"]
ProviderName = Literal["config", "mock", "openai", "openai_compatible", "deepseek", "zai"]


class WorkflowError(RuntimeError):
    """Raised when an end-to-end workflow fails."""


@dataclass(frozen=True)
class GenerateChapterOptions:
    root: Path
    chapter_number: int
    instruction: str | None = None
    force: bool = False
    resume: bool = False
    provider_name: ProviderName = "config"
    agent_config_path: Path | None = None
    model_name: str | None = None
    target_words: int | None = None
    style_note: str | None = None
    skip_polish: bool = False
    skip_audit: bool = False
    stop_after: StopAfter | None = None
    use_search_context: bool = True
    use_vector_context: bool = False


@dataclass(frozen=True)
class GenerateChapterResult:
    run_log: AgentRunLog
    run_log_path: Path
    message: str


def generate_chapter(
    options: GenerateChapterOptions,
    provider_loader: Callable[[Path, ProviderName, str, int], ModelProvider] | None = None,
) -> GenerateChapterResult:
    root = options.root.resolve()
    if options.chapter_number < 1:
        raise WorkflowError("chapter_number must be a positive integer")
    if options.skip_polish and options.stop_after == "polish":
        raise WorkflowError("--stop-after polish cannot be used with --skip-polish")
    if options.skip_audit and options.stop_after == "audit":
        raise WorkflowError("--stop-after audit cannot be used with --skip-audit")

    loader = provider_loader or (
        lambda root, provider_name, step, chapter_number: _load_provider_for_step(
            root,
            provider_name,
            step,
            chapter_number,
            agent_config_path=options.agent_config_path,
            model_name=options.model_name,
        )
    )
    run_log = _new_run_log(options)
    run_log_path = _run_log_path(root, run_log.run_id)
    root.joinpath("runs").mkdir(parents=True, exist_ok=True)

    try:
        _run_plan_step(root, options, run_log, loader)
        if _should_stop(options, "plan"):
            return _complete(root, run_log, run_log_path, "Stopped after plan.")

        _run_write_step(root, options, run_log, loader)
        if _should_stop(options, "write"):
            return _complete(root, run_log, run_log_path, "Stopped after write.")

        if options.skip_polish:
            if options.skip_audit:
                return _complete(root, run_log, run_log_path, "Generated draft; polish and audit were skipped.")
            return _complete(root, run_log, run_log_path, "Generated draft; polish was skipped so audit was not run.")

        _run_polish_step(root, options, run_log, loader)
        if _should_stop(options, "polish"):
            return _complete(root, run_log, run_log_path, "Stopped after polish.")

        if options.skip_audit:
            return _complete(root, run_log, run_log_path, "Generated polished chapter; audit was skipped.")

        audit_report = _run_audit_step(root, options, run_log, loader)
        if audit_report.overall_status == "passed":
            return _complete(
                root,
                run_log,
                run_log_path,
                "Audit passed. You can run novel propose-state-update next.",
            )
        return _complete(
            root,
            run_log,
            run_log_path,
            f"Audit status: {audit_report.overall_status}. {audit_report.summary}",
        )
    except Exception as exc:
        _fail_run(run_log, str(exc))
        _write_run_log(root, run_log_path, run_log)
        raise WorkflowError(str(exc)) from exc


def read_workflow_instruction(instruction: str | None, input_path: Path | None) -> str | None:
    if instruction and input_path:
        raise WorkflowError("provide either --instruction or --input, not both")
    if input_path:
        if not input_path.exists():
            raise WorkflowError(f"workflow instruction input file is missing: {input_path}")
        return input_path.read_text(encoding="utf-8").strip() or None
    return instruction.strip() if instruction and instruction.strip() else None


def _run_plan_step(
    root: Path,
    options: GenerateChapterOptions,
    run_log: AgentRunLog,
    loader: Callable[[Path, ProviderName, str, int], ModelProvider],
) -> None:
    step = _start_step(
        run_log,
        step_id="step_001",
        agent="plot_agent",
        input_files=[
            "project.yaml",
            "memory/inspiration.md",
            "memory/canon/characters.json",
            "memory/canon/locations.json",
            "memory/canon/items.json",
            "memory/canon/world.json",
            "memory/canon/hidden_truths.json",
            "memory/canon/foreshadowing.json",
            "memory/state/current_state.json",
            "memory/state/timeline.json",
        ],
    )
    if _resume_existing_step(
        root,
        options,
        step,
        "plan",
        [
            root / "memory" / "chapters" / f"{options.chapter_number:03d}" / "plan.json",
            root / "memory" / "chapters" / f"{options.chapter_number:03d}" / "plan.md",
        ],
    ):
        return
    try:
        result = plan_chapter(
            ChapterPlanningOptions(
                root=root,
                chapter_number=options.chapter_number,
                instruction=options.instruction,
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            loader(root, options.provider_name, "plan", options.chapter_number),
        )
    except Exception as exc:
        _fail_step(step, exc)
        raise
    step.output_files = [
        _rel(root, result.plan_json_path),
        _rel(root, result.plan_markdown_path),
    ]
    step.status = "completed"


def _run_write_step(
    root: Path,
    options: GenerateChapterOptions,
    run_log: AgentRunLog,
    loader: Callable[[Path, ProviderName, str, int], ModelProvider],
) -> None:
    step = _start_step(
        run_log,
        step_id="step_002",
        agent="writer_agent",
        input_files=[
            f"memory/chapters/{options.chapter_number:03d}/plan.json",
            "memory/style_guide.md",
            "memory/state/current_state.json",
            "memory/state/timeline.json",
        ],
    )
    if _resume_existing_step(
        root,
        options,
        step,
        "write",
        [root / "memory" / "chapters" / f"{options.chapter_number:03d}" / "draft.md"],
    ):
        return
    try:
        result = write_chapter_draft(
            ChapterDraftingOptions(
                root=root,
                chapter_number=options.chapter_number,
                instruction=options.instruction,
                force=options.force,
                target_words=options.target_words,
                style_note=options.style_note,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            loader(root, options.provider_name, "write", options.chapter_number),
        )
    except Exception as exc:
        _fail_step(step, exc)
        raise
    step.output_files = [_rel(root, result.draft_path)]
    step.status = "completed"


def _run_polish_step(
    root: Path,
    options: GenerateChapterOptions,
    run_log: AgentRunLog,
    loader: Callable[[Path, ProviderName, str, int], ModelProvider],
) -> None:
    step = _start_step(
        run_log,
        step_id="step_003",
        agent="polish_agent",
        input_files=[
            f"memory/chapters/{options.chapter_number:03d}/plan.json",
            f"memory/chapters/{options.chapter_number:03d}/draft.md",
            "memory/style_guide.md",
        ],
    )
    if _resume_existing_step(
        root,
        options,
        step,
        "polish",
        [root / "memory" / "chapters" / f"{options.chapter_number:03d}" / "polished.md"],
    ):
        return
    try:
        result = polish_chapter(
            ChapterPolishingOptions(
                root=root,
                chapter_number=options.chapter_number,
                instruction=options.instruction,
                force=options.force,
                style_note=options.style_note,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            loader(root, options.provider_name, "polish", options.chapter_number),
        )
    except Exception as exc:
        _fail_step(step, exc)
        raise
    step.output_files = [_rel(root, result.polished_path)]
    step.status = "completed"


def _run_audit_step(
    root: Path,
    options: GenerateChapterOptions,
    run_log: AgentRunLog,
    loader: Callable[[Path, ProviderName, str, int], ModelProvider],
):
    step = _start_step(
        run_log,
        step_id="step_004",
        agent="audit_agent",
        input_files=[
            f"memory/chapters/{options.chapter_number:03d}/plan.json",
            f"memory/chapters/{options.chapter_number:03d}/draft.md",
            f"memory/chapters/{options.chapter_number:03d}/polished.md",
            "memory/state/current_state.json",
            "memory/state/timeline.json",
        ],
    )
    audit_path = root / "memory" / "chapters" / f"{options.chapter_number:03d}" / "audit.json"
    if _resume_existing_step(root, options, step, "audit", [audit_path]):
        from novel.core.io import load_json_model
        from novel.core.schemas import AuditReport

        return load_json_model(audit_path, AuditReport)
    try:
        result = audit_chapter(
            ChapterAuditOptions(
                root=root,
                chapter_number=options.chapter_number,
                instruction=options.instruction,
                force=options.force,
                use_search_context=options.use_search_context,
                use_vector_context=options.use_vector_context,
            ),
            loader(root, options.provider_name, "audit", options.chapter_number),
        )
    except Exception as exc:
        _fail_step(step, exc)
        raise
    step.output_files = [_rel(root, result.audit_path)]
    step.status = "completed"
    return result.report


def _load_provider_for_step(
    root: Path,
    provider_name: ProviderName,
    step: str,
    chapter_number: int,
    *,
    agent_config_path: Path | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    if step == "plan":
        return load_planning_provider(
            root,
            provider_name,
            chapter_number=chapter_number,
            agent_config_path=agent_config_path,
            model_name=model_name,
        )
    if step == "write":
        from novel.core.drafting import load_drafting_provider

        return load_drafting_provider(
            root,
            provider_name,
            agent_config_path=agent_config_path,
            model_name=model_name,
        )
    if step == "polish":
        from novel.core.polishing import load_polishing_provider

        return load_polishing_provider(
            root,
            provider_name,
            agent_config_path=agent_config_path,
            model_name=model_name,
        )
    if step == "audit":
        return load_audit_provider(
            root,
            provider_name,
            chapter_number=chapter_number,
            agent_config_path=agent_config_path,
            model_name=model_name,
        )
    raise WorkflowError(f"unknown workflow step: {step}")


def _new_run_log(options: GenerateChapterOptions) -> AgentRunLog:
    now = _utc_now()
    return AgentRunLog(
        run_id=_run_id(now),
        task="generate_chapter",
        chapter_number=options.chapter_number,
        started_at=now,
        status="running",
        input_files=[
            "project.yaml",
            "memory/inspiration.md",
            "memory/canon/characters.json",
            "memory/state/current_state.json",
            "memory/state/timeline.json",
        ],
        steps=[],
        output_files=[],
        errors=[],
    )


def _start_step(
    run_log: AgentRunLog,
    *,
    step_id: str,
    agent: str,
    input_files: list[str],
) -> AgentRunStep:
    step = AgentRunStep(
        step_id=step_id,
        agent=agent,
        input_files=input_files,
        output_files=[],
        status="running",
    )
    run_log.steps.append(step)
    return step


def _fail_step(step: AgentRunStep, exc: Exception) -> None:
    step.status = "failed"
    step.error = str(exc)


def _resume_existing_step(
    root: Path,
    options: GenerateChapterOptions,
    step: AgentRunStep,
    step_name: str,
    output_paths: list[Path],
) -> bool:
    if not options.resume or options.force:
        return False
    existing = [path for path in output_paths if path.exists()]
    if not existing:
        return False
    missing = [path for path in output_paths if not path.exists()]
    if missing:
        names = ", ".join(_rel(root, path) for path in missing)
        raise WorkflowError(f"cannot resume {step_name}; missing expected output files: {names}")
    step.output_files = [_rel(root, path) for path in output_paths]
    step.status = "completed"
    return True


def _fail_run(run_log: AgentRunLog, error: str) -> None:
    run_log.status = "failed"
    run_log.ended_at = _utc_now()
    run_log.errors.append(error)
    run_log.output_files = _unique_outputs(run_log.steps)


def _complete(
    root: Path,
    run_log: AgentRunLog,
    run_log_path: Path,
    message: str,
) -> GenerateChapterResult:
    run_log.status = "completed"
    run_log.ended_at = _utc_now()
    run_log.output_files = _unique_outputs(run_log.steps)
    _write_run_log(root, run_log_path, run_log)
    return GenerateChapterResult(run_log=run_log, run_log_path=run_log_path, message=message)


def _write_run_log(root: Path, run_log_path: Path, run_log: AgentRunLog) -> None:
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_model_json(run_log_path, run_log)


def _should_stop(options: GenerateChapterOptions, step: StopAfter) -> bool:
    return options.stop_after == step


def _unique_outputs(steps: list[AgentRunStep]) -> list[str]:
    seen: set[str] = set()
    outputs: list[str] = []
    for step in steps:
        for path in step.output_files:
            if path not in seen:
                outputs.append(path)
                seen.add(path)
    return outputs


def _run_log_path(root: Path, run_id: str) -> Path:
    return root / "runs" / f"{run_id}.json"


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_id(timestamp: datetime) -> str:
    return "run_" + timestamp.strftime("%Y%m%d_%H%M%S_%f")
