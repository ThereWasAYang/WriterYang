from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from novel.core.canon import format_canon_summary, load_canon_files
from novel.core.io import load_json_model, load_yaml_model
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


class DraftingError(RuntimeError):
    """Raised when chapter drafting cannot proceed safely."""


@dataclass(frozen=True)
class ChapterDraftingOptions:
    root: Path
    chapter_number: int
    instruction: str | None = None
    force: bool = False
    target_words: int | None = None
    style_note: str | None = None
    use_search_context: bool = False


@dataclass(frozen=True)
class ChapterDraftingResult:
    draft_path: Path
    draft_markdown: str
    warnings: tuple[str, ...] = ()


def write_chapter_draft(
    options: ChapterDraftingOptions,
    provider: ModelProvider,
) -> ChapterDraftingResult:
    root = options.root.resolve()
    if options.chapter_number < 1:
        raise DraftingError("chapter_number must be a positive integer")

    chapter_dir = root / "memory" / "chapters" / f"{options.chapter_number:03d}"
    plan_path = chapter_dir / "plan.json"
    draft_path = chapter_dir / "draft.md"
    if not plan_path.exists():
        raise DraftingError(f"{plan_path} is missing; run novel plan-chapter first")
    _refuse_existing(draft_path, options.force)

    project = load_yaml_model(root / "project.yaml", ProjectConfig)
    plan = load_json_model(plan_path, ChapterPlan)
    if plan.chapter_number != options.chapter_number:
        raise DraftingError(
            f"plan.json chapter_number {plan.chapter_number} does not match requested "
            f"chapter {options.chapter_number}"
        )

    warnings: list[str] = []
    style_guide = _read_style_guide(root, warnings)
    inspiration_md = _read_required_text(root / "memory" / "inspiration.md", "memory/inspiration.md")
    inspiration_json = _read_optional_text(root / "memory" / "inspiration.json")
    canon = load_canon_files(root)
    state = load_json_model(root / "memory" / "state" / "current_state.json", EntityState)
    timeline = load_json_model(root / "memory" / "state" / "timeline.json", TimelineFile)
    search_context = (
        retrieve_context(root, chapter_number=options.chapter_number, instruction=options.instruction)
        .render_for_prompt()
        if options.use_search_context
        else ""
    )

    model_request = ModelRequest(
        system_prompt=build_writer_system_prompt(),
        user_prompt=build_writer_user_prompt(
            project=project,
            plan=plan,
            inspiration_md=inspiration_md,
            inspiration_json=inspiration_json,
            style_guide=style_guide,
            canon_summary=format_canon_summary(canon),
            state=state,
            timeline=timeline,
            instruction=options.instruction,
            target_words=options.target_words,
            style_note=options.style_note,
            search_context=search_context,
        ),
        context=format_canon_summary(canon),
    )
    body = _clean_body(
        "".join(provider.stream(model_request))
        if hasattr(provider, "stream")
        else provider.generate(model_request).content
    )
    if not body:
        raise DraftingError("writer provider returned empty draft content")

    draft_markdown = render_draft_markdown(
        plan=plan,
        body=body,
        created_at=_utc_now(),
    )
    chapter_dir.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(draft_markdown, encoding="utf-8")
    return ChapterDraftingResult(
        draft_path=draft_path,
        draft_markdown=draft_markdown,
        warnings=tuple(warnings),
    )


def load_drafting_provider(
    root: Path,
    provider_name: str,
    *,
    agent_config_path: Path | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    return create_agent_provider(
        agent_config_path or default_agent_config_path(root),
        "writer",
        overrides=ProviderOverrides(provider_name=provider_name, model_name=model_name),
        mock_response=default_mock_draft_body(),
    )


def read_drafting_instruction(instruction: str | None, input_path: Path | None) -> str | None:
    if instruction and input_path:
        raise DraftingError("provide either --instruction or --input, not both")
    if input_path:
        if not input_path.exists():
            raise DraftingError(f"writing instruction input file is missing: {input_path}")
        return input_path.read_text(encoding="utf-8").strip() or None
    return instruction.strip() if instruction and instruction.strip() else None


def build_writer_system_prompt() -> str:
    return load_prompt_template("writer_system")


def build_writer_user_prompt(
    *,
    project: ProjectConfig,
    plan: ChapterPlan,
    inspiration_md: str,
    inspiration_json: str,
    style_guide: str,
    canon_summary: str,
    state: EntityState,
    timeline: TimelineFile,
    instruction: str | None,
    target_words: int | None,
    style_note: str | None,
    search_context: str = "",
) -> str:
    return (
        f"项目：{project.title}\n"
        f"语言：{project.language}\n"
        f"类型：{', '.join(project.genre)}\n"
        f"章节：{plan.chapter_number} - {plan.title}\n"
        f"目标字数：{target_words if target_words else '未指定'}\n"
        f"用户额外写作要求：{instruction or '无'}\n"
        f"临时文风要求：{style_note or '无'}\n\n"
        "请只输出正文 Markdown，不要包含 YAML front matter，"
        "不要包含 provider 原始响应、调试信息、JSON、分析说明或大纲。\n\n"
        f"{search_context}\n"
        f"ChapterPlan：\n{plan.model_dump_json(indent=2)}\n\n"
        f"Style guide：\n{style_guide}\n\n"
        f"Canon 摘要：\n{canon_summary}\n\n"
        f"Current state：\n{state.model_dump_json(indent=2)}\n\n"
        f"Timeline：\n{timeline.model_dump_json(indent=2)}\n\n"
        f"Inspiration.md：\n{inspiration_md}\n\n"
        f"Inspiration.json：\n{inspiration_json}\n"
    )


def render_draft_markdown(*, plan: ChapterPlan, body: str, created_at: str) -> str:
    return (
        "---\n"
        f"chapter_number: {plan.chapter_number}\n"
        f"title: {json.dumps(plan.title, ensure_ascii=False)}\n"
        "status: draft\n"
        "created_by: writer_agent\n"
        "based_on: plan.json\n"
        f"created_at: {created_at}\n"
        "---\n\n"
        f"# 第{_chapter_number_text(plan.chapter_number)}章 {plan.title}\n\n"
        f"{body.strip()}\n"
    )


def default_mock_draft_body() -> str:
    return (
        "雨落在旧车站的铁皮檐上，声音细密得像有人在暗处轻轻敲门。\n\n"
        "林澈站在候车厅门口，手电光扫过褪色的站牌和积水里的倒影。这里已经废弃多年，"
        "可广播喇叭里忽然传出一段含混的旋律，像从很远的雨夜绕回来。\n\n"
        "他没有立刻后退，只是把呼吸压低，沿着墙边往里走。长椅下露出半截湿透的车票，"
        "纸面上的日期被水泡得模糊，只剩几个足以让他停住的数字。\n\n"
        "广播停下时，整个车站安静得不合常理。林澈把车票夹进笔记本，抬头望向空荡的站台，"
        "心里第一次生出一种清晰的不安：这地方并没有真正沉默。"
    )


def _read_style_guide(root: Path, warnings: list[str]) -> str:
    path = root / "memory" / "style_guide.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    warnings.append("memory/style_guide.md is missing; using default style guidance")
    return (
        "# Style Guide\n\n"
        "## Overall Style\n\n"
        "保持清晰、克制、连贯，避免过度解释。\n"
    )


def _read_required_text(path: Path, label: str) -> str:
    if not path.exists():
        raise DraftingError(f"{label} is missing")
    return path.read_text(encoding="utf-8")


def _read_optional_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _refuse_existing(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise DraftingError(f"{path} already exists; use --force to overwrite it")


def _clean_body(content: str) -> str:
    body = content.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    return body


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _chapter_number_text(chapter_number: int) -> str:
    numerals = {
        0: "零",
        1: "一",
        2: "二",
        3: "三",
        4: "四",
        5: "五",
        6: "六",
        7: "七",
        8: "八",
        9: "九",
        10: "十",
    }
    if chapter_number in numerals:
        return numerals[chapter_number]
    if chapter_number < 20:
        return "十" + numerals[chapter_number - 10]
    if chapter_number < 100:
        tens, ones = divmod(chapter_number, 10)
        return numerals[tens] + "十" + (numerals[ones] if ones else "")
    return str(chapter_number)
