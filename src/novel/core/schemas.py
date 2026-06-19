from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from novel.core.agent_defaults import PROFILE_NAMES, TASK_ONLY_CONFIG_FIELDS, TASK_TO_PROFILE


EntityId = str
Visibility = Literal["reader_visible", "hidden", "partially_revealed"]
Importance = Literal["low", "medium", "high", "critical"]
GenericStatus = Literal["active", "inactive", "resolved", "unresolved", "deprecated"]
TimelineEventRole = Literal["current_action", "flashback", "memory", "revelation", "summary", "backstory"]
TimelineCertainty = Literal["certain", "inferred", "uncertain"]
CreationScopeType = Literal["chapters", "segments"]
SessionRewriteAction = Literal["revision_rewrite", "plot_replan"]
SessionRewriteStatus = Literal["started", "completed", "unresolved", "failed"]
SessionRewriteUndoStatus = Literal["not_requested", "restored", "failed"]
SessionProgressStatus = Literal["idle", "running", "cancel_requested", "cancelled", "completed", "failed"]
MemoryRepairOperationType = Literal["add", "replace", "remove"]
MemoryRepairRiskLevel = Literal["low", "medium", "high"]
MemoryChangeKind = Literal["memory_repair", "setting_change"]
MemoryChangeDomain = Literal[
    "characters",
    "locations",
    "items",
    "world",
    "hidden_truths",
    "foreshadowing",
    "current_state",
    "timeline",
]
MemoryChangeStage = Literal[
    "pre_creation",
    "outline_discussion",
    "content_review",
    "post_chapter",
    "unknown",
]
JsonResponseFormat = Literal["auto", "json_object", "json_schema", "json_schema_strict"]
MemoryChangeFollowupActionType = Literal[
    "none",
    "revise_outline",
    "reapprove_outline",
    "revise_content",
    "reaudit_chapters",
    "rebuild_state_proposal",
    "start_revision_session",
    "manual_review",
]
MemoryChangeClarificationStatus = Literal["needs_clarification", "ready"]
MemoryChangeConversationRole = Literal["user", "agent"]
ManagementEventType = Literal[
    "chapter_accepted",
    "chapter_memory_generated",
    "chapter_memory_failed",
    "canon_proposal_applied",
    "state_update_proposed",
    "state_update_applied",
    "timeline_updated",
    "canon_drift_proposed",
    "memory_repair_proposed",
    "memory_repair_applied",
    "memory_repair_failed",
    "provider_output_truncated",
]
CreationSessionStatus = Literal[
    "drafting_intent",
    "outline_proposed",
    "outline_approved",
    "generating",
    "needs_revision",
    "needs_user_review",
    "accepted",
    "archived",
]
CreationOutlineStatus = Literal["draft", "proposed", "approved"]
CreationContentStatus = Literal["not_started", "generating", "needs_revision", "needs_user_review", "accepted", "archived"]
RevisionRoute = Literal["plot_replan", "writer_rewrite", "revision_patch"]
RevisionRouteRiskLevel = Literal["low", "medium", "high"]
AskIntentTask = Literal["session_start", "memory_repair_suggest", "memory_repair_apply", "export", "status", "show", "unknown"]
DecisionSource = Literal["model", "fallback", "mock", "deterministic"]
AuditIssueSourceLayer = Literal["plan", "draft", "polished", "state", "timeline", "canon", "style", "unknown"]
AuditEvidenceStrength = Literal["weak", "medium", "strong"]
AuditRepairRoute = Literal["plot_replan", "writer_rewrite", "revision_rewrite", "manual_review"]
VectorContextMode = Literal["auto", "on", "off"]
PolishMode = Literal["single_pass", "auto", "review_gate"]
ContextRequestKind = Literal["chapter_prose", "entity", "query"]
ChapterMemoryGenerationStatus = Literal["model_generated", "deterministic_fallback"]
ChapterMemoryStatus = Literal["accepted"]
ChapterMemoryVisibility = Literal["reader_visible", "author_only", "hidden_truth", "audit_only"]


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class SchemaVersionedModel(FlexibleModel):
    schema_version: int = Field(default=2, ge=1)


class TargetLength(FlexibleModel):
    type: str | None = None
    planned_chapters: int | None = Field(default=None, ge=1)


class Narration(FlexibleModel):
    pov: str
    tense: str


class WebConfig(FlexibleModel):
    default_port: int = Field(default=8765, ge=1, le=65535)


class ContextBudgetConfig(FlexibleModel):
    enabled: bool = False
    recent_window_chapters: int = Field(default=3, ge=0)
    max_full_timeline_events: int = Field(default=40, ge=1)
    max_full_state_entities: int = Field(default=60, ge=1)
    digest_dropped: bool = True


class PolishConfig(FlexibleModel):
    mode: PolishMode = "single_pass"


class AuditRecallConfig(FlexibleModel):
    enabled: bool = True
    max_recall_rounds: int = Field(default=1, ge=0, le=2)
    max_requests_per_round: int = Field(default=3, ge=1, le=10)


class ChapterMemoryConfig(FlexibleModel):
    enabled: bool = True
    generate_on_accept: bool = True
    strict_accept: bool = False
    inject_into_tasks: list[str] = Field(default_factory=lambda: ["plan", "write"])


class CanonDriftConfig(FlexibleModel):
    enabled: bool = True


class ProjectConfig(SchemaVersionedModel):
    project_id: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=1)
    language: str = Field(min_length=1)
    genre: list[str] = Field(min_length=1)
    narration: Narration
    created_at: datetime
    updated_at: datetime
    target_length: TargetLength | None = None
    default_style_profile_id: str | None = None
    web: WebConfig | None = None
    context_budget: ContextBudgetConfig | None = None
    chapter_memory: ChapterMemoryConfig | None = None
    polish: PolishConfig | None = None
    audit_recall: AuditRecallConfig | None = None
    canon_drift: CanonDriftConfig | None = None


class ThinkingConfig(FlexibleModel):
    type: Literal["enabled", "disabled"] = "disabled"


class AgentConfig(FlexibleModel):
    inherit_default: bool = False
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")
    base_url_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    reasoning: str | None = None
    thinking: ThinkingConfig = Field(default_factory=ThinkingConfig)
    max_context_tokens: int | None = Field(default=None, gt=0)
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    json_response_format: JsonResponseFormat = "auto"

    @field_validator("api_key_env", "base_url_env")
    @classmethod
    def reject_raw_keys(cls, value: str | None) -> str | None:
        if value and value.startswith(("sk-", "sk_")):
            raise ValueError("store environment variable names, not raw API keys")
        return value


class AgentConfigPatch(FlexibleModel):
    inherit_default: bool | None = None
    provider: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    api_key_env: str | None = Field(default=None, min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")
    base_url_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    reasoning: str | None = None
    thinking: ThinkingConfig | None = None
    max_context_tokens: int | None = Field(default=None, gt=0)
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    json_response_format: JsonResponseFormat | None = None

    @field_validator("api_key_env", "base_url_env")
    @classmethod
    def reject_raw_keys(cls, value: str | None) -> str | None:
        if value and value.startswith(("sk-", "sk_")):
            raise ValueError("store environment variable names, not raw API keys")
        return value


class AgentsConfig(SchemaVersionedModel):
    default: AgentConfig | None = None
    profiles: dict[str, AgentConfig | AgentConfigPatch] = Field(default_factory=dict)
    tasks: dict[str, AgentConfig | AgentConfigPatch] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_default_or_profiles(self) -> "AgentsConfig":
        if self.model_extra and "agents" in self.model_extra:
            raise ValueError("agents config uses removed agents mapping; use profiles/tasks")
        if self.default is None and not self.profiles:
            raise ValueError("agents config requires a default config or at least one profile config")
        if self.default is not None and self.default.inherit_default:
            raise ValueError("default config cannot inherit default")
        unknown_profiles = sorted(set(self.profiles) - set(PROFILE_NAMES))
        if unknown_profiles:
            raise ValueError(f"unknown profile config: {', '.join(unknown_profiles)}")
        for profile_name, profile_config in sorted(self.profiles.items()):
            task_only_fields = sorted(TASK_ONLY_CONFIG_FIELDS & set(profile_config.model_fields_set))
            if task_only_fields:
                fields = ", ".join(task_only_fields)
                raise ValueError(
                    f"profile {profile_name} contains task-only config field(s): {fields}; "
                    "move temperature/reasoning/thinking overrides to tasks.<task>"
                )
        unknown_tasks = sorted(set(self.tasks) - set(TASK_TO_PROFILE))
        if unknown_tasks:
            raise ValueError(f"unknown task config: {', '.join(unknown_tasks)}")
        return self


class EmbeddingProviderConfig(FlexibleModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    base_url_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    dimensions: int | None = Field(default=None, gt=0)
    batch_size: int = Field(default=16, gt=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)

    @field_validator("api_key_env", "base_url_env")
    @classmethod
    def reject_raw_keys(cls, value: str | None) -> str | None:
        if value and value.startswith(("sk-", "sk_")):
            raise ValueError("store environment variable names, not raw API keys")
        return value


class EmbeddingsConfig(SchemaVersionedModel):
    active_provider: str = Field(default="local", min_length=1)
    providers: dict[str, EmbeddingProviderConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_active_provider(self) -> EmbeddingsConfig:
        if self.active_provider not in self.providers:
            raise ValueError(f"active_provider is not configured: {self.active_provider}")
        return self


class InspirationBrief(SchemaVersionedModel):
    id: EntityId = Field(pattern=r"^[a-z0-9_]+$")
    source_type: str = Field(min_length=1)
    source_summary: str = Field(min_length=1)
    themes: list[str] = Field(min_length=1)
    weak_outline: str = Field(min_length=1)
    mood: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    potential_characters: list[str] = Field(default_factory=list)
    potential_locations: list[str] = Field(default_factory=list)
    potential_conflicts: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


class GeneratedStyleGuide(SchemaVersionedModel):
    model_config = ConfigDict(extra="forbid")

    style_sources: list[str] = Field(min_length=1)
    overall_style: str = Field(min_length=1)
    narrative_view: str = Field(min_length=1)
    language_rules: list[str] = Field(default_factory=list)
    dialogue_rules: list[str] = Field(default_factory=list)
    pacing_rules: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    sample_paragraph: str = Field(min_length=1)
    revision_notes: list[str] = Field(default_factory=list)

    @field_validator(
        "style_sources",
        "language_rules",
        "dialogue_rules",
        "pacing_rules",
        "avoid",
        "revision_notes",
        mode="before",
    )
    @classmethod
    def normalize_string_list(cls, value: object) -> object:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return value

    @field_validator("overall_style", "narrative_view", "sample_paragraph", mode="before")
    @classmethod
    def normalize_text_field(cls, value: object) -> object:
        return str(value).strip() if value is not None else value


class Relationship(FlexibleModel):
    target_id: EntityId
    type: str
    reader_visible: bool | None = None
    description: str | None = None


class Ability(FlexibleModel):
    name: str
    description: str
    limitations: str | None = None


class Secret(FlexibleModel):
    id: EntityId
    visibility: Visibility
    description: str
    planned_reveal: str | None = None


class Character(FlexibleModel):
    id: EntityId = Field(pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    reader_visible_summary: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    private_author_notes: str | None = None
    appearance: dict[str, Any] | None = None
    personality: dict[str, Any] | None = None
    relationships: list[Relationship] = Field(default_factory=list)
    abilities: list[Ability] = Field(default_factory=list)
    secrets: list[Secret] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_secret_ids(self) -> Character:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for secret in self.secrets:
            if secret.id in seen:
                duplicates.add(secret.id)
            seen.add(secret.id)
        if duplicates:
            raise ValueError(f"duplicate secret ids: {', '.join(sorted(duplicates))}")
        return self


class CharactersFile(SchemaVersionedModel):
    characters: list[Character] = Field(default_factory=list)


class LocationRule(FlexibleModel):
    id: EntityId | None = None
    description: str
    visibility: Visibility


class Location(FlexibleModel):
    id: EntityId = Field(pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    reader_visible_summary: str = Field(min_length=1)
    private_author_notes: str | None = None
    parent_location_id: EntityId | None = None
    connected_location_ids: list[EntityId] = Field(default_factory=list)
    rules: list[LocationRule] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class LocationsFile(SchemaVersionedModel):
    locations: list[Location] = Field(default_factory=list)


class SpecialProperty(FlexibleModel):
    description: str
    visibility: Visibility


class Item(FlexibleModel):
    id: EntityId = Field(pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    reader_visible_summary: str = Field(min_length=1)
    private_author_notes: str | None = None
    origin: str | None = None
    special_properties: list[SpecialProperty] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ItemsFile(SchemaVersionedModel):
    items: list[Item] = Field(default_factory=list)


class WorldRule(FlexibleModel):
    id: EntityId = Field(pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    visibility: Visibility
    limitations: list[str] = Field(default_factory=list)
    known_by_character_ids: list[EntityId] = Field(default_factory=list)


class WorldFile(SchemaVersionedModel):
    world_rules: list[WorldRule] = Field(default_factory=list)


class PlannedReveal(FlexibleModel):
    chapter: int = Field(ge=1)
    method: str | None = None


class HiddenTruth(FlexibleModel):
    id: EntityId = Field(pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    visibility: Visibility
    importance: Importance
    related_entity_ids: list[EntityId] = Field(default_factory=list)
    planned_reveal: PlannedReveal | None = None
    foreshadowing_ids: list[EntityId] = Field(default_factory=list)


class HiddenTruthsFile(SchemaVersionedModel):
    hidden_truths: list[HiddenTruth] = Field(default_factory=list)


class PlannedPayoff(FlexibleModel):
    chapter: int = Field(ge=1)
    description: str


class ForeshadowingThread(FlexibleModel):
    id: EntityId = Field(pattern=r"^[a-z0-9_]+$")
    type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    introduced_in_chapter: int = Field(ge=1)
    description: str = Field(min_length=1)
    status: GenericStatus
    importance: Importance
    reader_visible: bool | None = None
    hidden_truth: str | None = None
    hidden_truth_id: EntityId | None = None
    planned_payoff: PlannedPayoff | None = None
    related_entity_ids: list[EntityId] = Field(default_factory=list)

    @model_validator(mode="after")
    def payoff_not_before_intro(self) -> ForeshadowingThread:
        if self.planned_payoff and self.planned_payoff.chapter < self.introduced_in_chapter:
            raise ValueError("planned_payoff.chapter must be greater than or equal to introduced_in_chapter")
        return self


class ForeshadowingFile(SchemaVersionedModel):
    foreshadowing_threads: list[ForeshadowingThread] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_threads_alias(cls, data: object) -> object:
        if isinstance(data, dict) and "threads" in data and "foreshadowing_threads" not in data:
            data = dict(data)
            data["foreshadowing_threads"] = data["threads"]
        return data


class CanonProposal(SchemaVersionedModel):
    characters: list[Character] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)
    world_rules: list[WorldRule] = Field(default_factory=list)
    hidden_truths: list[HiddenTruth] = Field(default_factory=list)
    foreshadowing_threads: list[ForeshadowingThread] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class StoryPosition(FlexibleModel):
    latest_chapter: int = Field(default=0, ge=0)
    in_story_time: str | None = None
    summary: str | None = None


class CharacterState(FlexibleModel):
    entity_id: EntityId
    location_id: EntityId | None = None
    health: str | None = None
    mental_state: str | None = None
    knowledge: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    possessions: list[EntityId] = Field(default_factory=list)
    last_updated_chapter: int = Field(ge=0)


class ItemState(FlexibleModel):
    entity_id: EntityId
    holder_id: EntityId | None = None
    location_id: EntityId | None = None
    condition: str | None = None
    known_properties: list[str] = Field(default_factory=list)
    last_updated_chapter: int = Field(ge=0)


class LocationState(FlexibleModel):
    entity_id: EntityId
    accessibility: str | None = None
    condition: str | None = None
    active_events: list[str] = Field(default_factory=list)
    last_updated_chapter: int = Field(ge=0)


class EntityState(SchemaVersionedModel):
    story_position: StoryPosition
    character_states: list[CharacterState]
    item_states: list[ItemState]
    location_states: list[LocationState]


class TimelineNarrativePosition(FlexibleModel):
    chapter: int = Field(ge=1)
    scene: int | None = Field(default=None, ge=1)
    sequence: int | None = Field(default=None, ge=1)


class TimelineStoryPosition(FlexibleModel):
    time_label: str = Field(min_length=1)
    order: float | None = None
    thread_id: EntityId | None = None
    certainty: TimelineCertainty | None = None


class TimelineEvent(FlexibleModel):
    model_config = ConfigDict(extra="forbid")

    id: EntityId = Field(pattern=r"^[a-z0-9_]+$")
    summary: str = Field(min_length=1)
    reader_visible: bool
    narrative_position: TimelineNarrativePosition | None = None
    story_position: TimelineStoryPosition
    event_role: TimelineEventRole | None = None
    location_id: EntityId | None = None
    participant_ids: list[EntityId] = Field(default_factory=list)
    causes: list[str] = Field(default_factory=list)
    effects: list[str] = Field(default_factory=list)
    state_change_ids: list[EntityId] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class AnchoredTimelineEvent(TimelineEvent):
    narrative_position: TimelineNarrativePosition


class TimelineFile(SchemaVersionedModel):
    events: list[TimelineEvent] = Field(default_factory=list)


class StateChange(FlexibleModel):
    id: EntityId = Field(pattern=r"^[a-z0-9_]+$")
    chapter: int = Field(ge=1)
    entity_id: EntityId
    field: str = Field(min_length=1)
    new_value: Any
    reason: str = Field(min_length=1)
    source: str = Field(min_length=1)
    old_value: Any | None = None


class StateUpdateProposal(SchemaVersionedModel):
    chapter_number: int = Field(ge=1)
    state_changes: list[StateChange] = Field(default_factory=list)
    timeline_events: list[AnchoredTimelineEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime

    @model_validator(mode="after")
    def unique_change_and_event_ids(self) -> StateUpdateProposal:
        _require_unique_values([change.id for change in self.state_changes], "state_change id")
        _require_unique_values([event.id for event in self.timeline_events], "timeline event id")
        for change in self.state_changes:
            if change.chapter != self.chapter_number:
                raise ValueError(f"state change {change.id} chapter must match proposal chapter_number")
        for event in self.timeline_events:
            if event.narrative_position is None:
                raise ValueError(f"timeline event {event.id} narrative_position is required for StateUpdateProposal")
            if event.narrative_position.chapter != self.chapter_number:
                raise ValueError(f"timeline event {event.id} chapter must match proposal chapter_number")
        return self


class StateUpdateApplyLog(SchemaVersionedModel):
    id: str = Field(min_length=1, pattern=r"^state_apply_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    chapter_number: int = Field(ge=1)
    proposal_path: str = Field(min_length=1)
    state_path: str = Field(min_length=1)
    timeline_path: str = Field(min_length=1)
    state_backup_path: str = Field(min_length=1)
    timeline_backup_path: str = Field(min_length=1)
    applied_at: datetime
    status: Literal["applied", "rolled_back"]
    errors: list[str] = Field(default_factory=list)


class CanonProposalCounts(FlexibleModel):
    characters: int = Field(default=0, ge=0)
    locations: int = Field(default=0, ge=0)
    items: int = Field(default=0, ge=0)
    world_rules: int = Field(default=0, ge=0)
    hidden_truths: int = Field(default=0, ge=0)
    foreshadowing_threads: int = Field(default=0, ge=0)


class CanonApplyLog(SchemaVersionedModel):
    id: str = Field(min_length=1, pattern=r"^canon_apply_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    original_proposal_path: str = Field(min_length=1)
    proposal_snapshot_path: str = Field(min_length=1)
    target_files: list[str] = Field(default_factory=list)
    proposal_counts: CanonProposalCounts = Field(default_factory=CanonProposalCounts)
    validation_warning_count: int = Field(default=0, ge=0)
    applied_at: datetime
    status: Literal["applied"] = "applied"


class ChapterMetadata(SchemaVersionedModel):
    chapter_number: int = Field(ge=1)
    status: Literal["planned", "drafted", "polished", "audited", "accepted"]
    plan_path: str | None = None
    draft_path: str | None = None
    polished_path: str | None = None
    audit_path: str | None = None
    state_update_proposal_path: str | None = None
    state_update_apply_log_path: str | None = None
    chapter_memory_path: str | None = None
    accepted_at: datetime | None = None
    updated_at: datetime


class ChapterMemorySource(FlexibleModel):
    polished_path: str = Field(min_length=1)
    polished_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    plan_path: str | None = None
    audit_path: str | None = None
    state_update_proposal_path: str | None = None
    state_update_apply_log_path: str | None = None


class ChapterMemorySourceRef(FlexibleModel):
    path: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    id: str | None = None
    quote: str | None = None


class ChapterMemoryItem(FlexibleModel):
    summary: str = Field(min_length=1)
    description: str | None = None
    visibility: ChapterMemoryVisibility = "author_only"
    related_entity_ids: list[EntityId] = Field(default_factory=list)
    timeline_event_ids: list[EntityId] = Field(default_factory=list)
    source_refs: list[ChapterMemorySourceRef] = Field(default_factory=list)


class ChapterMemory(SchemaVersionedModel):
    chapter_number: int = Field(ge=1)
    title: str = Field(min_length=1)
    status: ChapterMemoryStatus = "accepted"
    generated_at: datetime
    generation_status: ChapterMemoryGenerationStatus
    source: ChapterMemorySource
    reader_visible_summary: str = Field(min_length=1)
    plot_beats: list[ChapterMemoryItem] = Field(default_factory=list)
    character_knowledge_changes: list[ChapterMemoryItem] = Field(default_factory=list)
    state_changes: list[ChapterMemoryItem] = Field(default_factory=list)
    timeline_event_ids: list[EntityId] = Field(default_factory=list)
    open_threads: list[ChapterMemoryItem] = Field(default_factory=list)
    foreshadowing: list[ChapterMemoryItem] = Field(default_factory=list)
    continuity_notes: list[ChapterMemoryItem] = Field(default_factory=list)
    retrieval_hints: list[ChapterMemoryItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def memory_item_timeline_refs_are_listed(self) -> "ChapterMemory":
        timeline_ids = list(self.timeline_event_ids)
        for item in self.all_items():
            for event_id in item.timeline_event_ids:
                if event_id not in timeline_ids:
                    timeline_ids.append(event_id)
        _require_unique_values(timeline_ids, "chapter memory timeline_event id")
        self.timeline_event_ids = timeline_ids
        return self

    def all_items(self) -> list[ChapterMemoryItem]:
        return [
            *self.plot_beats,
            *self.character_knowledge_changes,
            *self.state_changes,
            *self.open_threads,
            *self.foreshadowing,
            *self.continuity_notes,
            *self.retrieval_hints,
        ]


class CreationArchiveEntry(FlexibleModel):
    source_path: str = Field(min_length=1)
    archive_path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    created_at: datetime


class CreationSession(SchemaVersionedModel):
    session_id: str = Field(min_length=1, pattern=r"^session_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    scope_type: CreationScopeType
    chapter_range: list[int] = Field(min_length=1)
    segment_range: list[int] | None = None
    user_intent: str = Field(min_length=1)
    status: CreationSessionStatus
    outline_status: CreationOutlineStatus = "draft"
    content_status: CreationContentStatus = "not_started"
    approved_outline_path: str | None = None
    final_output_paths: list[str] = Field(default_factory=list)
    audit_history: list[str] = Field(default_factory=list)
    revision_history: list[str] = Field(default_factory=list)
    revision_route_history: list["RevisionRouteRecord"] = Field(default_factory=list)
    archive_paths: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    max_auto_revision_rounds: int = Field(default=3, ge=0)

    @model_validator(mode="after")
    def validate_scope_and_status(self) -> CreationSession:
        if sorted(set(self.chapter_range)) != self.chapter_range:
            raise ValueError("chapter_range must be sorted and unique")
        if self.scope_type == "segments" and not self.segment_range:
            raise ValueError("segment sessions require segment_range")
        if self.status in {"outline_approved", "generating", "needs_revision", "needs_user_review", "accepted", "archived"}:
            if self.outline_status != "approved":
                raise ValueError("approved-or-later sessions require outline_status=approved")
        if self.status == "archived" and self.content_status != "archived":
            raise ValueError("archived sessions require content_status=archived")
        return self


class CreationOutlineChapter(FlexibleModel):
    chapter_number: int = Field(ge=1)
    title: str = Field(min_length=1)
    plan_path: str = Field(
        min_length=1,
        description="outline_proposal 中指向 session 内草稿 plan；approved_outline 中指向正式章节 plan。",
    )
    summary: str = Field(min_length=1)


class CreationOutline(SchemaVersionedModel):
    session_id: str = Field(min_length=1)
    user_intent: str = Field(min_length=1)
    chapters: list[CreationOutlineChapter] = Field(min_length=1)
    created_at: datetime


class CreationArchiveManifest(SchemaVersionedModel):
    session_id: str = Field(min_length=1)
    created_at: datetime
    entries: list[CreationArchiveEntry] = Field(default_factory=list)


class RevisionRouteDecision(SchemaVersionedModel):
    route: RevisionRoute
    reason: str = Field(min_length=1)
    chapter_numbers: list[int] = Field(default_factory=list)
    instruction_for_plot: str | None = None
    instruction_for_writer: str | None = None
    instruction_for_revision: str | None = None
    risk_level: RevisionRouteRiskLevel = "medium"

    @model_validator(mode="after")
    def require_matching_instruction(self) -> RevisionRouteDecision:
        if self.route == "plot_replan" and not _has_text(self.instruction_for_plot):
            raise ValueError("plot_replan requires instruction_for_plot")
        if self.route == "writer_rewrite" and not _has_text(self.instruction_for_writer):
            raise ValueError("writer_rewrite requires instruction_for_writer")
        if self.route == "revision_patch" and not _has_text(self.instruction_for_revision):
            raise ValueError("revision_patch requires instruction_for_revision")
        return self


class AskIntentDecision(SchemaVersionedModel):
    task: AskIntentTask
    reason: str = Field(min_length=1)
    chapter_range: list[int] = Field(default_factory=list)
    repair_id: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    user_message: str | None = None
    source: DecisionSource = "model"

    @model_validator(mode="after")
    def validate_apply_has_repair_id(self) -> "AskIntentDecision":
        if self.task == "memory_repair_apply" and not _has_text(self.repair_id):
            raise ValueError("memory_repair_apply requires repair_id")
        return self


class AuditRepairRouteDecision(SchemaVersionedModel):
    route: AuditRepairRoute
    reason: str = Field(min_length=1)
    chapter_number: int = Field(ge=1)
    issue_ids: list[str] = Field(default_factory=list)
    source_layer: AuditIssueSourceLayer | None = None
    risk_level: RevisionRouteRiskLevel = "medium"
    source: DecisionSource = "model"


class RevisionRouteRecord(FlexibleModel):
    created_at: datetime
    user_instruction: str
    from_audit: bool = False
    decision: RevisionRouteDecision


class AgentRunStep(FlexibleModel):
    step_id: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    input_files: list[str] = Field(default_factory=list)
    output_files: list[str] = Field(default_factory=list)
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    error: str | None = None


class AgentRunLog(SchemaVersionedModel):
    run_id: str = Field(min_length=1, pattern=r"^run_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    task: str = Field(min_length=1)
    chapter_number: int | None = Field(default=None, ge=1)
    started_at: datetime
    ended_at: datetime | None = None
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    steps: list[AgentRunStep] = Field(default_factory=list)
    input_files: list[str] = Field(default_factory=list)
    output_files: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ExportSourceChapter(FlexibleModel):
    chapter_number: int = Field(ge=1)
    title: str = Field(min_length=1)
    path: str = Field(min_length=1)
    accepted: bool
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class ExportRecord(FlexibleModel):
    id: str = Field(min_length=1, pattern=r"^export_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    type: Literal["markdown", "docx", "html", "txt"]
    source_chapters: list[int] = Field(min_length=1)
    source_chapter_details: list[ExportSourceChapter] = Field(default_factory=list)
    output_path: str = Field(min_length=1)
    created_at: datetime
    title: str | None = None


class ExportManifest(SchemaVersionedModel):
    exports: list[ExportRecord] = Field(default_factory=list)


class RevisionRecord(FlexibleModel):
    id: str = Field(min_length=1, pattern=r"^revision_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    chapter_number: int = Field(ge=1)
    target: Literal["draft", "polished"]
    source_file: str = Field(min_length=1)
    output_file: str = Field(min_length=1)
    instruction: str | None = None
    from_audit: bool = False
    audit_file: str | None = None
    audit_issue_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    provider: str | None = None


class RevisionLog(SchemaVersionedModel):
    chapter_number: int = Field(ge=1)
    revisions: list[RevisionRecord] = Field(default_factory=list)


class RequiredContext(FlexibleModel):
    canon_entity_ids: list[EntityId] = Field(default_factory=list)
    state_entity_ids: list[EntityId] = Field(default_factory=list)
    timeline_event_ids: list[EntityId] = Field(default_factory=list)


class ChapterScene(FlexibleModel):
    scene_number: int = Field(ge=1)
    location_id: EntityId
    participant_ids: list[EntityId] = Field(default_factory=list)
    purpose: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    emotional_beat: str = Field(min_length=1)
    plot_points: list[str] = Field(default_factory=list)


class ChapterPlan(SchemaVersionedModel):
    chapter_number: int = Field(ge=1)
    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    required_context: RequiredContext
    scenes: list[ChapterScene] = Field(min_length=1)
    must_include: list[str]
    must_avoid: list[str]
    ending_hook: str = Field(min_length=1)
    expected_state_changes: list[str]

    @model_validator(mode="after")
    def scene_numbers_are_sequential(self) -> ChapterPlan:
        expected = list(range(1, len(self.scenes) + 1))
        actual = [scene.scene_number for scene in self.scenes]
        if actual != expected:
            raise ValueError(f"scene numbers must be sequential from 1, got {actual}")
        return self


ContextTask = Literal[
    "inspiration",
    "canon",
    "plan",
    "write",
    "polish",
    "audit",
    "state_update",
    "revision",
]
ContextVisibility = Literal["reader_visible", "author_only", "hidden_truth", "audit_only"]


class ContextItem(FlexibleModel):
    id: EntityId = Field(min_length=1)
    type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    visibility: ContextVisibility
    reason: str = Field(min_length=1)
    priority: int
    content: dict[str, Any] = Field(default_factory=dict)


class ContextExclusion(FlexibleModel):
    id: EntityId = Field(min_length=1)
    type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    visibility: ContextVisibility
    reason: str = Field(min_length=1)


class ContextBundle(SchemaVersionedModel):
    chapter_number: int | None = Field(default=None, ge=1)
    task: ContextTask
    query: str
    included: list[ContextItem] = Field(default_factory=list)
    excluded: list[ContextExclusion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime

    def render_for_prompt(self) -> str:
        if not self.included and not self.excluded:
            return (
                "Context bundle: no related context was selected. "
                "Do not invent missing context; rely on loaded canon/state/timeline.\n"
            )
        lines = [
            "Context bundle (explainable retrieval):",
            f"- task: {self.task}",
            f"- query: {self.query}",
            f"- chapter_number: {self.chapter_number if self.chapter_number is not None else 'project'}",
            "- included:",
        ]
        if not self.included:
            lines.append("  none")
        for index, item in enumerate(sorted(self.included, key=lambda value: (-value.priority, value.type, value.id)), start=1):
            lines.extend(
                [
                    f"  {index}. [{item.type}] {item.id} ({item.source})",
                    f"     visibility: {item.visibility}; priority: {item.priority}; reason: {item.reason}",
                    f"     content: {json_dumps_compact(item.content)}",
                ]
            )
        if self.excluded:
            lines.append("- excluded:")
            for excluded_item in self.excluded:
                lines.append(
                    f"  - [{excluded_item.type}] {excluded_item.id} ({excluded_item.source}); "
                    f"visibility: {excluded_item.visibility}; reason: {excluded_item.reason}"
                )
        if self.warnings:
            lines.append("- warnings:")
            lines.extend(f"  - {warning}" for warning in self.warnings)
        return "\n".join(lines) + "\n"


class ContextRequest(FlexibleModel):
    kind: ContextRequestKind
    ref: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AuditEvidence(FlexibleModel):
    source: str
    quote: str


class AuditIssue(FlexibleModel):
    id: EntityId = Field(pattern=r"^[a-z0-9_]+$")
    severity: Importance
    type: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence: list[AuditEvidence] = Field(default_factory=list)
    suggested_fix: str | None = None
    source_layer: AuditIssueSourceLayer | None = None
    blocking_reason: str | None = None
    evidence_strength: AuditEvidenceStrength | None = None
    is_hard_blocker: bool | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class AuditReport(SchemaVersionedModel):
    chapter_number: int = Field(ge=1)
    audited_file: str = Field(min_length=1)
    overall_status: Literal["passed", "needs_revision", "blocked"]
    summary: str = Field(min_length=1)
    issues: list[AuditIssue]
    created_at: datetime
    passed_checks: list[str] = Field(default_factory=list)
    need_context: list[ContextRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def passed_reports_have_no_blocking_severity_issues(self) -> AuditReport:
        if self.overall_status == "passed":
            severe = [issue.id for issue in self.issues if issue.severity in {"medium", "high", "critical"}]
            if severe:
                raise ValueError(
                    "passed audit reports cannot contain medium, high, or critical issues: "
                    + ", ".join(severe)
                )
        for issue in self.issues:
            if issue.type != "informational" and not issue.suggested_fix:
                raise ValueError(f"audit issue {issue.id} must include suggested_fix")
        return self


class SessionRewriteIssue(FlexibleModel):
    id: EntityId = Field(pattern=r"^[a-z0-9_]+$")
    severity: Importance
    type: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence: list[AuditEvidence] = Field(default_factory=list)
    suggested_fix: str | None = None


class SessionAuditRevision(FlexibleModel):
    instruction: str = Field(min_length=1)
    previous_audit_path: str = Field(min_length=1)
    new_audit_path: str = Field(min_length=1)
    created_at: datetime


class SessionRewriteEvent(SchemaVersionedModel):
    event_id: str = Field(min_length=1, pattern=r"^rewrite_[a-z0-9_]+$")
    session_id: str = Field(min_length=1, pattern=r"^session_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    chapter_number: int = Field(ge=1)
    round_number: int = Field(ge=1)
    action: SessionRewriteAction
    status: SessionRewriteStatus
    trigger_audit_path: str = Field(min_length=1)
    blocking_issues: list[SessionRewriteIssue] = Field(default_factory=list)
    rejected_text_snapshot_path: str | None = None
    before_output_path: str | None = None
    after_output_path: str | None = None
    can_undo: bool = True
    undo_status: SessionRewriteUndoStatus = "not_requested"
    audit_revision_history: list[SessionAuditRevision] = Field(default_factory=list)
    restored_from_snapshot_path: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class SessionRewriteEvents(SchemaVersionedModel):
    events: list[SessionRewriteEvent] = Field(default_factory=list)


class SessionProgressEvent(FlexibleModel):
    stage: str = Field(min_length=1)
    message: str = Field(min_length=1)
    chapter_number: int | None = Field(default=None, ge=1)
    round_number: int | None = Field(default=None, ge=0)
    created_at: datetime


class SessionProgress(SchemaVersionedModel):
    session_id: str = Field(min_length=1, pattern=r"^session_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    status: SessionProgressStatus
    current_stage: str | None = None
    current_message: str | None = None
    current_chapter: int | None = Field(default=None, ge=1)
    current_round: int | None = Field(default=None, ge=0)
    events: list[SessionProgressEvent] = Field(default_factory=list)
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    error: str | None = None


class MemoryRepairOperation(FlexibleModel):
    op: MemoryRepairOperationType
    file: str = Field(min_length=1)
    path: str = Field(min_length=1)
    value: object | None = None
    reason: str = Field(min_length=1)


class MemoryChangeImpact(SchemaVersionedModel):
    domains: list[MemoryChangeDomain] = Field(default_factory=list)
    entity_ids: list[EntityId] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    affected_chapters: list[int] = Field(default_factory=list)
    affected_sessions: list[str] = Field(default_factory=list)
    stale_chapters: list[int] = Field(default_factory=list)
    risk_level: MemoryRepairRiskLevel = "medium"
    reference_count: int = Field(default=0, ge=0)
    summary: str = Field(default="")


class MemoryChangeFollowupAction(FlexibleModel):
    action: MemoryChangeFollowupActionType
    reason: str = Field(min_length=1)
    chapter_numbers: list[int] = Field(default_factory=list)
    session_id: str | None = None
    auto: bool = False


class MemoryChangeConversationTurn(FlexibleModel):
    role: MemoryChangeConversationRole
    content: str = Field(min_length=1)
    created_at: datetime


class MemoryChangeClarificationDecision(SchemaVersionedModel):
    status: MemoryChangeClarificationStatus
    questions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    assumptions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    source: DecisionSource = "model"

    @model_validator(mode="after")
    def require_questions_when_needed(self) -> "MemoryChangeClarificationDecision":
        if self.status == "needs_clarification" and not any(_has_text(question) for question in self.questions):
            raise ValueError("needs_clarification requires at least one question")
        return self


class MemoryChangeClarificationSession(SchemaVersionedModel):
    clarification_id: str = Field(min_length=1, pattern=r"^clarify_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    change_kind: Literal["setting_change"] = "setting_change"
    original_request: str = Field(min_length=1)
    stage: MemoryChangeStage = "unknown"
    session_id: str | None = None
    chapter_number: int | None = Field(default=None, ge=1)
    audit_issue_ids: list[str] = Field(default_factory=list)
    status: Literal["needs_clarification", "proposal_ready", "closed"] = "needs_clarification"
    questions: list[str] = Field(default_factory=list)
    conversation_turns: list[MemoryChangeConversationTurn] = Field(default_factory=list)
    proposal_path: str | None = None
    created_at: datetime
    updated_at: datetime


class MemoryChangeBatch(FlexibleModel):
    batch_id: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    instruction: str = Field(min_length=1)
    target_files: list[str] = Field(default_factory=list)
    domains: list[MemoryChangeDomain] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_scope(self) -> "MemoryChangeBatch":
        if not self.target_files and not self.domains:
            raise ValueError("batch requires target_files or domains")
        return self


class MemoryChangeBatchPlan(SchemaVersionedModel):
    change_kind: Literal["setting_change"] = "setting_change"
    stage: MemoryChangeStage = "unknown"
    batches: list[MemoryChangeBatch] = Field(min_length=1, max_length=8)
    confidence: float = Field(default=0.0, ge=0, le=1)
    assumptions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    source: DecisionSource = "model"


class MemoryRepairDecision(SchemaVersionedModel):
    change_kind: MemoryChangeKind = "memory_repair"
    target_files: list[str] = Field(default_factory=list)
    operations: list[MemoryRepairOperation] = Field(default_factory=list)
    domains: list[MemoryChangeDomain] = Field(default_factory=list)
    stage: MemoryChangeStage = "unknown"
    impact: MemoryChangeImpact | None = None
    followup_actions: list[MemoryChangeFollowupAction] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    assumptions: list[str] = Field(default_factory=list)
    needs_user_confirmation: bool = True
    notes: list[str] = Field(default_factory=list)
    source: DecisionSource = "model"


class MemoryRepairProposal(SchemaVersionedModel):
    repair_id: str = Field(min_length=1, pattern=r"^repair_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    created_by: Literal["memory_repair"] = "memory_repair"
    change_kind: MemoryChangeKind = "memory_repair"
    user_request: str = Field(min_length=1)
    target_files: list[str] = Field(default_factory=list)
    operations: list[MemoryRepairOperation] = Field(default_factory=list)
    domains: list[MemoryChangeDomain] = Field(default_factory=list)
    stage: MemoryChangeStage = "unknown"
    impact: MemoryChangeImpact | None = None
    followup_actions: list[MemoryChangeFollowupAction] = Field(default_factory=list)
    risk_level: MemoryRepairRiskLevel = "medium"
    confidence: float = Field(default=0.0, ge=0, le=1)
    assumptions: list[str] = Field(default_factory=list)
    needs_user_confirmation: bool = True
    validation_before: dict[str, object] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    created_at: datetime


class MemoryRepairApplyLog(SchemaVersionedModel):
    repair_id: str = Field(min_length=1)
    applied_at: datetime
    status: Literal["applied", "failed", "rolled_back"]
    target_files: list[str] = Field(default_factory=list)
    backups: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ManagementEvent(SchemaVersionedModel):
    event_id: str = Field(min_length=1, pattern=r"^mgmt_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    event_type: ManagementEventType
    message: str = Field(min_length=1)
    source: str | None = None
    target_files: list[str] = Field(default_factory=list)
    status: Literal["info", "success", "warning", "error"] = "info"
    details: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


def _require_unique_values(values: list[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ValueError(f"duplicate {label}: {', '.join(sorted(duplicates))}")


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())


def json_dumps_compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
