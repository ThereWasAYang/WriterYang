from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from novel.core.contracts.common import SchemaV3Model
from novel.core.contracts.commands import PublicCommand
from novel.core.contracts.tracing import WorkflowBudget


class DecisionRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CommandProposal(SchemaV3Model):
    command: PublicCommand | None = None
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    risk: DecisionRisk
    estimated_model_calls: int = Field(ge=0)
    requires_confirmation: bool
    clarification_question: str | None = None
    budget: WorkflowBudget

    @model_validator(mode="after")
    def command_or_clarification(self) -> CommandProposal:
        if self.command is None and not self.clarification_question:
            raise ValueError("proposal without command requires clarification_question")
        if self.command is not None and self.clarification_question:
            raise ValueError("executable proposal cannot also request clarification")
        return self
