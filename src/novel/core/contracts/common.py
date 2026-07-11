from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


CURRENT_SCHEMA_VERSION = 3

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ArtifactId = Annotated[str, Field(pattern=r"^art_[0-9a-f]{32}$")]


class StrictModel(BaseModel):
    """Base class for control-plane and inter-agent contracts."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SchemaV3Model(StrictModel):
    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION, frozen=True)

    @field_validator("schema_version")
    @classmethod
    def require_current_schema(cls, value: int) -> int:
        return ensure_schema_version(value)


class Surface(StrEnum):
    CLI = "cli"
    WEB = "web"
    ASK = "ask"
    INTERNAL = "internal"


class TaskId(StrEnum):
    INSPIRATION = "inspiration"
    STYLE_GUIDE = "style_guide"
    CANON = "canon"
    PLAN = "plan"
    WRITE = "write"
    POLISH = "polish"
    REVISION = "revision"
    AUDIT = "audit"
    STATE_UPDATE = "state_update"
    CHAPTER_MEMORY = "chapter_memory"
    INTENT_ROUTER = "intent_router"
    MEMORY_REPAIR = "memory_repair"
    SETUP = "setup"


class ProfileId(StrEnum):
    SCRIBE = "scribe"
    ARCHITECT = "architect"
    LOREMASTER = "loremaster"
    CLERK = "clerk"


class ArtifactKind(StrEnum):
    PLAN = "plan"
    CANDIDATE = "candidate"
    AUDIT = "audit"
    STATE_PROPOSAL = "state_proposal"
    CHAPTER_MEMORY = "chapter_memory"
    ACCEPTANCE = "acceptance"
    SEGMENT_PATCH = "segment_patch"
    STATE = "state"
    TIMELINE = "timeline"


def ensure_schema_version(value: object) -> int:
    if value != CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported_project_schema: expected schema_version "
            f"{CURRENT_SCHEMA_VERSION}, got {value!r}"
        )
    return CURRENT_SCHEMA_VERSION


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value
