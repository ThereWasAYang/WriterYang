from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ValidationError

from novel.core.agent_output import (
    AgentInvocationContext,
    AgentOutputContract,
    AgentOutputContractError,
    generate_with_output_guard,
)
from novel.core.io import atomic_write_json, atomic_write_model_json, atomic_write_text, backup_file, load_json, load_json_model
from novel.core.management import record_management_event
from novel.core.prompts import load_prompt_template
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.schemas import (
    Character,
    CharactersFile,
    EntityState,
    ForeshadowingThread,
    ForeshadowingFile,
    HiddenTruth,
    HiddenTruthsFile,
    Item,
    ItemsFile,
    Location,
    LocationsFile,
    MemoryChangeDomain,
    MemoryChangeClarificationDecision,
    MemoryChangeClarificationSession,
    MemoryChangeConversationTurn,
    MemoryChangeFollowupAction,
    MemoryChangeImpact,
    MemoryChangeKind,
    MemoryChangeStage,
    MemoryRepairDecision,
    MemoryRepairApplyLog,
    MemoryRepairOperation,
    MemoryRepairProposal,
    TimelineFile,
    WorldRule,
    WorldFile,
)
from novel.core.validation import validate_project


class MemoryRepairError(RuntimeError):
    """Raised when a memory repair proposal cannot be created or applied safely."""


@dataclass(frozen=True)
class MemoryRepairSuggestResult:
    proposal: MemoryRepairProposal
    proposal_path: Path
    markdown_path: Path


@dataclass(frozen=True)
class MemoryRepairApplyResult:
    proposal: MemoryRepairProposal
    apply_log: MemoryRepairApplyLog
    apply_log_path: Path


@dataclass(frozen=True)
class SettingChangeSuggestionResult:
    status: Literal["proposal_ready", "needs_clarification"]
    proposal_result: MemoryRepairSuggestResult | None = None
    clarification: MemoryChangeClarificationSession | None = None


ALLOWED_MEMORY_FILES: dict[str, type[BaseModel]] = {
    "memory/state/timeline.json": TimelineFile,
    "memory/state/current_state.json": EntityState,
    "memory/canon/characters.json": CharactersFile,
    "memory/canon/locations.json": LocationsFile,
    "memory/canon/items.json": ItemsFile,
    "memory/canon/world.json": WorldFile,
    "memory/canon/hidden_truths.json": HiddenTruthsFile,
    "memory/canon/foreshadowing.json": ForeshadowingFile,
}

FILE_DOMAINS: dict[str, MemoryChangeDomain] = {
    "memory/canon/characters.json": "characters",
    "memory/canon/locations.json": "locations",
    "memory/canon/items.json": "items",
    "memory/canon/world.json": "world",
    "memory/canon/hidden_truths.json": "hidden_truths",
    "memory/canon/foreshadowing.json": "foreshadowing",
    "memory/state/current_state.json": "current_state",
    "memory/state/timeline.json": "timeline",
}

FILE_COLLECTION_KEYS: dict[str, str] = {
    "memory/canon/characters.json": "characters",
    "memory/canon/locations.json": "locations",
    "memory/canon/items.json": "items",
    "memory/canon/world.json": "world_rules",
    "memory/canon/hidden_truths.json": "hidden_truths",
    "memory/canon/foreshadowing.json": "foreshadowing_threads",
}

STATE_COLLECTION_KEYS = {"character_states", "item_states", "location_states"}

SCANNED_IMPACT_SUFFIXES = {".json", ".md"}

COLLECTION_FIELD_HINTS: dict[str, list[str]] = {
    "memory/canon/characters.json": list(Character.model_fields),
    "memory/canon/locations.json": list(Location.model_fields),
    "memory/canon/items.json": list(Item.model_fields),
    "memory/canon/world.json": list(WorldRule.model_fields),
    "memory/canon/hidden_truths.json": list(HiddenTruth.model_fields),
    "memory/canon/foreshadowing.json": list(ForeshadowingThread.model_fields),
}

COLLECTION_SCHEMA_HINTS: dict[str, str] = {
    "memory/canon/characters.json": (
        "strict add value schema: Character {id, name, role, reader_visible_summary, aliases[], private_author_notes?, "
        "appearance: object|null, personality: object|null, relationships: Relationship[], abilities: Ability[], secrets: Secret[], tags[]}.\n"
        "Character.role is narrative role only: use 主角, 主要人物, 配角, 次要人物 by default; legacy protagonist/supporting/minor/antagonist are compatible.\n"
        "Never put family rank, sect identity, profession, or jianghu identity in role; phrases such as 谢家长女, 谢家次子, 张家幼女, 唐门二房之女, 江湖散人, 武当俗家弟子 must go into tags and summary/notes.\n"
        "Ability {name: string, description: string, limitations?: string|null}; never use string arrays for abilities.\n"
        "Secret {id: snake_case, visibility: reader_visible|hidden|partially_revealed, description: string, planned_reveal?: string|null}; never use string arrays for secrets."
    ),
    "memory/canon/locations.json": (
        "strict add value schema: Location {id, name, type, reader_visible_summary, private_author_notes?, "
        "parent_location_id?, connected_location_ids[], rules: LocationRule[], tags[]}.\n"
        "LocationRule {id?: snake_case|null, description: string, visibility: reader_visible|hidden|partially_revealed}; never use string arrays for rules."
    ),
    "memory/canon/items.json": (
        "strict add value schema: Item {id, name, type, reader_visible_summary, private_author_notes?, origin?, special_properties: SpecialProperty[], tags[]}.\n"
        "SpecialProperty {description: string, visibility: reader_visible|hidden|partially_revealed}; never use string arrays for special_properties."
    ),
    "memory/canon/world.json": (
        "strict add value schema: WorldRule {id, name, description, visibility: reader_visible|hidden|partially_revealed, limitations[], known_by_character_ids[]}.\n"
        "Visibility enum is exactly reader_visible | hidden | partially_revealed; never use visible."
    ),
    "memory/canon/hidden_truths.json": (
        "strict add value schema: HiddenTruth {id, title, description, visibility: reader_visible|hidden|partially_revealed, "
        "importance: low|medium|high|critical, related_entity_ids[], planned_reveal: PlannedReveal|null, foreshadowing_ids[]}.\n"
        "PlannedReveal {chapter: integer >= 1, method?: string|null}; never use string values such as 后期 for planned_reveal.\n"
        "Importance enum is exactly low | medium | high | critical; never use major."
    ),
    "memory/canon/foreshadowing.json": (
        "strict add value schema: ForeshadowingThread {id, type, title, introduced_in_chapter: integer >= 1, description, status, "
        "importance: low|medium|high|critical, reader_visible?: bool|null, hidden_truth?, hidden_truth_id?, planned_payoff: PlannedPayoff|null, related_entity_ids[]}.\n"
        "PlannedPayoff {chapter: integer >= 1, description: string}; never use string values for planned_payoff or introduced_in_chapter."
    ),
}

COLLECTION_PATH_FILES: dict[str, str] = {
    collection_key: rel_path
    for rel_path, collection_key in FILE_COLLECTION_KEYS.items()
}

POINTER_PATH_FILES: dict[str, str] = {
    **COLLECTION_PATH_FILES,
    "events": "memory/state/timeline.json",
    "character_states": "memory/state/current_state.json",
    "item_states": "memory/state/current_state.json",
    "location_states": "memory/state/current_state.json",
}

SETTING_CHANGE_MAPPING_RULES = """设定变更默认映射规则：
- 文件、字段、visibility 和 JSON Pointer 由系统根据下方结构负责选择，不要要求用户提供。
- 新人物/明确姓名默认写入 memory/canon/characters.json，新增路径使用 /characters/-。
- Character.role 只表示叙事角色；新增人物默认使用中文叙事角色值：主角、主要人物、配角、次要人物。
- 用户说“主要人物”时默认 role="主要人物"；明确主角用 role="主角"；明确次要/背景用 role="次要人物"；未明确时用 role="配角"。
- 家族身份、门派身份、排行、职业/江湖身份必须写入 tags，并可写入 reader_visible_summary 或 private_author_notes；不要把“谢家长女”“谢家次子”“张家幼女”“唐门二房之女”“江湖散人”“武当俗家弟子”等写入 role。
- 新地点、宅邸、村庄、宫殿、门派驻地默认写入 memory/canon/locations.json，新增路径使用 /locations/-。
- 家族、门派、势力背景、时代背景、武学体系、世界规则默认写入 memory/canon/world.json，新增路径使用 /world_rules/-。
- 物品、武器、信物、法器默认写入 memory/canon/items.json，新增路径使用 /items/-。
- 隐藏设定、真相、秘密、暂不揭晓内容默认写入 memory/canon/hidden_truths.json，visibility 默认 hidden，新增路径使用 /hidden_truths/-。
- 伏笔、线索、开篇埋线默认写入 memory/canon/foreshadowing.json，新增路径使用 /foreshadowing_threads/-。
- 只有 exact id、exact name 或 exact alias 匹配时才修改已有实体；不要把新姓名近似联想到现有角色。
- 无精确匹配且用户没有明确要求替换/删除/合并时，按新增实体处理。
"""

NARRATIVE_CHARACTER_ROLES = {
    "主角",
    "主人公",
    "男主",
    "女主",
    "主要人物",
    "核心人物",
    "配角",
    "重要配角",
    "次要人物",
    "背景人物",
    "反派",
    "对手",
    "盟友",
    "导师",
    "线索人物",
    "群像主角",
    "protagonist",
    "main",
    "main_character",
    "supporting",
    "minor",
    "antagonist",
}

CHARACTER_ROLE_IDENTITY_PATTERNS = (
    re.compile(r"([\u4e00-\u9fff]{1,4}(?:家|氏)[长次二三四五六七八九十幼少庶嫡]?[子女])"),
    re.compile(r"([\u4e00-\u9fff]{0,8}[一二三四五六七八九十]房之[子女])"),
    re.compile(r"([\u4e00-\u9fff]{1,8}(?:门|派|宗|宫|教|帮|寨|庄|阁|楼|堂|会)(?:弟子|门人|传人|少主|掌门|门主|长老|护法|客卿))"),
    re.compile(
        r"(江湖散人|武林散人|俗家弟子|弟子|散人|剑客|刀客|刺客|医师|药师|捕快|镖师|商人|匠人|书生|先生|客卿|护卫|侍女|仆从|丫鬟|公子|小姐|少侠|侠客|道士|和尚|僧人|术士|修士|家主|门主|掌门|长老|少主|后人|族人|遗孤)"
    ),
)


def suggest_memory_repair(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    decision: MemoryRepairDecision | None = None,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
    session_id: str | None = None,
    chapter_number: int | None = None,
    audit_issue_ids: list[str] | None = None,
) -> MemoryRepairSuggestResult:
    root = root.resolve()
    request = user_request.strip()
    if not request:
        raise MemoryRepairError("memory repair request must not be empty")
    repair_id = _new_repair_id()
    decision_was_provided = decision is not None
    repair_decision = decision or generate_memory_repair_decision(
        root,
        request,
        provider_name=provider_name,
        provider=provider,
        change_kind=change_kind,
        stage=stage,
    )
    resolved_preflight_kind: MemoryChangeKind = change_kind or repair_decision.change_kind
    target_files, operations, notes = _sanitize_repair_decision(repair_decision)
    operations = _drop_unsafe_remove_operations(root, operations, notes)
    preflight_errors = _preflight_memory_repair_operations(root, operations, change_kind=resolved_preflight_kind)
    if preflight_errors and operations and not decision_was_provided:
        repair_decision = _repair_memory_repair_decision_target_schema(
            root,
            request,
            invalid_decision=repair_decision,
            preflight_errors=preflight_errors,
            provider_name=provider_name,
            provider=provider,
            change_kind=change_kind,
            stage=stage,
        )
        resolved_preflight_kind = change_kind or repair_decision.change_kind
        target_files, operations, notes = _sanitize_repair_decision(repair_decision)
        operations = _drop_unsafe_remove_operations(root, operations, notes)
        preflight_errors = _preflight_memory_repair_operations(root, operations, change_kind=resolved_preflight_kind)
    if preflight_errors and operations:
        raise MemoryRepairError(
            "setting change proposal failed target schema preflight or semantic preflight: "
            + _format_preflight_errors(preflight_errors)
        )
    resolved_kind: MemoryChangeKind = change_kind or repair_decision.change_kind
    resolved_stage: MemoryChangeStage = stage or repair_decision.stage or "unknown"
    domains = _dedupe_domains([*repair_decision.domains, *_domains_from_files(target_files)])
    impact = _analyze_memory_change_impact(
        root,
        operations,
        domains=domains,
        session_id=session_id,
        chapter_number=chapter_number,
    )
    followup_actions = _memory_change_followups(
        root,
        impact,
        stage=resolved_stage,
        session_id=session_id,
        chapter_number=chapter_number,
        change_kind=resolved_kind,
    )
    report = validate_project(root)
    proposal = MemoryRepairProposal(
        repair_id=repair_id,
        change_kind=resolved_kind,
        user_request=request,
        target_files=target_files,
        operations=operations,
        domains=domains,
        stage=resolved_stage,
        impact=impact,
        followup_actions=[*repair_decision.followup_actions, *followup_actions],
        risk_level=impact.risk_level if operations else "medium",
        confidence=repair_decision.confidence,
        assumptions=repair_decision.assumptions,
        needs_user_confirmation=True,
        validation_before={
            "ok": report.ok,
            "error_count": len(report.errors),
            "warning_count": len(report.warnings),
        },
        notes=_proposal_notes(target_files, operations, notes, change_kind=resolved_kind, audit_issue_ids=audit_issue_ids or []),
        created_at=_utc_now(),
    )
    repair_dir = _repair_dir(root, repair_id)
    proposal_path = repair_dir / "proposal.json"
    markdown_path = repair_dir / "proposal.md"
    atomic_write_model_json(proposal_path, proposal)
    atomic_write_json(markdown_path.with_suffix(".diff.json"), _preview_operations(root, proposal))
    atomic_write_text(markdown_path, render_memory_repair_markdown(proposal))
    record_management_event(
        root,
        "memory_repair_proposed",
        f"项目管家生成记忆修复建议：{repair_id}",
        source="orchestrator",
        target_files=target_files,
        status="info",
        details={
            "repair_id": repair_id,
            "change_kind": resolved_kind,
            "operation_count": len(operations),
            "affected_chapters": impact.affected_chapters,
            "affected_sessions": impact.affected_sessions,
        },
    )
    return MemoryRepairSuggestResult(proposal=proposal, proposal_path=proposal_path, markdown_path=markdown_path)


def suggest_setting_change(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    stage: MemoryChangeStage = "unknown",
    session_id: str | None = None,
    chapter_number: int | None = None,
    audit_issue_ids: list[str] | None = None,
) -> MemoryRepairSuggestResult:
    return suggest_memory_repair(
        root,
        user_request,
        provider_name=provider_name,
        provider=provider,
        change_kind="setting_change",
        stage=stage,
        session_id=session_id,
        chapter_number=chapter_number,
        audit_issue_ids=audit_issue_ids,
    )


def suggest_setting_change_interactive(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    stage: MemoryChangeStage = "unknown",
    session_id: str | None = None,
    chapter_number: int | None = None,
    audit_issue_ids: list[str] | None = None,
    max_clarification_rounds: int = 3,
) -> SettingChangeSuggestionResult:
    root = root.resolve()
    request = user_request.strip()
    if not request:
        raise MemoryRepairError("setting change request must not be empty")
    decision = generate_memory_change_clarification_decision(
        root,
        request,
        provider_name=provider_name,
        provider=provider,
        stage=stage,
        conversation_turns=[
            MemoryChangeConversationTurn(role="user", content=request, created_at=_utc_now()),
        ],
    )
    if decision.status == "needs_clarification" and max_clarification_rounds > 0:
        clarification = _new_clarification_session(
            root,
            request,
            decision=decision,
            stage=stage,
            session_id=session_id,
            chapter_number=chapter_number,
            audit_issue_ids=audit_issue_ids or [],
        )
        return SettingChangeSuggestionResult(status="needs_clarification", clarification=clarification)
    proposal_result = suggest_setting_change(
        root,
        request,
        provider_name=provider_name,
        provider=provider,
        stage=stage,
        session_id=session_id,
        chapter_number=chapter_number,
        audit_issue_ids=audit_issue_ids,
    )
    return SettingChangeSuggestionResult(status="proposal_ready", proposal_result=proposal_result)


def answer_setting_change_clarification(
    root: Path,
    clarification_id: str,
    answer: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    max_clarification_rounds: int = 3,
) -> SettingChangeSuggestionResult:
    root = root.resolve()
    clean_answer = answer.strip()
    if not clean_answer:
        raise MemoryRepairError("setting change clarification answer must not be empty")
    clarification = load_setting_change_clarification(root, clarification_id)
    if clarification.status != "needs_clarification":
        raise MemoryRepairError(f"setting change clarification is not waiting for input: {clarification_id}")
    now = _utc_now()
    turns = [
        *clarification.conversation_turns,
        MemoryChangeConversationTurn(role="user", content=clean_answer, created_at=now),
    ]
    combined_request = _combined_setting_change_request(clarification.original_request, turns)
    decision = generate_memory_change_clarification_decision(
        root,
        combined_request,
        provider_name=provider_name,
        provider=provider,
        stage=clarification.stage,
        conversation_turns=turns,
    )
    user_answer_count = sum(1 for turn in turns if turn.role == "user") - 1
    if decision.status == "needs_clarification" and user_answer_count < max_clarification_rounds:
        clarification.conversation_turns = [
            *turns,
            MemoryChangeConversationTurn(
                role="agent",
                content="\n".join(decision.questions),
                created_at=_utc_now(),
            ),
        ]
        clarification.questions = decision.questions
        clarification.updated_at = _utc_now()
        _write_clarification_session(root, clarification)
        return SettingChangeSuggestionResult(status="needs_clarification", clarification=clarification)
    if decision.status == "needs_clarification":
        proposal_result = _no_op_setting_change_proposal(
            root,
            combined_request,
            stage=clarification.stage,
            session_id=clarification.session_id,
            chapter_number=clarification.chapter_number,
            audit_issue_ids=clarification.audit_issue_ids,
            notes=[
                "设定变更澄清已达到最大轮数，仍无法安全定位可应用 patch。",
                *decision.questions,
                *decision.notes,
            ],
        )
    else:
        proposal_result = suggest_setting_change(
            root,
            combined_request,
            provider_name=provider_name,
            provider=provider,
            stage=clarification.stage,
            session_id=clarification.session_id,
            chapter_number=clarification.chapter_number,
            audit_issue_ids=clarification.audit_issue_ids,
        )
    clarification.status = "proposal_ready"
    clarification.proposal_path = str(proposal_result.proposal_path.relative_to(root))
    clarification.questions = []
    clarification.updated_at = _utc_now()
    clarification.conversation_turns = turns
    _write_clarification_session(root, clarification)
    return SettingChangeSuggestionResult(status="proposal_ready", proposal_result=proposal_result, clarification=clarification)


def load_setting_change_clarification(root: Path, clarification_id: str) -> MemoryChangeClarificationSession:
    return load_json_model(_clarification_path(root.resolve(), clarification_id), MemoryChangeClarificationSession)


def generate_memory_change_clarification_decision(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    stage: MemoryChangeStage = "unknown",
    conversation_turns: list[MemoryChangeConversationTurn] | None = None,
) -> MemoryChangeClarificationDecision:
    request = user_request.strip()
    if provider is None and provider_name.lower() == "mock":
        return _mock_memory_change_clarification_decision(request)
    repair_provider = provider or create_agent_provider(
        default_agent_config_path(root),
        "orchestrator",
        overrides=ProviderOverrides(provider_name=provider_name),
    )
    user_prompt = _memory_change_clarification_user_prompt(
        root,
        request,
        stage=stage,
        conversation_turns=conversation_turns or [],
    )
    try:
        content = generate_with_output_guard(
            repair_provider,
            ModelRequest(
                system_prompt=load_prompt_template("memory_change_clarification_system"),
                user_prompt=user_prompt,
                json_schema_name="MemoryChangeClarificationDecision",
            ),
            root=root,
            invocation=AgentInvocationContext(
                agent_name="orchestrator",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_change_clarification",
            ),
            contract=AgentOutputContract(
                output_kind="json",
                target_name="MemoryChangeClarificationDecision",
                json_schema_name="MemoryChangeClarificationDecision",
                allow_user_questions=False,
            ),
        )
    except AgentOutputContractError:
        return _fallback_clarification_decision("provider output violated MemoryChangeClarificationDecision contract")
    try:
        return parse_memory_change_clarification_decision(content)
    except MemoryRepairError as exc:
        return _fallback_clarification_decision(f"provider returned invalid clarification decision: {exc}")


def parse_memory_change_clarification_decision(content: str) -> MemoryChangeClarificationDecision:
    raw = _extract_json_object(content)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryChangeClarificationDecision JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MemoryRepairError("provider returned MemoryChangeClarificationDecision as a non-object JSON value")
    data = dict(data)
    data["source"] = data.get("source") or "model"
    data["questions"] = _normalize_string_list(data.get("questions"))
    data["assumptions"] = _normalize_string_list(data.get("assumptions"))
    data["notes"] = _normalize_string_list(data.get("notes"))
    try:
        return MemoryChangeClarificationDecision.model_validate(data)
    except ValidationError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryChangeClarificationDecision: {exc}") from exc


def generate_memory_repair_decision(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
) -> MemoryRepairDecision:
    request = user_request.strip()
    if provider is None and provider_name.lower() == "mock":
        return _mock_memory_repair_decision(root, request, change_kind=change_kind, stage=stage)
    repair_provider = provider or create_agent_provider(
        default_agent_config_path(root),
        "orchestrator",
        overrides=ProviderOverrides(provider_name=provider_name),
    )
    user_prompt = _memory_repair_user_prompt(root, request, change_kind=change_kind, stage=stage)
    try:
        content = generate_with_output_guard(
            repair_provider,
            ModelRequest(
                system_prompt=load_prompt_template("memory_repair_system"),
                user_prompt=user_prompt,
                json_schema_name="MemoryRepairDecision",
            ),
            root=root,
            invocation=AgentInvocationContext(
                agent_name="orchestrator",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_repair_decision",
            ),
            contract=AgentOutputContract(
                output_kind="json",
                target_name="MemoryRepairDecision",
                json_schema_name="MemoryRepairDecision",
                allow_user_questions=False,
            ),
        )
    except AgentOutputContractError:
        return _empty_memory_repair_decision("provider output violated MemoryRepairDecision contract")
    try:
        return parse_memory_repair_decision(content)
    except MemoryRepairError as first_error:
        try:
            repair_content = generate_with_output_guard(
                repair_provider,
                ModelRequest(
                    system_prompt=load_prompt_template("memory_repair_system"),
                    user_prompt=_repair_decision_repair_prompt(
                        original_prompt=user_prompt,
                        invalid_output=content,
                        error=str(first_error),
                    ),
                    json_schema_name="MemoryRepairDecision",
                ),
                root=root,
                invocation=AgentInvocationContext(
                    agent_name="orchestrator",
                    caller="memory_repair",
                    interaction_mode="internal_task",
                    task="memory_repair_decision_repair",
                ),
                contract=AgentOutputContract(
                    output_kind="json",
                    target_name="MemoryRepairDecision",
                    json_schema_name="MemoryRepairDecision",
                    allow_user_questions=False,
                ),
            )
        except AgentOutputContractError:
            return _empty_memory_repair_decision("provider repair output violated MemoryRepairDecision contract")
        try:
            return parse_memory_repair_decision(repair_content)
        except MemoryRepairError as second_error:
            return _empty_memory_repair_decision(f"provider returned invalid MemoryRepairDecision: {second_error}")


def _repair_memory_repair_decision_target_schema(
    root: Path,
    user_request: str,
    *,
    invalid_decision: MemoryRepairDecision,
    preflight_errors: list[str],
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
) -> MemoryRepairDecision:
    request = user_request.strip()
    repair_provider = provider or create_agent_provider(
        default_agent_config_path(root),
        "orchestrator",
        overrides=ProviderOverrides(provider_name=provider_name),
    )
    original_prompt = _memory_repair_user_prompt(root, request, change_kind=change_kind, stage=stage)
    try:
        content = generate_with_output_guard(
            repair_provider,
            ModelRequest(
                system_prompt=load_prompt_template("memory_repair_system"),
                user_prompt=_target_schema_repair_prompt(
                    original_prompt=original_prompt,
                    invalid_decision=invalid_decision,
                    preflight_errors=preflight_errors,
                ),
                json_schema_name="MemoryRepairDecision",
            ),
            root=root,
            invocation=AgentInvocationContext(
                agent_name="orchestrator",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_repair_target_schema_repair",
            ),
            contract=AgentOutputContract(
                output_kind="json",
                target_name="MemoryRepairDecision",
                json_schema_name="MemoryRepairDecision",
                allow_user_questions=False,
            ),
        )
    except AgentOutputContractError as exc:
        raise MemoryRepairError(
            "provider target-schema repair output violated MemoryRepairDecision contract: "
            + ", ".join(exc.reason_codes)
        ) from exc
    try:
        return parse_memory_repair_decision(content)
    except MemoryRepairError as exc:
        raise MemoryRepairError(f"provider returned invalid target-schema repair decision: {exc}") from exc


def parse_memory_repair_decision(content: str) -> MemoryRepairDecision:
    raw = _extract_json_object(content)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryRepairDecision JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MemoryRepairError("provider returned MemoryRepairDecision as a non-object JSON value")
    data = dict(data)
    data = _normalize_memory_repair_decision_data(data)
    data["needs_user_confirmation"] = True
    data["source"] = data.get("source") or "model"
    try:
        return MemoryRepairDecision.model_validate(data)
    except ValidationError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryRepairDecision: {exc}") from exc


def _normalize_memory_repair_decision_data(data: dict[str, object]) -> dict[str, object]:
    data = dict(data)
    data["target_files"] = _normalize_string_list(data.get("target_files"))
    data["assumptions"] = _normalize_string_list(data.get("assumptions"))
    data["notes"] = _normalize_string_list(data.get("notes"))
    operations = data.get("operations")
    if not isinstance(operations, list):
        return data
    normalized_operations: list[object] = []
    for raw_operation in operations:
        if not isinstance(raw_operation, dict):
            normalized_operations.append(raw_operation)
            continue
        normalized_operations.append(_normalize_memory_repair_operation(raw_operation))
    data["operations"] = normalized_operations
    return data


def _normalize_memory_repair_operation(raw_operation: dict[str, object]) -> dict[str, object]:
    operation = dict(raw_operation)
    op = operation.get("op")
    path = operation.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        return operation
    inferred_file = _infer_file_from_pointer_path(path)
    if not isinstance(operation.get("file"), str) or not operation.get("file"):
        if inferred_file:
            operation["file"] = inferred_file
    if op == "add":
        normalized_path = _normalize_add_collection_path(path)
        can_default_reason = normalized_path != path or _is_append_collection_path(normalized_path)
        if normalized_path != path:
            operation["path"] = normalized_path
            if not isinstance(operation.get("file"), str) or not operation.get("file"):
                operation["file"] = _infer_file_from_pointer_path(normalized_path) or inferred_file
        if can_default_reason and (not isinstance(operation.get("reason"), str) or not operation.get("reason")):
            operation["reason"] = "用户要求新增设定；系统根据集合路径补齐操作原因。"
    return operation


def _infer_file_from_pointer_path(path: str) -> str | None:
    parts = [part for part in path.strip("/").split("/") if part]
    if not parts:
        return None
    return POINTER_PATH_FILES.get(_unescape_pointer(parts[0]))


def _normalize_add_collection_path(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) != 2:
        return path
    collection_key = _unescape_pointer(parts[0])
    item_selector = _unescape_pointer(parts[1])
    if collection_key not in COLLECTION_PATH_FILES or item_selector == "-" or item_selector.isdigit():
        return path
    return f"/{_escape_pointer(collection_key)}/-"


def _is_append_collection_path(path: str) -> bool:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) != 2:
        return False
    return _unescape_pointer(parts[0]) in COLLECTION_PATH_FILES and _unescape_pointer(parts[1]) == "-"


def apply_memory_repair(root: Path, proposal_path: Path) -> MemoryRepairApplyResult:
    root = root.resolve()
    proposal = load_json_model(_resolve_proposal_path(root, proposal_path), MemoryRepairProposal)
    backups: list[str] = []
    touched_files: list[str] = []
    apply_log_path = _repair_dir(root, proposal.repair_id) / "apply_log.json"
    try:
        if not proposal.operations:
            raise MemoryRepairError("memory repair proposal has no operations to apply")
        preflight_errors = _preflight_memory_repair_operations(root, proposal.operations, change_kind=proposal.change_kind)
        if preflight_errors:
            raise MemoryRepairError(
                "memory repair proposal failed target schema preflight or semantic preflight: "
                + _format_preflight_errors(preflight_errors)
            )
        grouped = _group_operations(proposal.operations)
        for rel_path, operations in grouped.items():
            _ensure_allowed_file(rel_path)
            path = root / rel_path
            data = load_json(path)
            updated = _apply_operations_to_data(data, operations)
            _validate_file_model(rel_path, updated)
            backup = backup_file(path, reason="memory_repair")
            backups.append(str(backup.relative_to(root)))
            touched_files.append(rel_path)
            atomic_write_json(path, updated)
        report = validate_project(root)
        if not report.ok:
            raise MemoryRepairError(
                "memory repair produced validation errors: "
                + "; ".join(message.message for message in report.errors)
            )
        apply_log = MemoryRepairApplyLog(
            repair_id=proposal.repair_id,
            applied_at=_utc_now(),
            status="applied",
            target_files=touched_files,
            backups=backups,
        )
        atomic_write_model_json(apply_log_path, apply_log)
        record_management_event(
            root,
            "memory_repair_applied",
            f"项目管家已应用记忆修复：{proposal.repair_id}",
            source="orchestrator",
            target_files=touched_files,
            status="success",
            details={
                "repair_id": proposal.repair_id,
                "change_kind": proposal.change_kind,
                "affected_chapters": proposal.impact.affected_chapters if proposal.impact else [],
                "affected_sessions": proposal.impact.affected_sessions if proposal.impact else [],
                "stale_chapters": proposal.impact.stale_chapters if proposal.impact else [],
            },
        )
        return MemoryRepairApplyResult(proposal=proposal, apply_log=apply_log, apply_log_path=apply_log_path)
    except Exception as exc:
        _restore_backups(root, touched_files, backups)
        apply_log = MemoryRepairApplyLog(
            repair_id=proposal.repair_id,
            applied_at=_utc_now(),
            status="rolled_back" if backups else "failed",
            target_files=touched_files,
            backups=backups,
            errors=[f"{exc.__class__.__name__}: {exc}"],
        )
        atomic_write_model_json(apply_log_path, apply_log)
        record_management_event(
            root,
            "memory_repair_failed",
            f"项目管家记忆修复失败：{proposal.repair_id}",
            source="orchestrator",
            target_files=touched_files or proposal.target_files,
            status="error",
            details={"repair_id": proposal.repair_id, "error": str(exc)},
        )
        raise MemoryRepairError(str(exc)) from exc


def render_memory_repair_markdown(proposal: MemoryRepairProposal) -> str:
    lines = [
        f"# Memory Repair {proposal.repair_id}",
        "",
        f"- Created by: {proposal.created_by}",
        f"- Change kind: {proposal.change_kind}",
        f"- Stage: {proposal.stage}",
        f"- Risk: {proposal.risk_level}",
        f"- Confidence: {proposal.confidence:.2f}",
        f"- Needs confirmation: {proposal.needs_user_confirmation}",
        f"- Request: {proposal.user_request}",
        "",
        "## Target Files",
        "",
    ]
    lines.extend(f"- {path}" for path in proposal.target_files)
    lines.extend(["", "## Operations", ""])
    if proposal.operations:
        for operation in proposal.operations:
            lines.append(f"- `{operation.op}` `{operation.file}` `{operation.path}`: {operation.reason}")
    else:
        lines.append("- 暂无可安全自动应用的操作；请补充具体 ID 或手动编辑 proposal。")
    if proposal.impact:
        lines.extend(["", "## Impact", ""])
        lines.append(f"- Summary: {proposal.impact.summary}")
        lines.append(f"- Domains: {', '.join(proposal.impact.domains) or 'none'}")
        lines.append(f"- Entity IDs: {', '.join(proposal.impact.entity_ids) or 'none'}")
        lines.append(
            "- Affected chapters: "
            + (", ".join(str(number) for number in proposal.impact.affected_chapters) or "none")
        )
        lines.append(f"- Affected sessions: {', '.join(proposal.impact.affected_sessions) or 'none'}")
        lines.append(
            "- Stale accepted chapters: "
            + (", ".join(str(number) for number in proposal.impact.stale_chapters) or "none")
        )
    if proposal.followup_actions:
        lines.extend(["", "## Follow-up Actions", ""])
        for action in proposal.followup_actions:
            chapters = ",".join(str(number) for number in action.chapter_numbers) or "-"
            lines.append(f"- `{action.action}` session={action.session_id or '-'} chapters={chapters}: {action.reason}")
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in proposal.notes)
    return "\n".join(lines) + "\n"


def _sanitize_repair_decision(decision: MemoryRepairDecision) -> tuple[list[str], list[MemoryRepairOperation], list[str]]:
    notes = list(decision.notes)
    target_files = sorted({path for path in decision.target_files if path in ALLOWED_MEMORY_FILES})
    dropped_targets = sorted({path for path in decision.target_files if path not in ALLOWED_MEMORY_FILES})
    if dropped_targets:
        notes.append("已忽略非白名单目标文件：" + ", ".join(dropped_targets))
    operations: list[MemoryRepairOperation] = []
    for operation in decision.operations:
        if operation.file not in ALLOWED_MEMORY_FILES:
            notes.append(f"已忽略非白名单操作目标：{operation.file} {operation.path}")
            continue
        operations.append(operation)
        if operation.file not in target_files:
            target_files.append(operation.file)
    return sorted(target_files), operations, notes


def _drop_unsafe_remove_operations(
    root: Path,
    operations: list[MemoryRepairOperation],
    notes: list[str],
) -> list[MemoryRepairOperation]:
    safe_operations: list[MemoryRepairOperation] = []
    for operation in operations:
        if operation.op != "remove":
            safe_operations.append(operation)
            continue
        entity_ids = _entity_ids_from_operation_path(root, operation)
        referenced = sorted(
            entity_id
            for entity_id in entity_ids
            if _entity_id_has_references(root, entity_id, exclude_file=operation.file)
        )
        if referenced:
            notes.append(
                "已拒绝删除仍被其他 memory/章节/session 文件引用的实体："
                + ", ".join(referenced)
            )
            continue
        safe_operations.append(operation)
    return safe_operations


def _memory_repair_user_prompt(
    root: Path,
    request: str,
    *,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
) -> str:
    task_note = ""
    if change_kind == "setting_change":
        task_note = (
            "本次任务是 setting_change：允许根据用户明确请求新增、修改或删除人物/背景设定。\n"
            "新增实体时必须生成稳定小写下划线 id，并填齐目标 schema 必填字段。\n"
            "修改必须定位到明确 ID、exact name 或 exact alias；不要做近似联想匹配。\n"
            "无精确匹配且用户没有明确要求替换/删除/合并时，按新增实体处理。\n"
            "删除被引用实体必须同时安全清理引用，否则 operations 留空。\n"
            "每个 operation 必须包含 file、path、reason；array 新增必须使用 /collection/-，不能使用 /characters/{id} 这类路径。\n"
            "不要要求用户提供文件结构、字段、visibility 或 JSON Pointer；这些由当前结构索引决定。\n"
            f"{SETTING_CHANGE_MAPPING_RULES}"
            f"创作阶段：{stage or 'unknown'}。\n\n"
        )
    return (
        "请生成 MemoryRepairDecision JSON。\n"
        f"{task_note}"
        "允许 target_files：\n"
        + "\n".join(f"- {path}" for path in sorted(ALLOWED_MEMORY_FILES))
        + "\n\n"
        "当前文件结构与 JSON Pointer 路径索引：\n"
        f"{_memory_pointer_index(root)}\n\n"
        "当前可见 ID 摘要：\n"
        f"{_memory_id_summary(root)}\n\n"
        f"用户请求：\n{request}\n"
    )


def build_memory_repair_user_prompt(
    root: Path,
    request: str,
    *,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
) -> str:
    return _memory_repair_user_prompt(root, request, change_kind=change_kind, stage=stage)


def _memory_change_clarification_user_prompt(
    root: Path,
    request: str,
    *,
    stage: MemoryChangeStage,
    conversation_turns: list[MemoryChangeConversationTurn],
) -> str:
    transcript = "\n".join(
        f"- {turn.role}: {turn.content}"
        for turn in conversation_turns
    ) or "- user: " + request
    return (
        "请判断本次 setting_change 是否已经足以生成安全的 MemoryRepairProposal。\n"
        "只要用户的创作意图足够明确，就输出 ready；文件、字段、visibility 和 JSON Pointer 映射是系统责任，不是用户责任。\n"
        "如果只是新实体属于 characters/locations/items/world/hidden_truths/foreshadowing 哪类需要系统判断，不要为此追问用户。\n"
        "如果缺少具体新增/修改内容、用户要求替换/删除但目标不唯一，或存在会改变剧情含义的真实创作歧义，才输出 needs_clarification。\n"
        "不要要求用户提供现有文件完整结构、目标文件、字段名、visibility 或 JSON Pointer；现有文件结构和 JSON Pointer 路径索引已经在本 prompt 中提供。\n"
        "不要把新姓名近似联想到现有角色；只有 exact id、exact name 或 exact alias 匹配才视为已有实体。\n"
        f"{SETTING_CHANGE_MAPPING_RULES}"
        f"创作阶段：{stage or 'unknown'}。\n\n"
        "允许 target_files：\n"
        + "\n".join(f"- {path}" for path in sorted(ALLOWED_MEMORY_FILES))
        + "\n\n"
        "当前文件结构与 JSON Pointer 路径索引：\n"
        f"{_memory_pointer_index(root)}\n\n"
        "当前可见 ID 摘要：\n"
        f"{_memory_id_summary(root)}\n\n"
        "对话记录：\n"
        f"{transcript}\n\n"
        f"合并后的用户请求：\n{request}\n"
    )


def _memory_pointer_index(root: Path) -> str:
    sections: list[str] = []
    for rel_path in sorted(ALLOWED_MEMORY_FILES):
        sections.append(_file_pointer_index(root, rel_path))
    return "\n".join(sections)


def _file_pointer_index(root: Path, rel_path: str) -> str:
    path = root / rel_path
    if not path.exists():
        return f"- {rel_path}: missing"
    try:
        data = load_json(path)
    except Exception as exc:
        return f"- {rel_path}: unreadable ({exc.__class__.__name__})"
    lines = [f"- {rel_path}"]
    if isinstance(data, dict):
        lines.append("  top-level keys: " + ", ".join(sorted(str(key) for key in data)))
    collection_key = FILE_COLLECTION_KEYS.get(rel_path)
    if collection_key and isinstance(data, dict):
        collection = data.get(collection_key)
        fields = COLLECTION_FIELD_HINTS.get(rel_path, [])
        lines.append(f"  collection: /{collection_key}")
        lines.append(f"  add new item path: /{collection_key}/-")
        if fields:
            lines.append("  common item fields: " + ", ".join(fields))
        schema_hint = COLLECTION_SCHEMA_HINTS.get(rel_path)
        if schema_hint:
            lines.extend(f"  {line}" for line in schema_hint.splitlines())
        if isinstance(collection, list) and collection:
            for index, item in enumerate(collection[:20]):
                if not isinstance(item, dict):
                    lines.append(f"  existing[{index}] path: /{collection_key}/{index}")
                    continue
                item_id = item.get("id") if isinstance(item.get("id"), str) else "-"
                name = item.get("name") or item.get("title") or "-"
                item_fields = sorted(str(key) for key in item)
                examples = [
                    f"/{collection_key}/{index}/{field}"
                    for field in item_fields
                    if field != "id"
                ][:8]
                lines.append(f"  existing[{index}]: id={item_id}; name/title={name}; path=/{collection_key}/{index}")
                lines.append("    fields: " + ", ".join(item_fields))
                if examples:
                    lines.append("    replace paths: " + ", ".join(examples))
        else:
            lines.append(f"  existing items: none; use /{collection_key}/- for add")
        return "\n".join(lines)
    if rel_path == "memory/state/current_state.json" and isinstance(data, dict):
        lines.extend(_state_pointer_index(data))
    elif rel_path == "memory/state/timeline.json" and isinstance(data, dict):
        lines.extend(_timeline_pointer_index(data))
    return "\n".join(lines)


def _state_pointer_index(data: dict[str, object]) -> list[str]:
    lines = [
        "  story position paths: /story_position/latest_chapter, /story_position/current_arc",
        "  add state paths: /character_states/-, /item_states/-, /location_states/-",
    ]
    for key in sorted(STATE_COLLECTION_KEYS):
        collection = data.get(key)
        if not isinstance(collection, list):
            continue
        lines.append(f"  collection: /{key}")
        for index, item in enumerate(collection[:20]):
            if not isinstance(item, dict):
                continue
            entity_id = item.get("entity_id") or item.get("id") or "-"
            fields = sorted(str(field) for field in item)
            examples = [f"/{key}/{index}/{field}" for field in fields if field not in {"entity_id", "id"}][:8]
            lines.append(f"  existing[{index}]: entity_id={entity_id}; path=/{key}/{index}")
            if examples:
                lines.append("    replace paths: " + ", ".join(examples))
    return lines


def _timeline_pointer_index(data: dict[str, object]) -> list[str]:
    events = data.get("events")
    lines = [
        "  collection: /events",
        "  add event path: /events/-",
        "  common event fields: id, chapter, summary, narrative_position, story_position, event_role, certainty, causes, effects, state_change_ids",
    ]
    if not isinstance(events, list) or not events:
        lines.append("  existing events: none; use /events/- for add")
        return lines
    for index, item in enumerate(events[:40]):
        if not isinstance(item, dict):
            continue
        event_id = item.get("id") if isinstance(item.get("id"), str) else "-"
        summary = item.get("summary") if isinstance(item.get("summary"), str) else "-"
        fields = sorted(str(field) for field in item)
        examples = [f"/events/{index}/{field}" for field in fields if field != "id"][:8]
        lines.append(f"  existing[{index}]: id={event_id}; summary={summary}; path=/events/{index}")
        if examples:
            lines.append("    replace paths: " + ", ".join(examples))
    return lines


def _memory_id_summary(root: Path) -> str:
    lines: list[str] = []
    for rel_path in sorted(ALLOWED_MEMORY_FILES):
        path = root / rel_path
        if not path.exists():
            lines.append(f"- {rel_path}: missing")
            continue
        try:
            data = load_json(path)
        except Exception as exc:
            lines.append(f"- {rel_path}: unreadable ({exc.__class__.__name__})")
            continue
        ids = _collect_ids(data)
        if ids:
            lines.append(f"- {rel_path}: " + ", ".join(ids[:40]))
        else:
            lines.append(f"- {rel_path}: no explicit ids found")
    return "\n".join(lines)


def _collect_ids(value: object) -> list[str]:
    found: list[str] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            item_id = node.get("id")
            if isinstance(item_id, str) and item_id not in found:
                found.append(item_id)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return found


def _repair_decision_repair_prompt(*, original_prompt: str, invalid_output: str, error: str) -> str:
    return (
        f"{original_prompt}\n\n"
        "上一次输出不能被解析为 MemoryRepairDecision。\n"
        f"错误：{error}\n\n"
        "请重新只输出 JSON object。不要 Markdown 或解释。\n"
        "修复规则：\n"
        "- 每个 operation 必须包含 op、file、path、reason。\n"
        "- 如果是新增数组条目，path 必须使用 /collection/-，例如 /characters/-、/hidden_truths/-、/foreshadowing_threads/-。\n"
        "- 如果上次输出使用 /characters/{id}、/hidden_truths/{id}、/foreshadowing_threads/{id} 这类新增路径，请改成对应 /collection/-，并保留 value.id。\n"
        "- 如果缺少 file，但 path 能唯一映射到允许文件，请补齐 file。\n"
        "- 不要要求用户提供现有文件结构、目标文件、字段、visibility 或 JSON Pointer；原始 prompt 已提供这些结构上下文。\n"
        "- 只有创作意图本身缺失、替换/删除目标不唯一或删除风险无法安全处理时，operations 才能为空。\n"
        f"上一次输出：\n{invalid_output[:3000]}\n"
    )


def _target_schema_repair_prompt(
    *,
    original_prompt: str,
    invalid_decision: MemoryRepairDecision,
    preflight_errors: list[str],
) -> str:
    invalid_json = json.dumps(invalid_decision.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return (
        f"{original_prompt}\n\n"
        "上一次输出已经可以解析为 MemoryRepairDecision，但把 operations 应用到目标 memory/canon 文件后没有通过目标文件 schema/semantic preflight。\n"
        "本次修复目标是让 operation.value 完全符合目标文件 Pydantic schema 和 setting_change 字段语义，而不只是补齐 op/file/path/reason。\n"
        "请重新只输出修复后的 MemoryRepairDecision JSON object。不要 Markdown 或解释。\n"
        "修复规则：\n"
        "- 保留用户创作意图和安全的 file/path；修正 value 的字段类型、嵌套对象和 enum。\n"
        "- add 到集合时，value 必须是对应集合元素的完整对象，且满足上方 strict add value schema。\n"
        "- visibility 只能是 reader_visible、hidden 或 partially_revealed；importance 只能是 low、medium、high 或 critical。\n"
        "- abilities、secrets、rules、special_properties 必须是对象数组，不要使用字符串数组。\n"
        "- planned_reveal 和 planned_payoff 必须是对象或 null，不要使用字符串。\n"
        "- introduced_in_chapter 必须是整数；如果用户说“开篇”，默认使用 1。\n"
        "- Character.role 只能表示叙事角色；默认使用主角、主要人物、配角、次要人物。家族身份、门派身份、排行、职业/江湖身份必须移入 tags，并可保留在 summary/notes。\n"
        "- 不要把谢家长女、谢家次子、张家幼女、唐门二房之女、江湖散人、武当俗家弟子这类身份短语写入 Character.role。\n"
        "- 如果仍无法安全修复，operations 置空并在 notes 中写明 target schema 缺失信息；不要向用户提问。\n\n"
        "目标 schema preflight 错误 / semantic preflight 错误：\n"
        f"{_format_preflight_errors(preflight_errors, max_chars=6000)}\n\n"
        f"上一次 MemoryRepairDecision：\n{invalid_json[:5000]}\n"
    )


def _empty_memory_repair_decision(note: str) -> MemoryRepairDecision:
    return MemoryRepairDecision(
        target_files=[],
        operations=[],
        confidence=0.0,
        assumptions=[],
        needs_user_confirmation=True,
        notes=[note, "没有生成可安全自动应用的 patch；请提供具体 event/entity id 或手动编辑 proposal。"],
        source="fallback",
    )


def _fallback_clarification_decision(note: str) -> MemoryChangeClarificationDecision:
    return MemoryChangeClarificationDecision(
        status="needs_clarification",
        questions=["请补充目标设定的名称或 ID，以及希望改成的具体内容。"],
        confidence=0.0,
        assumptions=[],
        notes=[note],
        source="fallback",
    )


def _mock_memory_change_clarification_decision(request: str) -> MemoryChangeClarificationDecision:
    normalized = request.strip()
    if not normalized:
        return _fallback_clarification_decision("empty request")
    unclear_patterns = (
        "还没想好",
        "随便",
        "某个",
        "某人",
        "一个人物",
        "一个角色",
        "改一下",
        "优化一下",
    )
    has_specific_target = bool(re.search(r"\b(char|loc|item|world|truth|thread)_[a-z0-9_]+\b", normalized)) or any(
        marker in normalized for marker in ("沈微", "林澈", "world_")
    )
    has_specific_change = any(marker in normalized for marker in ("新增", "删除", "设定为", "改成", "规则为", "背景是"))
    if any(pattern in normalized for pattern in unclear_patterns) and not (has_specific_target and has_specific_change):
        return MemoryChangeClarificationDecision(
            status="needs_clarification",
            questions=["请补充目标设定的名称或 ID，以及希望新增/修改后的具体内容。"],
            confidence=0.35,
            assumptions=["mock provider fixture only; not used as real business inference"],
            notes=[],
            source="mock",
        )
    return MemoryChangeClarificationDecision(
        status="ready",
        questions=[],
        confidence=0.8,
        assumptions=["mock provider fixture only; not used as real business inference"],
        notes=[],
        source="mock",
    )


def _new_clarification_session(
    root: Path,
    request: str,
    *,
    decision: MemoryChangeClarificationDecision,
    stage: MemoryChangeStage,
    session_id: str | None,
    chapter_number: int | None,
    audit_issue_ids: list[str],
) -> MemoryChangeClarificationSession:
    now = _utc_now()
    clarification = MemoryChangeClarificationSession(
        clarification_id=_new_clarification_id(),
        original_request=request,
        stage=stage,
        session_id=session_id,
        chapter_number=chapter_number,
        audit_issue_ids=audit_issue_ids,
        status="needs_clarification",
        questions=decision.questions,
        conversation_turns=[
            MemoryChangeConversationTurn(role="user", content=request, created_at=now),
            MemoryChangeConversationTurn(role="agent", content="\n".join(decision.questions), created_at=now),
        ],
        created_at=now,
        updated_at=now,
    )
    _write_clarification_session(root, clarification)
    return clarification


def _write_clarification_session(root: Path, clarification: MemoryChangeClarificationSession) -> None:
    atomic_write_model_json(_clarification_path(root, clarification.clarification_id), clarification)


def _no_op_setting_change_proposal(
    root: Path,
    request: str,
    *,
    stage: MemoryChangeStage,
    session_id: str | None,
    chapter_number: int | None,
    audit_issue_ids: list[str],
    notes: list[str],
) -> MemoryRepairSuggestResult:
    decision = MemoryRepairDecision(
        change_kind="setting_change",
        target_files=[],
        operations=[],
        domains=[],
        stage=stage,
        confidence=0.0,
        assumptions=[],
        needs_user_confirmation=True,
        notes=notes,
        source="fallback",
    )
    return suggest_memory_repair(
        root,
        request,
        provider_name="mock",
        decision=decision,
        change_kind="setting_change",
        stage=stage,
        session_id=session_id,
        chapter_number=chapter_number,
        audit_issue_ids=audit_issue_ids,
    )


def _combined_setting_change_request(original_request: str, turns: list[MemoryChangeConversationTurn]) -> str:
    answer_lines = [
        f"{index}. {turn.content}"
        for index, turn in enumerate(turns, start=1)
        if turn.role == "user" and turn.content != original_request
    ]
    if not answer_lines:
        return original_request
    return (
        "原始设定变更请求：\n"
        f"{original_request}\n\n"
        "用户补充信息：\n"
        + "\n".join(answer_lines)
    )


def _mock_memory_repair_decision(
    root: Path,
    request: str,
    *,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
) -> MemoryRepairDecision:
    target_files = _mock_infer_target_files(request)
    operations = _mock_infer_operations(root, request, target_files)
    return MemoryRepairDecision(
        change_kind=change_kind or ("setting_change" if _looks_like_setting_change(request) else "memory_repair"),
        target_files=target_files,
        operations=operations,
        domains=_domains_from_files(target_files),
        stage=stage or "unknown",
        confidence=0.8 if operations else 0.2,
        assumptions=["mock provider fixture only; not used as real business inference"],
        needs_user_confirmation=True,
        notes=["mock provider generated deterministic repair proposal for tests."],
        source="mock",
    )


def _mock_infer_target_files(request: str) -> list[str]:
    text = request.lower()
    targets: list[str] = []
    if any(token in text for token in ("timeline", "时间线", "事件", "回忆", "插叙", "倒序")):
        targets.append("memory/state/timeline.json")
    if any(token in text for token in ("state", "状态", "位置", "持有人", "知道", "知识")):
        targets.append("memory/state/current_state.json")
    if any(token in text for token in ("canon", "设定", "角色", "人物", "地点", "物品", "世界观", "背景")):
        targets.extend(["memory/canon/characters.json", "memory/canon/locations.json", "memory/canon/items.json"])
    if any(token in text for token in ("世界", "世界观", "规则", "背景")):
        targets.append("memory/canon/world.json")
    if any(token in text for token in ("隐藏", "真相", "秘密")):
        targets.append("memory/canon/hidden_truths.json")
    if any(token in text for token in ("伏笔", "铺垫")):
        targets.append("memory/canon/foreshadowing.json")
    return sorted(set(targets or ["memory/state/timeline.json"]))


def _mock_infer_operations(root: Path, request: str, target_files: list[str]) -> list[MemoryRepairOperation]:
    operations: list[MemoryRepairOperation] = []
    operations.extend(_mock_infer_setting_operations(root, request, target_files))
    if operations or "memory/state/timeline.json" not in target_files:
        return operations
    event_id = _extract_event_id(request)
    event_role = _mock_infer_event_role(request)
    if not event_id or not event_role:
        return operations
    timeline_path = root / "memory" / "state" / "timeline.json"
    if not timeline_path.exists():
        return operations
    timeline = load_json(timeline_path)
    events = timeline.get("events") if isinstance(timeline, dict) else None
    if not isinstance(events, list):
        return operations
    for index, event in enumerate(events):
        if isinstance(event, dict) and event.get("id") == event_id:
            operations.append(
                MemoryRepairOperation(
                    op="replace" if "event_role" in event else "add",
                    file="memory/state/timeline.json",
                    path=f"/events/{index}/event_role",
                    value=event_role,
                    reason=f"用户指出 timeline event {event_id} 的叙事类型应为 {event_role}",
                )
            )
            break
    return operations


def _looks_like_setting_change(request: str) -> bool:
    return any(token in request for token in ("设定", "人物", "角色", "背景", "世界观", "新增", "增加", "删除", "改成", "修改"))


def _mock_infer_setting_operations(root: Path, request: str, target_files: list[str]) -> list[MemoryRepairOperation]:
    operations: list[MemoryRepairOperation] = []
    if "memory/canon/characters.json" in target_files:
        operations.extend(_mock_character_operations(root, request))
    if "memory/canon/world.json" in target_files:
        operations.extend(_mock_world_operations(root, request))
    return operations


def _mock_character_operations(root: Path, request: str) -> list[MemoryRepairOperation]:
    path = root / "memory/canon/characters.json"
    if not path.exists():
        return []
    characters_data = load_json(path)
    characters = characters_data.get("characters") if isinstance(characters_data, dict) else None
    if not isinstance(characters, list):
        return []
    character_id = _extract_entity_id(request, "char_")
    if character_id is None:
        character_id = _match_character_id_by_name(characters, request)
    if any(token in request for token in ("新增", "增加", "添加", "新人物", "新角色")):
        name = _extract_named_value(request) or "测试人物"
        new_id = character_id or f"char_{_slugify_name(name)}"
        if any(isinstance(item, dict) and item.get("id") == new_id for item in characters):
            return []
        return [
            MemoryRepairOperation(
                op="add",
                file="memory/canon/characters.json",
                path="/characters/-",
                value={
                    "id": new_id,
                    "name": name,
                    "role": "supporting",
                    "reader_visible_summary": f"{name}是用户新增的人物设定。",
                    "aliases": [],
                    "private_author_notes": "由 setting-change mock proposal 新增。",
                    "relationships": [],
                    "abilities": [],
                    "secrets": [],
                    "tags": ["setting_change"],
                },
                reason=f"用户要求新增人物设定：{name}",
            )
        ]
    if not character_id:
        return []
    index = _find_entity_index(characters, character_id)
    if index is None:
        return []
    if any(token in request for token in ("删除", "移除", "删掉")):
        return [
            MemoryRepairOperation(
                op="remove",
                file="memory/canon/characters.json",
                path=f"/characters/{index}",
                reason=f"用户要求删除未被引用的人物设定：{character_id}",
            )
        ]
    new_summary = _extract_after_tokens(request, ("总结为", "摘要为", "设定为", "改成", "修改为"))
    if new_summary:
        return [
            MemoryRepairOperation(
                op="replace",
                file="memory/canon/characters.json",
                path=f"/characters/{index}/reader_visible_summary",
                value=new_summary,
                reason=f"用户要求修改人物 {character_id} 的读者可见设定摘要。",
            )
        ]
    return []


def _mock_world_operations(root: Path, request: str) -> list[MemoryRepairOperation]:
    path = root / "memory/canon/world.json"
    if not path.exists():
        return []
    world_data = load_json(path)
    rules = world_data.get("world_rules") if isinstance(world_data, dict) else None
    if not isinstance(rules, list):
        return []
    rule_id = _extract_entity_id(request, "world_")
    if rule_id is None:
        rule_id = _match_entity_id_by_name(rules, request)
    if not rule_id and any(token in request for token in ("新增", "增加", "添加")):
        name = _extract_named_value(request) or "新世界规则"
        new_id = f"world_{_slugify_name(name)}"
        if any(isinstance(item, dict) and item.get("id") == new_id for item in rules):
            return []
        return [
            MemoryRepairOperation(
                op="add",
                file="memory/canon/world.json",
                path="/world_rules/-",
                value={
                    "id": new_id,
                    "name": name,
                    "description": _extract_after_tokens(request, ("规则为", "设定为", "：", ":")) or f"{name}。",
                    "visibility": "reader_visible",
                    "limitations": [],
                    "known_by_character_ids": [],
                },
                reason=f"用户要求新增世界规则：{name}",
            )
        ]
    if not rule_id:
        return []
    index = _find_entity_index(rules, rule_id)
    if index is None:
        return []
    new_description = _extract_after_tokens(request, ("描述为", "规则为", "设定为", "改成", "修改为"))
    if new_description:
        return [
            MemoryRepairOperation(
                op="replace",
                file="memory/canon/world.json",
                path=f"/world_rules/{index}/description",
                value=new_description,
                reason=f"用户要求修改世界规则 {rule_id}。",
            )
        ]
    return []


def _extract_entity_id(request: str, prefix: str) -> str | None:
    match = re.search(rf"\b({re.escape(prefix)}[a-zA-Z0-9_]+)\b", request)
    return match.group(1) if match else None


def _match_character_id_by_name(characters: list[object], request: str) -> str | None:
    return _match_entity_id_by_name(characters, request)


def _match_entity_id_by_name(entities: list[object], request: str) -> str | None:
    matches: list[str] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_id = entity.get("id")
        name = entity.get("name") or entity.get("title")
        if isinstance(entity_id, str) and isinstance(name, str) and name and name in request:
            matches.append(entity_id)
    return matches[0] if len(matches) == 1 else None


def _find_entity_index(entities: list[object], entity_id: str) -> int | None:
    for index, entity in enumerate(entities):
        if isinstance(entity, dict) and entity.get("id") == entity_id:
            return index
    return None


def _entity_id_has_references(root: Path, entity_id: str, *, exclude_file: str) -> bool:
    for path in _impact_scan_paths(root):
        rel_path = _safe_rel(root, path)
        if rel_path == exclude_file:
            continue
        try:
            if entity_id in path.read_text(encoding="utf-8"):
                return True
        except Exception:
            continue
    return False


def _extract_named_value(request: str) -> str | None:
    patterns = [
        r"(?:新增|增加|添加)(?:一个|一名|人物|角色|设定|世界规则|规则)?[：:\s]*([\u4e00-\u9fffA-Za-z0-9_]{2,24})",
        r"(?:名叫|叫做|名字是|名称是)([\u4e00-\u9fffA-Za-z0-9_]{2,24})",
    ]
    for pattern in patterns:
        match = re.search(pattern, request)
        if match:
            return match.group(1).strip(" ，,。.;；：:")
    return None


def _extract_after_tokens(request: str, tokens: tuple[str, ...]) -> str | None:
    for token in tokens:
        if token in request:
            text = request.split(token, 1)[1].strip()
            return text.strip(" ，,。.;；") or None
    return None


def _slugify_name(name: str) -> str:
    ascii_text = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    if ascii_text:
        return ascii_text[:40]
    codepoints = "_".join(f"{ord(char):x}" for char in name[:6])
    return codepoints or "new_entity"


def _extract_event_id(request: str) -> str | None:
    match = re.search(r"\b(event_[a-zA-Z0-9_]+)\b", request)
    return match.group(1) if match else None


def _mock_infer_event_role(request: str) -> str | None:
    if any(token in request for token in ("回忆", "插叙", "过去")):
        return "flashback"
    if any(token in request for token in ("当前行动", "当前发生", "现在发生")):
        return "current_action"
    if any(token in request for token in ("揭示", "发现真相")):
        return "revelation"
    return None


def _dedupe_domains(domains: list[MemoryChangeDomain]) -> list[MemoryChangeDomain]:
    return sorted(set(domains))


def _domains_from_files(target_files: list[str]) -> list[MemoryChangeDomain]:
    return [domain for path in target_files if (domain := FILE_DOMAINS.get(path))]


def _analyze_memory_change_impact(
    root: Path,
    operations: list[MemoryRepairOperation],
    *,
    domains: list[MemoryChangeDomain],
    session_id: str | None,
    chapter_number: int | None,
) -> MemoryChangeImpact:
    entity_ids = _affected_entity_ids(root, operations)
    affected_files: set[str] = {operation.file for operation in operations}
    affected_chapters: set[int] = set()
    affected_sessions: set[str] = set()
    reference_count = 0

    if chapter_number and chapter_number > 0:
        affected_chapters.add(chapter_number)
    if session_id:
        affected_sessions.add(session_id)

    for chapter in _chapters_from_timeline_operations(root, operations):
        affected_chapters.add(chapter)

    if entity_ids:
        for path in _impact_scan_paths(root):
            rel_path = _safe_rel(root, path)
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if not any(entity_id in text for entity_id in entity_ids):
                continue
            reference_count += 1
            affected_files.add(rel_path)
            chapter = _chapter_number_from_path(path)
            if chapter:
                affected_chapters.add(chapter)
            session = _session_id_from_path(path)
            if session:
                affected_sessions.add(session)

    for session in _sessions_referencing_chapters(root, affected_chapters):
        affected_sessions.add(session)

    stale_chapters = sorted(chapter for chapter in affected_chapters if _chapter_is_accepted(root, chapter))
    risk_level: MemoryRepairRiskLevel = "low"
    if any(operation.op == "remove" for operation in operations) or stale_chapters:
        risk_level = "high"
    elif affected_chapters or len(affected_files) > len({operation.file for operation in operations}):
        risk_level = "medium"
    summary = _impact_summary(entity_ids, affected_chapters, affected_sessions, stale_chapters)
    return MemoryChangeImpact(
        domains=domains,
        entity_ids=sorted(entity_ids),
        affected_files=sorted(affected_files),
        affected_chapters=sorted(affected_chapters),
        affected_sessions=sorted(affected_sessions),
        stale_chapters=stale_chapters,
        risk_level=risk_level,
        reference_count=reference_count,
        summary=summary,
    )


def _memory_change_followups(
    root: Path,
    impact: MemoryChangeImpact,
    *,
    stage: MemoryChangeStage,
    session_id: str | None,
    chapter_number: int | None,
    change_kind: MemoryChangeKind,
) -> list[MemoryChangeFollowupAction]:
    if change_kind != "setting_change":
        return []
    actions: list[MemoryChangeFollowupAction] = []
    session_data = _safe_session_data(root, session_id) if session_id else None
    session_chapters = _session_chapters(session_data)
    affected_chapters = sorted(set(impact.affected_chapters) | ({chapter_number} if chapter_number else set()))
    if session_data:
        status = str(session_data.get("status") or "")
        outline_status = str(session_data.get("outline_status") or "")
        content_status = str(session_data.get("content_status") or "")
        if status in {"accepted", "archived"} or content_status in {"accepted", "archived"}:
            actions.append(
                MemoryChangeFollowupAction(
                    action="start_revision_session",
                    session_id=session_id,
                    chapter_numbers=session_chapters or affected_chapters,
                    auto=False,
                    reason="当前 session 已认可或归档；设定变更不会静默改写既有章节。",
                )
            )
        elif content_status == "not_started":
            actions.append(
                MemoryChangeFollowupAction(
                    action="revise_outline",
                    session_id=session_id,
                    chapter_numbers=session_chapters or affected_chapters,
                    auto=True,
                    reason="设定变更发生在大纲阶段，需要基于最新 memory 重生成 outline proposal。",
                )
            )
            if outline_status == "approved":
                actions.append(
                    MemoryChangeFollowupAction(
                        action="reapprove_outline",
                        session_id=session_id,
                        chapter_numbers=session_chapters or affected_chapters,
                        auto=False,
                        reason="原大纲已批准，重生成后需要用户重新批准。",
                    )
                )
        elif content_status in {"needs_user_review", "needs_revision"} or stage == "content_review":
            actions.extend(
                [
                    MemoryChangeFollowupAction(
                        action="revise_content",
                        session_id=session_id,
                        chapter_numbers=session_chapters or affected_chapters,
                        auto=True,
                        reason="设定变更影响审查中的正文，需要走现有修订链路。",
                    ),
                    MemoryChangeFollowupAction(
                        action="reaudit_chapters",
                        session_id=session_id,
                        chapter_numbers=session_chapters or affected_chapters,
                        auto=True,
                        reason="正文同步设定变更后必须重新 Audit。",
                    ),
                    MemoryChangeFollowupAction(
                        action="rebuild_state_proposal",
                        session_id=session_id,
                        chapter_numbers=session_chapters or affected_chapters,
                        auto=True,
                        reason="正文重审通过后必须重建 state/timeline proposal。",
                    ),
                ]
            )
        else:
            actions.append(
                MemoryChangeFollowupAction(
                    action="manual_review",
                    session_id=session_id,
                    chapter_numbers=session_chapters or affected_chapters,
                    auto=False,
                    reason="当前 session 状态不适合自动同步设定变更。",
                )
            )
    for stale_chapter in impact.stale_chapters:
        actions.append(
            MemoryChangeFollowupAction(
                action="start_revision_session",
                chapter_numbers=[stale_chapter],
                auto=False,
                reason="已 accepted 的章节只标记为受影响，需要用户显式发起修订 session。",
            )
        )
    if not actions:
        actions.append(
            MemoryChangeFollowupAction(
                action="none",
                chapter_numbers=affected_chapters,
                auto=False,
                reason="未发现需要自动同步的当前 session；后续创作会读取最新设定。",
            )
        )
    return actions


def _affected_entity_ids(root: Path, operations: list[MemoryRepairOperation]) -> set[str]:
    ids: set[str] = set()
    for operation in operations:
        if isinstance(operation.value, dict):
            for key in ("id", "entity_id", "hidden_truth_id"):
                value = operation.value.get(key)
                if isinstance(value, str) and value:
                    ids.add(value)
        if isinstance(operation.value, list):
            ids.update(item for item in operation.value if isinstance(item, str) and _looks_like_entity_id(item))
        ids.update(_entity_ids_from_operation_path(root, operation))
    return ids


def _entity_ids_from_operation_path(root: Path, operation: MemoryRepairOperation) -> set[str]:
    path = root / operation.file
    if not path.exists():
        return set()
    try:
        data = load_json(path)
    except Exception:
        return set()
    parts = [_unescape_pointer(part) for part in operation.path.strip("/").split("/") if part]
    if not parts:
        return set()
    ids: set[str] = set()
    collection_key = FILE_COLLECTION_KEYS.get(operation.file)
    if collection_key and parts[0] == collection_key and len(parts) >= 2:
        entity = _list_entity_at(data, collection_key, parts[1])
        ids.update(_ids_from_entity(entity))
    elif operation.file == "memory/state/current_state.json" and parts[0] in STATE_COLLECTION_KEYS and len(parts) >= 2:
        entity = _list_entity_at(data, parts[0], parts[1])
        ids.update(_ids_from_entity(entity))
    elif operation.file == "memory/state/timeline.json" and parts[0] == "events" and len(parts) >= 2:
        entity = _list_entity_at(data, "events", parts[1])
        ids.update(_ids_from_entity(entity))
    return ids


def _list_entity_at(data: object, collection_key: str, index_text: str) -> object | None:
    if not isinstance(data, dict):
        return None
    collection = data.get(collection_key)
    if not isinstance(collection, list) or not index_text.isdigit():
        return None
    index = int(index_text)
    if index < 0 or index >= len(collection):
        return None
    return collection[index]


def _ids_from_entity(entity: object | None) -> set[str]:
    if not isinstance(entity, dict):
        return set()
    ids: set[str] = set()
    for key in ("id", "entity_id", "hidden_truth_id"):
        value = entity.get(key)
        if isinstance(value, str) and value:
            ids.add(value)
    for key in ("related_entity_ids", "participant_ids", "known_by_character_ids", "state_change_ids"):
        value = entity.get(key)
        if isinstance(value, list):
            ids.update(item for item in value if isinstance(item, str) and _looks_like_entity_id(item))
    return ids


def _chapters_from_timeline_operations(root: Path, operations: list[MemoryRepairOperation]) -> set[int]:
    chapters: set[int] = set()
    timeline_path = root / "memory/state/timeline.json"
    if not timeline_path.exists():
        return chapters
    try:
        timeline = load_json(timeline_path)
    except Exception:
        return chapters
    for operation in operations:
        if operation.file != "memory/state/timeline.json":
            continue
        parts = [_unescape_pointer(part) for part in operation.path.strip("/").split("/") if part]
        if len(parts) < 2 or parts[0] != "events":
            continue
        event = _list_entity_at(timeline, "events", parts[1])
        if isinstance(event, dict):
            chapter = _coerce_positive_int(event.get("chapter"))
            narrative = event.get("narrative_position")
            if not chapter and isinstance(narrative, dict):
                chapter = _coerce_positive_int(narrative.get("chapter"))
            if chapter:
                chapters.add(chapter)
    return chapters


def _impact_scan_paths(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for base in (root / "memory" / "canon", root / "memory" / "state", root / "memory" / "chapters", root / "memory" / "sessions"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in SCANNED_IMPACT_SUFFIXES:
                candidates.append(path)
    return candidates


def _chapter_number_from_path(path: Path) -> int | None:
    for part in path.parts:
        if re.fullmatch(r"[0-9]{3}", part):
            return int(part)
    return None


def _session_id_from_path(path: Path) -> str | None:
    for part in path.parts:
        if re.fullmatch(r"session_[0-9]{8}_[0-9]{6}_[0-9]{6}", part):
            return part
    return None


def _sessions_referencing_chapters(root: Path, chapters: set[int]) -> set[str]:
    if not chapters:
        return set()
    sessions_dir = root / "memory" / "sessions"
    if not sessions_dir.exists():
        return set()
    sessions: set[str] = set()
    for session_json in sessions_dir.glob("session_*/session.json"):
        try:
            data = load_json(session_json)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        chapter_range = {
            number for item in data.get("chapter_range", []) if (number := _coerce_positive_int(item))
        }
        if chapter_range & chapters:
            sessions.add(session_json.parent.name)
    return sessions


def _chapter_is_accepted(root: Path, chapter_number: int) -> bool:
    metadata_path = root / "memory" / "chapters" / f"{chapter_number:03d}" / "metadata.json"
    if not metadata_path.exists():
        return False
    try:
        data = load_json(metadata_path)
    except Exception:
        return False
    return isinstance(data, dict) and data.get("status") == "accepted"


def _safe_session_data(root: Path, session_id: str | None) -> dict[str, object] | None:
    if not session_id:
        return None
    path = root / "memory" / "sessions" / session_id / "session.json"
    if not path.exists():
        return None
    try:
        data = load_json(path)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _session_chapters(session_data: dict[str, object] | None) -> list[int]:
    if not session_data:
        return []
    chapters: list[int] = []
    chapter_range = session_data.get("chapter_range", [])
    if not isinstance(chapter_range, list):
        return []
    for item in chapter_range:
        number = _coerce_positive_int(item)
        if number:
            chapters.append(number)
    return sorted(set(chapters))


def _impact_summary(
    entity_ids: set[str],
    affected_chapters: set[int],
    affected_sessions: set[str],
    stale_chapters: list[int],
) -> str:
    parts = []
    if entity_ids:
        parts.append(f"涉及实体 {', '.join(sorted(entity_ids))}")
    if affected_chapters:
        parts.append("影响章节 " + ", ".join(str(number) for number in sorted(affected_chapters)))
    if affected_sessions:
        parts.append("影响 session " + ", ".join(sorted(affected_sessions)))
    if stale_chapters:
        parts.append("其中已认可章节 " + ", ".join(str(number) for number in stale_chapters))
    return "；".join(parts) if parts else "未发现章节或 session 引用；后续创作会使用最新 memory。"


def _coerce_positive_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _looks_like_entity_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]*", value))


def _safe_rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _proposal_notes(
    target_files: list[str],
    operations: list[MemoryRepairOperation],
    decision_notes: list[str],
    *,
    change_kind: MemoryChangeKind,
    audit_issue_ids: list[str],
) -> list[str]:
    notes = ["项目管家只生成 proposal，不会静默修改正式 memory 文件。"]
    if change_kind == "setting_change":
        notes.append("设定变更会先进行影响分析；已 accepted/archived 章节不会被静默改写。")
    if audit_issue_ids:
        notes.append("该 proposal 来源于 Audit issue：" + ", ".join(audit_issue_ids))
    notes.extend(decision_notes)
    if not operations:
        notes.append("没有足够信息生成可安全自动应用的 patch；请在请求中提供具体 event/entity id。")
    if "memory/state/timeline.json" in target_files:
        notes.append("timeline 修复应区分 narrative_position 与 story_position。")
    return notes


def _extract_json_object(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise MemoryRepairError("provider response did not contain a JSON object")
    return stripped[start : end + 1]


def _normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    normalized.append(text)
            elif item is not None:
                normalized.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
        return normalized
    return [json.dumps(value, ensure_ascii=False, sort_keys=True)]


def _preview_operations(root: Path, proposal: MemoryRepairProposal) -> dict[str, object]:
    preview: dict[str, object] = {}
    for rel_path, operations in _group_operations(proposal.operations).items():
        try:
            preview[rel_path] = _apply_operations_to_data(load_json(root / rel_path), operations)
        except Exception as exc:
            preview[rel_path] = {"error": str(exc)}
    return preview


def _preflight_memory_repair_operations(
    root: Path,
    operations: list[MemoryRepairOperation],
    *,
    change_kind: MemoryChangeKind | None = None,
) -> list[str]:
    if not operations:
        return []
    errors: list[str] = []
    try:
        grouped = _group_operations(operations)
    except Exception as exc:
        return [str(exc)]
    for rel_path, file_operations in grouped.items():
        try:
            data = load_json(root / rel_path)
            updated = _apply_operations_to_data(data, file_operations)
            _validate_file_model(rel_path, updated)
        except Exception as exc:
            errors.append(f"{rel_path}: {exc}")
    if change_kind == "setting_change":
        errors.extend(_preflight_setting_change_semantics(operations))
    return errors


def _preflight_setting_change_semantics(operations: list[MemoryRepairOperation]) -> list[str]:
    errors: list[str] = []
    for operation in operations:
        if operation.file != "memory/canon/characters.json":
            continue
        parts = _pointer_parts(operation.path)
        if len(parts) < 2 or parts[0] != "characters":
            continue
        location = _operation_semantic_location(operation)
        if operation.op in {"add", "replace"} and len(parts) == 2 and isinstance(operation.value, dict):
            errors.extend(_preflight_character_role_semantics(operation.value, location))
        elif operation.op in {"add", "replace"} and len(parts) == 3 and parts[2] == "role" and isinstance(operation.value, str):
            errors.extend(_preflight_character_role_value(operation.value, location))
    return errors


def _preflight_character_role_semantics(character: dict[str, object], location: str) -> list[str]:
    errors = _preflight_character_role_value(character.get("role"), location)
    tags = _string_values(character.get("tags"))
    identity_phrases = _character_identity_phrases_from_fields(character)
    missing_tags = [phrase for phrase in identity_phrases if phrase not in tags]
    if missing_tags:
        errors.append(
            f"{location}: Character identity phrase(s) must be in tags, not only summary/notes/role: "
            + ", ".join(missing_tags[:8])
        )
    return errors


def _preflight_character_role_value(value: object, location: str) -> list[str]:
    if not isinstance(value, str):
        return []
    role = value.strip()
    if not role:
        return []
    if role.lower() in NARRATIVE_CHARACTER_ROLES:
        return []
    phrases = _identity_phrases(role)
    if not phrases:
        return []
    return [
        f"{location}: Character.role semantic preflight failed: role={role!r} looks like identity/rank/profession, "
        "but role must be narrative role only. Use 主角/主要人物/配角/次要人物 or compatible legacy protagonist/supporting/minor/antagonist, "
        "and move identity phrase(s) into tags: "
        + ", ".join(phrases[:8])
    ]


def _character_identity_phrases_from_fields(character: dict[str, object]) -> list[str]:
    phrases: list[str] = []
    for key in ("role", "reader_visible_summary", "private_author_notes"):
        value = character.get(key)
        if isinstance(value, str):
            phrases.extend(_identity_phrases(value))
    return _dedupe_preserve_order(phrases)


def _identity_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for pattern in CHARACTER_ROLE_IDENTITY_PATTERNS:
        for match in pattern.finditer(text):
            phrase = next((group for group in reversed(match.groups()) if group), match.group(0)).strip()
            phrases.append(_trim_identity_phrase(phrase))
    return _dedupe_preserve_order(phrase for phrase in phrases if phrase and phrase.lower() not in NARRATIVE_CHARACTER_ROLES)


def _trim_identity_phrase(phrase: str) -> str:
    cleaned = phrase.strip()
    for marker in ("身为", "作为", "是", "为", "乃"):
        if marker in cleaned:
            cleaned = cleaned.rsplit(marker, 1)[-1].strip()
    return cleaned


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _operation_semantic_location(operation: MemoryRepairOperation) -> str:
    label = f"{operation.file} {operation.path}"
    if isinstance(operation.value, dict):
        item_id = operation.value.get("id")
        name = operation.value.get("name")
        details = [str(value) for value in (item_id, name) if isinstance(value, str) and value]
        if details:
            label += f" ({'/'.join(details)})"
    return label


def _pointer_parts(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        return []
    return [_unescape_pointer(part) for part in pointer.strip("/").split("/") if part]


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _format_preflight_errors(errors: list[str], *, max_chars: int = 10000) -> str:
    text = "\n".join(f"- {error}" for error in errors)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n... truncated ..."


def _group_operations(operations: list[MemoryRepairOperation]) -> dict[str, list[MemoryRepairOperation]]:
    grouped: dict[str, list[MemoryRepairOperation]] = {}
    for operation in operations:
        _ensure_allowed_file(operation.file)
        grouped.setdefault(operation.file, []).append(operation)
    return grouped


def _apply_operations_to_data(data: object, operations: list[MemoryRepairOperation]) -> object:
    updated = json.loads(json.dumps(data, ensure_ascii=False))
    for operation in operations:
        _apply_operation(updated, operation)
    return updated


def _apply_operation(data: object, operation: MemoryRepairOperation) -> None:
    parent, key = _resolve_pointer_parent(data, operation.path)
    if operation.op == "replace":
        if isinstance(parent, list):
            parent[int(key)] = operation.value
        elif isinstance(parent, dict):
            if key not in parent:
                raise MemoryRepairError(f"replace path does not exist: {operation.path}")
            parent[key] = operation.value
        return
    if operation.op == "add":
        if isinstance(parent, list):
            if key == "-":
                parent.append(operation.value)
            else:
                parent.insert(int(key), operation.value)
        elif isinstance(parent, dict):
            parent[key] = operation.value
        return
    if operation.op == "remove":
        if isinstance(parent, list):
            parent.pop(int(key))
        elif isinstance(parent, dict):
            if key not in parent:
                raise MemoryRepairError(f"remove path does not exist: {operation.path}")
            del parent[key]


def _resolve_pointer_parent(data: object, pointer: str) -> tuple[Any, str]:
    if not pointer.startswith("/"):
        raise MemoryRepairError(f"invalid JSON pointer: {pointer}")
    parts = [_unescape_pointer(part) for part in pointer.strip("/").split("/")]
    if not parts:
        raise MemoryRepairError("operation path cannot target the document root")
    target: Any = data
    for part in parts[:-1]:
        if isinstance(target, list):
            target = target[int(part)]
        elif isinstance(target, dict):
            target = target[part]
        else:
            raise MemoryRepairError(f"invalid JSON pointer path: {pointer}")
    return target, parts[-1]


def _unescape_pointer(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _ensure_allowed_file(rel_path: str) -> None:
    if rel_path not in ALLOWED_MEMORY_FILES:
        raise MemoryRepairError(f"memory repair target is not allowed: {rel_path}")


def _validate_file_model(rel_path: str, data: object) -> None:
    model = ALLOWED_MEMORY_FILES[rel_path]
    try:
        model.model_validate(data)
    except ValidationError as exc:
        raise MemoryRepairError(f"schema validation failed for {rel_path}: {exc}") from exc


def _restore_backups(root: Path, touched_files: list[str], backups: list[str]) -> None:
    for rel_path, backup_rel in zip(touched_files, backups, strict=False):
        backup_path = root / backup_rel
        if backup_path.exists():
            shutil.copy2(backup_path, root / rel_path)


def _resolve_proposal_path(root: Path, proposal_path: Path) -> Path:
    path = proposal_path if proposal_path.is_absolute() else root / proposal_path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise MemoryRepairError("repair proposal must be inside the project workspace") from exc
    if path.name != "proposal.json" or "memory/repairs" not in str(path.relative_to(root)):
        raise MemoryRepairError("repair proposal must be memory/repairs/{repair_id}/proposal.json")
    return path


def _repair_dir(root: Path, repair_id: str) -> Path:
    return root / "memory" / "repairs" / repair_id


def _clarification_dir(root: Path, clarification_id: str) -> Path:
    if not re.fullmatch(r"clarify_[0-9]{8}_[0-9]{6}_[0-9]{6}", clarification_id):
        raise MemoryRepairError("invalid setting change clarification id")
    return root / "memory" / "repairs" / "clarifications" / clarification_id


def _clarification_path(root: Path, clarification_id: str) -> Path:
    path = _clarification_dir(root, clarification_id) / "session.json"
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise MemoryRepairError("setting change clarification must be inside the project workspace") from exc
    return resolved


def _new_repair_id() -> str:
    return "repair_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _new_clarification_id() -> str:
    return "clarify_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
