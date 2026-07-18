from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

from novel.core.contracts import BudgetUsage, WorkflowBudget


class WorkflowBudgetExceeded(RuntimeError):
    def __init__(self, dimension: str, used: int, limit: int) -> None:
        super().__init__(f"workflow budget exceeded: {dimension} used={used} limit={limit}")
        self.dimension = dimension
        self.used = used
        self.limit = limit


@dataclass
class WorkflowBudgetTracker:
    budget: WorkflowBudget
    usage: BudgetUsage

    def consume_chapters(self, count: int) -> None:
        self._consume("chapters", count, self.budget.max_chapters)

    def consume_model_call(self) -> None:
        self._consume("model_calls", 1, self.budget.max_model_calls)

    def consume_provider_attempt(self) -> None:
        self._consume("provider_attempts", 1, self.budget.max_provider_attempts)

    def consume_auto_revision_rounds(self, count: int) -> None:
        self._consume("auto_revision_rounds", count, self.budget.max_auto_revision_rounds)

    def consume_tokens(self, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self._consume("input_tokens", input_tokens, self.budget.max_input_tokens)
        self._consume("output_tokens", output_tokens, self.budget.max_output_tokens)

    def snapshot(self) -> BudgetUsage:
        return self.usage.model_copy(deep=True)

    def _consume(self, field: str, count: int, limit: int | None) -> None:
        if count <= 0:
            return
        used = int(getattr(self.usage, field)) + count
        if limit is not None and used > limit:
            raise WorkflowBudgetExceeded(field, used, limit)
        self.usage = self.usage.model_copy(update={field: used})


_ACTIVE_BUDGET: ContextVar[WorkflowBudgetTracker | None] = ContextVar(
    "writeryang_workflow_budget",
    default=None,
)


@contextmanager
def workflow_budget_scope(
    budget: WorkflowBudget,
    *,
    initial_usage: BudgetUsage | None = None,
) -> Iterator[WorkflowBudgetTracker]:
    tracker = WorkflowBudgetTracker(budget=budget, usage=initial_usage or BudgetUsage())
    token: Token[WorkflowBudgetTracker | None] = _ACTIVE_BUDGET.set(tracker)
    try:
        yield tracker
    finally:
        _ACTIVE_BUDGET.reset(token)


def active_budget_tracker() -> WorkflowBudgetTracker | None:
    return _ACTIVE_BUDGET.get()


def consume_model_call() -> None:
    tracker = active_budget_tracker()
    if tracker:
        tracker.consume_model_call()


def consume_provider_attempt() -> None:
    tracker = active_budget_tracker()
    if tracker:
        tracker.consume_provider_attempt()


def consume_auto_revision_round() -> None:
    tracker = active_budget_tracker()
    if tracker:
        tracker.consume_auto_revision_rounds(1)


def consume_response_tokens(input_tokens: int | None, output_tokens: int | None) -> None:
    tracker = active_budget_tracker()
    if tracker:
        tracker.consume_tokens(
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
        )
