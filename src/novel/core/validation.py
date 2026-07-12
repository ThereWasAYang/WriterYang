from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
from typing import Iterable, Sequence

from pydantic import ValidationError
import yaml

from novel.core.agent_defaults import PROFILE_NAMES, TASK_TO_PROFILE
from novel.core.artifact_store import sha256_file
from novel.core.chapter_memory import validate_chapter_memory
from novel.core.consistency import ConsistencyResult, check_canon_consistency, check_project_consistency
from novel.core.env import load_project_env
from novel.core.io import load_json_model, load_yaml_model
from novel.core.contracts import AcceptanceCommit, CURRENT_SCHEMA_VERSION
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
    MemoryChangeClarificationSession,
    MemoryRepairProposal,
    ProjectConfig,
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
    _validate_canon_consistency_findings(report, root)
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
    _validate_provider_config_entries(report, root, loaded.agents)
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
        report.error(path, f"不支持的 schema_version：{version}")


def _load_required_json(path: Path, model_type: type, report: ValidationReport):
    if not path.exists():
        report.error(path, "缺少必需文件")
        return None
    try:
        return load_json_model(path, model_type)
    except ValidationError as exc:
        _add_validation_error(report, path, exc)
    except Exception as exc:
        report.error(path, f"无法读取 JSON 文件（{exc.__class__.__name__}）")
    return None


def _load_required_yaml(path: Path, model_type: type, report: ValidationReport):
    if not path.exists():
        report.error(path, "缺少必需文件")
        return None
    try:
        return load_yaml_model(path, model_type)
    except ValidationError as exc:
        _add_validation_error(report, path, exc)
    except Exception as exc:
        report.error(path, f"无法读取 YAML 文件（{exc.__class__.__name__}）")
    return None


def _load_optional_yaml(path: Path, model_type: type, report: ValidationReport):
    if not path.exists():
        report.warning(path, "可选文件不存在")
        return None
    try:
        return load_yaml_model(path, model_type)
    except ValidationError as exc:
        _add_validation_error(report, path, exc)
    except Exception as exc:
        report.error(path, f"无法读取 YAML 文件（{exc.__class__.__name__}）")
    return None


def _add_validation_error(report: ValidationReport, path: Path, exc: ValidationError) -> None:
    for error in exc.errors():
        if tuple(error["loc"]) == ("schema_version",):
            report.error(path, f"不支持的 schema_version：{error.get('input')}")
            continue
        loc = ".".join(str(part) for part in error["loc"])
        suffix = f"：{loc}" if loc else ""
        report.error(path, f"{_pydantic_error_message(error)}{suffix}")


def _pydantic_error_message(error: Mapping[str, object]) -> str:
    error_type = str(error.get("type") or "")
    message = str(error.get("msg") or "")
    if error_type == "missing":
        return "缺少必填字段"
    if error_type.startswith("string_type"):
        return "字段应为字符串"
    if error_type.startswith("int_type") or error_type.startswith("int_parsing"):
        return "字段应为整数"
    if error_type.startswith("bool_type") or error_type.startswith("bool_parsing"):
        return "字段应为布尔值"
    if error_type.startswith("list_type"):
        return "字段应为数组"
    if error_type.startswith("dict_type") or error_type.startswith("model_attributes_type"):
        return "字段应为对象"
    if error_type.startswith("literal_error"):
        return "字段值不在允许范围内"
    if error_type.startswith("string_too_short"):
        return "字符串长度不足"
    if error_type.startswith("string_pattern_mismatch"):
        return "字符串格式不符合要求"
    if error_type.startswith("value_error"):
        reason = None
        context = error.get("ctx")
        if isinstance(context, Mapping):
            context_error = context.get("error")
            if context_error is not None:
                reason = str(context_error)
        if not reason and message.startswith("Value error, "):
            reason = message.removeprefix("Value error, ")
        if reason:
            return f"字段值未通过业务校验：{reason}"
        return "字段值未通过业务校验"
    return f"字段未通过 schema 校验（{message or error_type}）"


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
        report.error(path, f"重复的 {label}：{duplicate}")


def _validate_provider_config_entries(
    report: ValidationReport, root: Path, agents: AgentsConfig | None
) -> None:
    if not agents:
        return
    path = root / "config" / "agents.yaml"
    required_profiles = set(PROFILE_NAMES)
    if agents.default is None:
        report.warning(path, "缺少 default API 配置；真实项目应在 config/agents.yaml 中定义 default")
        missing = sorted(required_profiles - set(agents.profiles))
        for name in missing:
            report.warning(path, f"缺少推荐 profile：{name}")
    else:
        _validate_single_provider_config(report, path, "default", agents.default)
        _validate_provider_env_presence(report, root, path, "default", agents.default)
        if agents.default.provider.lower() == "mock":
            report.warning(path, "default API 配置使用 mock provider；mock 仅用于测试")
    for name, config in agents.profiles.items():
        _validate_single_provider_config(report, path, f"profile {name}", config)
        if getattr(config, "inherit_default", False) is True:
            if agents.default is None:
                report.error(path, f"profile {name} 继承 default，但 default API 配置不存在")
            continue
        if isinstance(config, AgentConfigPatch):
            provided = set(config.model_dump(exclude_none=True)) - {"inherit_default"}
            missing_fields = sorted({"provider", "model", "api_key_env"} - provided)
            detail = f"；缺少 {', '.join(missing_fields)}" if missing_fields else ""
            report.error(
                path,
                f"profile {name} 配置不完整；请设置 inherit_default: true，或提供完整独立配置{detail}",
            )
            continue
        _validate_provider_env_presence(report, root, path, f"profile {name}", config)
        if config.provider and config.provider.lower() == "mock":
            report.warning(path, f"profile {name} 使用 mock provider；mock 仅用于测试")
    unknown_tasks = sorted(set(agents.tasks) - set(TASK_TO_PROFILE))
    for name in unknown_tasks:
        report.error(path, f"未知 task 配置：{name}")
    for name, config in agents.tasks.items():
        _validate_single_provider_config(report, path, f"task {name}", config)
        if getattr(config, "inherit_default", False) is True:
            report.warning(path, f"task {name} 设置了 inherit_default；task 配置已经在对应 profile 上应用")


def _validate_single_provider_config(
    report: ValidationReport, path: Path, name: str, config: AgentConfig | AgentConfigPatch
) -> None:
    if config.api_key_env and config.api_key_env.startswith(("sk-", "sk_")):
        report.error(path, f"provider 配置 {name} 疑似直接保存了原始 API key")


def _validate_provider_env_presence(
    report: ValidationReport,
    root: Path,
    path: Path,
    name: str,
    config: AgentConfig | AgentConfigPatch,
) -> None:
    env = load_project_env(root)
    if config.api_key_env and not env.get(config.api_key_env):
        report.warning(path, f"provider 配置 {name} 的 api_key_env 未设置：{config.api_key_env}")
    if config.base_url_env and config.provider == "openai_compatible" and not env.get(config.base_url_env):
        report.warning(path, f"provider 配置 {name} 的 base_url_env 未设置：{config.base_url_env}")


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
            report.warning(path, f"embedding provider {name} 使用了不支持的 provider：{config.provider}")
        if provider != "local_hash" and not config.api_key_env:
            report.error(path, f"embedding provider {name} 缺少 api_key_env")
        if config.api_key_env and config.api_key_env.startswith(("sk-", "sk_")):
            report.error(path, f"embedding provider {name} 疑似直接保存了原始 API key")


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
                        f"角色 {character.id} 的 relationship 引用了不存在的角色："
                        f"{relationship.target_id}",
                    )

    if loaded.locations:
        path = root / "memory" / "canon" / "locations.json"
        for location in loaded.locations.locations:
            if location.parent_location_id and location.parent_location_id not in location_ids:
                report.warning(
                    path,
                    f"地点 {location.id} 的 parent_location_id 不存在："
                    f"{location.parent_location_id}",
                )
            for connected_id in location.connected_location_ids:
                if connected_id not in location_ids:
                    report.warning(
                        path,
                        f"地点 {location.id} 的 connected_location_ids 引用了不存在的地点：{connected_id}",
                    )

    if loaded.world:
        path = root / "memory" / "canon" / "world.json"
        for rule in loaded.world.world_rules:
            for character_id in rule.known_by_character_ids:
                if character_id not in character_ids:
                    report.warning(
                        path,
                        f"世界规则 {rule.id} 的 known_by_character_ids 引用了不存在的角色：{character_id}",
                    )

    if loaded.hidden_truths:
        path = root / "memory" / "canon" / "hidden_truths.json"
        for truth in loaded.hidden_truths.hidden_truths:
            for entity_id in truth.related_entity_ids:
                if entity_id not in entity_ids:
                    report.warning(
                        path,
                        f"隐藏真相 {truth.id} 的 related_entity_ids 引用了不存在的实体：{entity_id}",
                    )
            for thread_id in truth.foreshadowing_ids:
                if thread_id not in thread_ids:
                    report.warning(
                        path,
                        f"隐藏真相 {truth.id} 的 foreshadowing_ids 引用了不存在的伏笔线：{thread_id}",
                    )

    if loaded.foreshadowing:
        path = root / "memory" / "canon" / "foreshadowing.json"
        for thread in loaded.foreshadowing.foreshadowing_threads:
            if thread.hidden_truth_id and thread.hidden_truth_id not in truth_ids:
                report.warning(
                    path,
                    f"伏笔线 {thread.id} 的 hidden_truth_id 引用了不存在的隐藏真相："
                    f"{thread.hidden_truth_id}",
                )
            for entity_id in thread.related_entity_ids:
                if entity_id not in entity_ids:
                    report.warning(
                        path,
                        f"伏笔线 {thread.id} 的 related_entity_ids 引用了不存在的实体："
                        f"{entity_id}",
                    )

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
        for event in loaded.timeline.events:
            if event.location_id and event.location_id not in location_ids:
                report.warning(
                    path,
                    f"Timeline 事件 {event.id} 的 location_id 引用了不存在的地点：{event.location_id}",
                )
            for participant_id in event.participant_ids:
                if participant_id not in character_ids:
                    report.warning(
                        path,
                        f"Timeline 事件 {event.id} 的 participant_ids 引用了不存在的角色："
                        f"{participant_id}",
                    )
            for change_id in event.state_change_ids:
                if not _state_change_id_exists(root, change_id):
                    report.warning(path, f"Timeline 事件 {event.id} 引用了不存在的 state_change_id：{change_id}")

    if loaded.timeline is not None:
        _validate_chapter_plan_references(report, root, canon_context_ids, character_ids, location_ids, timeline_ids)


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

    item_locations = {item_state.entity_id: item_state.location_id for item_state in state.item_states}

    for character_state in state.character_states:
        if character_state.entity_id not in character_ids:
            report.warning(
                path,
                f"character state 引用了不存在的角色：{character_state.entity_id}",
            )
        if character_state.location_id and character_state.location_id not in location_ids:
            report.warning(
                path,
                f"角色状态 {character_state.entity_id} 的 location_id 引用了不存在的地点："
                f"{character_state.location_id}",
            )
        if character_state.last_updated_chapter > latest_chapter:
            report.error(
                path,
                f"角色状态 {character_state.entity_id} 的 last_updated_chapter 大于 "
                "story_position.latest_chapter",
            )
        for item_id in character_state.possessions:
            if item_id not in item_ids:
                report.warning(
                    path,
                    f"角色状态 {character_state.entity_id} 的 possession 引用了不存在的物品："
                    f"{item_id}",
                )
        if _is_dead_health(character_state.health):
            if character_state.goals:
                report.warning(
                    path,
                    f"已死亡角色 {character_state.entity_id} 在 current_state 中仍有 active goals",
                )
            if character_state.location_id:
                report.warning(
                    path,
                    f"已死亡角色 {character_state.entity_id} 在 current_state 中仍有 location_id",
                )

    for item_state in state.item_states:
        if item_state.entity_id not in item_ids:
            report.warning(path, f"item state 引用了不存在的物品：{item_state.entity_id}")
        if item_state.holder_id and item_state.holder_id not in character_ids:
            report.warning(
                path,
                f"物品状态 {item_state.entity_id} 的 holder_id 引用了不存在的角色："
                f"{item_state.holder_id}",
            )
        if item_state.location_id and item_state.location_id not in location_ids:
            report.warning(
                path,
                f"物品状态 {item_state.entity_id} 的 location_id 引用了不存在的地点："
                f"{item_state.location_id}",
            )
        if item_state.last_updated_chapter > latest_chapter:
            report.error(
                path,
                f"物品状态 {item_state.entity_id} 的 last_updated_chapter 大于 "
                "story_position.latest_chapter",
            )

    for location_state in state.location_states:
        if location_state.entity_id not in location_ids:
            report.warning(
                path,
                f"location state 引用了不存在的地点：{location_state.entity_id}",
            )
        if location_state.last_updated_chapter > latest_chapter:
            report.error(
                path,
                f"地点状态 {location_state.entity_id} 的 last_updated_chapter 大于 "
                "story_position.latest_chapter",
            )
        for event_id in location_state.active_events:
            if _looks_like_id(event_id) and event_id not in timeline_ids:
                report.warning(
                    path,
                    f"地点状态 {location_state.entity_id} 的 active_events 引用了不存在的事件：{event_id}",
                )

    for item_id, location_id in item_locations.items():
        if location_id and location_id not in entity_ids:
            report.warning(path, f"物品 {item_id} 引用了不存在的实体地点：{location_id}")

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
                narrative = event.narrative_position
                if narrative is None:
                    continue
                for participant_id in event.participant_ids:
                    death_chapter = death_chapters.get(participant_id)
                    if death_chapter is not None and narrative.chapter > death_chapter:
                        report.warning(
                            timeline_path,
                            f"角色 {participant_id} 在死亡记录章节 {death_chapter} 之后仍出现在事件 {event.id} 中",
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
        report.warning(chapter_dir, f"章节目录名应为数字：{chapter_dir.name}")
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
                    f"plan.json 的 chapter_number={plan.chapter_number} 与目录 {chapter_dir.name} 不一致",
                )
        except ValidationError as exc:
            _add_validation_error(report, plan_path, exc)
        except Exception as exc:
            report.error(plan_path, f"无法读取 chapter plan（{exc.__class__.__name__}）")

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
                    f"audit.json 的 chapter_number={audit.chapter_number} 与目录 {chapter_dir.name} 不一致",
                )
            audited_file = chapter_dir / audit.audited_file
            if audit.audited_file not in {"draft.md", "polished.md"}:
                report.warning(audit_path, f"audited_file 不是常规章节正文文件：{audit.audited_file}")
            if not audited_file.exists():
                report.error(audit_path, f"audited_file 不存在：{audit.audited_file}")
            else:
                _validate_chapter_markdown(report, audited_file, plan, audit.chapter_number)
                if sha256_file(audited_file) != audit.audited_sha256:
                    report.error(audit_path, "audited_sha256 与当前 audited_file 内容不一致，Audit 已过期")
        except ValidationError as exc:
            _add_validation_error(report, audit_path, exc)
        except Exception as exc:
            report.error(audit_path, f"无法读取 audit report（{exc.__class__.__name__}）")

    metadata_path = chapter_dir / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = load_json_model(metadata_path, ChapterMetadata)
            _validate_model_schema_version(report, metadata_path, metadata)
            if dir_chapter_number is not None and metadata.chapter_number != dir_chapter_number:
                report.error(
                    metadata_path,
                    f"metadata.json 的 chapter_number={metadata.chapter_number} 与目录 {chapter_dir.name} 不一致",
                )
            _validate_chapter_metadata_links(report, root, chapter_dir, metadata)
        except ValidationError as exc:
            _add_validation_error(report, metadata_path, exc)
        except Exception as exc:
            report.error(metadata_path, f"无法读取 chapter metadata（{exc.__class__.__name__}）")

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
        report.error(path, f"无法读取 JSON 文件（{exc.__class__.__name__}）")
    return None


def _validate_run_and_export_outputs(report: ValidationReport, root: Path) -> None:
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
    clarifications_dir = repairs_dir / "clarifications"
    if clarifications_dir.exists():
        for clarification_path in sorted(clarifications_dir.glob("clarify_*/session.json")):
            _validate_optional_chapter_json(report, clarification_path, MemoryChangeClarificationSession)


def _validate_consistency_findings(report: ValidationReport, root: Path) -> None:
    _append_consistency_findings(report, check_project_consistency(root))


def _validate_canon_consistency_findings(report: ValidationReport, root: Path) -> None:
    _append_consistency_findings(report, check_canon_consistency(root))


def _append_consistency_findings(report: ValidationReport, result: ConsistencyResult) -> None:
    for finding in result.findings:
        message = f"{finding.id}: {finding.description} 证据：{finding.quote}"
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
            report.warning(path, f"state change {change.id} 引用了不存在的实体：{change.entity_id}")
    for event in proposal.timeline_events:
        if event.id in timeline_ids and event.id not in applied_event_ids:
            report.error(path, f"timeline event id 已存在：{event.id}")
        if event.location_id and event.location_id not in location_ids:
            report.warning(path, f"timeline event {event.id} 的 location_id 引用了不存在的地点：{event.location_id}")
        for participant_id in event.participant_ids:
            if participant_id not in character_ids:
                report.warning(
                    path,
                    f"timeline event {event.id} 的 participant_ids 引用了不存在的角色：{participant_id}",
                )
        for change_id in event.state_change_ids:
            if change_id not in change_ids and not _state_change_id_exists(path.parents[3], change_id):
                report.warning(path, f"timeline event {event.id} 引用了不存在的 state_change_id：{change_id}")


def _applied_event_ids_for_proposal(path: Path, proposal: StateUpdateProposal) -> set[str]:
    acceptance_path = path.with_name("acceptance.json")
    if acceptance_path.is_file():
        try:
            acceptance = load_json_model(acceptance_path, AcceptanceCommit)
            if acceptance.state_proposal.sha256 == sha256_file(path):
                return {event.id for event in proposal.timeline_events}
        except Exception:
            pass
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
        report.warning(path, f"无法读取 front matter（{exc.__class__.__name__}）")
        return
    chapter_number = metadata.get("chapter_number")
    if expected_chapter_number is not None and chapter_number not in {None, expected_chapter_number}:
        report.error(path, f"front matter chapter_number={chapter_number} 与 {expected_chapter_number} 不一致")
    if plan and metadata.get("title") and metadata.get("title") != plan.title:
        report.warning(path, "front matter title 与 plan title 不一致")
    based_on = metadata.get("based_on")
    if path.name == "draft.md" and based_on not in {None, "plan.json"}:
        report.warning(path, f"draft.md 的 based_on 应为 plan.json，当前为 {based_on}")
    if path.name == "polished.md" and based_on not in {None, "draft.md"}:
        report.warning(path, f"polished.md 的 based_on 应为 draft.md，当前为 {based_on}")


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
            report.error(path, f"{field_name} 引用了不存在的文件：{relative_path}")
    if metadata.status == "accepted":
        polished_path = root / metadata.polished_path if metadata.polished_path else chapter_dir / "polished.md"
        if not polished_path.exists():
            report.error(path, "已认可章节缺少 accepted 正文")
        if metadata.accepted_at is None:
            report.error(path, "已认可章节 metadata 必须包含 accepted_at")


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
            report.error(inspiration_path, f"无法读取 inspiration brief（{exc.__class__.__name__}）")


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
                    report.warning(plan_path, f"required_context 引用了不存在的 canon entity：{entity_id}")
            for entity_id in plan.required_context.state_entity_ids:
                if entity_id not in entity_ids:
                    report.warning(plan_path, f"required_context 引用了不存在的 state entity：{entity_id}")
            for event_id in plan.required_context.timeline_event_ids:
                if event_id not in timeline_ids:
                    report.warning(plan_path, f"required_context 引用了不存在的 timeline event：{event_id}")
        for scene in plan.scenes:
            if scene.location_id and scene.location_id not in location_ids:
                report.warning(
                    plan_path,
                    f"scene {scene.scene_number} 的 location_id 引用了不存在的地点："
                    f"{scene.location_id}",
                )
            for participant_id in scene.participant_ids:
                if participant_id not in character_ids:
                    report.warning(
                        plan_path,
                        f"scene {scene.scene_number} 的 participant_ids 引用了不存在的角色："
                    f"{participant_id}",
                )


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
