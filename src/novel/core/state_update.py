from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from novel.core.agent_output import (
    AgentInvocationContext,
    AgentOutputContract,
    generate_with_output_guard,
)
from novel.core.canon import format_canon_summary, load_canon_files
from novel.core.io import atomic_write_model_json, atomic_write_text, backup_file, backup_if_exists, load_json_model, load_yaml_model
from novel.core.management import record_management_event
from novel.core.polishing import DraftDocument, PolishingError, read_markdown_with_front_matter
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.prompts import load_prompt_template
from novel.core.schemas import (
    AuditReport,
    ChapterMetadata,
    ChapterPlan,
    CharacterState,
    EntityState,
    ItemState,
    LocationState,
    ProjectConfig,
    StateChange,
    StateUpdateApplyLog,
    StateUpdateProposal,
    TimelineFile,
)
from novel.core.validation import validate_canon


class StateUpdateError(RuntimeError):
    """Raised when state or timeline update cannot proceed safely."""


@dataclass(frozen=True)
class StateUpdateProposeOptions:
    root: Path
    chapter_number: int
    instruction: str | None = None
    force: bool = False
    allow_unresolved_audit: bool = False


@dataclass(frozen=True)
class StateUpdateApplyOptions:
    root: Path
    chapter_number: int


@dataclass(frozen=True)
class AcceptChapterOptions:
    root: Path
    chapter_number: int
    allow_issues: bool = False
    propose: bool = False
    instruction: str | None = None
    force_proposal: bool = False


@dataclass(frozen=True)
class StateUpdateProposeResult:
    proposal_path: Path
    proposal: StateUpdateProposal
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StateUpdateApplyResult:
    state_path: Path
    timeline_path: Path
    state_backup_path: Path
    timeline_backup_path: Path
    apply_log_path: Path
    apply_log: StateUpdateApplyLog
    state: EntityState
    timeline: TimelineFile


@dataclass(frozen=True)
class AcceptChapterResult:
    proposal_result: StateUpdateProposeResult | None
    apply_result: StateUpdateApplyResult
    accepted_path: Path
    metadata_path: Path
    metadata: ChapterMetadata


@dataclass(frozen=True)
class StateUpdateContext:
    project: ProjectConfig
    plan: ChapterPlan
    polished: DraftDocument
    audit: AuditReport
    canon_summary: str
    state_json: str
    timeline_json: str


def propose_state_update(
    options: StateUpdateProposeOptions,
    provider: ModelProvider,
) -> StateUpdateProposeResult:
    root = options.root.resolve()
    if options.chapter_number < 1:
        raise StateUpdateError("chapter_number must be a positive integer")
    chapter_dir = _chapter_dir(root, options.chapter_number)
    proposal_path = chapter_dir / "state_update_proposal.json"
    _refuse_existing(proposal_path, options.force)

    context = load_state_update_context(root, options.chapter_number)
    _ensure_audit_allows_progress(context.audit, allow_issues=options.allow_unresolved_audit)

    user_prompt = build_state_update_user_prompt(
        context=context,
        instruction=options.instruction,
    )
    proposal, warnings = _generate_state_update_proposal_with_repair(provider, context, user_prompt, options)

    if options.force:
        backup_if_exists(proposal_path, reason="force")
    atomic_write_model_json(proposal_path, proposal)
    record_management_event(
        root,
        "state_update_proposed",
        f"已生成第 {options.chapter_number} 章状态/时间线更新 proposal。",
        source=f"chapter_{options.chapter_number:03d}",
        target_files=[str(proposal_path.relative_to(root))],
        status="info",
        details={"warning_count": len(warnings)},
    )
    return StateUpdateProposeResult(
        proposal_path=proposal_path,
        proposal=proposal,
        warnings=tuple(warnings),
    )


def apply_state_update(options: StateUpdateApplyOptions) -> StateUpdateApplyResult:
    root = options.root.resolve()
    if options.chapter_number < 1:
        raise StateUpdateError("chapter_number must be a positive integer")
    proposal_path = _chapter_dir(root, options.chapter_number) / "state_update_proposal.json"
    if not proposal_path.exists():
        raise StateUpdateError(f"{proposal_path} is missing; run novel propose-state-update first")

    proposal = parse_state_update_proposal(proposal_path.read_text(encoding="utf-8"))
    if proposal.chapter_number != options.chapter_number:
        raise StateUpdateError(
            f"state_update_proposal.json chapter_number {proposal.chapter_number} does not match "
            f"requested chapter {options.chapter_number}"
        )
    validate_state_update_proposal(root, proposal, check_existing_timeline_ids=True)

    state_path = root / "memory" / "state" / "current_state.json"
    timeline_path = root / "memory" / "state" / "timeline.json"
    state = load_json_model(state_path, EntityState)
    timeline = load_json_model(timeline_path, TimelineFile)

    updated_state = apply_state_changes_to_state(state, proposal.state_changes, root)
    updated_timeline = TimelineFile(events=[*timeline.events, *proposal.timeline_events])

    _validate_state_change_old_values(state, proposal.state_changes, root)
    _validate_applied_state(updated_state)
    _validate_applied_timeline(root, updated_timeline)

    state_backup = backup_file(state_path, reason="state_update")
    timeline_backup = backup_file(timeline_path, reason="state_update")
    apply_log_path = _chapter_dir(root, options.chapter_number) / "state_update_apply_log.json"
    apply_log = _new_apply_log(
        chapter_number=options.chapter_number,
        root=root,
        proposal_path=proposal_path,
        state_path=state_path,
        timeline_path=timeline_path,
        state_backup_path=state_backup,
        timeline_backup_path=timeline_backup,
        status="applied",
    )
    try:
        atomic_write_model_json(state_path, updated_state)
        atomic_write_model_json(timeline_path, updated_timeline)
    except Exception as exc:
        shutil.copy2(state_backup, state_path)
        shutil.copy2(timeline_backup, timeline_path)
        apply_log = apply_log.model_copy(
            update={
                "status": "rolled_back",
                "errors": [f"{exc.__class__.__name__}: {exc}"],
            }
        )
        atomic_write_model_json(apply_log_path, apply_log)
        record_management_event(
            root,
            "state_update_applied",
            f"第 {options.chapter_number} 章状态更新失败并已回滚。",
            source=f"chapter_{options.chapter_number:03d}",
            target_files=[str(state_path.relative_to(root)), str(timeline_path.relative_to(root))],
            status="error",
            details={"apply_log_path": str(apply_log_path.relative_to(root)), "error": str(exc)},
        )
        raise StateUpdateError(
            f"state update write failed and was rolled back; see {apply_log_path}"
        ) from exc
    atomic_write_model_json(apply_log_path, apply_log)
    target_files = [str(state_path.relative_to(root)), str(timeline_path.relative_to(root))]
    record_management_event(
        root,
        "state_update_applied",
        f"已应用第 {options.chapter_number} 章状态更新。",
        source=f"chapter_{options.chapter_number:03d}",
        target_files=target_files,
        status="success",
        details={"apply_log_path": str(apply_log_path.relative_to(root))},
    )
    record_management_event(
        root,
        "timeline_updated",
        f"已追加第 {options.chapter_number} 章时间线事件。",
        source=f"chapter_{options.chapter_number:03d}",
        target_files=[str(timeline_path.relative_to(root))],
        status="success",
        details={"event_count": len(proposal.timeline_events)},
    )
    return StateUpdateApplyResult(
        state_path=state_path,
        timeline_path=timeline_path,
        state_backup_path=state_backup,
        timeline_backup_path=timeline_backup,
        apply_log_path=apply_log_path,
        apply_log=apply_log,
        state=updated_state,
        timeline=updated_timeline,
    )


def accept_chapter(options: AcceptChapterOptions, provider: ModelProvider | None = None) -> AcceptChapterResult:
    root = options.root.resolve()
    audit = _load_audit(root, options.chapter_number)
    _ensure_audit_allows_progress(audit, allow_issues=options.allow_issues)

    proposal_result: StateUpdateProposeResult | None = None
    proposal_path = _chapter_dir(root, options.chapter_number) / "state_update_proposal.json"
    if not proposal_path.exists():
        if not options.propose:
            raise StateUpdateError(
                f"{proposal_path} is missing; run novel propose-state-update first or pass --propose"
            )
        if provider is None:
            raise StateUpdateError("--propose requires a provider")
        proposal_result = propose_state_update(
            StateUpdateProposeOptions(
                root=root,
                chapter_number=options.chapter_number,
                instruction=options.instruction,
                force=options.force_proposal,
                allow_unresolved_audit=options.allow_issues,
            ),
            provider,
        )

    apply_result = _load_existing_apply_result(root, options.chapter_number) or apply_state_update(
        StateUpdateApplyOptions(root=root, chapter_number=options.chapter_number)
    )
    accepted_path = mark_chapter_accepted(root, options.chapter_number)
    metadata_path = write_chapter_metadata(
        root,
        options.chapter_number,
        status="accepted",
        apply_log_path=apply_result.apply_log_path,
    )
    metadata = load_json_model(metadata_path, ChapterMetadata)
    return AcceptChapterResult(
        proposal_result=proposal_result,
        apply_result=apply_result,
        accepted_path=accepted_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _load_existing_apply_result(root: Path, chapter_number: int) -> StateUpdateApplyResult | None:
    apply_log_path = _chapter_dir(root, chapter_number) / "state_update_apply_log.json"
    if not apply_log_path.exists():
        return None
    apply_log = load_json_model(apply_log_path, StateUpdateApplyLog)
    if apply_log.chapter_number != chapter_number:
        raise StateUpdateError(
            f"state_update_apply_log.json chapter_number {apply_log.chapter_number} does not match "
            f"requested chapter {chapter_number}"
        )
    if apply_log.status != "applied":
        raise StateUpdateError(f"existing state update apply log is not applied: {apply_log.status}")
    state_path = root / apply_log.state_path
    timeline_path = root / apply_log.timeline_path
    return StateUpdateApplyResult(
        state_path=state_path,
        timeline_path=timeline_path,
        state_backup_path=root / apply_log.state_backup_path,
        timeline_backup_path=root / apply_log.timeline_backup_path,
        apply_log_path=apply_log_path,
        apply_log=apply_log,
        state=load_json_model(state_path, EntityState),
        timeline=load_json_model(timeline_path, TimelineFile),
    )


def load_state_update_context(root: Path, chapter_number: int) -> StateUpdateContext:
    chapter_dir = _chapter_dir(root, chapter_number)
    plan_path = chapter_dir / "plan.json"
    polished_path = chapter_dir / "polished.md"
    audit_path = chapter_dir / "audit.json"
    if not plan_path.exists():
        raise StateUpdateError(f"{plan_path} is missing; run novel plan-chapter first")
    if not polished_path.exists():
        raise StateUpdateError(f"{polished_path} is missing; run novel polish-chapter first")
    if not audit_path.exists():
        raise StateUpdateError(f"{audit_path} is missing; run novel audit-chapter first")

    canon = load_canon_files(root)
    return StateUpdateContext(
        project=load_yaml_model(root / "project.yaml", ProjectConfig),
        plan=load_json_model(plan_path, ChapterPlan),
        polished=_read_front_matter(polished_path),
        audit=load_json_model(audit_path, AuditReport),
        canon_summary=format_canon_summary(canon),
        state_json=(root / "memory" / "state" / "current_state.json").read_text(encoding="utf-8"),
        timeline_json=(root / "memory" / "state" / "timeline.json").read_text(encoding="utf-8"),
    )


def load_state_update_provider(
    root: Path,
    provider_name: str,
    *,
    chapter_number: int = 1,
    agent_config_path: Path | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    return create_agent_provider(
        agent_config_path or default_agent_config_path(root),
        "state_update",
        fallback_agents=("audit",),
        overrides=ProviderOverrides(provider_name=provider_name, model_name=model_name),
        mock_response=default_mock_state_update_proposal_json(chapter_number),
    )


def read_state_update_instruction(instruction: str | None, input_path: Path | None) -> str | None:
    if instruction and input_path:
        raise StateUpdateError("provide either --instruction or --input, not both")
    if input_path:
        if not input_path.exists():
            raise StateUpdateError(f"state update instruction input file is missing: {input_path}")
        return input_path.read_text(encoding="utf-8").strip() or None
    return instruction.strip() if instruction and instruction.strip() else None


def validate_state_update_proposal(
    root: Path,
    proposal: StateUpdateProposal,
    *,
    check_existing_timeline_ids: bool,
) -> list[str]:
    warnings: list[str] = []
    canon = load_canon_files(root)
    character_ids = {item.id for item in canon.characters.characters}
    location_ids = {item.id for item in canon.locations.locations}
    item_ids = {item.id for item in canon.items.items}
    entity_ids = character_ids | location_ids | item_ids | {"story_position"}

    _require_unique([change.id for change in proposal.state_changes], "state_change id")
    _require_unique([event.id for event in proposal.timeline_events], "timeline event id")

    for change in proposal.state_changes:
        if change.entity_id not in entity_ids:
            raise StateUpdateError(f"state change {change.id} references missing entity: {change.entity_id}")
        _validate_state_change_field(change, character_ids, location_ids, item_ids)

    for event in proposal.timeline_events:
        _validate_timeline_event_compatibility(event)
        if event.location_id and event.location_id not in location_ids:
            raise StateUpdateError(f"timeline event {event.id} references missing location: {event.location_id}")
        for participant_id in event.participant_ids:
            if participant_id not in character_ids:
                raise StateUpdateError(
                    f"timeline event {event.id} references missing participant: {participant_id}"
                )
        missing_change_ids = sorted(set(event.state_change_ids) - {change.id for change in proposal.state_changes})
        if missing_change_ids:
            raise StateUpdateError(
                f"timeline event {event.id} references missing state changes: {', '.join(missing_change_ids)}"
            )

    _validate_proposed_timeline_scene_bounds(root, proposal)
    _validate_proposed_item_holder_location_conflicts(root, proposal, item_ids)

    if check_existing_timeline_ids:
        timeline = load_json_model(root / "memory" / "state" / "timeline.json", TimelineFile)
        existing_event_ids = {event.id for event in timeline.events}
        conflicts = sorted(existing_event_ids & {event.id for event in proposal.timeline_events})
        if conflicts:
            raise StateUpdateError(f"timeline event id conflict: {', '.join(conflicts)}")
    _validate_proposed_timeline_monotonic(root, proposal)

    canon_report = validate_canon(root)
    for message in canon_report.errors:
        raise StateUpdateError(f"canon validation error blocks state update: {message.message}")
    for message in canon_report.warnings:
        warnings.append(f"canon warning: {message.message}")
    return warnings


def _validate_timeline_event_compatibility(event) -> None:
    if event.chapter != event.narrative_position.chapter:
        raise StateUpdateError(f"timeline event {event.id} chapter must match narrative_position.chapter")
    if event.scene != event.narrative_position.scene:
        raise StateUpdateError(f"timeline event {event.id} scene must match narrative_position.scene")
    if event.in_story_time != event.story_position.time_label:
        raise StateUpdateError(f"timeline event {event.id} in_story_time must match story_position.time_label")


def _validate_proposed_timeline_scene_bounds(root: Path, proposal: StateUpdateProposal) -> None:
    plan_path = root / "memory" / "chapters" / f"{proposal.chapter_number:03d}" / "plan.json"
    if not plan_path.exists():
        return
    plan = load_json_model(plan_path, ChapterPlan)
    scene_count = len(plan.scenes)
    for event in proposal.timeline_events:
        scene = event.narrative_position.scene
        if scene and scene > scene_count:
            raise StateUpdateError(
                f"timeline event {event.id} narrative_position.scene {scene} exceeds ChapterPlan scene count {scene_count}"
            )


def _validate_proposed_timeline_monotonic(root: Path, proposal: StateUpdateProposal) -> None:
    if not proposal.timeline_events:
        return
    timeline = load_json_model(root / "memory" / "state" / "timeline.json", TimelineFile)
    existing_max = max((_timeline_event_key(event) for event in timeline.events), default=None)
    previous = existing_max
    for event in proposal.timeline_events:
        key = _timeline_event_key(event)
        if previous is not None and key < previous:
            raise StateUpdateError(
                "timeline event order conflict: "
                f"{event.id} chapter={event.narrative_position.chapter}, "
                f"scene={event.narrative_position.scene}, sequence={event.narrative_position.sequence} "
                "would be ordered before "
                f"existing or previous event key chapter={previous[0]}, scene={previous[1]}, sequence={previous[2]}. "
                "Regenerate the state update proposal with monotonically increasing narrative_position values."
            )
        previous = key


def _timeline_event_key(event) -> tuple[int, int, int]:
    narrative = event.narrative_position
    return (int(narrative.chapter), int(narrative.scene or 0), int(narrative.sequence or 0))


def _validate_proposed_item_holder_location_conflicts(
    root: Path,
    proposal: StateUpdateProposal,
    item_ids: set[str],
) -> None:
    state = load_json_model(root / "memory" / "state" / "current_state.json", EntityState)
    item_positions = {
        item.entity_id: {"holder_id": item.holder_id, "location_id": item.location_id}
        for item in state.item_states
    }
    for item_id in item_ids:
        item_positions.setdefault(item_id, {"holder_id": None, "location_id": None})
    for change in proposal.state_changes:
        if change.entity_id not in item_ids or change.field not in {"holder_id", "location_id"}:
            continue
        item_positions.setdefault(change.entity_id, {"holder_id": None, "location_id": None})
        item_positions[change.entity_id][change.field] = change.new_value
    conflicts = [
        item_id
        for item_id, position in item_positions.items()
        if position.get("holder_id") and position.get("location_id")
    ]
    if conflicts:
        raise StateUpdateError(
            "item holder/location conflict in proposed changes: "
            + ", ".join(sorted(conflicts))
            + ". Set either holder_id or location_id, not both."
        )


def apply_state_changes_to_state(
    state: EntityState,
    changes: list[StateChange],
    root: Path,
) -> EntityState:
    canon = load_canon_files(root)
    character_ids = {item.id for item in canon.characters.characters}
    location_ids = {item.id for item in canon.locations.locations}
    item_ids = {item.id for item in canon.items.items}

    updated = state.model_copy(deep=True)
    character_states = {item.entity_id: item for item in updated.character_states}
    item_states = {item.entity_id: item for item in updated.item_states}
    location_states = {item.entity_id: item for item in updated.location_states}

    for change in changes:
        if change.entity_id == "story_position":
            _apply_model_field(updated.story_position, change.field, change.new_value, change.id)
            continue
        if change.entity_id in character_ids:
            target = character_states.get(change.entity_id)
            if target is None:
                target = CharacterState(entity_id=change.entity_id, last_updated_chapter=change.chapter)
                updated.character_states.append(target)
                character_states[change.entity_id] = target
        elif change.entity_id in item_ids:
            target = item_states.get(change.entity_id)
            if target is None:
                target = ItemState(entity_id=change.entity_id, last_updated_chapter=change.chapter)
                updated.item_states.append(target)
                item_states[change.entity_id] = target
        elif change.entity_id in location_ids:
            target = location_states.get(change.entity_id)
            if target is None:
                target = LocationState(entity_id=change.entity_id, last_updated_chapter=change.chapter)
                updated.location_states.append(target)
                location_states[change.entity_id] = target
        else:
            raise StateUpdateError(f"state change {change.id} references missing entity: {change.entity_id}")
        _apply_model_field(target, change.field, change.new_value, change.id)
        if hasattr(target, "last_updated_chapter"):
            setattr(target, "last_updated_chapter", change.chapter)

    if updated.story_position.latest_chapter < max((change.chapter for change in changes), default=0):
        updated.story_position.latest_chapter = max(change.chapter for change in changes)
    return EntityState.model_validate(updated.model_dump(mode="json"))


def mark_chapter_accepted(root: Path, chapter_number: int) -> Path:
    path = _chapter_dir(root, chapter_number) / "polished.md"
    document = _read_front_matter(path)
    metadata = dict(document.metadata)
    metadata["status"] = "accepted"
    metadata["accepted_at"] = _utc_now()
    metadata_text = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
    backup_if_exists(path, reason="accept")
    atomic_write_text(path, f"---\n{metadata_text}\n---\n\n{document.body.strip()}\n")
    return path


def write_chapter_metadata(
    root: Path,
    chapter_number: int,
    *,
    status: Literal["planned", "drafted", "polished", "audited", "accepted"],
    apply_log_path: Path | None = None,
) -> Path:
    chapter_dir = _chapter_dir(root, chapter_number)
    metadata_path = chapter_dir / "metadata.json"
    accepted_at = _utc_now() if status == "accepted" else None
    metadata = ChapterMetadata(
        chapter_number=chapter_number,
        status=status,
        plan_path=_relative_if_exists(root, chapter_dir / "plan.json"),
        draft_path=_relative_if_exists(root, chapter_dir / "draft.md"),
        polished_path=_relative_if_exists(root, chapter_dir / "polished.md"),
        audit_path=_relative_if_exists(root, chapter_dir / "audit.json"),
        state_update_proposal_path=_relative_if_exists(root, chapter_dir / "state_update_proposal.json"),
        state_update_apply_log_path=str(apply_log_path.relative_to(root)) if apply_log_path else None,
        accepted_at=accepted_at,
        updated_at=_utc_now(),
    )
    backup_if_exists(metadata_path, reason="metadata")
    atomic_write_model_json(metadata_path, metadata)
    return metadata_path


def load_chapter_metadata(root: Path, chapter_number: int) -> ChapterMetadata | None:
    path = _chapter_dir(root, chapter_number) / "metadata.json"
    if not path.exists():
        return None
    return load_json_model(path, ChapterMetadata)


def build_state_update_system_prompt() -> str:
    return load_prompt_template("state_update_system")


def build_state_update_user_prompt(
    *,
    context: StateUpdateContext,
    instruction: str | None,
) -> str:
    return (
        f"项目：{context.project.title}\n"
        f"语言：{context.project.language}\n"
        f"章节：{context.plan.chapter_number} - {context.plan.title}\n"
        f"用户额外状态更新说明：{instruction or '无'}\n\n"
        "请输出严格 JSON，结构如下：\n"
        "{\n"
        '  "chapter_number": 1,\n'
        '  "state_changes": [],\n'
        '  "timeline_events": [],\n'
        '  "warnings": [],\n'
        '  "created_at": "2026-05-23T00:00:00Z"\n'
        "}\n\n"
        "StateChange 字段：id, chapter, entity_id, field, old_value, new_value, reason, source。\n"
        "TimelineEvent 字段：id, chapter, scene, in_story_time, narrative_position, story_position, "
        "event_role, location_id, participant_ids, summary, reader_visible, causes, effects, state_change_ids, tags。\n"
        "- narrative_position 包含 chapter, scene, sequence，表示正文呈现顺序。\n"
        "- story_position 包含 time_label, order, thread_id, certainty，表示故事世界时间；无法判断真实顺序时 order 留空。\n"
        "- event_role 可用 current_action/flashback/memory/revelation/summary/backstory。\n\n"
        "字段约束：\n"
        "- StateChange.id 和 TimelineEvent.id 必须使用小写字母、数字和下划线。\n"
        "- entity_id 必须引用已有 character/location/item，或使用 story_position。\n"
        "- character 可用 field: location_id, health, mental_state, knowledge, goals, possessions, last_updated_chapter。\n"
        "- item 可用 field: holder_id, location_id, condition, known_properties, last_updated_chapter。\n"
        "- location 可用 field: accessibility, condition, active_events, last_updated_chapter。\n"
        "- story_position 可用 field: latest_chapter, in_story_time, summary。\n"
        "- 不要把 field 写成 location 或 holder；应写成 location_id 或 holder_id。\n\n"
        "时间线顺序约束：\n"
        "- timeline_events 必须按正文呈现顺序输出，即 narrative_position 单调递增。\n"
        "- timeline_event.scene 必须与 narrative_position.scene 一致，并对应 ChapterPlan 中实际发生的 scene_number。\n"
        "- narrative_position.scene 不得超过 ChapterPlan.scenes 的最大 scene_number。\n"
        "- 插叙、回忆、揭示旧事时，narrative_position 仍写正文出现位置，story_position 写故事世界时间。\n"
        "- 不要为了倒序/插叙把 narrative_position 倒退；如果无法判断 scene，宁可省略 scene 或写入 warnings。\n"
        "- 新 timeline event 的 narrative_position 不能倒退到现有 timeline 的最后事件之前。\n\n"
        f"ChapterPlan：\n{context.plan.model_dump_json(indent=2)}\n\n"
        f"AuditReport：\n{context.audit.model_dump_json(indent=2)}\n\n"
        f"Polished metadata：\n{json.dumps(context.polished.metadata, ensure_ascii=False, indent=2, default=str)}\n\n"
        f"Polished body：\n{context.polished.body}\n\n"
        f"Canon 摘要：\n{context.canon_summary}\n\n"
        f"Current state：\n{context.state_json}\n\n"
        f"Timeline：\n{context.timeline_json}\n"
    )


def parse_state_update_proposal(content: str) -> StateUpdateProposal:
    json_text = _extract_json_object(content)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise StateUpdateError(f"provider did not return valid StateUpdateProposal JSON: {exc}") from exc
    try:
        return StateUpdateProposal.model_validate(_normalize_state_update_data(data))
    except ValidationError as exc:
        raise StateUpdateError(f"provider returned invalid StateUpdateProposal: {exc}") from exc


def _generate_state_update_proposal_with_repair(
    provider: ModelProvider,
    context: StateUpdateContext,
    user_prompt: str,
    options: StateUpdateProposeOptions,
) -> tuple[StateUpdateProposal, tuple[str, ...]]:
    request = ModelRequest(
        system_prompt=build_state_update_system_prompt(),
        user_prompt=user_prompt,
        context=context.canon_summary,
        json_schema_name="StateUpdateProposal",
    )
    content = generate_with_output_guard(
        provider,
        request,
        root=options.root,
        invocation=AgentInvocationContext(
            agent_name="state_update",
            caller="cli",
            interaction_mode="internal_task",
            task="propose_state_update",
            chapter_number=options.chapter_number,
        ),
        contract=AgentOutputContract(
            output_kind="json",
            target_name="StateUpdateProposal",
            json_schema_name="StateUpdateProposal",
        ),
    )
    try:
        proposal = _parse_and_validate_state_update_response(content, options)
        warnings = validate_state_update_proposal(options.root, proposal, check_existing_timeline_ids=False)
        return proposal, tuple(warnings)
    except StateUpdateError as first_error:
        repair_content = generate_with_output_guard(
            provider,
            ModelRequest(
                system_prompt=build_state_update_system_prompt(),
                user_prompt=_repair_prompt(
                    schema_name="StateUpdateProposal",
                    original_prompt=user_prompt,
                    invalid_output=content,
                    error=str(first_error),
                ),
                context=context.canon_summary,
                json_schema_name="StateUpdateProposal",
            ),
            root=options.root,
            invocation=AgentInvocationContext(
                agent_name="state_update",
                caller="cli",
                interaction_mode="internal_task",
                task="propose_state_update_repair",
                chapter_number=options.chapter_number,
            ),
            contract=AgentOutputContract(
                output_kind="json",
                target_name="StateUpdateProposal",
                json_schema_name="StateUpdateProposal",
            ),
        )
        try:
            proposal = _parse_and_validate_state_update_response(repair_content, options)
            warnings = validate_state_update_proposal(options.root, proposal, check_existing_timeline_ids=False)
            return proposal, tuple(warnings)
        except StateUpdateError as second_error:
            raise StateUpdateError(
                "provider returned invalid StateUpdateProposal after repair retry: "
                f"{second_error}"
            ) from second_error


def _parse_and_validate_state_update_response(
    content: str,
    options: StateUpdateProposeOptions,
) -> StateUpdateProposal:
    proposal = parse_state_update_proposal(content)
    if proposal.chapter_number != options.chapter_number:
        raise StateUpdateError(
            f"provider returned chapter_number {proposal.chapter_number}, expected {options.chapter_number}"
        )
    return proposal


def _normalize_state_update_data(data: object) -> object:
    if not isinstance(data, dict):
        return data
    normalized = dict(data)
    warnings = list(normalized.get("warnings") or []) if isinstance(normalized.get("warnings"), list) else []
    changes = normalized.get("state_changes")
    if isinstance(changes, list):
        normalized_changes = []
        for change in changes:
            if not isinstance(change, dict):
                normalized_changes.append(change)
                continue
            item = dict(change)
            field = item.get("field")
            if field == "location":
                item["field"] = "location_id"
            elif field == "holder":
                item["field"] = "holder_id"
            _normalize_state_change_values(item)
            normalized_changes.append(item)
        normalized["state_changes"] = normalized_changes
    events = normalized.get("timeline_events")
    if isinstance(events, list):
        normalized_events = []
        for event in events:
            if not isinstance(event, dict):
                normalized_events.append(event)
                continue
            item = dict(event)
            if "location" in item and "location_id" not in item:
                item["location_id"] = item.pop("location")
            narrative = item.get("narrative_position")
            if not isinstance(narrative, dict):
                item["narrative_position"] = {
                    "chapter": item.get("chapter"),
                    "scene": item.get("scene"),
                }
            else:
                narrative = dict(narrative)
                if narrative.get("chapter") is None and item.get("chapter") is not None:
                    narrative["chapter"] = item.get("chapter")
                if narrative.get("scene") is None and item.get("scene") is not None:
                    narrative["scene"] = item.get("scene")
                item["narrative_position"] = narrative
            story = item.get("story_position")
            if not isinstance(story, dict):
                item["story_position"] = {
                    "time_label": item.get("in_story_time"),
                }
            else:
                story = dict(story)
                time_label = story.get("time_label")
                in_story_time = item.get("in_story_time")
                if time_label is None and in_story_time is not None:
                    story["time_label"] = in_story_time
                elif isinstance(time_label, str) and time_label and in_story_time != time_label:
                    item["in_story_time"] = time_label
                    event_id = item.get("id") or "unknown_event"
                    warnings.append(
                        f"normalized timeline event {event_id} in_story_time to story_position.time_label"
                    )
                item["story_position"] = story
            normalized_events.append(item)
        normalized["timeline_events"] = normalized_events
    if warnings:
        normalized["warnings"] = warnings
    return normalized


def _normalize_state_change_values(item: dict[str, object]) -> None:
    field = item.get("field")
    if field in {"knowledge", "goals", "known_properties", "active_events"}:
        for value_key in ("old_value", "new_value"):
            if isinstance(item.get(value_key), str):
                item[value_key] = [item[value_key]]
    if field == "possessions":
        for value_key in ("old_value", "new_value"):
            value = item.get(value_key)
            if isinstance(value, str):
                ids = _extract_entity_ids(value)
                item[value_key] = ids if ids else [value]


def _extract_entity_ids(value: str) -> list[str]:
    return re.findall(r"\b[a-z]+_[a-z0-9_]+\b", value)


def _repair_prompt(
    *,
    schema_name: str,
    original_prompt: str,
    invalid_output: str,
    error: str,
) -> str:
    return (
        f"你上一次输出的 {schema_name} JSON 无法通过解析、schema 校验或引用校验。\n"
        "请只输出修正后的 JSON，不要解释，不要 Markdown 包装。\n"
        "不要创造正文中没有发生的重大事件，不要修改 canon。\n\n"
        f"校验错误摘要：\n{error[:2400]}\n\n"
        f"上一次输出：\n{invalid_output[:6000]}\n\n"
        f"原始任务要求：\n{original_prompt[:6000]}\n"
    )


def default_mock_state_update_proposal_json(chapter_number: int = 1) -> str:
    return json.dumps(
        {
            "chapter_number": chapter_number,
            "state_changes": [
                {
                    "id": f"change_{chapter_number:03d}_001",
                    "chapter": chapter_number,
                    "entity_id": "char_lin_che",
                    "field": "possessions",
                    "old_value": [],
                    "new_value": ["item_broken_ticket"],
                    "reason": "林澈在旧车站拾起破损车票。",
                    "source": f"memory/chapters/{chapter_number:03d}/polished.md",
                },
                {
                    "id": f"change_{chapter_number:03d}_002",
                    "chapter": chapter_number,
                    "entity_id": "item_broken_ticket",
                    "field": "holder_id",
                    "old_value": None,
                    "new_value": "char_lin_che",
                    "reason": "破损车票由林澈收起。",
                    "source": f"memory/chapters/{chapter_number:03d}/polished.md",
                },
                {
                    "id": f"change_{chapter_number:03d}_003",
                    "chapter": chapter_number,
                    "entity_id": "story_position",
                    "field": "latest_chapter",
                    "old_value": chapter_number - 1,
                    "new_value": chapter_number,
                    "reason": "第本章已完成并通过审核。",
                    "source": f"memory/chapters/{chapter_number:03d}/audit.json",
                },
            ],
            "timeline_events": [
                {
                    "id": f"event_{chapter_number:03d}_001",
                    "chapter": chapter_number,
                    "scene": 1,
                    "in_story_time": "第1天，雨夜",
                    "narrative_position": {
                        "chapter": chapter_number,
                        "scene": 1,
                        "sequence": 1,
                    },
                    "story_position": {
                        "time_label": "第1天，雨夜",
                        "order": float(chapter_number),
                        "thread_id": "main",
                        "certainty": "certain",
                    },
                    "event_role": "current_action",
                    "location_id": "loc_old_station",
                    "participant_ids": ["char_lin_che"],
                    "summary": "林澈在旧车站听见异常广播，并发现破损车票。",
                    "reader_visible": True,
                    "causes": [],
                    "effects": ["林澈开始调查旧车站异常", "破损车票由林澈持有"],
                    "state_change_ids": [
                        f"change_{chapter_number:03d}_001",
                        f"change_{chapter_number:03d}_002",
                    ],
                    "tags": ["章节事件", "线索"],
                }
            ],
            "warnings": [],
            "created_at": _utc_now(),
        },
        ensure_ascii=False,
    )


def _ensure_audit_allows_progress(audit: AuditReport, *, allow_issues: bool) -> None:
    severe = [issue for issue in audit.issues if issue.severity in {"medium", "high", "critical"}]
    if audit.overall_status == "blocked" or severe:
        if not allow_issues:
            raise StateUpdateError(
                "audit has unresolved medium, high, or critical issues; pass the explicit allow flag to continue"
            )


def _validate_state_change_field(
    change: StateChange,
    character_ids: set[str],
    location_ids: set[str],
    item_ids: set[str],
) -> None:
    character_fields = {"location_id", "health", "mental_state", "knowledge", "goals", "possessions", "last_updated_chapter"}
    item_fields = {"holder_id", "location_id", "condition", "known_properties", "last_updated_chapter"}
    location_fields = {"accessibility", "condition", "active_events", "last_updated_chapter"}
    story_fields = {"latest_chapter", "in_story_time", "summary"}

    if change.entity_id == "story_position":
        allowed = story_fields
    elif change.entity_id in character_ids:
        allowed = character_fields
    elif change.entity_id in item_ids:
        allowed = item_fields
    elif change.entity_id in location_ids:
        allowed = location_fields
    else:
        allowed = set()
    if change.field not in allowed:
        raise StateUpdateError(f"state change {change.id} uses unsupported field: {change.field}")

    if change.field in {"location_id"} and change.new_value is not None and change.new_value not in location_ids:
        raise StateUpdateError(f"state change {change.id} references missing location: {change.new_value}")
    if change.field == "holder_id" and change.new_value is not None and change.new_value not in character_ids:
        raise StateUpdateError(f"state change {change.id} references missing holder: {change.new_value}")
    if change.field == "possessions":
        if not isinstance(change.new_value, list):
            raise StateUpdateError(f"state change {change.id} possessions value must be a list")
        values = change.new_value
        for item_id in values:
            if item_id not in item_ids:
                raise StateUpdateError(f"state change {change.id} references missing possession: {item_id}")


def _apply_model_field(target: Any, field: str, value: Any, change_id: str) -> None:
    if not hasattr(target, field):
        raise StateUpdateError(f"state change {change_id} uses unsupported field: {field}")
    setattr(target, field, value)


def _validate_applied_state(state: EntityState) -> None:
    for item in state.item_states:
        if item.holder_id and item.location_id:
            raise StateUpdateError(f"item {item.entity_id} has both holder_id and location_id")
    item_holders = {item.entity_id: item.holder_id for item in state.item_states}
    for character in state.character_states:
        for item_id in character.possessions:
            holder = item_holders.get(item_id)
            if holder and holder != character.entity_id:
                raise StateUpdateError(
                    f"character {character.entity_id} possession conflicts with item {item_id} holder_id {holder}"
                )
    character_possessions: dict[str, str] = {}
    for character in state.character_states:
        for item_id in character.possessions:
            previous_holder = character_possessions.get(item_id)
            if previous_holder and previous_holder != character.entity_id:
                raise StateUpdateError(
                    f"item {item_id} appears in possessions of both {previous_holder} and {character.entity_id}"
                )
            character_possessions[item_id] = character.entity_id


def _validate_state_change_old_values(
    state: EntityState,
    changes: list[StateChange],
    root: Path,
) -> None:
    canon = load_canon_files(root)
    character_ids = {item.id for item in canon.characters.characters}
    location_ids = {item.id for item in canon.locations.locations}
    item_ids = {item.id for item in canon.items.items}
    character_states = {item.entity_id: item for item in state.character_states}
    item_states = {item.entity_id: item for item in state.item_states}
    location_states = {item.entity_id: item for item in state.location_states}
    for change in changes:
        if change.old_value is None:
            continue
        if change.entity_id == "story_position":
            target = state.story_position
        elif change.entity_id in character_ids:
            target = character_states.get(change.entity_id)
        elif change.entity_id in item_ids:
            target = item_states.get(change.entity_id)
        elif change.entity_id in location_ids:
            target = location_states.get(change.entity_id)
        else:
            target = None
        if target is None and change.entity_id != "story_position":
            # Missing entity state means the project has not tracked this entity yet.
            # Treat old_value as model-inferred story context, not an authoritative
            # conflict against current_state.json.
            continue
        actual = _current_state_value_for_change(target, change)
        if actual != change.old_value:
            raise StateUpdateError(
                f"state change {change.id} old_value mismatch for {change.entity_id}.{change.field}: "
                f"expected {change.old_value!r}, actual {actual!r}"
            )


def _current_state_value_for_change(target: Any | None, change: StateChange) -> Any:
    if target is not None:
        return getattr(target, change.field, None)
    defaults = {
        "possessions": [],
        "knowledge": [],
        "goals": [],
        "known_properties": [],
        "active_events": [],
    }
    return defaults.get(change.field)


def _validate_applied_timeline(root: Path, timeline: TimelineFile) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for event in timeline.events:
        if event.id in seen:
            duplicates.add(event.id)
        seen.add(event.id)
    if duplicates:
        raise StateUpdateError(f"duplicate timeline event id: {', '.join(sorted(duplicates))}")


def _new_apply_log(
    *,
    chapter_number: int,
    root: Path,
    proposal_path: Path,
    state_path: Path,
    timeline_path: Path,
    state_backup_path: Path,
    timeline_backup_path: Path,
    status: Literal["applied", "rolled_back"],
) -> StateUpdateApplyLog:
    now = _utc_now()
    return StateUpdateApplyLog(
        id="state_apply_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f"),
        chapter_number=chapter_number,
        proposal_path=str(proposal_path.relative_to(root)),
        state_path=str(state_path.relative_to(root)),
        timeline_path=str(timeline_path.relative_to(root)),
        state_backup_path=str(state_backup_path.relative_to(root)),
        timeline_backup_path=str(timeline_backup_path.relative_to(root)),
        applied_at=now,
        status=status,
        errors=[],
    )


def _relative_if_exists(root: Path, path: Path) -> str | None:
    return str(path.relative_to(root)) if path.exists() else None


def _load_audit(root: Path, chapter_number: int) -> AuditReport:
    path = _chapter_dir(root, chapter_number) / "audit.json"
    if not path.exists():
        raise StateUpdateError(f"{path} is missing; run novel audit-chapter first")
    return load_json_model(path, AuditReport)


def _read_front_matter(path: Path) -> DraftDocument:
    try:
        return read_markdown_with_front_matter(path)
    except PolishingError as exc:
        raise StateUpdateError(str(exc)) from exc


def _refuse_existing(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise StateUpdateError(f"{path} already exists; use --force to overwrite it")


def _require_unique(values: list[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise StateUpdateError(f"duplicate {label}: {', '.join(sorted(duplicates))}")


def _extract_json_object(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise StateUpdateError("provider response does not contain a JSON object")
    return stripped[start : end + 1]


def _chapter_dir(root: Path, chapter_number: int) -> Path:
    return root / "memory" / "chapters" / f"{chapter_number:03d}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
