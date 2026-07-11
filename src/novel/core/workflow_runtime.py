from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import TypeVar
import uuid

from novel.core.budget import active_budget_tracker
from novel.core.contracts import (
    ArtifactRef,
    BudgetUsage,
    ProfileId,
    Surface,
    TaskId,
    WorkflowBudget,
    WorkflowNodeRun,
    WorkflowRun,
)
from novel.core.io import atomic_write_model_json, load_json_model
from novel.core.timeutil import utc_now


T = TypeVar("T")


@dataclass(frozen=True)
class WorkflowNodeDefinition:
    name: str
    node_type: str
    task_id: TaskId | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_type: str
    nodes: tuple[WorkflowNodeDefinition, ...]


WORKFLOW_DEFINITIONS: dict[str, WorkflowDefinition] = {
    "creation_session": WorkflowDefinition(
        workflow_type="creation_session",
        nodes=tuple(
            WorkflowNodeDefinition(name, "model" if task else "deterministic", task)
            for name, task in (
                ("outline", TaskId.PLAN),
                ("writer", TaskId.WRITE),
                ("polish", TaskId.POLISH),
                ("audit", TaskId.AUDIT),
                ("state_update", TaskId.STATE_UPDATE),
                ("chapter_memory", TaskId.CHAPTER_MEMORY),
                ("acceptance", None),
            )
        ),
    ),
    "revision_session": WorkflowDefinition(
        workflow_type="revision_session",
        nodes=(
            WorkflowNodeDefinition("select", "deterministic"),
            WorkflowNodeDefinition("revision", "model", TaskId.REVISION),
            WorkflowNodeDefinition("audit", "model", TaskId.AUDIT),
            WorkflowNodeDefinition("state_update", "model", TaskId.STATE_UPDATE),
            WorkflowNodeDefinition("acceptance", "deterministic"),
        ),
    ),
}


@dataclass
class WorkflowRuntime:
    root: Path
    workflow_run_id: str
    command_id: str
    surface: Surface
    budget: WorkflowBudget
    run: WorkflowRun
    parent_stack: list[str] = field(default_factory=list)

    @property
    def run_dir(self) -> Path:
        return self.root / "runs" / self.workflow_run_id

    def execute_node(
        self,
        *,
        name: str,
        node_type: str,
        function: Callable[[], T],
        task_id: TaskId | None = None,
        profile_id: ProfileId | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_template: str | None = None,
        rendered_prompt: str | None = None,
        repair_count: int = 0,
        input_artifacts: list[ArtifactRef] | None = None,
        input_paths: list[str] | None = None,
        output_details: Callable[[T], tuple[list[ArtifactRef], list[str]]] | None = None,
    ) -> T:
        before = _budget_usage()
        node = WorkflowNodeRun(
            node_id=f"node_{uuid.uuid4().hex}",
            workflow_run_id=self.workflow_run_id,
            node_type=node_type,  # type: ignore[arg-type]
            name=name,
            parent_node_id=self.parent_stack[-1] if self.parent_stack else None,
            command_id=self.command_id,
            surface=self.surface,
            task_id=task_id,
            profile_id=profile_id,
            provider=provider,
            model=model,
            input_artifacts=input_artifacts or [],
            input_paths=input_paths or [],
            prompt_template_hash=_hash_text(prompt_template),
            rendered_prompt_hash=_hash_text(rendered_prompt),
            repair_count=repair_count,
            budget_before=before,
            status="running",
            started_at=utc_now(),
        )
        self.parent_stack.append(node.node_id)
        self._write_node(node)
        try:
            value = function()
            artifacts, paths = output_details(value) if output_details else ([], [])
            after = _budget_usage()
            completed = node.model_copy(
                update={
                    "output_artifacts": artifacts,
                    "output_paths": paths,
                    "retry_count": _provider_retry_count(node_type, before, after),
                    "budget_after": after,
                    "status": "completed",
                    "ended_at": utc_now(),
                }
            )
            self._write_node(completed)
            return value
        except Exception as exc:
            after = _budget_usage()
            failed = node.model_copy(
                update={
                    "retry_count": _provider_retry_count(node_type, before, after),
                    "budget_after": after,
                    "status": "failed",
                    "ended_at": utc_now(),
                    "error": str(exc),
                }
            )
            self._write_node(failed)
            raise
        finally:
            self.parent_stack.pop()

    def _write_node(self, node: WorkflowNodeRun) -> None:
        self.run_dir.joinpath("nodes").mkdir(parents=True, exist_ok=True)
        atomic_write_model_json(self.run_dir / "nodes" / f"{node.node_id}.json", node)
        if node.node_id not in self.run.node_ids:
            self.run = self.run.model_copy(update={"node_ids": [*self.run.node_ids, node.node_id]})
        self._write_run("running")

    def _write_run(self, status: str) -> None:
        now = utc_now()
        ended_at = now if status in {"completed", "failed", "cancelled"} else None
        self.run = self.run.model_copy(
            update={
                "status": status,
                "budget_usage": _budget_usage(),
                "updated_at": now,
                "ended_at": ended_at,
            }
        )
        atomic_write_model_json(self.run_dir / "run.json", self.run)


_ACTIVE_RUNTIME: ContextVar[WorkflowRuntime | None] = ContextVar(
    "writeryang_workflow_runtime",
    default=None,
)


@contextmanager
def workflow_runtime_scope(
    *,
    root: Path,
    workflow_run_id: str,
    command_id: str,
    surface: Surface,
    budget: WorkflowBudget,
) -> Iterator[WorkflowRuntime]:
    run_path = root / "runs" / workflow_run_id / "run.json"
    now = utc_now()
    if run_path.exists():
        run = load_json_model(run_path, WorkflowRun).model_copy(
            update={"status": "running", "updated_at": now, "ended_at": None}
        )
    else:
        run = WorkflowRun(
            workflow_run_id=workflow_run_id,
            root_command_id=command_id,
            surface=surface,
            budget=budget,
            status="running",
            started_at=now,
            updated_at=now,
        )
    runtime = WorkflowRuntime(root, workflow_run_id, command_id, surface, budget, run)
    token: Token[WorkflowRuntime | None] = _ACTIVE_RUNTIME.set(runtime)
    try:
        yield runtime
    except Exception:
        runtime._write_run("failed")
        raise
    else:
        runtime._write_run("completed")
    finally:
        _ACTIVE_RUNTIME.reset(token)


def active_workflow_runtime() -> WorkflowRuntime | None:
    return _ACTIVE_RUNTIME.get()


def execute_runtime_node(**kwargs: object) -> object:
    runtime = active_workflow_runtime()
    function = kwargs.pop("function")
    if not callable(function):
        raise TypeError("function must be callable")
    if runtime is None:
        return function()
    return runtime.execute_node(function=function, **kwargs)  # type: ignore[arg-type]


def _budget_usage() -> BudgetUsage:
    tracker = active_budget_tracker()
    return tracker.snapshot() if tracker else BudgetUsage()


def _hash_text(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def _provider_retry_count(node_type: str, before: BudgetUsage, after: BudgetUsage) -> int:
    if node_type != "model":
        return 0
    return max(0, after.provider_attempts - before.provider_attempts - 1)
