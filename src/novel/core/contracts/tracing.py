from __future__ import annotations

from pydantic import Field, model_validator

from novel.core.contracts.common import SchemaV3Model


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
