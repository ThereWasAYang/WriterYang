from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError
import yaml

from novel.core.agent_output import (
    AgentInvocationContext,
    AgentOutputContract,
    generate_with_output_guard,
)
from novel.core.context_budget import project_context_budget
from novel.core.io import atomic_write_model_json, backup_if_exists, load_json_model, load_yaml_model
from novel.core.json_extract import JsonExtractionError, extract_json_object
from novel.core.contracts import AcceptanceCommit, CURRENT_SCHEMA_VERSION
from novel.core.plan_refs import (
    plan_focus_entity_ids,
    plan_timeline_event_ids,
)
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.prompts import load_prompt_template, prompt_template_version
from novel.core.schemas import (
    AuditReport,
    ChapterMemory,
    ChapterMemoryConfig,
    ChapterMemoryItem,
    ChapterMemorySource,
    ChapterMemorySourceRef,
    ChapterPlan,
    ProjectConfig,
    StateChange,
    StateUpdateApplyLog,
    StateUpdateProposal,
    TimelineEvent,
    TimelineFile,
)
from novel.core.timeutil import utc_now


class ChapterMemoryError(RuntimeError):
    """Raised when chapter memory cannot be generated or loaded."""


CHAPTER_MEMORY_DETAIL_LIMIT = 8
CHAPTER_MEMORY_OVERVIEW_LIMIT = 20


@dataclass(frozen=True)
class ChapterMemoryDocument:
    metadata: dict[str, object]
    body: str


@dataclass(frozen=True)
class ChapterMemoryOptions:
    root: Path
    chapter_number: int
    force: bool = False


@dataclass(frozen=True)
class ChapterMemoryContext:
    root: Path
    chapter_number: int
    project: ProjectConfig
    plan: ChapterPlan
    polished: ChapterMemoryDocument
    audit: AuditReport | None
    proposal: StateUpdateProposal | None
    apply_log: StateUpdateApplyLog | None
    timeline: TimelineFile
    source: ChapterMemorySource


@dataclass(frozen=True)
class ChapterMemoryResult:
    memory_path: Path
    memory: ChapterMemory
    warnings: tuple[str, ...] = ()


def generate_chapter_memory(
    options: ChapterMemoryOptions,
    provider: ModelProvider | None = None,
    *,
    initial_warnings: tuple[str, ...] = (),
) -> ChapterMemoryResult:
    root = options.root.resolve()
    if options.chapter_number < 1:
        raise ChapterMemoryError("chapter_number must be a positive integer")
    chapter_dir = _chapter_dir(root, options.chapter_number)
    memory_path = chapter_memory_path(root, options.chapter_number)
    _refuse_existing(memory_path, options.force)

    context = load_chapter_memory_context(root, options.chapter_number)
    warnings: list[str] = list(initial_warnings)
    memory: ChapterMemory | None = None
    if provider is not None:
        try:
            memory = _generate_model_chapter_memory(provider, context)
        except Exception as exc:
            warnings.append(f"model chapter memory generation skipped: {exc}")
    if memory is None:
        memory = build_deterministic_chapter_memory(context, warnings=warnings)
        warnings = list(memory.warnings)
    elif warnings:
        memory = memory.model_copy(update={"warnings": [*memory.warnings, *warnings]})

    validation_warnings = validate_chapter_memory(root, memory)
    if validation_warnings:
        memory = memory.model_copy(update={"warnings": [*memory.warnings, *validation_warnings]})
        warnings.extend(validation_warnings)

    chapter_dir.mkdir(parents=True, exist_ok=True)
    if options.force:
        backup_if_exists(memory_path, reason="chapter_memory")
    atomic_write_model_json(memory_path, memory)
    return ChapterMemoryResult(memory_path=memory_path, memory=memory, warnings=tuple(dict.fromkeys(warnings)))


def load_chapter_memory_provider(
    root: Path,
    provider_name: str,
    *,
    chapter_number: int = 1,
    agent_config_path: Path | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    return create_agent_provider(
        agent_config_path or default_agent_config_path(root),
        "chapter_memory",
        overrides=ProviderOverrides(provider_name=provider_name, model_name=model_name),
        mock_response=default_mock_chapter_memory_json(chapter_number),
    )


def load_chapter_memory_context(root: Path, chapter_number: int) -> ChapterMemoryContext:
    chapter_dir = _chapter_dir(root, chapter_number)
    plan_path = chapter_dir / "plan.json"
    polished_path = chapter_dir / "polished.md"
    if not plan_path.exists():
        raise ChapterMemoryError(f"{plan_path} is missing; run novel plan-chapter first")
    if not polished_path.exists():
        raise ChapterMemoryError(f"{polished_path} is missing; run novel polish-chapter first")

    project = load_yaml_model(root / "project.yaml", ProjectConfig)
    plan = load_json_model(plan_path, ChapterPlan)
    if plan.chapter_number != chapter_number:
        raise ChapterMemoryError("plan.json chapter_number does not match requested chapter")
    polished = _read_markdown_with_front_matter(polished_path)
    if polished.metadata.get("status") != "accepted":
        raise ChapterMemoryError("chapter memory can only be generated for accepted polished.md")

    audit = _load_optional_model(chapter_dir / "audit.json", AuditReport)
    proposal = _load_optional_model(chapter_dir / "state_update_proposal.json", StateUpdateProposal)
    apply_log = _load_optional_model(chapter_dir / "state_update_apply_log.json", StateUpdateApplyLog)
    timeline = load_json_model(root / "memory" / "state" / "timeline.json", TimelineFile)
    source = ChapterMemorySource(
        polished_path=_rel(root, polished_path),
        polished_sha256=_sha256(polished_path),
        plan_path=_relative_if_exists(root, plan_path),
        audit_path=_relative_if_exists(root, chapter_dir / "audit.json"),
        state_update_proposal_path=_relative_if_exists(root, chapter_dir / "state_update_proposal.json"),
        state_update_apply_log_path=_relative_if_exists(root, chapter_dir / "state_update_apply_log.json"),
    )
    return ChapterMemoryContext(
        root=root,
        chapter_number=chapter_number,
        project=project,
        plan=plan,
        polished=polished,
        audit=audit,
        proposal=proposal,
        apply_log=apply_log,
        timeline=timeline,
        source=source,
    )


def build_deterministic_chapter_memory(
    context: ChapterMemoryContext,
    *,
    warnings: list[str] | None = None,
) -> ChapterMemory:
    warnings = list(warnings or [])
    fallback_warning = "chapter memory generated by deterministic fallback"
    if fallback_warning not in warnings:
        warnings.append(fallback_warning)
    timeline_events = _chapter_timeline_events(context)
    timeline_event_ids = [event.id for event in timeline_events]
    state_changes = context.proposal.state_changes if context.proposal else []
    polished_ref = _source_ref(context.source.polished_path, "accepted_polished")
    plan_ref = _source_ref(context.source.plan_path, "chapter_plan") if context.source.plan_path else polished_ref
    proposal_ref = (
        _source_ref(context.source.state_update_proposal_path, "state_update_proposal")
        if context.source.state_update_proposal_path
        else polished_ref
    )
    timeline_ref = _source_ref("memory/state/timeline.json", "timeline")

    return ChapterMemory(
        chapter_number=context.chapter_number,
        title=context.plan.title,
        status="accepted",
        generated_at=utc_now(),
        generation_status="deterministic_fallback",
        source=context.source,
        reader_visible_summary=_reader_visible_summary_from_polished(context),
        plot_beats=_plot_beat_items(context.plan, plan_ref),
        character_knowledge_changes=_knowledge_change_items(state_changes, proposal_ref),
        state_changes=_state_change_items(state_changes, proposal_ref),
        timeline_event_ids=timeline_event_ids,
        continuity_notes=_continuity_items(context, timeline_events, timeline_ref),
        retrieval_hints=[
            ChapterMemoryItem(
                summary=f"需要核对第 {context.chapter_number} 章正文细节时，优先读取 accepted polished.md。",
                visibility="reader_visible",
                timeline_event_ids=timeline_event_ids,
                source_refs=[polished_ref],
            )
        ],
        warnings=warnings,
    )


def validate_chapter_memory(root: Path, memory: ChapterMemory) -> list[str]:
    root = root.resolve()
    warnings: list[str] = chapter_memory_freshness_warnings(root, memory, force_hash=True)
    chapter_dir = _chapter_dir(root, memory.chapter_number)
    metadata_path = chapter_dir / "metadata.json"
    if metadata_path.exists():
        try:
            metadata_data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata_data.get("status") != "accepted":
                warnings.append("chapter metadata is not accepted")
        except Exception as exc:
            warnings.append(f"could not read chapter metadata: {exc}")
    timeline_ids = _timeline_ids(root)
    for event_id in memory.timeline_event_ids:
        if event_id not in timeline_ids:
            warnings.append(f"unknown timeline_event_id in chapter memory: {event_id}")
    for item in memory.all_items():
        if not item.source_refs:
            warnings.append(f"chapter memory item lacks source_refs: {item.summary[:80]}")
        for ref in item.source_refs:
            if not (root / ref.path).exists():
                warnings.append(f"chapter memory source_ref is missing: {ref.path}")
    return list(dict.fromkeys(warnings))


def chapter_memory_freshness_warnings(
    root: Path,
    memory: ChapterMemory,
    *,
    force_hash: bool = False,
) -> list[str]:
    root = root.resolve()
    warnings: list[str] = []
    polished_path = root / memory.source.polished_path
    if not polished_path.exists():
        return [f"source polished file is missing: {memory.source.polished_path}"]
    if force_hash or _polished_may_be_newer_than_memory(root, memory, polished_path):
        actual_sha = _sha256(polished_path)
        if actual_sha != memory.source.polished_sha256:
            warnings.append("stale chapter memory: polished_sha256 does not match accepted polished.md")
    if not _memory_source_matches_acceptance(root, memory):
        try:
            metadata = _read_markdown_front_matter_metadata(polished_path)
            if metadata.get("status") != "accepted":
                warnings.append("chapter memory source has no accepted lifecycle binding")
        except Exception as exc:
            warnings.append(f"could not verify chapter memory source acceptance: {exc}")
    return warnings


def _memory_source_matches_acceptance(root: Path, memory: ChapterMemory) -> bool:
    acceptance_path = _chapter_dir(root, memory.chapter_number) / "acceptance.json"
    if not acceptance_path.is_file():
        return False
    try:
        acceptance = load_json_model(acceptance_path, AcceptanceCommit)
    except Exception:
        return False
    return (
        acceptance.chapter_number == memory.chapter_number
        and acceptance.candidate.path == memory.source.polished_path
        and acceptance.candidate.sha256 == memory.source.polished_sha256
    )


def load_chapter_memories(
    root: Path,
    *,
    before_chapter_number: int | None = None,
    include_stale: bool = False,
) -> tuple[list[ChapterMemory], list[str]]:
    root = root.resolve()
    memories: list[ChapterMemory] = []
    warnings: list[str] = []
    chapters_dir = root / "memory" / "chapters"
    if not chapters_dir.exists():
        return memories, warnings
    for path in sorted(chapters_dir.glob("[0-9][0-9][0-9]/chapter_memory.json")):
        try:
            memory = load_json_model(path, ChapterMemory)
        except Exception as exc:
            warnings.append(f"could not load chapter memory {path.relative_to(root)}: {exc}")
            continue
        if before_chapter_number is not None and memory.chapter_number >= before_chapter_number:
            continue
        freshness_warnings = chapter_memory_freshness_warnings(root, memory)
        if freshness_warnings:
            warning_prefix = f"chapter {memory.chapter_number}: "
            warnings.extend(warning_prefix + warning for warning in freshness_warnings)
        if freshness_warnings and not include_stale:
            continue
        memories.append(memory)
    return memories, warnings


def render_chapter_memory_prompt_text(
    root: Path,
    *,
    project: ProjectConfig,
    chapter_number: int,
    task: str,
    plan: ChapterPlan | None = None,
) -> str:
    config = project.chapter_memory or ChapterMemoryConfig()
    if not config.enabled or task not in set(config.inject_into_tasks):
        return ""
    memories, warnings = load_chapter_memories(root, before_chapter_number=chapter_number)
    if not memories:
        return (
            "ChapterMemory: no accepted chapter memory is available yet. "
            "Do not invent history; rely on canon/current_state/timeline and accepted chapter prose.\n"
        )
    selected = _select_memories_for_prompt(memories, project=project, chapter_number=chapter_number, plan=plan)
    overview = _select_overview_memories(memories, selected)
    omitted_count = max(0, len(memories) - len(overview))
    lines = [
        "ChapterMemory context (auxiliary retrieval guide; not a source of truth):",
        "- Treat ChapterMemory as compressed navigation/context only.",
        "- If it conflicts with canon, current_state, timeline, or accepted polished.md, those authoritative files win.",
        "- Before relying on a detail, use the source paths below to verify against authoritative memory/prose.",
        "- overview:",
    ]
    for memory in overview:
        lines.append(
            f"  - chapter {memory.chapter_number} {memory.title}: "
            f"{_compact(memory.reader_visible_summary, 240)} "
            f"(source: {memory.source.polished_path})"
        )
    if omitted_count:
        lines.append(
            f"  - {omitted_count} older ChapterMemory entries omitted from overview; "
            "use search type chapter_memory to locate and verify older details."
        )
    lines.append("- selected details:")
    if task == "write":
        lines.extend(_render_writer_memory(memory) for memory in selected)
    else:
        lines.extend(_render_plot_memory(memory) for memory in selected)
    if warnings:
        lines.append("- warnings:")
        lines.extend(f"  - {warning}" for warning in warnings[:8])
    return "\n".join(line for line in lines if line) + "\n"


def chapter_memory_path(root: Path, chapter_number: int) -> Path:
    return _chapter_dir(root.resolve(), chapter_number) / "chapter_memory.json"


def build_chapter_memory_system_prompt() -> str:
    return load_prompt_template("chapter_memory_system")


def build_chapter_memory_user_prompt(context: ChapterMemoryContext) -> str:
    return (
        f"项目：{context.project.title}\n"
        f"语言：{context.project.language}\n"
        f"章节：{context.chapter_number} - {context.plan.title}\n\n"
        "请输出严格 JSON，符合 ChapterMemory schema。\n"
        "ChapterMemory 是辅助检索和上下文压缩指南，不是正式事实源。\n"
        "真正事实源仍是 canon、current_state、timeline、accepted polished.md。\n"
        "不要把正文没有发生的事件写进记忆；不确定内容写入 warnings。\n"
        "每个列表项都要包含 visibility 和 source_refs。\n"
        "Writer 可见内容必须保守，hidden_truth 不得伪装成 reader_visible。\n\n"
        f"Source：\n{context.source.model_dump_json(indent=2)}\n\n"
        f"ChapterPlan：\n{context.plan.model_dump_json(indent=2)}\n\n"
        f"AuditReport：\n{context.audit.model_dump_json(indent=2) if context.audit else '{}'}\n\n"
        f"StateUpdateProposal：\n{context.proposal.model_dump_json(indent=2) if context.proposal else '{}'}\n\n"
        f"StateUpdateApplyLog：\n{context.apply_log.model_dump_json(indent=2) if context.apply_log else '{}'}\n\n"
        f"Accepted polished metadata：\n{json.dumps(context.polished.metadata, ensure_ascii=False, indent=2, default=str)}\n\n"
        f"Accepted polished body：\n{context.polished.body}\n\n"
        f"Chapter timeline events：\n{json.dumps([event.model_dump(mode='json') for event in _chapter_timeline_events(context)], ensure_ascii=False, indent=2, default=str)}\n"
    )


def parse_chapter_memory(content: str, context: ChapterMemoryContext) -> ChapterMemory:
    try:
        json_text = extract_json_object(content)
    except JsonExtractionError as exc:
        raise ChapterMemoryError("provider did not return a JSON object") from exc
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ChapterMemoryError(f"provider did not return valid ChapterMemory JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ChapterMemoryError("provider returned non-object ChapterMemory JSON")
    normalized = dict(data)
    normalized["chapter_number"] = context.chapter_number
    normalized.setdefault("title", context.plan.title)
    normalized.setdefault("status", "accepted")
    normalized.setdefault("generated_at", utc_now().isoformat().replace("+00:00", "Z"))
    normalized["generation_status"] = "model_generated"
    normalized["source"] = context.source.model_dump(mode="json")
    normalized.setdefault("reader_visible_summary", _reader_visible_summary_from_polished(context))
    normalized.setdefault("timeline_event_ids", [event.id for event in _chapter_timeline_events(context)])
    try:
        return ChapterMemory.model_validate(normalized).model_copy(update={"schema_version": CURRENT_SCHEMA_VERSION})
    except ValidationError as exc:
        raise ChapterMemoryError(f"provider returned invalid ChapterMemory: {exc}") from exc


def default_mock_chapter_memory_json(chapter_number: int = 1) -> str:
    return json.dumps(
        {
            "chapter_number": chapter_number,
            "title": f"Chapter {chapter_number}",
            "status": "accepted",
            "generated_at": "2026-06-05T00:00:00Z",
            "generation_status": "model_generated",
            "source": {
                "polished_path": f"memory/chapters/{chapter_number:03d}/polished.md",
                "polished_sha256": "0" * 64,
            },
            "reader_visible_summary": "本章建立主要行动线索，并保留后续检索入口。",
            "plot_beats": [],
            "character_knowledge_changes": [],
            "state_changes": [],
            "timeline_event_ids": [],
            "open_threads": [],
            "foreshadowing": [],
            "continuity_notes": [],
            "retrieval_hints": [],
            "warnings": [],
        },
        ensure_ascii=False,
    )


def _generate_model_chapter_memory(provider: ModelProvider, context: ChapterMemoryContext) -> ChapterMemory:
    content = generate_with_output_guard(
        provider,
        ModelRequest(
            system_prompt=build_chapter_memory_system_prompt(),
            user_prompt=build_chapter_memory_user_prompt(context),
            json_schema_name="ChapterMemory",
            prompt_version=prompt_template_version("chapter_memory_system"),
        ),
        root=context.root,
        invocation=AgentInvocationContext(
            agent_name="chapter_memory",
            caller="cli",
            interaction_mode="internal_task",
            task="generate_chapter_memory",
            chapter_number=context.chapter_number,
        ),
        contract=AgentOutputContract(
            output_kind="json",
            target_name="ChapterMemory",
            json_schema_name="ChapterMemory",
        ),
    )
    return parse_chapter_memory(content, context)


def _select_memories_for_prompt(
    memories: list[ChapterMemory],
    *,
    project: ProjectConfig,
    chapter_number: int,
    plan: ChapterPlan | None,
) -> list[ChapterMemory]:
    budget = project_context_budget(project)
    recent_min = max(1, chapter_number - budget.recent_window_chapters)
    focus_ids = plan_focus_entity_ids(plan)
    event_ids = plan_timeline_event_ids(plan)
    selected = [
        memory
        for memory in memories
        if memory.chapter_number >= recent_min or _memory_matches(memory, focus_ids=focus_ids, event_ids=event_ids)
    ]
    if not selected:
        selected = memories[-min(len(memories), budget.recent_window_chapters or 1) :]
    return selected[-CHAPTER_MEMORY_DETAIL_LIMIT:]


def _select_overview_memories(memories: list[ChapterMemory], selected: list[ChapterMemory]) -> list[ChapterMemory]:
    selected_numbers = {memory.chapter_number for memory in selected}
    selected_memories = [memory for memory in memories if memory.chapter_number in selected_numbers]
    if len(selected_memories) >= CHAPTER_MEMORY_OVERVIEW_LIMIT:
        return selected_memories[-CHAPTER_MEMORY_OVERVIEW_LIMIT:]
    remaining_slots = CHAPTER_MEMORY_OVERVIEW_LIMIT - len(selected_memories)
    recent = [memory for memory in memories if memory.chapter_number not in selected_numbers][-remaining_slots:]
    return sorted([*recent, *selected_memories], key=lambda memory: memory.chapter_number)


def _memory_matches(memory: ChapterMemory, *, focus_ids: set[str], event_ids: set[str]) -> bool:
    if event_ids.intersection(memory.timeline_event_ids):
        return True
    if not focus_ids:
        return False
    for item in memory.all_items():
        if focus_ids.intersection(item.related_entity_ids):
            return True
    return False


def _render_writer_memory(memory: ChapterMemory) -> str:
    safe_notes = _reader_visible_items(memory.continuity_notes, limit=3)
    hints = _reader_visible_items(memory.retrieval_hints, limit=3)
    payload = {
        "chapter_number": memory.chapter_number,
        "title": memory.title,
        "reader_visible_summary": memory.reader_visible_summary,
        "continuity_notes": [_item_prompt_payload(item) for item in safe_notes],
        "retrieval_hints": [_item_prompt_payload(item) for item in hints],
        "source": memory.source.polished_path,
    }
    return f"  - {json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)}"


def _render_plot_memory(memory: ChapterMemory) -> str:
    payload = {
        "chapter_number": memory.chapter_number,
        "title": memory.title,
        "reader_visible_summary": memory.reader_visible_summary,
        "plot_beats": [_item_prompt_payload(item) for item in memory.plot_beats[:5]],
        "character_knowledge_changes": [_item_prompt_payload(item) for item in memory.character_knowledge_changes[:5]],
        "state_changes": [_item_prompt_payload(item) for item in memory.state_changes[:5]],
        "timeline_event_ids": memory.timeline_event_ids,
        "open_threads": [_item_prompt_payload(item) for item in memory.open_threads[:5]],
        "foreshadowing": [_item_prompt_payload(item) for item in memory.foreshadowing[:5]],
        "continuity_notes": [_item_prompt_payload(item) for item in memory.continuity_notes[:5]],
        "retrieval_hints": [_item_prompt_payload(item) for item in memory.retrieval_hints[:3]],
        "source": memory.source.polished_path,
    }
    return f"  - {json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)}"


def _item_prompt_payload(item: ChapterMemoryItem) -> dict[str, object]:
    return {
        "summary": item.summary,
        "visibility": item.visibility,
        "related_entity_ids": item.related_entity_ids,
        "timeline_event_ids": item.timeline_event_ids,
        "source_refs": [ref.model_dump(mode="json", exclude_none=True) for ref in item.source_refs],
    }


def _reader_visible_items(items: list[ChapterMemoryItem], *, limit: int) -> list[ChapterMemoryItem]:
    return [item for item in items if item.visibility == "reader_visible"][:limit]


def _plot_beat_items(plan: ChapterPlan, source_ref: ChapterMemorySourceRef) -> list[ChapterMemoryItem]:
    items: list[ChapterMemoryItem] = []
    for scene in plan.scenes:
        points = "; ".join(scene.plot_points[:3])
        summary = scene.summary if not points else f"{scene.summary} ({points})"
        items.append(
            ChapterMemoryItem(
                summary=summary,
                visibility="author_only",
                related_entity_ids=[scene.location_id, *scene.participant_ids],
                source_refs=[source_ref],
            )
        )
    return items


def _knowledge_change_items(
    changes: list[StateChange],
    source_ref: ChapterMemorySourceRef,
) -> list[ChapterMemoryItem]:
    return [
        ChapterMemoryItem(
            summary=f"{change.entity_id} knowledge -> {_compact_json(change.new_value)}",
            description=change.reason,
            visibility="author_only",
            related_entity_ids=[change.entity_id],
            source_refs=[source_ref],
        )
        for change in changes
        if change.field == "knowledge"
    ]


def _state_change_items(changes: list[StateChange], source_ref: ChapterMemorySourceRef) -> list[ChapterMemoryItem]:
    return [
        ChapterMemoryItem(
            summary=f"{change.entity_id}.{change.field} -> {_compact_json(change.new_value)}",
            description=change.reason,
            visibility="author_only",
            related_entity_ids=[change.entity_id],
            source_refs=[source_ref],
        )
        for change in changes
    ]


def _continuity_items(
    context: ChapterMemoryContext,
    timeline_events: list[TimelineEvent],
    source_ref: ChapterMemorySourceRef,
) -> list[ChapterMemoryItem]:
    items: list[ChapterMemoryItem] = []
    for event in timeline_events:
        items.append(
            ChapterMemoryItem(
                summary=event.summary,
                visibility="reader_visible" if event.reader_visible else "author_only",
                related_entity_ids=[*(event.participant_ids), *([event.location_id] if event.location_id else [])],
                timeline_event_ids=[event.id],
                source_refs=[source_ref],
            )
        )
    if context.audit and context.audit.summary:
        items.append(
            ChapterMemoryItem(
                summary=f"审核摘要：{context.audit.summary}",
                visibility="author_only",
                source_refs=[_source_ref(context.source.audit_path or context.source.polished_path, "audit_report")],
            )
        )
    return items


def _chapter_timeline_events(context: ChapterMemoryContext) -> list[TimelineEvent]:
    if context.proposal and context.proposal.timeline_events:
        proposal_ids = {event.id for event in context.proposal.timeline_events}
        applied = [event for event in context.timeline.events if event.id in proposal_ids]
        proposal_events: list[TimelineEvent] = [event for event in context.proposal.timeline_events]
        return applied or proposal_events
    return [
        event
        for event in context.timeline.events
        if event.narrative_position is not None and event.narrative_position.chapter == context.chapter_number
    ]


def _timeline_ids(root: Path) -> set[str]:
    path = root / "memory" / "state" / "timeline.json"
    if not path.exists():
        return set()
    try:
        return {event.id for event in load_json_model(path, TimelineFile).events}
    except Exception:
        return set()


def _load_optional_model(path: Path, model_type: type[Any]):
    if not path.exists():
        return None
    return load_json_model(path, model_type)


def accepted_chapter_numbers(root: Path) -> list[int]:
    root = root.resolve()
    chapters_dir = root / "memory" / "chapters"
    if not chapters_dir.exists():
        return []
    numbers: list[int] = []
    for child in sorted(chapters_dir.iterdir()):
        if not child.is_dir() or not child.name.isdigit():
            continue
        if _is_accepted_chapter_dir(child):
            numbers.append(int(child.name))
    return numbers


def _is_accepted_chapter_dir(chapter_dir: Path) -> bool:
    metadata_path = chapter_dir / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            return metadata.get("status") == "accepted"
        except Exception:
            return False
    polished_path = chapter_dir / "polished.md"
    if not polished_path.exists():
        return False
    try:
        polished = _read_markdown_with_front_matter(polished_path)
    except Exception:
        return False
    return polished.metadata.get("status") == "accepted"


def _read_markdown_with_front_matter(path: Path) -> ChapterMemoryDocument:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        raise ChapterMemoryError(f"{path} is missing YAML front matter")
    try:
        _, metadata_text, body = content.split("---\n", 2)
    except ValueError as exc:
        raise ChapterMemoryError(f"{path} has invalid YAML front matter") from exc
    metadata = yaml.safe_load(metadata_text) or {}
    if not isinstance(metadata, dict):
        raise ChapterMemoryError(f"{path} YAML front matter must be a mapping")
    return ChapterMemoryDocument(metadata=metadata, body=body.strip())


def _read_markdown_front_matter_metadata(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        first_line = handle.readline()
        if first_line.strip() != "---":
            raise ChapterMemoryError(f"{path} is missing YAML front matter")
        metadata_lines: list[str] = []
        for line in handle:
            if line.strip() == "---":
                break
            metadata_lines.append(line)
        else:
            raise ChapterMemoryError(f"{path} has invalid YAML front matter")
    metadata = yaml.safe_load("".join(metadata_lines)) or {}
    if not isinstance(metadata, dict):
        raise ChapterMemoryError(f"{path} YAML front matter must be a mapping")
    return metadata


def _polished_may_be_newer_than_memory(root: Path, memory: ChapterMemory, polished_path: Path) -> bool:
    memory_path = chapter_memory_path(root, memory.chapter_number)
    try:
        return polished_path.stat().st_mtime >= memory_path.stat().st_mtime
    except OSError:
        return True


def _reader_visible_summary_from_polished(context: ChapterMemoryContext, *, limit: int = 360) -> str:
    paragraphs: list[str] = []
    for line in context.polished.body.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        paragraphs.append(text)
        if len(" ".join(paragraphs)) >= limit:
            break
    summary = _compact(" ".join(paragraphs), limit) if paragraphs else ""
    if summary:
        return summary
    return f"第 {context.chapter_number} 章 accepted polished.md 已归档；核对细节请读取 {context.source.polished_path}。"


def _relative_if_exists(root: Path, path: Path) -> str | None:
    return _rel(root, path) if path.exists() else None


def _source_ref(path: str | None, kind: str) -> ChapterMemorySourceRef:
    return ChapterMemorySourceRef(path=path or "memory/state/timeline.json", kind=kind)


def _refuse_existing(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise ChapterMemoryError(f"{path} already exists; use --force to overwrite it")


def _compact(value: str, limit: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _compact_json(value: object) -> str:
    return _compact(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str), 240)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _chapter_dir(root: Path, chapter_number: int) -> Path:
    return root / "memory" / "chapters" / f"{chapter_number:03d}"


def _rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root))
