from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EntityId = str
Visibility = Literal["reader_visible", "hidden", "partially_revealed"]
Importance = Literal["low", "medium", "high", "critical"]
GenericStatus = Literal["active", "inactive", "resolved", "unresolved", "deprecated"]


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class TargetLength(FlexibleModel):
    type: str | None = None
    planned_chapters: int | None = Field(default=None, ge=1)


class Narration(FlexibleModel):
    pov: str
    tense: str


class ProjectConfig(FlexibleModel):
    project_id: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=1)
    language: str = Field(min_length=1)
    genre: list[str] = Field(min_length=1)
    narration: Narration
    created_at: datetime
    updated_at: datetime
    target_length: TargetLength | None = None
    default_style_profile_id: str | None = None


class AgentConfig(FlexibleModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")
    base_url_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    reasoning: str | None = None
    max_context_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)

    @field_validator("api_key_env", "base_url_env")
    @classmethod
    def reject_raw_keys(cls, value: str | None) -> str | None:
        if value and value.startswith(("sk-", "sk_")):
            raise ValueError("store environment variable names, not raw API keys")
        return value


class AgentsConfig(FlexibleModel):
    agents: dict[str, AgentConfig] = Field(min_length=1)


class InspirationBrief(FlexibleModel):
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


class CharactersFile(FlexibleModel):
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


class LocationsFile(FlexibleModel):
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


class ItemsFile(FlexibleModel):
    items: list[Item] = Field(default_factory=list)


class WorldRule(FlexibleModel):
    id: EntityId = Field(pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    visibility: Visibility
    limitations: list[str] = Field(default_factory=list)
    known_by_character_ids: list[EntityId] = Field(default_factory=list)


class WorldFile(FlexibleModel):
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


class HiddenTruthsFile(FlexibleModel):
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


class ForeshadowingFile(FlexibleModel):
    foreshadowing_threads: list[ForeshadowingThread] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_threads_alias(cls, data: object) -> object:
        if isinstance(data, dict) and "threads" in data and "foreshadowing_threads" not in data:
            data = dict(data)
            data["foreshadowing_threads"] = data["threads"]
        return data


class CanonProposal(FlexibleModel):
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


class EntityState(FlexibleModel):
    story_position: StoryPosition
    character_states: list[CharacterState]
    item_states: list[ItemState]
    location_states: list[LocationState]


class TimelineEvent(FlexibleModel):
    id: EntityId = Field(pattern=r"^[a-z0-9_]+$")
    chapter: int = Field(ge=1)
    in_story_time: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    reader_visible: bool
    scene: int | None = Field(default=None, ge=1)
    location_id: EntityId | None = None
    participant_ids: list[EntityId] = Field(default_factory=list)
    causes: list[str] = Field(default_factory=list)
    effects: list[str] = Field(default_factory=list)
    state_change_ids: list[EntityId] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class TimelineFile(FlexibleModel):
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


class StateUpdateProposal(FlexibleModel):
    chapter_number: int = Field(ge=1)
    state_changes: list[StateChange] = Field(default_factory=list)
    timeline_events: list[TimelineEvent] = Field(default_factory=list)
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
            if event.chapter != self.chapter_number:
                raise ValueError(f"timeline event {event.id} chapter must match proposal chapter_number")
        return self


class AgentRunStep(FlexibleModel):
    step_id: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    input_files: list[str] = Field(default_factory=list)
    output_files: list[str] = Field(default_factory=list)
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    error: str | None = None


class AgentRunLog(FlexibleModel):
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


class ExportRecord(FlexibleModel):
    id: str = Field(min_length=1, pattern=r"^export_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    type: Literal["markdown", "docx", "html", "txt"]
    source_chapters: list[int] = Field(min_length=1)
    output_path: str = Field(min_length=1)
    created_at: datetime
    title: str | None = None


class ExportManifest(FlexibleModel):
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


class RevisionLog(FlexibleModel):
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


class ChapterPlan(FlexibleModel):
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


class AuditReport(FlexibleModel):
    chapter_number: int = Field(ge=1)
    audited_file: str = Field(min_length=1)
    overall_status: Literal["passed", "needs_revision", "blocked"]
    summary: str = Field(min_length=1)
    issues: list[AuditIssue]
    created_at: datetime
    passed_checks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def passed_reports_have_no_high_severity_issues(self) -> AuditReport:
        if self.overall_status == "passed":
            severe = [issue.id for issue in self.issues if issue.severity in {"high", "critical"}]
            if severe:
                raise ValueError(
                    "passed audit reports cannot contain high or critical issues: "
                    + ", ".join(severe)
                )
        for issue in self.issues:
            if issue.type != "informational" and not issue.suggested_fix:
                raise ValueError(f"audit issue {issue.id} must include suggested_fix")
        return self


def _require_unique_values(values: list[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ValueError(f"duplicate {label}: {', '.join(sorted(duplicates))}")
