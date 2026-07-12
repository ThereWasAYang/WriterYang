from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from novel.core.contracts.artifacts import ArtifactRef
from novel.core.contracts.common import SchemaV3Model, Surface
from novel.core.contracts.tracing import BudgetUsage, WorkflowBudget, default_workflow_budget


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


class ProjectStatusCommand(SchemaV3Model):
    type: Literal["project.status"] = "project.status"


class ProjectInitCommand(SchemaV3Model):
    type: Literal["project.init"] = "project.init"
    title: str = Field(min_length=1)
    project_id: str | None = None
    language: str = "zh-CN"
    genre: list[str] = Field(default_factory=list)


class ProjectValidateCommand(SchemaV3Model):
    type: Literal["project.validate"] = "project.validate"


class ProjectShowCommand(SchemaV3Model):
    type: Literal["project.show"] = "project.show"
    target: Literal["characters", "timeline", "state", "canon"] = "canon"


class SearchCommand(SchemaV3Model):
    type: Literal["search"] = "search"
    query: str = Field(min_length=1)
    search_type: Literal[
        "character", "location", "item", "event", "chapter", "chapter_memory", "all"
    ] = "all"
    limit: int = Field(default=10, ge=1, le=100)
    chapter_number: int | None = Field(default=None, ge=1)
    highlight: bool = False
    use_vector: bool = False
    embedding_provider_name: str = "config"
    embedding_config_path: str | None = None


class InspirationGenerateCommand(SchemaV3Model):
    type: Literal["inspiration.generate"] = "inspiration.generate"
    source_text: str = Field(min_length=1)
    source_type: str = Field(default="user_text", min_length=1)
    write_json: bool = False
    overwrite: bool = False
    allow_default_placeholder: bool = False
    provider_name: str = "config"
    agent_config_path: str | None = None
    model_name: str | None = None
    use_search_context: bool = False
    use_vector_context: VectorContextMode = "auto"


class CanonSuggestCommand(SchemaV3Model):
    type: Literal["canon.suggest"] = "canon.suggest"
    output_path: str | None = None
    provider_name: str = "config"
    agent_config_path: str | None = None
    model_name: str | None = None
    use_search_context: bool = False
    use_vector_context: VectorContextMode = "auto"


class CanonApplyCommand(SchemaV3Model):
    type: Literal["canon.apply"] = "canon.apply"
    proposal_path: str = Field(min_length=1)


class ChapterMemoryGenerateCommand(SchemaV3Model):
    type: Literal["chapter_memory.generate"] = "chapter_memory.generate"
    chapter_number: int = Field(ge=1)
    force: bool = False
    provider_name: str = "config"
    agent_config_path: str | None = None
    model_name: str | None = None


class ChapterMemoryRebuildCommand(SchemaV3Model):
    type: Literal["chapter_memory.rebuild"] = "chapter_memory.rebuild"
    mode: Literal["missing", "missing_or_stale", "all"] = "missing_or_stale"
    provider_name: str = "config"
    agent_config_path: str | None = None
    model_name: str | None = None


class IndexUpdateCommand(SchemaV3Model):
    type: Literal["index.rebuild", "index.refresh"]
    embedding_provider_name: str = "config"
    embedding_config_path: str | None = None
    with_embeddings: bool = False


class StyleGuideSaveCommand(SchemaV3Model):
    type: Literal["style_guide.save"] = "style_guide.save"
    content: str = Field(min_length=1)


class StyleGuideGenerateCommand(SchemaV3Model):
    type: Literal["style_guide.generate"] = "style_guide.generate"
    instruction: str = Field(min_length=1)
    provider_name: str = "config"
    agent_config_path: str | None = None
    model_name: str | None = None
    include_project_context: bool = True
    include_existing_style: bool = True


class ChapterCandidateSaveCommand(SchemaV3Model):
    type: Literal["chapter_candidate.save"] = "chapter_candidate.save"
    chapter_number: int = Field(ge=1)
    target: Literal["draft", "polished"]
    source_file: str = Field(min_length=1)
    content: str = Field(min_length=1)
    instruction: str | None = None


class AgentConfigUpdateCommand(SchemaV3Model):
    type: Literal["agent_config.update"] = "agent_config.update"
    default: dict[str, object] | None = None
    profiles: dict[str, dict[str, object]] = Field(default_factory=dict)
    tasks: dict[str, dict[str, object]] = Field(default_factory=dict)
    clear_profiles: list[str] = Field(default_factory=list)
    clear_tasks: list[str] = Field(default_factory=list)


class DefaultProviderSetupCommand(SchemaV3Model):
    type: Literal["setup.default_provider"] = "setup.default_provider"
    provider: str = "openai_compatible"
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1, repr=False)
    model: str = Field(min_length=1)
    max_context_tokens: int = Field(ge=1)
    max_tokens: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    ping: bool = True


class EmbeddingProviderSetupCommand(SchemaV3Model):
    type: Literal["setup.embedding_provider"] = "setup.embedding_provider"
    skip: bool = False
    provider: str = "openai_compatible"
    provider_name: str = "configured"
    base_url: str = ""
    api_key: str = Field(default="", repr=False)
    model: str = ""
    dimensions: int | None = Field(default=None, ge=1)
    batch_size: int | None = Field(default=None, ge=1)
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=1, ge=0)
    ping: bool = True

    @model_validator(mode="after")
    def require_connection_fields_unless_skipped(self) -> EmbeddingProviderSetupCommand:
        if not self.skip and not all((self.base_url.strip(), self.api_key.strip(), self.model.strip())):
            raise ValueError("base_url, api_key, and model are required unless skip is true")
        return self


class ProjectWebPortSetupCommand(SchemaV3Model):
    type: Literal["setup.project_web_port"] = "setup.project_web_port"
    requested_port: int = Field(ge=1, le=65535)
    host: str = Field(default="127.0.0.1", min_length=1)


class WebLauncherConfigCommand(SchemaV3Model):
    type: Literal["setup.web_launcher"] = "setup.web_launcher"
    host: str = Field(default="127.0.0.1", min_length=1)
    requested_port: int = Field(ge=1, le=65535)
    current_host: str | None = None
    current_port: int | None = Field(default=None, ge=1, le=65535)


class SchemaExportCommand(SchemaV3Model):
    type: Literal["schema.export"] = "schema.export"
    output_path: str = Field(min_length=1)


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
        ProjectStatusCommand,
        ProjectInitCommand,
        ProjectValidateCommand,
        ProjectShowCommand,
        SearchCommand,
        InspirationGenerateCommand,
        CanonSuggestCommand,
        CanonApplyCommand,
        ChapterMemoryGenerateCommand,
        ChapterMemoryRebuildCommand,
        IndexUpdateCommand,
        StyleGuideSaveCommand,
        StyleGuideGenerateCommand,
        ChapterCandidateSaveCommand,
        AgentConfigUpdateCommand,
        DefaultProviderSetupCommand,
        EmbeddingProviderSetupCommand,
        ProjectWebPortSetupCommand,
        WebLauncherConfigCommand,
        SchemaExportCommand,
    ],
    Field(discriminator="type"),
]


class CommandEnvelope(SchemaV3Model):
    command_id: str = Field(pattern=r"^cmd_[0-9a-f]{32}$")
    request_id: str = Field(pattern=r"^req_[0-9a-f]{32}$")
    parent_request_id: str | None = Field(default=None, min_length=1)
    workflow_run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    surface: Surface
    project_root: str = Field(min_length=1)
    command: PublicCommand
    confirmed: bool = False
    budget: WorkflowBudget = Field(default_factory=default_workflow_budget)
    initial_budget_usage: BudgetUsage = Field(default_factory=BudgetUsage)
    issued_at: datetime


class CommandResult(SchemaV3Model):
    command_id: str = Field(pattern=r"^cmd_[0-9a-f]{32}$")
    request_id: str = Field(pattern=r"^req_[0-9a-f]{32}$")
    workflow_run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    command_type: str = Field(min_length=1)
    result: dict[str, object] = Field(default_factory=dict)
    next_allowed_commands: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    changed_artifacts: list[ArtifactRef] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    budget_usage: BudgetUsage = Field(default_factory=BudgetUsage)
