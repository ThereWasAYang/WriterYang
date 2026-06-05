from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from pydantic import ValidationError
import yaml

from novel.core.chapter_memory import validate_chapter_memory
from novel.core.consistency import check_project_consistency
from novel.core.env import load_project_env
from novel.core.io import load_json_model, load_yaml_model
from novel.core.migration import CURRENT_SCHEMA_VERSION
from novel.core.schemas import (
    AgentConfig,
    AgentConfigPatch,
    AgentsConfig,
    AuditReport,
    ChapterMemory,
    ChapterMetadata,
    ChapterPlan,
    CharactersFile,
    CreationArchiveManifest,
    CreationOutline,
    CreationSession,
    SessionRewriteEvents,
    EmbeddingsConfig,
    EntityState,
    ForeshadowingFile,
    HiddenTruthsFile,
    InspirationBrief,
    ItemsFile,
    LocationsFile,
    MemoryRepairApplyLog,
    MemoryRepairProposal,
    ProjectConfig,
    AgentRunLog,
    ExportManifest,
    RevisionLog,
    StateUpdateApplyLog,
    StateUpdateProposal,
    TimelineFile,
    WorldFile,
)


@dataclass(frozen=True)
class ValidationMessage:
    level: str
    path: Path
    message: str


@dataclass
class ValidationReport:
    root: Path
    messages: list[ValidationMessage] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationMessage]:
        return [message for message in self.messages if message.level == "error"]

    @property
    def warnings(self) -> list[ValidationMessage]:
        return [message for message in self.messages if message.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, path: Path, message: str) -> None:
        self.messages.append(ValidationMessage("error", path, message))

    def warning(self, path: Path, message: str) -> None:
        self.messages.append(ValidationMessage("warning", path, message))


@dataclass(frozen=True)
class LoadedProject:
    project: ProjectConfig | None = None
    agents: AgentsConfig | None = None
    embeddings: EmbeddingsConfig | None = None
    characters: CharactersFile | None = None
    locations: LocationsFile | None = None
    items: ItemsFile | None = None
    world: WorldFile | None = None
    hidden_truths: HiddenTruthsFile | None = None
    foreshadowing: ForeshadowingFile | None = None
    state: EntityState | None = None
    timeline: TimelineFile | None = None


def validate_project(root: Path) -> ValidationReport:
    root = root.resolve()
    report = ValidationReport(root=root)

    loaded = _load_project_files(root, report, include_state=True)
    _validate_loaded_project(report, root, loaded, include_state=True)
    return report


def validate_canon(root: Path) -> ValidationReport:
    root = root.resolve()
    report = ValidationReport(root=root)
    loaded = LoadedProject(
        characters=_load_required_json(
            root / "memory" / "canon" / "characters.json", CharactersFile, report
        ),
        locations=_load_required_json(
            root / "memory" / "canon" / "locations.json", LocationsFile, report
        ),
        items=_load_required_json(root / "memory" / "canon" / "items.json", ItemsFile, report),
        world=_load_required_json(root / "memory" / "canon" / "world.json", WorldFile, report),
        hidden_truths=_load_required_json(
            root / "memory" / "canon" / "hidden_truths.json", HiddenTruthsFile, report
        ),
        foreshadowing=_load_required_json(
            root / "memory" / "canon" / "foreshadowing.json", ForeshadowingFile, report
        ),
    )
    _validate_loaded_project(report, root, loaded, include_state=False)
    return report


def _load_project_files(root: Path, report: ValidationReport, *, include_state: bool) -> LoadedProject:
    loaded = LoadedProject(
        project=_load_required_yaml(root / "project.yaml", ProjectConfig, report),
        agents=_load_required_yaml(root / "config" / "agents.yaml", AgentsConfig, report),
        embeddings=_load_optional_yaml(root / "config" / "embeddings.yaml", EmbeddingsConfig, report),
        characters=_load_required_json(
            root / "memory" / "canon" / "characters.json", CharactersFile, report
        ),
        locations=_load_required_json(
            root / "memory" / "canon" / "locations.json", LocationsFile, report
        ),
        items=_load_required_json(root / "memory" / "canon" / "items.json", ItemsFile, report),
        world=_load_required_json(root / "memory" / "canon" / "world.json", WorldFile, report),
        hidden_truths=_load_required_json(
            root / "memory" / "canon" / "hidden_truths.json", HiddenTruthsFile, report
        ),
        foreshadowing=_load_required_json(
            root / "memory" / "canon" / "foreshadowing.json", ForeshadowingFile, report
        ),
        state=_load_required_json(
            root / "memory" / "state" / "current_state.json", EntityState, report
        ) if include_state else None,
        timeline=_load_required_json(
            root / "memory" / "state" / "timeline.json", TimelineFile, report
        ) if include_state else None,
    )
    return loaded


def _validate_loaded_project(
    report: ValidationReport, root: Path, loaded: LoadedProject, *, include_state: bool
) -> None:
    _validate_schema_versions(report, root, loaded)
    _validate_duplicate_ids(report, root, loaded)
    _validate_agent_names(report, root, loaded.agents)
    _validate_embedding_config(report, root, loaded.embeddings)
    _validate_references(report, root, loaded)
    if include_state:
        _validate_optional_agent_outputs(report, root)
        _validate_chapter_outputs(report, root, loaded)
        _validate_run_and_export_outputs(report, root)
        _validate_session_outputs(report, root)
        _validate_memory_repair_outputs(report, root)
        _validate_consistency_findings(report, root)


def _validate_schema_versions(report: ValidationReport, root: Path, loaded: LoadedProject) -> None:
    files = (
        (root / "project.yaml", loaded.project),
        (root / "config" / "agents.yaml", loaded.agents),
        (root / "config" / "embeddings.yaml", loaded.embeddings),
        (root / "memory" / "canon" / "characters.json", loaded.characters),
        (root / "memory" / "canon" / "locations.json", loaded.locations),
        (root / "memory" / "canon" / "items.json", loaded.items),
        (root / "memory" / "canon" / "world.json", loaded.world),
        (root / "memory" / "canon" / "hidden_truths.json", loaded.hidden_truths),
        (root / "memory" / "canon" / "foreshadowing.json", loaded.foreshadowing),
        (root / "memory" / "state" / "current_state.json", loaded.state),
        (root / "memory" / "state" / "timeline.json", loaded.timeline),
    )
    for path, model in files:
        _validate_model_schema_version(report, path, model)


def _validate_model_schema_version(report: ValidationReport, path: Path, model: object | None) -> None:
    if model is None:
        return
    version = getattr(model, "schema_version", None)
    if version != CURRENT_SCHEMA_VERSION:
        report.error(path, f"unsupported schema_version: {version}")


def _load_required_json(path: Path, model_type: type, report: ValidationReport):
    if not path.exists():
        report.error(path, "required file is missing")
        return None
    try:
        return load_json_model(path, model_type)
    except ValidationError as exc:
        _add_validation_error(report, path, exc)
    except Exception as exc:
        report.error(path, f"could not load JSON: {exc}")
    return None


def _load_required_yaml(path: Path, model_type: type, report: ValidationReport):
    if not path.exists():
        report.error(path, "required file is missing")
        return None
    try:
        return load_yaml_model(path, model_type)
    except ValidationError as exc:
        _add_validation_error(report, path, exc)
    except Exception as exc:
        report.error(path, f"could not load YAML: {exc}")
    return None


def _load_optional_yaml(path: Path, model_type: type, report: ValidationReport):
    if not path.exists():
        report.warning(path, "optional file is missing")
        return None
    try:
        return load_yaml_model(path, model_type)
    except ValidationError as exc:
        _add_validation_error(report, path, exc)
    except Exception as exc:
        report.error(path, f"could not load YAML: {exc}")
    return None


def _add_validation_error(report: ValidationReport, path: Path, exc: ValidationError) -> None:
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        suffix = f" at {loc}" if loc else ""
        report.error(path, f"{error['msg']}{suffix}")


def _validate_duplicate_ids(report: ValidationReport, root: Path, loaded: LoadedProject) -> None:
    if loaded.characters:
        _require_unique(
            report,
            root / "memory" / "canon" / "characters.json",
            [character.id for character in loaded.characters.characters],
            "character id",
        )
    if loaded.locations:
        _require_unique(
            report,
            root / "memory" / "canon" / "locations.json",
            [location.id for location in loaded.locations.locations],
            "location id",
        )
    if loaded.items:
        _require_unique(
            report,
            root / "memory" / "canon" / "items.json",
            [item.id for item in loaded.items.items],
            "item id",
        )
    if loaded.world:
        _require_unique(
            report,
            root / "memory" / "canon" / "world.json",
            [rule.id for rule in loaded.world.world_rules],
            "world rule id",
        )
    if loaded.hidden_truths:
        _require_unique(
            report,
            root / "memory" / "canon" / "hidden_truths.json",
            [truth.id for truth in loaded.hidden_truths.hidden_truths],
            "hidden truth id",
        )
    if loaded.foreshadowing:
        _require_unique(
            report,
            root / "memory" / "canon" / "foreshadowing.json",
            [thread.id for thread in loaded.foreshadowing.foreshadowing_threads],
            "foreshadowing thread id",
        )
    if loaded.timeline:
        _require_unique(
            report,
            root / "memory" / "state" / "timeline.json",
            [event.id for event in loaded.timeline.events],
            "timeline event id",
        )


def _require_unique(
    report: ValidationReport, path: Path, values: Sequence[str], label: str
) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    for duplicate in sorted(duplicates):
        report.error(path, f"duplicate {label}: {duplicate}")


def _validate_agent_names(
    report: ValidationReport, root: Path, agents: AgentsConfig | None
) -> None:
    if not agents:
        return
    path = root / "config" / "agents.yaml"
    required_agents = {
        "orchestrator",
        "inspiration",
        "canon",
        "plot",
        "writer",
        "polish",
        "audit",
        "state_update",
        "chapter_memory",
    }
    if agents.default is None:
        report.warning(path, "default API config is missing; real projects should define config/agents.yaml default")
        missing = sorted(required_agents - set(agents.agents))
        for name in missing:
            report.warning(path, f"recommended agent is missing: {name}")
    else:
        _validate_single_agent_config(report, path, "default", agents.default)
        _validate_agent_env_presence(report, root, path, "default", agents.default)
        if agents.default.provider.lower() == "mock":
            report.warning(path, "default API config uses mock provider; mock is intended for tests only")
    for name, config in agents.agents.items():
        _validate_single_agent_config(report, path, name, config)
        _validate_agent_env_presence(report, root, path, name, config)
        if config.provider and config.provider.lower() == "mock":
            report.warning(path, f"agent {name} uses mock provider; mock is intended for tests only")
        if agents.default is None and isinstance(config, AgentConfigPatch):
            missing_fields = sorted({"provider", "model", "api_key_env"} - set(config.model_dump(exclude_none=True)))
            if missing_fields:
                report.error(
                    path,
                    f"agent {name} is incomplete without a default API config: missing {', '.join(missing_fields)}",
                )


def _validate_single_agent_config(
    report: ValidationReport, path: Path, name: str, config: AgentConfig | AgentConfigPatch
) -> None:
    if config.api_key_env and config.api_key_env.startswith(("sk-", "sk_")):
        report.error(path, f"agent {name} appears to store a raw API key")


def _validate_agent_env_presence(
    report: ValidationReport,
    root: Path,
    path: Path,
    name: str,
    config: AgentConfig | AgentConfigPatch,
) -> None:
    env = load_project_env(root)
    if config.api_key_env and not env.get(config.api_key_env):
        report.warning(path, f"agent {name} api_key_env is not set: {config.api_key_env}")
    if config.base_url_env and config.provider == "openai_compatible" and not env.get(config.base_url_env):
        report.warning(path, f"agent {name} base_url_env is not set: {config.base_url_env}")


def _validate_embedding_config(
    report: ValidationReport, root: Path, embeddings: EmbeddingsConfig | None
) -> None:
    if not embeddings:
        return
    path = root / "config" / "embeddings.yaml"
    supported = {"local_hash", "dashscope", "zhipu", "openai", "openai_compatible"}
    for name, config in embeddings.providers.items():
        provider = config.provider.lower()
        if provider not in supported:
            report.warning(path, f"embedding provider {name} uses unsupported provider: {config.provider}")
        if provider != "local_hash" and not config.api_key_env:
            report.error(path, f"embedding provider {name} requires api_key_env")
        if config.api_key_env and config.api_key_env.startswith(("sk-", "sk_")):
            report.error(path, f"embedding provider {name} appears to store a raw API key")


def _validate_references(report: ValidationReport, root: Path, loaded: LoadedProject) -> None:
    character_ids = _ids(loaded.characters.characters if loaded.characters else [])
    location_ids = _ids(loaded.locations.locations if loaded.locations else [])
    item_ids = _ids(loaded.items.items if loaded.items else [])
    world_ids = _ids(loaded.world.world_rules if loaded.world else [])
    truth_ids = _ids(loaded.hidden_truths.hidden_truths if loaded.hidden_truths else [])
    thread_ids = _ids(loaded.foreshadowing.foreshadowing_threads if loaded.foreshadowing else [])
    entity_ids = character_ids | location_ids | item_ids | world_ids
    canon_context_ids = entity_ids | truth_ids | thread_ids
    timeline_ids = _ids(loaded.timeline.events if loaded.timeline else [])

    if loaded.characters:
        path = root / "memory" / "canon" / "characters.json"
        for character in loaded.characters.characters:
            for relationship in character.relationships:
                if relationship.target_id not in character_ids:
                    report.warning(
                        path,
                        f"character {character.id} relationship references missing character: "
                        f"{relationship.target_id}",
                    )

    if loaded.locations:
        path = root / "memory" / "canon" / "locations.json"
        for location in loaded.locations.locations:
            if location.parent_location_id and location.parent_location_id not in location_ids:
                report.warning(
                    path,
                    f"location {location.id} parent_location_id is missing: "
                    f"{location.parent_location_id}",
                )
            for connected_id in location.connected_location_ids:
                if connected_id not in location_ids:
                    report.warning(
                        path,
                        f"location {location.id} connected_location_ids references missing "
                        f"location: {connected_id}",
                    )

    if loaded.world:
        path = root / "memory" / "canon" / "world.json"
        for rule in loaded.world.world_rules:
            for character_id in rule.known_by_character_ids:
                if character_id not in character_ids:
                    report.warning(
                        path,
                        f"world rule {rule.id} known_by_character_ids references missing "
                        f"character: {character_id}",
                    )

    if loaded.hidden_truths:
        path = root / "memory" / "canon" / "hidden_truths.json"
        for truth in loaded.hidden_truths.hidden_truths:
            for entity_id in truth.related_entity_ids:
                if entity_id not in entity_ids:
                    report.warning(
                        path,
                        f"hidden truth {truth.id} related_entity_ids references missing entity: {entity_id}",
                    )
            for thread_id in truth.foreshadowing_ids:
                if thread_id not in thread_ids:
                    report.warning(
                        path,
                        f"hidden truth {truth.id} foreshadowing_ids references missing thread: {thread_id}",
                    )

    if loaded.foreshadowing:
        path = root / "memory" / "canon" / "foreshadowing.json"
        for thread in loaded.foreshadowing.foreshadowing_threads:
            if thread.hidden_truth_id and thread.hidden_truth_id not in truth_ids:
                report.warning(
                    path,
                    f"foreshadowing thread {thread.id} hidden_truth_id references missing hidden truth: "
                    f"{thread.hidden_truth_id}",
                )
            for entity_id in thread.related_entity_ids:
                if entity_id not in entity_ids:
                    report.warning(
                        path,
                        f"foreshadowing thread {thread.id} related_entity_ids references missing entity: "
                        f"{entity_id}",
                    )
        _validate_hidden_truth_not_reader_visible(report, path, loaded)

    if loaded.state:
        _validate_state_references(
            report,
            root,
            loaded.state,
            entity_ids,
            character_ids,
            location_ids,
            item_ids,
            timeline_ids,
        )

    if loaded.timeline:
        path = root / "memory" / "state" / "timeline.json"
        previous: tuple[int, int, int] | None = None
        for event in loaded.timeline.events:
            current = _event_narrative_key(event)
            if previous and current < previous:
                report.warning(path, "timeline events should be ordered by narrative_position chapter, scene, sequence")
                break
            previous = current
            if event.location_id and event.location_id not in location_ids:
                report.warning(
                    path,
                    f"event {event.id} location_id references missing location: {event.location_id}",
                )
            for participant_id in event.participant_ids:
                if participant_id not in character_ids:
                    report.warning(
                        path,
                        f"event {event.id} participant_ids references missing character: "
                        f"{participant_id}",
                    )
            for change_id in event.state_change_ids:
                if not _state_change_id_exists(root, change_id):
                    report.warning(path, f"event {event.id} references missing state_change_id: {change_id}")
            for cause_id in event.causes:
                if _looks_like_id(cause_id) and cause_id not in timeline_ids:
                    report.warning(path, f"event {event.id} causes references missing event: {cause_id}")
            for effect_id in event.effects:
                if _looks_like_id(effect_id) and effect_id not in timeline_ids:
                    report.warning(path, f"event {event.id} effects references missing event: {effect_id}")

    if loaded.timeline is not None:
        _validate_chapter_plan_references(report, root, canon_context_ids, character_ids, location_ids, timeline_ids)


def _validate_hidden_truth_not_reader_visible(
    report: ValidationReport, path: Path, loaded: LoadedProject
) -> None:
    if not loaded.hidden_truths:
        return
    visible_summaries = []
    for character in loaded.characters.characters if loaded.characters else []:
        visible_summaries.append((character.id, character.reader_visible_summary))
    for location in loaded.locations.locations if loaded.locations else []:
        visible_summaries.append((location.id, location.reader_visible_summary))
    for item in loaded.items.items if loaded.items else []:
        visible_summaries.append((item.id, item.reader_visible_summary))

    for truth in loaded.hidden_truths.hidden_truths:
        hidden_fragments = [truth.description.strip(), truth.title.strip()]
        for entity_id, summary in visible_summaries:
            for fragment in hidden_fragments:
                if fragment and fragment in summary:
                    report.error(
                        path,
                        f"hidden truth {truth.id} appears in reader_visible_summary for {entity_id}",
                    )


def _validate_state_references(
    report: ValidationReport,
    root: Path,
    state: EntityState,
    entity_ids: set[str],
    character_ids: set[str],
    location_ids: set[str],
    item_ids: set[str],
    timeline_ids: set[str],
) -> None:
    path = root / "memory" / "state" / "current_state.json"
    latest_chapter = state.story_position.latest_chapter

    item_holders = {item_state.entity_id: item_state.holder_id for item_state in state.item_states}
    item_locations = {item_state.entity_id: item_state.location_id for item_state in state.item_states}
    possession_owner: dict[str, str] = {}

    for character_state in state.character_states:
        if character_state.entity_id not in character_ids:
            report.warning(
                path,
                f"character state references missing character: {character_state.entity_id}",
            )
        if character_state.location_id and character_state.location_id not in location_ids:
            report.warning(
                path,
                f"character {character_state.entity_id} location_id references missing location: "
                f"{character_state.location_id}",
            )
        if character_state.last_updated_chapter > latest_chapter:
            report.error(
                path,
                f"character {character_state.entity_id} last_updated_chapter is greater than "
                "story_position.latest_chapter",
            )
        for item_id in character_state.possessions:
            previous_owner = possession_owner.get(item_id)
            if previous_owner and previous_owner != character_state.entity_id:
                report.error(
                    path,
                    f"item {item_id} appears in possessions of both {previous_owner} and "
                    f"{character_state.entity_id}",
                )
            possession_owner[item_id] = character_state.entity_id
            if item_id not in item_ids:
                report.warning(
                    path,
                    f"character {character_state.entity_id} possession references missing item: "
                    f"{item_id}",
                )
            elif item_holders.get(item_id) and item_holders[item_id] != character_state.entity_id:
                report.warning(
                    path,
                    f"character {character_state.entity_id} possession conflicts with item "
                    f"{item_id} holder_id {item_holders[item_id]}",
                )
        if _is_dead_health(character_state.health):
            if character_state.goals:
                report.warning(
                    path,
                    f"dead character {character_state.entity_id} still has active goals in current_state",
                )
            if character_state.location_id:
                report.warning(
                    path,
                    f"dead character {character_state.entity_id} still has location_id in current_state",
                )

    for item_state in state.item_states:
        if item_state.entity_id not in item_ids:
            report.warning(path, f"item state references missing item: {item_state.entity_id}")
        if item_state.holder_id and item_state.holder_id not in character_ids:
            report.warning(
                path,
                f"item {item_state.entity_id} holder_id references missing character: "
                f"{item_state.holder_id}",
            )
        if item_state.location_id and item_state.location_id not in location_ids:
            report.warning(
                path,
                f"item {item_state.entity_id} location_id references missing location: "
                f"{item_state.location_id}",
            )
        if item_state.holder_id and item_state.location_id:
            report.error(
                path,
                f"item {item_state.entity_id} has both holder_id and location_id",
            )
        if item_state.last_updated_chapter > latest_chapter:
            report.error(
                path,
                f"item {item_state.entity_id} last_updated_chapter is greater than "
                "story_position.latest_chapter",
            )

    for location_state in state.location_states:
        if location_state.entity_id not in location_ids:
            report.warning(
                path,
                f"location state references missing location: {location_state.entity_id}",
            )
        if location_state.last_updated_chapter > latest_chapter:
            report.error(
                path,
                f"location {location_state.entity_id} last_updated_chapter is greater than "
                "story_position.latest_chapter",
            )
        for event_id in location_state.active_events:
            if _looks_like_id(event_id) and event_id not in timeline_ids:
                report.warning(
                    path,
                    f"location {location_state.entity_id} active_events references missing event: {event_id}",
                )

    for item_id, holder_id in item_holders.items():
        if not holder_id:
            continue
        matching = [
            character_state
            for character_state in state.character_states
            if character_state.entity_id == holder_id and item_id in character_state.possessions
        ]
        if not matching:
            report.warning(
                path,
                f"item {item_id} holder_id {holder_id} is not mirrored in character possessions",
            )
        owner = possession_owner.get(item_id)
        if owner and owner != holder_id:
            report.error(path, f"item {item_id} holder_id {holder_id} conflicts with possession owner {owner}")

    for item_id, location_id in item_locations.items():
        if location_id and location_id not in entity_ids:
            report.warning(path, f"item {item_id} references missing entity location: {location_id}")

    death_chapters: dict[str, int] = {}
    for character_state in state.character_states:
        if _is_dead_health(character_state.health):
            death_chapters[character_state.entity_id] = character_state.last_updated_chapter
    if death_chapters:
        timeline_path = root / "memory" / "state" / "timeline.json"
        try:
            timeline = load_json_model(timeline_path, TimelineFile)
        except Exception:
            timeline = None
        if timeline:
            for event in timeline.events:
                for participant_id in event.participant_ids:
                    death_chapter = death_chapters.get(participant_id)
                    if death_chapter is not None and event.narrative_position.chapter > death_chapter:
                        report.warning(
                            timeline_path,
                            f"character {participant_id} appears in event {event.id} after death state "
                            f"recorded at chapter {death_chapter}",
                        )


def _is_dead_health(health: str | None) -> bool:
    value = (health or "").lower()
    return any(marker in value for marker in ("dead", "deceased", "死亡", "已死", "身亡"))


def _looks_like_id(value: str) -> bool:
    return "_" in value and value == value.lower()


def _validate_chapter_outputs(report: ValidationReport, root: Path, loaded: LoadedProject) -> None:
    chapters_dir = root / "memory" / "chapters"
    if not chapters_dir.exists():
        return
    for chapter_dir in sorted(path for path in chapters_dir.iterdir() if path.is_dir()):
        _validate_single_chapter_output(report, root, chapter_dir, loaded)


def _validate_single_chapter_output(
    report: ValidationReport,
    root: Path,
    chapter_dir: Path,
    loaded: LoadedProject,
) -> None:
    try:
        dir_chapter_number = int(chapter_dir.name)
    except ValueError:
        report.warning(chapter_dir, f"chapter directory name should be numeric: {chapter_dir.name}")
        dir_chapter_number = None

    plan: ChapterPlan | None = None
    audit: AuditReport | None = None
    plan_path = chapter_dir / "plan.json"
    if plan_path.exists():
        try:
            plan = load_json_model(plan_path, ChapterPlan)
            _validate_model_schema_version(report, plan_path, plan)
            if dir_chapter_number is not None and plan.chapter_number != dir_chapter_number:
                report.error(
                    plan_path,
                    f"plan chapter_number {plan.chapter_number} does not match directory {chapter_dir.name}",
                )
        except ValidationError as exc:
            _add_validation_error(report, plan_path, exc)
        except Exception as exc:
            report.error(plan_path, f"could not load chapter plan: {exc}")

    draft_path = chapter_dir / "draft.md"
    polished_path = chapter_dir / "polished.md"
    for markdown_path in (draft_path, polished_path):
        if markdown_path.exists():
            _validate_chapter_markdown(report, markdown_path, plan, dir_chapter_number)

    audit_path = chapter_dir / "audit.json"
    if audit_path.exists():
        try:
            audit = load_json_model(audit_path, AuditReport)
            _validate_model_schema_version(report, audit_path, audit)
            if dir_chapter_number is not None and audit.chapter_number != dir_chapter_number:
                report.error(
                    audit_path,
                    f"audit chapter_number {audit.chapter_number} does not match directory {chapter_dir.name}",
                )
            audited_file = chapter_dir / audit.audited_file
            if audit.audited_file not in {"draft.md", "polished.md"}:
                report.warning(audit_path, f"audited_file is unusual: {audit.audited_file}")
            if not audited_file.exists():
                report.error(audit_path, f"audited_file is missing: {audit.audited_file}")
            else:
                _validate_chapter_markdown(report, audited_file, plan, audit.chapter_number)
        except ValidationError as exc:
            _add_validation_error(report, audit_path, exc)
        except Exception as exc:
            report.error(audit_path, f"could not load audit report: {exc}")

    metadata_path = chapter_dir / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = load_json_model(metadata_path, ChapterMetadata)
            _validate_model_schema_version(report, metadata_path, metadata)
            if dir_chapter_number is not None and metadata.chapter_number != dir_chapter_number:
                report.error(
                    metadata_path,
                    f"metadata chapter_number {metadata.chapter_number} does not match directory {chapter_dir.name}",
                )
            _validate_chapter_metadata_links(report, root, chapter_dir, metadata)
        except ValidationError as exc:
            _add_validation_error(report, metadata_path, exc)
        except Exception as exc:
            report.error(metadata_path, f"could not load chapter metadata: {exc}")

    _validate_optional_chapter_json(report, chapter_dir / "revision_log.json", RevisionLog)
    proposal = _validate_optional_chapter_json(
        report,
        chapter_dir / "state_update_proposal.json",
        StateUpdateProposal,
    )
    if isinstance(proposal, StateUpdateProposal):
        _validate_state_update_proposal_references(report, chapter_dir / "state_update_proposal.json", proposal, loaded)
    _validate_optional_chapter_json(
        report,
        chapter_dir / "state_update_apply_log.json",
        StateUpdateApplyLog,
    )
    chapter_memory = _validate_optional_chapter_json(report, chapter_dir / "chapter_memory.json", ChapterMemory)
    if isinstance(chapter_memory, ChapterMemory):
        for warning in validate_chapter_memory(root, chapter_memory):
            report.warning(chapter_dir / "chapter_memory.json", warning)


def _validate_optional_chapter_json(
    report: ValidationReport,
    path: Path,
    model_type: type,
) -> object | None:
    if not path.exists():
        return None
    try:
        model: object = load_json_model(path, model_type)
        _validate_model_schema_version(report, path, model)
        return model
    except ValidationError as exc:
        _add_validation_error(report, path, exc)
    except Exception as exc:
        report.error(path, f"could not load JSON: {exc}")
    return None


def _validate_run_and_export_outputs(report: ValidationReport, root: Path) -> None:
    runs_dir = root / "runs"
    if runs_dir.exists():
        for run_path in sorted(runs_dir.glob("run_*.json")):
            _validate_optional_chapter_json(report, run_path, AgentRunLog)
    export_manifest_path = root / "exports" / "export_manifest.json"
    _validate_optional_chapter_json(report, export_manifest_path, ExportManifest)


def _validate_session_outputs(report: ValidationReport, root: Path) -> None:
    sessions_dir = root / "memory" / "sessions"
    if sessions_dir.exists():
        for session_dir in sorted(path for path in sessions_dir.glob("session_*") if path.is_dir()):
            _validate_optional_chapter_json(report, session_dir / "session.json", CreationSession)
            _validate_optional_chapter_json(report, session_dir / "outline_proposal.json", CreationOutline)
            _validate_optional_chapter_json(report, session_dir / "approved_outline.json", CreationOutline)
            _validate_optional_chapter_json(report, session_dir / "rewrite_events.json", SessionRewriteEvents)
    archive_dir = root / "memory" / "archive"
    if archive_dir.exists():
        for manifest_path in sorted(archive_dir.glob("session_*/manifest.json")):
            _validate_optional_chapter_json(report, manifest_path, CreationArchiveManifest)


def _validate_memory_repair_outputs(report: ValidationReport, root: Path) -> None:
    repairs_dir = root / "memory" / "repairs"
    if not repairs_dir.exists():
        return
    for repair_dir in sorted(path for path in repairs_dir.glob("repair_*") if path.is_dir()):
        _validate_optional_chapter_json(report, repair_dir / "proposal.json", MemoryRepairProposal)
        _validate_optional_chapter_json(report, repair_dir / "apply_log.json", MemoryRepairApplyLog)


def _validate_consistency_findings(report: ValidationReport, root: Path) -> None:
    result = check_project_consistency(root)
    for finding in result.findings:
        message = f"{finding.id}: {finding.description} Evidence: {finding.quote}"
        if finding.severity in {"high", "critical"}:
            report.error(finding.source, message)
        else:
            report.warning(finding.source, message)


def _validate_state_update_proposal_references(
    report: ValidationReport,
    path: Path,
    proposal: StateUpdateProposal,
    loaded: LoadedProject,
) -> None:
    character_ids = _ids(loaded.characters.characters if loaded.characters else [])
    location_ids = _ids(loaded.locations.locations if loaded.locations else [])
    item_ids = _ids(loaded.items.items if loaded.items else [])
    entity_ids = character_ids | location_ids | item_ids | {"story_position"}
    timeline_ids = _ids(loaded.timeline.events if loaded.timeline else [])
    change_ids = {change.id for change in proposal.state_changes}
    applied_event_ids = _applied_event_ids_for_proposal(path, proposal)
    for change in proposal.state_changes:
        if change.entity_id not in entity_ids:
            report.warning(path, f"state change {change.id} references missing entity: {change.entity_id}")
    for event in proposal.timeline_events:
        if event.id in timeline_ids and event.id not in applied_event_ids:
            report.error(path, f"timeline event id already exists: {event.id}")
        if event.location_id and event.location_id not in location_ids:
            report.warning(path, f"timeline event {event.id} location_id references missing location: {event.location_id}")
        for participant_id in event.participant_ids:
            if participant_id not in character_ids:
                report.warning(
                    path,
                    f"timeline event {event.id} participant_ids references missing character: {participant_id}",
                )
        for change_id in event.state_change_ids:
            if change_id not in change_ids and not _state_change_id_exists(path.parents[3], change_id):
                report.warning(path, f"timeline event {event.id} references missing state_change_id: {change_id}")


def _applied_event_ids_for_proposal(path: Path, proposal: StateUpdateProposal) -> set[str]:
    apply_log_path = path.with_name("state_update_apply_log.json")
    if not apply_log_path.exists():
        return set()
    try:
        apply_log = load_json_model(apply_log_path, StateUpdateApplyLog)
    except Exception:
        return set()
    if apply_log.status != "applied" or apply_log.chapter_number != proposal.chapter_number:
        return set()
    return {event.id for event in proposal.timeline_events}


def _validate_chapter_markdown(
    report: ValidationReport,
    path: Path,
    plan: ChapterPlan | None,
    expected_chapter_number: int | None,
) -> None:
    try:
        metadata = _read_markdown_metadata(path)
    except Exception as exc:
        report.warning(path, f"could not read front matter: {exc}")
        return
    chapter_number = metadata.get("chapter_number")
    if expected_chapter_number is not None and chapter_number not in {None, expected_chapter_number}:
        report.error(path, f"front matter chapter_number {chapter_number} does not match {expected_chapter_number}")
    if plan and metadata.get("title") and metadata.get("title") != plan.title:
        report.warning(path, "front matter title differs from plan title")
    based_on = metadata.get("based_on")
    if path.name == "draft.md" and based_on not in {None, "plan.json"}:
        report.warning(path, f"draft based_on should be plan.json, got {based_on}")
    if path.name == "polished.md" and based_on not in {None, "draft.md"}:
        report.warning(path, f"polished based_on should be draft.md, got {based_on}")


def _validate_chapter_metadata_links(
    report: ValidationReport,
    root: Path,
    chapter_dir: Path,
    metadata: ChapterMetadata,
) -> None:
    path = chapter_dir / "metadata.json"
    field_paths = {
        "plan_path": metadata.plan_path,
        "draft_path": metadata.draft_path,
        "polished_path": metadata.polished_path,
        "audit_path": metadata.audit_path,
        "state_update_proposal_path": metadata.state_update_proposal_path,
        "state_update_apply_log_path": metadata.state_update_apply_log_path,
    }
    for field_name, relative_path in field_paths.items():
        if relative_path and not (root / relative_path).exists():
            report.error(path, f"{field_name} references missing file: {relative_path}")
    if metadata.status == "accepted":
        polished_path = root / metadata.polished_path if metadata.polished_path else chapter_dir / "polished.md"
        if not polished_path.exists():
            report.error(path, "accepted chapter is missing polished.md")
        else:
            try:
                front_matter = _read_markdown_metadata(polished_path)
                if front_matter.get("status") != "accepted":
                    report.error(path, "accepted metadata conflicts with polished.md front matter status")
            except Exception as exc:
                report.error(path, f"could not read accepted polished.md front matter: {exc}")
        if metadata.accepted_at is None:
            report.error(path, "accepted chapter metadata must include accepted_at")


def _read_markdown_metadata(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    marker = "\n---"
    end = text.find(marker, 4)
    if end == -1:
        return {}
    data = yaml.safe_load(text[4:end])
    return data if isinstance(data, dict) else {}


def _validate_optional_agent_outputs(report: ValidationReport, root: Path) -> None:
    inspiration_path = root / "memory" / "inspiration.json"
    if inspiration_path.exists():
        try:
            brief = load_json_model(inspiration_path, InspirationBrief)
            _validate_model_schema_version(report, inspiration_path, brief)
        except ValidationError as exc:
            _add_validation_error(report, inspiration_path, exc)
        except Exception as exc:
            report.error(inspiration_path, f"could not load inspiration brief: {exc}")


def _validate_chapter_plan_references(
    report: ValidationReport,
    root: Path,
    entity_ids: set[str],
    character_ids: set[str],
    location_ids: set[str],
    timeline_ids: set[str],
) -> None:
    chapters_dir = root / "memory" / "chapters"
    if not chapters_dir.exists():
        return
    for plan_path in sorted(chapters_dir.glob("*/plan.json")):
        try:
            plan = load_json_model(plan_path, ChapterPlan)
        except Exception:
            continue
        if plan.required_context:
            for entity_id in plan.required_context.canon_entity_ids:
                if entity_id not in entity_ids:
                    report.warning(plan_path, f"required_context references missing canon entity: {entity_id}")
            for entity_id in plan.required_context.state_entity_ids:
                if entity_id not in entity_ids:
                    report.warning(plan_path, f"required_context references missing state entity: {entity_id}")
            for event_id in plan.required_context.timeline_event_ids:
                if event_id not in timeline_ids:
                    report.warning(plan_path, f"required_context references missing timeline event: {event_id}")
        for scene in plan.scenes:
            if scene.location_id and scene.location_id not in location_ids:
                report.warning(
                    plan_path,
                    f"scene {scene.scene_number} location_id references missing location: "
                    f"{scene.location_id}",
                )
            for participant_id in scene.participant_ids:
                if participant_id not in character_ids:
                    report.warning(
                        plan_path,
                        f"scene {scene.scene_number} participant_ids references missing character: "
                    f"{participant_id}",
                )


def _event_narrative_key(event) -> tuple[int, int, int]:
    narrative = event.narrative_position
    return (narrative.chapter, narrative.scene or 0, narrative.sequence or 0)


def _state_change_id_exists(root: Path, change_id: str) -> bool:
    chapters_dir = root / "memory" / "chapters"
    if not chapters_dir.exists():
        return False
    for proposal_path in chapters_dir.glob("*/state_update_proposal.json"):
        try:
            data = load_json_model(proposal_path, StateUpdateProposal)
        except Exception:
            continue
        if any(change.id == change_id for change in data.state_changes):
            return True
    return False


def _ids(items: Iterable[object]) -> set[str]:
    return {getattr(item, "id") for item in items}
