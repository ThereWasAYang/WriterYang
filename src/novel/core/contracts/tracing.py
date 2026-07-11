from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from novel.core.contracts.artifacts import ArtifactRef
from novel.core.contracts.common import ProfileId, SchemaV3Model, Surface, TaskId


class WorkflowBudget(SchemaV3Model):
    max_chapters: int = Field(ge=1)
    max_model_calls: int = Field(ge=0)
    max_provider_attempts: int = Field(ge=0)
    max_auto_revision_rounds: int = Field(ge=0)
    max_input_tokens: int | None = Field(default=None, ge=0)
    max_output_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def provider_attempts_cover_model_calls(self) -> WorkflowBudget:
        if self.max_provider_attempts < self.max_model_calls:
            raise ValueError("max_provider_attempts cannot be smaller than max_model_calls")
        return self


class BudgetUsage(SchemaV3Model):
    chapters: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    provider_attempts: int = Field(default=0, ge=0)
    auto_revision_rounds: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


def default_workflow_budget() -> WorkflowBudget:
    return WorkflowBudget(
        max_chapters=20,
        max_model_calls=100,
        max_provider_attempts=300,
        max_auto_revision_rounds=10,
    )


class WorkflowNodeRun(SchemaV3Model):
    node_id: str = Field(pattern=r"^node_[0-9a-f]{32}$")
    workflow_run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    node_type: Literal["command", "model", "deterministic"]
    name: str = Field(min_length=1)
    parent_node_id: str | None = Field(default=None, pattern=r"^node_[0-9a-f]{32}$")
    request_id: str | None = Field(default=None, min_length=1)
    parent_request_id: str | None = Field(default=None, min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    command_id: str | None = Field(default=None, pattern=r"^cmd_[0-9a-f]{32}$")
    surface: Surface
    task_id: TaskId | None = None
    profile_id: ProfileId | None = None
    provider: str | None = None
    model: str | None = None
    input_artifacts: list[ArtifactRef] = Field(default_factory=list)
    output_artifacts: list[ArtifactRef] = Field(default_factory=list)
    input_paths: list[str] = Field(default_factory=list)
    output_paths: list[str] = Field(default_factory=list)
    prompt_template_hash: str | None = None
    prompt_policy_hash: str | None = None
    rendered_prompt_hash: str | None = None
    retry_count: int = Field(default=0, ge=0)
    repair_count: int = Field(default=0, ge=0)
    budget_before: BudgetUsage = Field(default_factory=BudgetUsage)
    budget_after: BudgetUsage = Field(default_factory=BudgetUsage)
    status: Literal["running", "completed", "failed", "cancelled"]
    started_at: datetime
    ended_at: datetime | None = None
    error: str | None = None
    recovery_command: str | None = None


class WorkflowDecision(SchemaV3Model):
    decision_id: str = Field(pattern=r"^decision_[0-9a-f]{32}$")
    workflow_run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    name: str = Field(min_length=1)
    task_id: TaskId | None = None
    surface: Surface
    request_id: str = Field(min_length=1)
    parent_request_id: str | None = Field(default=None, min_length=1)
    parent_node_id: str | None = Field(default=None, pattern=r"^node_[0-9a-f]{32}$")
    session_id: str | None = Field(default=None, min_length=1)
    payload: dict[str, object]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class WorkflowRun(SchemaV3Model):
    workflow_run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    root_command_id: str = Field(pattern=r"^cmd_[0-9a-f]{32}$")
    root_request_id: str = Field(min_length=1)
    surface: Surface
    budget: WorkflowBudget
    budget_usage: BudgetUsage = Field(default_factory=BudgetUsage)
    node_ids: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    request_ids: list[str] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)
    status: Literal["running", "completed", "failed", "cancelled", "awaiting_user"]
    started_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None
