from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from pydantic import ValidationError

from novel.core.canon import format_canon_summary, load_canon_files
from novel.core.io import atomic_write_model_json, atomic_write_text, backup_if_exists, load_json_model, load_yaml_model
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.prompts import load_prompt_template
from novel.core.search import retrieve_context
from novel.core.schemas import (
    ChapterPlan,
    EntityState,
    ProjectConfig,
    TimelineFile,
)
from novel.core.validation import ValidationReport, validate_project


class PlanningError(RuntimeError):
    """Raised when chapter planning cannot proceed safely."""


@dataclass(frozen=True)
class ChapterPlanningOptions:
    root: Path
    chapter_number: int
    instruction: str | None = None
    force: bool = False
    use_search_context: bool = False


@dataclass(frozen=True)
class ChapterPlanningResult:
    plan: ChapterPlan
    plan_json_path: Path
    plan_markdown_path: Path
    validation_report: ValidationReport


def plan_chapter(options: ChapterPlanningOptions, provider: ModelProvider) -> ChapterPlanningResult:
    root = options.root.resolve()
    if options.chapter_number < 1:
        raise PlanningError("chapter_number must be a positive integer")
    _require_inspiration(root)

    chapter_dir = root / "memory" / "chapters" / f"{options.chapter_number:03d}"
    plan_json_path = chapter_dir / "plan.json"
    plan_markdown_path = chapter_dir / "plan.md"
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
    search_context = (
        retrieve_context(root, chapter_number=options.chapter_number, instruction=options.instruction)
        .render_for_prompt()
        if options.use_search_context
        else ""
    )

    response = provider.generate(
        ModelRequest(
            system_prompt=build_planning_system_prompt(),
            user_prompt=build_planning_user_prompt(
                project=project,
                chapter_number=options.chapter_number,
                inspiration_md=inspiration_md,
                inspiration_json=inspiration_json,
                style_guide=style_guide,
                canon_summary=format_canon_summary(canon),
                state=state,
                timeline=timeline,
                instruction=options.instruction,
                search_context=search_context,
            ),
            context=format_canon_summary(canon),
            json_schema_name="ChapterPlan",
        )
    )
    plan = parse_chapter_plan(response.content)
    if plan.chapter_number != options.chapter_number:
        raise PlanningError(
            f"provider returned chapter_number {plan.chapter_number}, expected {options.chapter_number}"
        )

    chapter_dir.mkdir(parents=True, exist_ok=True)
    if options.force:
        backup_if_exists(plan_json_path, reason="force")
        backup_if_exists(plan_markdown_path, reason="force")
    atomic_write_model_json(plan_json_path, plan)
    atomic_write_text(plan_markdown_path, render_plan_markdown(plan))
    return ChapterPlanningResult(
        plan=plan,
        plan_json_path=plan_json_path,
        plan_markdown_path=plan_markdown_path,
        validation_report=validate_project(root),
    )


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
) -> str:
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
        "- location_id 尽量引用已有 locations。\n"
        "- participant_ids 尽量引用已有 characters。\n"
        "- required_context 中的 ID 尽量来自已有 canon/state/timeline。\n"
        "- 不要提前揭示 hidden_truths，除非用户额外要求明确要求。\n"
        "- 输出必须是 JSON，不要 Markdown。\n\n"
        f"用户额外要求：\n{instruction or '无'}\n\n"
        f"{search_context}\n"
        f"Canon 摘要：\n{canon_summary}\n\n"
        f"Current state：\n{state.model_dump_json(indent=2)}\n\n"
        f"Timeline：\n{timeline.model_dump_json(indent=2)}\n\n"
        f"Style guide：\n{style_guide}\n\n"
        f"Inspiration.md：\n{inspiration_md}\n\n"
        f"Inspiration.json：\n{inspiration_json}\n"
    )


def parse_chapter_plan(content: str) -> ChapterPlan:
    json_text = _extract_json_object(content)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise PlanningError(f"provider did not return valid ChapterPlan JSON: {exc}") from exc
    try:
        data = _normalize_chapter_plan_data(data)
        return ChapterPlan.model_validate(data)
    except ValidationError as exc:
        raise PlanningError(f"provider returned invalid ChapterPlan: {exc}") from exc


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
        raise PlanningError("provider response does not contain a JSON object")
    return stripped[start : end + 1]


def _bullets(values: list[str]) -> list[str]:
    if not values:
        return ["- none"]
    return [f"- {value}" for value in values]
