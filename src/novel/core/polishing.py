from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Literal

import yaml

from novel.core.canon import format_canon_summary, load_canon_files
from novel.core.drafting import _chapter_number_text
from novel.core.io import load_json_model, load_yaml_model
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.schemas import (
    ChapterPlan,
    EntityState,
    ProjectConfig,
    TimelineFile,
)


EditMode = Literal["light", "normal", "deep"]


class PolishingError(RuntimeError):
    """Raised when chapter polishing cannot proceed safely."""


@dataclass(frozen=True)
class ChapterPolishingOptions:
    root: Path
    chapter_number: int
    instruction: str | None = None
    force: bool = False
    style_note: str | None = None
    keep_length: bool = False
    edit_mode: EditMode = "normal"


@dataclass(frozen=True)
class DraftDocument:
    metadata: dict[str, object]
    body: str


@dataclass(frozen=True)
class ChapterPolishingResult:
    polished_path: Path
    polished_markdown: str
    warnings: tuple[str, ...] = ()


def polish_chapter(
    options: ChapterPolishingOptions,
    provider: ModelProvider,
) -> ChapterPolishingResult:
    root = options.root.resolve()
    if options.chapter_number < 1:
        raise PolishingError("chapter_number must be a positive integer")
    if options.edit_mode not in {"light", "normal", "deep"}:
        raise PolishingError(f"unsupported edit mode: {options.edit_mode}")

    chapter_dir = root / "memory" / "chapters" / f"{options.chapter_number:03d}"
    plan_path = chapter_dir / "plan.json"
    draft_path = chapter_dir / "draft.md"
    polished_path = chapter_dir / "polished.md"
    if not draft_path.exists():
        raise PolishingError(f"{draft_path} is missing; run novel write-chapter first")
    if not plan_path.exists():
        raise PolishingError(f"{plan_path} is missing; run novel plan-chapter first")
    _refuse_existing(polished_path, options.force)

    project = load_yaml_model(root / "project.yaml", ProjectConfig)
    plan = load_json_model(plan_path, ChapterPlan)
    if plan.chapter_number != options.chapter_number:
        raise PolishingError(
            f"plan.json chapter_number {plan.chapter_number} does not match requested "
            f"chapter {options.chapter_number}"
        )
    draft = read_markdown_with_front_matter(draft_path)
    draft_chapter = draft.metadata.get("chapter_number")
    if draft_chapter != options.chapter_number:
        raise PolishingError(
            f"draft.md chapter_number {draft_chapter} does not match requested "
            f"chapter {options.chapter_number}"
        )

    warnings: list[str] = []
    style_guide = _read_style_guide(root, warnings)
    inspiration_md = _read_optional_text(root / "memory" / "inspiration.md")
    canon = load_canon_files(root)
    state = load_json_model(root / "memory" / "state" / "current_state.json", EntityState)
    timeline = load_json_model(root / "memory" / "state" / "timeline.json", TimelineFile)

    response = provider.generate(
        ModelRequest(
            system_prompt=build_polish_system_prompt(),
            user_prompt=build_polish_user_prompt(
                project=project,
                plan=plan,
                draft=draft,
                inspiration_md=inspiration_md,
                style_guide=style_guide,
                canon_summary=format_canon_summary(canon),
                state=state,
                timeline=timeline,
                instruction=options.instruction,
                style_note=options.style_note,
                keep_length=options.keep_length,
                edit_mode=options.edit_mode,
            ),
            context=format_canon_summary(canon),
        )
    )
    body = _clean_polished_body(response.content)
    if not body:
        raise PolishingError("polish provider returned empty polished content")

    title = str(draft.metadata.get("title") or plan.title)
    polished_markdown = render_polished_markdown(
        chapter_number=options.chapter_number,
        title=title,
        body=body,
        created_at=_utc_now(),
    )
    polished_path.write_text(polished_markdown, encoding="utf-8")
    return ChapterPolishingResult(
        polished_path=polished_path,
        polished_markdown=polished_markdown,
        warnings=tuple(warnings),
    )


def load_polishing_provider(
    root: Path,
    provider_name: str,
    *,
    agent_config_path: Path | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    return create_agent_provider(
        agent_config_path or default_agent_config_path(root),
        "polish",
        overrides=ProviderOverrides(provider_name=provider_name, model_name=model_name),
        mock_response=default_mock_polished_body(),
    )


def read_polishing_instruction(instruction: str | None, input_path: Path | None) -> str | None:
    if instruction and input_path:
        raise PolishingError("provide either --instruction or --input, not both")
    if input_path:
        if not input_path.exists():
            raise PolishingError(f"polishing instruction input file is missing: {input_path}")
        return input_path.read_text(encoding="utf-8").strip() or None
    return instruction.strip() if instruction and instruction.strip() else None


def resolve_edit_mode(*, light_edit: bool, deep_edit: bool) -> EditMode:
    if light_edit and deep_edit:
        raise PolishingError("use only one of --light-edit or --deep-edit")
    if light_edit:
        return "light"
    if deep_edit:
        return "deep"
    return "normal"


def build_polish_system_prompt() -> str:
    return (
        "你是 Polish Agent。请只输出润色后的小说正文。"
        "不要输出解释、分析、修改说明、JSON 或大纲。"
        "必须保留原章的核心剧情事实，不得新增重大设定，不得提前揭示 hidden_truths。"
        "不得改变角色已经知道或不知道的信息，不得擅自改变人物、物品、地点状态。"
        "不得改变 ChapterPlan 中的关键剧情目标和结尾钩子。"
        "必须遵守 style_guide.md。"
        "正文中不要出现“根据设定”“本章目标”“隐藏真相”“润色如下”等工作区语言。"
    )


def build_polish_user_prompt(
    *,
    project: ProjectConfig,
    plan: ChapterPlan,
    draft: DraftDocument,
    inspiration_md: str,
    style_guide: str,
    canon_summary: str,
    state: EntityState,
    timeline: TimelineFile,
    instruction: str | None,
    style_note: str | None,
    keep_length: bool,
    edit_mode: EditMode,
) -> str:
    return (
        f"项目：{project.title}\n"
        f"语言：{project.language}\n"
        f"类型：{', '.join(project.genre)}\n"
        f"章节：{plan.chapter_number} - {plan.title}\n"
        f"编辑模式：{edit_mode} ({_edit_mode_description(edit_mode)})\n"
        f"尽量保持长度：{'是' if keep_length else '否'}\n"
        f"用户额外润色要求：{instruction or '无'}\n"
        f"临时文风要求：{style_note or '无'}\n\n"
        "请只输出润色后的正文 Markdown，不要包含 YAML front matter，"
        "不要包含 provider 原始响应、调试信息、JSON、分析说明、大纲或包装语。\n\n"
        f"ChapterPlan：\n{plan.model_dump_json(indent=2)}\n\n"
        f"Draft metadata：\n{json.dumps(draft.metadata, ensure_ascii=False, indent=2, default=str)}\n\n"
        f"Draft body：\n{draft.body}\n\n"
        f"Style guide：\n{style_guide}\n\n"
        f"Canon 摘要：\n{canon_summary}\n\n"
        f"Current state：\n{state.model_dump_json(indent=2)}\n\n"
        f"Timeline：\n{timeline.model_dump_json(indent=2)}\n\n"
        f"Inspiration.md：\n{inspiration_md}\n"
    )


def read_markdown_with_front_matter(path: Path) -> DraftDocument:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        raise PolishingError(f"{path} is missing YAML front matter")
    try:
        _, metadata_text, body = content.split("---\n", 2)
    except ValueError as exc:
        raise PolishingError(f"{path} has invalid YAML front matter") from exc
    metadata = yaml.safe_load(metadata_text) or {}
    if not isinstance(metadata, dict):
        raise PolishingError(f"{path} YAML front matter must be a mapping")
    return DraftDocument(metadata=metadata, body=body.strip())


def render_polished_markdown(
    *,
    chapter_number: int,
    title: str,
    body: str,
    created_at: str,
) -> str:
    return (
        "---\n"
        f"chapter_number: {chapter_number}\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        "status: polished\n"
        "created_by: polish_agent\n"
        "based_on: draft.md\n"
        f"created_at: {created_at}\n"
        "---\n\n"
        f"# 第{_chapter_number_text(chapter_number)}章 {title}\n\n"
        f"{body.strip()}\n"
    )


def default_mock_polished_body() -> str:
    return (
        "雨水敲在旧车站的铁皮檐上，细密得像有人躲在暗处，一下一下叩门。\n\n"
        "林澈停在候车厅门口，手电光掠过褪色的站牌，也掠过积水里破碎的倒影。"
        "这里废弃多年，偏偏广播喇叭里传出一段含混的旋律，像从另一个雨夜绕了回来。\n\n"
        "他没有退。只是把呼吸压低，沿墙往里走。长椅下露出半截湿透的车票，"
        "纸面上的日期被水泡得模糊，只剩几个让他无法移开视线的数字。\n\n"
        "广播戛然而止，车站安静得近乎失真。林澈把车票夹进笔记本，抬头望向空荡站台。"
        "他第一次清楚地意识到：这地方从来没有真正沉默。"
    )


def _read_style_guide(root: Path, warnings: list[str]) -> str:
    path = root / "memory" / "style_guide.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    warnings.append("memory/style_guide.md is missing; using default style guidance")
    return "# Style Guide\n\n## Overall Style\n\n保持清晰、克制、连贯，避免过度解释。\n"


def _read_optional_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _refuse_existing(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise PolishingError(f"{path} already exists; use --force to overwrite it")


def _clean_polished_body(content: str) -> str:
    body = content.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    wrappers = ["以下是润色后的文本：", "润色如下：", "以下是润色后的正文："]
    for wrapper in wrappers:
        if body.startswith(wrapper):
            body = body[len(wrapper) :].strip()
    return body


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _edit_mode_description(edit_mode: EditMode) -> str:
    if edit_mode == "light":
        return "轻度润色，只优化句子、病句、重复词、轻微节奏问题"
    if edit_mode == "deep":
        return "深度润色，可调整段落顺序、增强情绪、优化对白和描写，但不能改变核心剧情事实"
    return "默认润色，优化语言、对白、段落节奏，但不改剧情事实"
