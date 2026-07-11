from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from pydantic import ValidationError

from novel.core.agent_output import (
    AgentInvocationContext,
    AgentOutputContract,
)
from novel.core.canon import CanonFiles, format_canon_summary, load_canon_files
from novel.core.chapter_memory import render_chapter_memory_prompt_text
from novel.core.context_budget import render_state_prompt_text, render_timeline_prompt_text
from novel.core.io import atomic_write_model_json, atomic_write_text, backup_if_exists, load_json_model, load_yaml_model
from novel.core.json_extract import JsonExtractionError, extract_json_object
from novel.core.contracts import CURRENT_SCHEMA_VERSION
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.prompts import load_prompt_template, prompt_template_version
from novel.core.search import retrieve_context_bundle, write_context_report
from novel.core.schemas import (
    ChapterPlan,
    EntityState,
    ProjectConfig,
    TimelineFile,
    VectorContextMode,
)
from novel.core.structured_generation import (
    REPAIR_ERROR_LIMIT,
    REPAIR_INVALID_OUTPUT_LIMIT,
    JsonRepairExhaustedError,
    generate_json_with_repair,
)
from novel.core.validation import ValidationReport


class PlanningError(RuntimeError):
    """Raised when chapter planning cannot proceed safely."""


@dataclass(frozen=True)
class ChapterPlanningOptions:
    root: Path
    chapter_number: int
    instruction: str | None = None
    force: bool = False
    use_search_context: bool = False
    use_vector_context: bool | VectorContextMode = "auto"
    output_dir: Path | None = None


@dataclass(frozen=True)
class ChapterPlanningResult:
    plan: ChapterPlan
    plan_json_path: Path
    plan_markdown_path: Path
    validation_report: ValidationReport
    context_report_path: Path | None = None


def plan_chapter(options: ChapterPlanningOptions, provider: ModelProvider) -> ChapterPlanningResult:
    root = options.root.resolve()
    if options.chapter_number < 1:
        raise PlanningError("chapter_number must be a positive integer")
    _require_inspiration(root)

    output_dir = _planning_output_dir(root, options)
    plan_json_path = output_dir / "plan.json"
    plan_markdown_path = output_dir / "plan.md"
    _refuse_existing(plan_json_path, options.force)
    _refuse_existing(plan_markdown_path, options.force)

    project = load_yaml_model(root / "project.yaml", ProjectConfig)
    canon = load_canon_files(root)
    _require_canon(canon)
    state = load_json_model(root / "memory" / "state" / "current_state.json", EntityState)
    timeline = load_json_model(root / "memory" / "state" / "timeline.json", TimelineFile)
    inspiration_md = (root / "memory" / "inspiration.md").read_text(encoding="utf-8")
    inspiration_json = _read_optional_text(root / "memory" / "inspiration.json")
    style_guide = _read_optional_text(root / "memory" / "style_guide.md")
    context_bundle = (
        retrieve_context_bundle(
            root,
            chapter_number=options.chapter_number,
            task="plan",
            instruction=options.instruction,
            use_vector=options.use_vector_context,
        )
        if options.use_search_context
        else None
    )
    search_context = context_bundle.render_for_prompt() if context_bundle else ""
    chapter_memory_context = render_chapter_memory_prompt_text(
        root,
        project=project,
        chapter_number=options.chapter_number,
        task="plan",
        plan=None,
    )

    canon_summary = format_canon_summary(canon)
    user_prompt = build_planning_user_prompt(
        project=project,
        chapter_number=options.chapter_number,
        inspiration_md=inspiration_md,
        inspiration_json=inspiration_json,
        style_guide=style_guide,
        canon_summary=canon_summary,
        state=state,
        timeline=timeline,
        instruction=options.instruction,
        search_context=search_context,
        chapter_memory_context=chapter_memory_context,
    )
    plan = _generate_chapter_plan_with_repair(
        root=root,
        provider=provider,
        user_prompt=user_prompt,
        canon_summary=canon_summary,
        chapter_number=options.chapter_number,
        canon=canon,
        state=state,
        timeline=timeline,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    if options.force:
        backup_if_exists(plan_json_path, reason="force")
        backup_if_exists(plan_markdown_path, reason="force")
    atomic_write_model_json(plan_json_path, plan)
    atomic_write_text(plan_markdown_path, render_plan_markdown(plan))
    context_report_path = (
        write_context_report(root, context_bundle, force=options.force, output_dir=output_dir)
        if context_bundle
        else None
    )
    return ChapterPlanningResult(
        plan=plan,
        plan_json_path=plan_json_path,
        plan_markdown_path=plan_markdown_path,
        validation_report=ValidationReport(root=root),
        context_report_path=context_report_path,
    )


def _planning_output_dir(root: Path, options: ChapterPlanningOptions) -> Path:
    if options.output_dir is None:
        return root / "memory" / "chapters" / f"{options.chapter_number:03d}"
    return options.output_dir if options.output_dir.is_absolute() else root / options.output_dir


def load_planning_provider(
    root: Path,
    provider_name: str,
    *,
    chapter_number: int = 1,
    agent_config_path: Path | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    return create_agent_provider(
        agent_config_path or default_agent_config_path(root),
        "plot",
        overrides=ProviderOverrides(provider_name=provider_name, model_name=model_name),
        mock_response=default_mock_chapter_plan_json(chapter_number),
    )


def read_planning_instruction(instruction: str | None, input_path: Path | None) -> str | None:
    if instruction and input_path:
        raise PlanningError("provide either --instruction or --input, not both")
    if input_path:
        if not input_path.exists():
            raise PlanningError(f"instruction input file is missing: {input_path}")
        return input_path.read_text(encoding="utf-8").strip() or None
    return instruction.strip() if instruction and instruction.strip() else None


def build_planning_system_prompt() -> str:
    return load_prompt_template("planning_system")


def build_planning_user_prompt(
    *,
    project: ProjectConfig,
    chapter_number: int,
    inspiration_md: str,
    inspiration_json: str,
    style_guide: str,
    canon_summary: str,
    state: EntityState,
    timeline: TimelineFile,
    instruction: str | None,
    search_context: str = "",
    chapter_memory_context: str = "",
) -> str:
    state_text = render_state_prompt_text(
        state,
        project=project,
        chapter_number=chapter_number,
        plan=None,
    )
    timeline_text = render_timeline_prompt_text(
        timeline,
        project=project,
        chapter_number=chapter_number,
        task="plan",
        plan=None,
    )
    return (
        f"项目：{project.title}\n"
        f"语言：{project.language}\n"
        f"类型：{', '.join(project.genre)}\n"
        f"目标章节：{chapter_number}\n\n"
        "请输出严格 JSON，符合 ChapterPlan schema，至少包含：\n"
        "chapter_number, title, goal, summary, required_context, scenes, "
        "must_include, must_avoid, expected_state_changes, ending_hook。\n"
        "每个 scene 至少包含：scene_number, location_id, participant_ids, purpose, "
        "summary, emotional_beat, plot_points。\n\n"
        "规则：\n"
        "- 只生成本章计划，不要写正文。\n"
        "- 不要修改 canon。\n"
        "- 不要直接更新 state/timeline。\n"
        "- location_id 必须引用已有 locations，禁止发明新地点 ID。\n"
        "- participant_ids 必须引用已有 characters，禁止发明新角色 ID。\n"
        "- required_context 中的 ID 必须来自已有 canon/state/timeline，禁止发明新 ID。\n"
        "- 不要提前揭示 hidden_truths，除非用户额外要求明确要求。\n"
        "- 输出必须是 JSON，不要 Markdown。\n\n"
        f"用户额外要求：\n{instruction or '无'}\n\n"
        f"{search_context}\n"
        f"{chapter_memory_context}\n"
        f"Canon 摘要：\n{canon_summary}\n\n"
        f"Current state：\n{state_text}\n\n"
        f"Timeline：\n{timeline_text}\n\n"
        f"Style guide：\n{style_guide}\n\n"
        f"Inspiration.md：\n{inspiration_md}\n\n"
        f"Inspiration.json：\n{inspiration_json}\n"
    )


def parse_chapter_plan(content: str) -> ChapterPlan:
    try:
        json_text = extract_json_object(content)
    except JsonExtractionError as exc:
        raise PlanningError("provider response does not contain a JSON object") from exc
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise PlanningError(f"provider did not return valid ChapterPlan JSON: {exc}") from exc
    try:
        data = _normalize_chapter_plan_data(data)
        return ChapterPlan.model_validate(data).model_copy(update={"schema_version": CURRENT_SCHEMA_VERSION})
    except ValidationError as exc:
        raise PlanningError(f"provider returned invalid ChapterPlan: {exc}") from exc


def _generate_chapter_plan_with_repair(
    *,
    root: Path,
    provider: ModelProvider,
    user_prompt: str,
    canon_summary: str,
    chapter_number: int,
    canon: CanonFiles,
    state: EntityState,
    timeline: TimelineFile,
) -> ChapterPlan:
    request = ModelRequest(
        system_prompt=build_planning_system_prompt(),
        user_prompt=user_prompt,
        context=canon_summary,
        json_schema_name="ChapterPlan",
        prompt_version=prompt_template_version("planning_system"),
    )
    contract = AgentOutputContract(
        output_kind="json",
        target_name="ChapterPlan",
        json_schema_name="ChapterPlan",
    )

    def parse_and_validate(content: str) -> ChapterPlan:
        plan = parse_chapter_plan(content)
        plan = _normalize_plan_reference_buckets(plan, canon, state, timeline)
        _validate_plan_for_write(plan, chapter_number, canon, state, timeline)
        return plan

    try:
        return generate_json_with_repair(
            provider,
            request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="plot",
                caller="cli",
                interaction_mode="internal_task",
                task="plan_chapter",
                chapter_number=chapter_number,
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="plot",
                caller="cli",
                interaction_mode="internal_task",
                task="plan_chapter_repair",
                chapter_number=chapter_number,
            ),
            contract=contract,
            parse=parse_and_validate,
            repair_prompt=lambda invalid_output, error: _repair_prompt(
                schema_name="ChapterPlan",
                invalid_output=invalid_output,
                error=error,
                allowed_ids=_allowed_id_summary(canon, state, timeline),
            ),
        )
    except JsonRepairExhaustedError as exc:
        raise PlanningError(str(exc)) from exc.second_error


def _validate_plan_for_write(
    plan: ChapterPlan,
    chapter_number: int,
    canon: CanonFiles,
    state: EntityState,
    timeline: TimelineFile,
) -> None:
    if plan.chapter_number != chapter_number:
        raise PlanningError(f"provider returned chapter_number {plan.chapter_number}, expected {chapter_number}")
    errors = _plan_reference_errors(plan, canon, state, timeline)
    if errors:
        raise PlanningError("provider returned ChapterPlan with missing references: " + "; ".join(errors))


def _normalize_plan_reference_buckets(
    plan: ChapterPlan,
    canon: CanonFiles,
    state: EntityState,
    timeline: TimelineFile,
) -> ChapterPlan:
    canon_ids, state_ids, timeline_ids = _allowed_reference_id_sets(canon, state, timeline)
    canon_context: list[str] = []
    state_context: list[str] = []
    timeline_context: list[str] = []
    unknown_context: list[tuple[str, str]] = []

    for bucket, values in (
        ("canon", plan.required_context.canon_entity_ids),
        ("state", plan.required_context.state_entity_ids),
        ("timeline", plan.required_context.timeline_event_ids),
    ):
        for value in values:
            if value in timeline_ids:
                timeline_context.append(value)
            elif value in canon_ids:
                canon_context.append(value)
            elif value in state_ids:
                state_context.append(value)
            else:
                unknown_context.append((bucket, value))

    for bucket, value in unknown_context:
        if bucket == "canon":
            canon_context.append(value)
        elif bucket == "state":
            state_context.append(value)
        else:
            timeline_context.append(value)

    required_context = plan.required_context.model_copy(
        update={
            "canon_entity_ids": _unique_preserve_order(canon_context),
            "state_entity_ids": _unique_preserve_order(state_context),
            "timeline_event_ids": _unique_preserve_order(timeline_context),
        }
    )
    return plan.model_copy(update={"required_context": required_context})


def _plan_reference_errors(
    plan: ChapterPlan,
    canon: CanonFiles,
    state: EntityState,
    timeline: TimelineFile,
) -> list[str]:
    character_ids = {item.id for item in canon.characters.characters}
    location_ids = {item.id for item in canon.locations.locations}
    canon_ids, state_ids, timeline_ids = _allowed_reference_id_sets(canon, state, timeline)

    errors: list[str] = []
    for scene in plan.scenes:
        if scene.location_id not in location_ids:
            errors.append(f"scene {scene.scene_number} references missing location_id: {scene.location_id}")
        for participant_id in scene.participant_ids:
            if participant_id not in character_ids:
                errors.append(
                    f"scene {scene.scene_number} references missing participant_id: {participant_id}"
                )
    for entity_id in plan.required_context.canon_entity_ids:
        if entity_id not in canon_ids:
            errors.append(f"required_context.canon_entity_ids references missing ID: {entity_id}")
    for entity_id in plan.required_context.state_entity_ids:
        if entity_id not in state_ids:
            errors.append(f"required_context.state_entity_ids references missing ID: {entity_id}")
    for event_id in plan.required_context.timeline_event_ids:
        if event_id not in timeline_ids:
            errors.append(f"required_context.timeline_event_ids references missing ID: {event_id}")
    return errors


def _allowed_reference_id_sets(
    canon: CanonFiles,
    state: EntityState,
    timeline: TimelineFile,
) -> tuple[set[str], set[str], set[str]]:
    character_ids = {item.id for item in canon.characters.characters}
    location_ids = {item.id for item in canon.locations.locations}
    item_ids = {item.id for item in canon.items.items}
    world_ids = {item.id for item in canon.world.world_rules}
    hidden_truth_ids = {item.id for item in canon.hidden_truths.hidden_truths}
    foreshadowing_ids = {item.id for item in canon.foreshadowing.foreshadowing_threads}
    canon_ids = character_ids | location_ids | item_ids | world_ids | hidden_truth_ids | foreshadowing_ids
    state_ids = (
        {item.entity_id for item in state.character_states}
        | {item.entity_id for item in state.item_states}
        | {item.entity_id for item in state.location_states}
        | {"story_position"}
        | canon_ids
    )
    timeline_ids = {event.id for event in timeline.events}
    return canon_ids, state_ids, timeline_ids


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _allowed_id_summary(canon: CanonFiles, state: EntityState, timeline: TimelineFile) -> str:
    return (
        "允许引用的 ID：\n"
        f"- characters: {', '.join(item.id for item in canon.characters.characters) or 'none'}\n"
        f"- locations: {', '.join(item.id for item in canon.locations.locations) or 'none'}\n"
        f"- items: {', '.join(item.id for item in canon.items.items) or 'none'}\n"
        f"- world_rules: {', '.join(item.id for item in canon.world.world_rules) or 'none'}\n"
        f"- hidden_truths: {', '.join(item.id for item in canon.hidden_truths.hidden_truths) or 'none'}\n"
        f"- foreshadowing: {', '.join(item.id for item in canon.foreshadowing.foreshadowing_threads) or 'none'}\n"
        "- state entities: "
        + (
            ", ".join(
                sorted(
                    {item.entity_id for item in state.character_states}
                    | {item.entity_id for item in state.item_states}
                    | {item.entity_id for item in state.location_states}
                    | {"story_position"}
                )
            )
            or "none"
        )
        + "\n"
        f"- timeline events: {', '.join(event.id for event in timeline.events) or 'none'}\n"
    )


def _repair_prompt(
    *,
    schema_name: str,
    invalid_output: str,
    error: str,
    allowed_ids: str,
) -> str:
    return (
        f"你上一次输出的 {schema_name} JSON 无法通过解析、schema 校验或引用校验。\n"
        "请只输出修正后的 JSON，不要解释，不要 Markdown 包装。\n"
        "不要发明角色、地点、物品、state 或 timeline ID；只能使用下方允许 ID。\n\n"
        f"{allowed_ids}\n"
        f"校验错误摘要：\n{error[:REPAIR_ERROR_LIMIT]}\n\n"
        f"上一次输出：\n{invalid_output[:REPAIR_INVALID_OUTPUT_LIMIT]}\n"
    )


def render_plan_markdown(plan: ChapterPlan) -> str:
    lines = [
        f"# Chapter {plan.chapter_number:03d}: {plan.title}",
        "",
        "## Goal",
        "",
        plan.goal,
        "",
        "## Summary",
        "",
        plan.summary,
        "",
        "## Must Include",
        "",
    ]
    lines.extend(_bullets(plan.must_include))
    lines.extend(["", "## Must Avoid", ""])
    lines.extend(_bullets(plan.must_avoid))
    lines.extend(["", "## Scenes", ""])
    for scene in plan.scenes:
        lines.extend(
            [
                f"### Scene {scene.scene_number}",
                "",
                f"- Location: {scene.location_id}",
                f"- Participants: {', '.join(scene.participant_ids) if scene.participant_ids else 'none'}",
                f"- Purpose: {scene.purpose}",
                f"- Emotional beat: {scene.emotional_beat}",
                f"- Summary: {scene.summary}",
                "- Plot points:",
            ]
        )
        lines.extend(_bullets(scene.plot_points))
        lines.append("")
    lines.extend(["## Expected State Changes", ""])
    lines.extend(_bullets(plan.expected_state_changes))
    lines.extend(["", "## Ending Hook", "", plan.ending_hook, ""])
    return "\n".join(lines)


def _normalize_chapter_plan_data(data: object) -> object:
    if not isinstance(data, dict):
        return data
    normalized = dict(data)
    required_context = normalized.get("required_context")
    if isinstance(required_context, list):
        normalized["required_context"] = {
            "canon_entity_ids": [value for value in required_context if isinstance(value, str)],
            "state_entity_ids": [],
            "timeline_event_ids": [],
        }
    expected_changes = normalized.get("expected_state_changes")
    if isinstance(expected_changes, list):
        normalized["expected_state_changes"] = [
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
            for value in expected_changes
        ]
    elif expected_changes is not None:
        normalized["expected_state_changes"] = [
            expected_changes
            if isinstance(expected_changes, str)
            else json.dumps(expected_changes, ensure_ascii=False, sort_keys=True)
        ]
    return normalized


def default_mock_chapter_plan_json(chapter_number: int = 1) -> str:
    return json.dumps(
        {
            "chapter_number": chapter_number,
            "title": "雨夜旧车站",
            "goal": "让主角第一次接触旧车站的异常，并建立追查动机。",
            "summary": "林澈在雨夜来到旧车站，听见停播多年的广播，并发现一张破损车票。",
            "required_context": {
                "canon_entity_ids": ["char_lin_che", "loc_old_station", "item_broken_ticket"],
                "state_entity_ids": ["char_lin_che", "item_broken_ticket"],
                "timeline_event_ids": [],
            },
            "scenes": [
                {
                    "scene_number": 1,
                    "location_id": "loc_old_station",
                    "participant_ids": ["char_lin_che"],
                    "purpose": "建立旧车站的异常氛围和主角的行动起点。",
                    "summary": "林澈进入旧车站，注意到广播声与废弃环境不相称。",
                    "emotional_beat": "潮湿、克制、警觉",
                    "plot_points": ["旧车站雨夜出现广播声", "林澈决定留下确认声音来源"],
                },
                {
                    "scene_number": 2,
                    "location_id": "loc_old_station",
                    "participant_ids": ["char_lin_che"],
                    "purpose": "引入关键物品并给出后续调查方向。",
                    "summary": "林澈在候车厅发现破损车票，车票上的残缺日期引起他的注意。",
                    "emotional_beat": "疑惑、被牵引",
                    "plot_points": ["破损车票首次出现", "林澈意识到车票可能与旧事有关"],
                },
            ],
            "must_include": ["旧车站广播声", "破损车票", "林澈的克制反应"],
            "must_avoid": ["直接解释旧车站的完整真相", "提前揭示隐藏真相"],
            "expected_state_changes": ["林澈获得破损车票", "林澈开始调查旧车站异常"],
            "ending_hook": "广播里忽然传出林澈熟悉却想不起来源的一段旋律。",
        },
        ensure_ascii=False,
    )


def _require_inspiration(root: Path) -> None:
    path = root / "memory" / "inspiration.md"
    if not path.exists():
        raise PlanningError("memory/inspiration.md is missing; run novel inspire first")
    if not path.read_text(encoding="utf-8").strip():
        raise PlanningError("memory/inspiration.md is empty; run novel inspire first")


def _require_canon(canon) -> None:
    if not canon.characters.characters:
        raise PlanningError("canon has no characters; run novel canon suggest/apply first")
    if not canon.locations.locations:
        raise PlanningError("canon has no locations; run novel canon suggest/apply first")


def _read_optional_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _refuse_existing(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise PlanningError(f"{path} already exists; use --force to overwrite it")


def _bullets(values: list[str]) -> list[str]:
    if not values:
        return ["- none"]
    return [f"- {value}" for value in values]
