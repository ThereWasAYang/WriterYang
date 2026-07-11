from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import Field

from novel.core.contracts.artifacts import ArtifactRef
from novel.core.contracts.common import SchemaV3Model, Surface


VectorContextMode = Literal["auto", "on", "off"]
PolishMode = Literal["single_pass", "auto", "review_gate"]
SessionCommandType = Literal[
    "session.show",
    "session.revise_outline",
    "session.approve_outline",
    "session.run",
    "session.revise_content",
    "session.revise_audit",
    "session.retry_rewrite",
    "session.undo_rewrite",
    "session.accept",
    "session.archive",
    "session.cancel",
]


class SessionStartCommand(SchemaV3Model):
    type: Literal["session.start"] = "session.start"
    user_intent: str = Field(min_length=1)
    chapter_range: list[int] = Field(min_length=1)
    provider_name: str = "config"
    force: bool = False
    use_search_context: bool = True
    use_vector_context: VectorContextMode = "auto"
    polish_mode: PolishMode | None = None


class SessionCommand(SchemaV3Model):
    type: SessionCommandType
    session_id: str = Field(min_length=1)
    instruction: str | None = None
    event_id: str | None = None
    provider_name: str = "config"
    force: bool = False
    from_audit: bool = False
    max_auto_revision_rounds: int | None = Field(default=None, ge=0)
    use_search_context: bool = True
    use_vector_context: VectorContextMode = "auto"
    polish_mode: PolishMode | None = None


class RevisionBlocksCommand(SchemaV3Model):
    type: Literal["revision.blocks"] = "revision.blocks"
    chapter_number: int = Field(ge=1)


class RevisionStartCommand(SchemaV3Model):
    type: Literal["revision.start"] = "revision.start"
    chapter_number: int = Field(ge=1)
    start_block: int = Field(ge=1)
    end_block: int = Field(ge=1)
    instruction: str = Field(min_length=1)


class RevisionCommand(SchemaV3Model):
    type: Literal["revision.show", "revision.run", "revision.accept"]
    revision_session_id: str = Field(min_length=1)
    provider_name: str = "config"
    use_search_context: bool = True
    use_vector_context: VectorContextMode = "auto"


class ProductionExportCommand(SchemaV3Model):
    type: Literal["export.markdown", "export.docx"]
    chapters: list[int] = Field(default_factory=list)
    from_chapter: int | None = Field(default=None, ge=1)
    to_chapter: int | None = Field(default=None, ge=1)
    output_path: str | None = None
    title: str | None = None
    include_toc: bool = False
    volume_title: str | None = None
    chapter_number_style: Literal["chinese", "arabic", "chapter", "plain"] = "chinese"
    force: bool = False


class PreviewPackageCommand(SchemaV3Model):
    type: Literal["preview.package"] = "preview.package"
    chapters: list[int] = Field(default_factory=list)
    from_chapter: int | None = Field(default=None, ge=1)
    to_chapter: int | None = Field(default=None, ge=1)
    source_kind: Literal["draft", "polished"] = "polished"
    title: str | None = None


class MemoryRepairSuggestCommand(SchemaV3Model):
    type: Literal["memory_repair.suggest"] = "memory_repair.suggest"
    request: str = Field(min_length=1)
    provider_name: str = "config"


class MemoryRepairApplyCommand(SchemaV3Model):
    type: Literal["memory_repair.apply"] = "memory_repair.apply"
    proposal_path: str = Field(min_length=1)


class SettingChangeSuggestCommand(SchemaV3Model):
    type: Literal["setting_change.suggest"] = "setting_change.suggest"
    request: str = Field(min_length=1)
    provider_name: str = "config"
    stage: Literal[
        "pre_creation",
        "outline_discussion",
        "content_review",
        "post_chapter",
        "unknown",
    ] = "pre_creation"
    session_id: str | None = None
    chapter_number: int | None = Field(default=None, ge=1)
    audit_issue_ids: list[str] = Field(default_factory=list)


class SettingChangeAnswerCommand(SchemaV3Model):
    type: Literal["setting_change.answer"] = "setting_change.answer"
    clarification_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    provider_name: str = "config"


class SettingChangeApplyCommand(SchemaV3Model):
    type: Literal["setting_change.apply"] = "setting_change.apply"
    proposal_path: str = Field(min_length=1)
    sync_session: bool = False
    session_id: str | None = None
    provider_name: str = "config"
    use_search_context: bool = True
    use_vector_context: VectorContextMode = "auto"
    polish_mode: PolishMode | None = None


PublicCommand = Annotated[
    Union[
        SessionStartCommand,
        SessionCommand,
        RevisionBlocksCommand,
        RevisionStartCommand,
        RevisionCommand,
        ProductionExportCommand,
        PreviewPackageCommand,
        MemoryRepairSuggestCommand,
        MemoryRepairApplyCommand,
        SettingChangeSuggestCommand,
        SettingChangeAnswerCommand,
        SettingChangeApplyCommand,
    ],
    Field(discriminator="type"),
]


class CommandEnvelope(SchemaV3Model):
    command_id: str = Field(pattern=r"^cmd_[0-9a-f]{32}$")
    workflow_run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    surface: Surface
    project_root: str = Field(min_length=1)
    command: PublicCommand
    confirmed: bool = False
    issued_at: datetime


class CommandResult(SchemaV3Model):
    command_id: str = Field(pattern=r"^cmd_[0-9a-f]{32}$")
    workflow_run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    command_type: str = Field(min_length=1)
    result: dict[str, object] = Field(default_factory=dict)
    next_allowed_commands: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    changed_artifacts: list[ArtifactRef] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
