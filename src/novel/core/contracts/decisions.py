from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from novel.core.contracts.common import SchemaV3Model


class DecisionRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CommandProposal(SchemaV3Model):
    command_type: str = Field(min_length=1)
    arguments: dict[str, object] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    risk: DecisionRisk
    estimated_model_calls: int = Field(ge=0)
    requires_confirmation: bool
    clarification_question: str | None = None
