from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from pydantic import ValidationError

from novel.core.io import load_yaml_model
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.schemas import InspirationBrief, ProjectConfig


class InspirationError(RuntimeError):
    """Raised when inspiration generation cannot proceed safely."""


@dataclass(frozen=True)
class InspirationOptions:
    root: Path
    source_text: str
    source_type: str = "user_text"
    write_json: bool = False
    overwrite: bool = False


@dataclass(frozen=True)
class InspirationResult:
    markdown_path: Path
    json_path: Path | None
    markdown: str
    brief: InspirationBrief | None


def run_inspiration_agent(
    options: InspirationOptions,
    provider: ModelProvider,
) -> InspirationResult:
    root = options.root.resolve()
    source_text = options.source_text.strip()
    if not source_text:
        raise InspirationError("inspiration input must not be empty")

    project = load_yaml_model(root / "project.yaml", ProjectConfig)
    markdown_path = root / "memory" / "inspiration.md"
    json_path = root / "memory" / "inspiration.json" if options.write_json else None

    _refuse_existing(markdown_path, options.overwrite)
    if json_path:
        _refuse_existing(json_path, options.overwrite)

    model_request = ModelRequest(
        system_prompt=build_inspiration_system_prompt(),
        user_prompt=build_inspiration_user_prompt(project, source_text),
        context=_project_context(project),
        json_schema_name="InspirationBrief" if options.write_json else None,
    )
    response = provider.generate(model_request)
    markdown = _ensure_markdown(response.content, source_text)
    brief = _brief_from_response(response.content, source_text, options.source_type)

    _write_new_or_overwrite(markdown_path, markdown, options.overwrite)
    if json_path:
        _write_new_or_overwrite(
            json_path,
            brief.model_dump_json(indent=2) + "\n",
            options.overwrite,
        )

    return InspirationResult(
        markdown_path=markdown_path,
        json_path=json_path,
        markdown=markdown,
        brief=brief if json_path else None,
    )


def load_inspiration_provider(
    root: Path,
    provider_name: str,
    *,
    agent_config_path: Path | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    return create_agent_provider(
        agent_config_path or default_agent_config_path(root),
        "inspiration",
        overrides=ProviderOverrides(provider_name=provider_name, model_name=model_name),
        mock_response=default_mock_inspiration_markdown(),
    )


def read_inspiration_input(text: str | None, input_path: Path | None) -> tuple[str, str]:
    if text and input_path:
        raise InspirationError("provide either direct text or --input, not both")
    if input_path:
        if not input_path.exists():
            raise InspirationError(f"input file is missing: {input_path}")
        return input_path.read_text(encoding="utf-8"), "file"
    if text:
        return text, "user_text"
    raise InspirationError("provide inspiration text or --input")


def build_inspiration_system_prompt() -> str:
    return (
        "你是一个长篇小说创作助手，负责把粗略灵感发展成可编辑的弱总纲。"
        "这个工具的目标不是聊天续写，而是通过结构化设定、状态、时间线、章节计划和审核流程，"
        "帮助作者长期创作长篇小说并保持一致性。"
        "请只提出方向和可能性，不要锁死强剧情，不要替作者做不可逆设定。"
    )


def build_inspiration_user_prompt(project: ProjectConfig, source_text: str) -> str:
    return (
        f"项目标题：{project.title}\n"
        f"语言：{project.language}\n"
        f"类型：{', '.join(project.genre)}\n\n"
        "用户原始灵感：\n"
        f"{source_text}\n\n"
        "请生成一份 Markdown 弱总纲，必须包含这些小节：\n"
        "# Inspiration\n"
        "## Source Summary\n"
        "## Themes\n"
        "## Mood\n"
        "## Weak Outline\n"
        "## Constraints\n"
        "## Potential Characters\n"
        "## Potential Locations\n"
        "## Potential Conflicts\n\n"
        "要求：\n"
        "- 主题、氛围、故事方向、约束、潜在角色/地点/冲突都要出现。\n"
        "- 使用弱总纲，不要生成强剧情约束。\n"
        "- 不要生成章节计划。\n"
        "- 不要创建 canon、state 或 timeline 文件内容。\n"
    )


def default_mock_inspiration_markdown() -> str:
    return (
        "# Inspiration\n\n"
        "## Source Summary\n\n"
        "用户提供了一个可以发展成长篇小说的初始灵感。\n\n"
        "## Themes\n\n"
        "- 记忆\n"
        "- 选择\n"
        "- 隐秘真相\n\n"
        "## Mood\n\n"
        "- 克制\n"
        "- 悬疑\n"
        "- 微妙的不安\n\n"
        "## Weak Outline\n\n"
        "故事可以围绕一个日常场景中的异常信号展开，主角在追查过程中逐步发现个人记忆、"
        "地点历史和隐藏冲突之间存在联系。整体方向保持开放，后续可由 canon 和章节计划继续细化。\n\n"
        "## Constraints\n\n"
        "- 保持悬念，不要过早解释核心真相。\n"
        "- 避免把弱总纲写成固定章节大纲。\n\n"
        "## Potential Characters\n\n"
        "- 对异常线索敏感的主角\n"
        "- 掌握部分旧事但有所隐瞒的同行者\n\n"
        "## Potential Locations\n\n"
        "- 带有旧日痕迹的公共空间\n"
        "- 主角用于整理线索的私人地点\n\n"
        "## Potential Conflicts\n\n"
        "- 主角想查清真相，但线索会动摇其自我认知。\n"
        "- 他人保护秘密的动机可能并非恶意。\n"
    )


def _project_context(project: ProjectConfig) -> str:
    planned = (
        project.target_length.planned_chapters
        if project.target_length and project.target_length.planned_chapters
        else "unknown"
    )
    return (
        f"Project ID: {project.project_id}\n"
        f"Title: {project.title}\n"
        f"Language: {project.language}\n"
        f"Genre: {', '.join(project.genre)}\n"
        f"Planned chapters: {planned}\n"
        f"Narration: {project.narration.pov}, {project.narration.tense}"
    )


def _ensure_markdown(content: str, source_text: str) -> str:
    content = content.strip()
    if content:
        return content + "\n"
    return default_mock_inspiration_markdown().replace(
        "用户提供了一个可以发展成长篇小说的初始灵感。",
        _summarize_source(source_text),
    )


def _brief_from_response(content: str, source_text: str, source_type: str) -> InspirationBrief:
    parsed = _try_parse_brief_json(content)
    if parsed:
        return parsed
    return InspirationBrief(
        id="inspiration_001",
        source_type=source_type,
        source_summary=_summarize_source(source_text),
        themes=_extract_list_section(content, "Themes") or ["记忆", "秘密", "选择"],
        mood=_extract_list_section(content, "Mood") or ["悬疑", "克制"],
        weak_outline=_extract_text_section(content, "Weak Outline")
        or "围绕用户初始灵感发展一条开放的长篇故事方向，保留后续设定和章节规划空间。",
        constraints=_extract_list_section(content, "Constraints")
        or ["不要过早解释核心真相", "不要把弱总纲写成固定章节大纲"],
        potential_characters=_extract_list_section(content, "Potential Characters"),
        potential_locations=_extract_list_section(content, "Potential Locations"),
        potential_conflicts=_extract_list_section(content, "Potential Conflicts"),
        created_at=datetime.now(timezone.utc),
    )


def _try_parse_brief_json(content: str) -> InspirationBrief | None:
    stripped = content.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
        return InspirationBrief.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        return None


def _extract_list_section(content: str, heading: str) -> list[str]:
    section = _extract_text_section(content, heading)
    if not section:
        return []
    items: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            items.append(stripped[2:].strip())
    return items


def _extract_text_section(content: str, heading: str) -> str:
    marker = f"## {heading}"
    start = content.find(marker)
    if start == -1:
        return ""
    section_start = start + len(marker)
    next_heading = content.find("\n## ", section_start)
    section = content[section_start:] if next_heading == -1 else content[section_start:next_heading]
    return section.strip()


def _summarize_source(source_text: str) -> str:
    compact = " ".join(source_text.split())
    return compact[:160] if len(compact) > 160 else compact


def _refuse_existing(path: Path, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise InspirationError(f"{path} already exists; use --overwrite to replace it")


def _write_new_or_overwrite(path: Path, content: str, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise InspirationError(f"{path} already exists; use --overwrite to replace it")
    path.write_text(content, encoding="utf-8")
