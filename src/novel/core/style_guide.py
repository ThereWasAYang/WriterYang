from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from novel.core.agent_output import AgentInvocationContext, AgentOutputContract
from novel.core.json_extract import extract_json_object
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.prompts import load_prompt_template, prompt_template_version
from novel.core.schemas import GeneratedStyleGuide, ProjectConfig
from novel.core.structured_generation import generate_json_with_repair
from novel.core.io import load_yaml_model


DEFAULT_STYLE_GUIDANCE = "# 文风设置\n\n## 整体风格\n\n保持清晰、克制、连贯，避免过度解释。\n"
STYLE_GUIDE_SCHEMA_NAME = "GeneratedStyleGuide"


class StyleGuideGenerationError(RuntimeError):
    """Raised when style guide generation cannot proceed safely."""


@dataclass(frozen=True)
class StyleGuideGenerationOptions:
    root: Path
    instruction: str
    include_project_context: bool = True
    include_existing_style: bool = True


@dataclass(frozen=True)
class StyleGuideGenerationResult:
    content: str
    guide: GeneratedStyleGuide
    warnings: tuple[str, ...] = ()


def default_style_guide_markdown() -> str:
    return (
        "# 文风设置\n\n"
        "## 整体风格\n\n"
        "## 叙事视角\n\n"
        "## 语言要求\n\n"
        "## 对白要求\n\n"
        "## 节奏\n\n"
        "## 禁用项\n\n"
        "## 示例段落\n\n"
        "## 修订备注\n"
    )


def generate_style_guide(
    options: StyleGuideGenerationOptions,
    provider: ModelProvider,
) -> StyleGuideGenerationResult:
    root = options.root.expanduser().resolve()
    instruction = options.instruction.strip()
    if not instruction:
        raise StyleGuideGenerationError("style guide generation instruction must not be empty")

    warnings: list[str] = []
    project = load_yaml_model(root / "project.yaml", ProjectConfig) if options.include_project_context else None
    existing_style = _existing_style_context(root, warnings) if options.include_existing_style else ""
    request = ModelRequest(
        system_prompt=build_style_guide_system_prompt(),
        user_prompt=build_style_guide_user_prompt(
            instruction,
            project=project,
            existing_style=existing_style,
            include_project_context=options.include_project_context,
            include_existing_style=options.include_existing_style,
        ),
        context=_project_context(project) if project else None,
        json_schema_name=STYLE_GUIDE_SCHEMA_NAME,
        prompt_version=prompt_template_version("style_guide_system"),
    )
    guide = generate_json_with_repair(
        provider,
        request,
        root=root,
        invocation=AgentInvocationContext(
            agent_name="style_guide",
            caller="web",
            interaction_mode="internal_task",
            task="generate_style_guide",
        ),
        repair_invocation=AgentInvocationContext(
            agent_name="style_guide",
            caller="web",
            interaction_mode="internal_task",
            task="generate_style_guide_repair",
        ),
        contract=AgentOutputContract(
            output_kind="json",
            target_name=STYLE_GUIDE_SCHEMA_NAME,
            json_schema_name=STYLE_GUIDE_SCHEMA_NAME,
        ),
        parse=parse_generated_style_guide,
        repair_prompt=_style_guide_repair_prompt,
    )
    return StyleGuideGenerationResult(
        content=render_generated_style_guide_markdown(guide),
        guide=guide,
        warnings=tuple(warnings),
    )


def load_style_guide_provider(
    root: Path,
    provider_name: str,
    *,
    agent_config_path: Path | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    return create_agent_provider(
        agent_config_path or default_agent_config_path(root),
        "style_guide",
        fallback_agents=("inspiration",),
        overrides=ProviderOverrides(provider_name=provider_name, model_name=model_name),
        mock_response=default_mock_generated_style_guide_json(),
    )


def build_style_guide_system_prompt() -> str:
    return load_prompt_template("style_guide_system")


def build_style_guide_user_prompt(
    instruction: str,
    *,
    project: ProjectConfig | None,
    existing_style: str,
    include_project_context: bool,
    include_existing_style: bool,
) -> str:
    project_text = _project_prompt_context(project) if include_project_context and project else "（未提供项目上下文）"
    existing_style_text = existing_style.strip() if include_existing_style and existing_style.strip() else "（未提供现有文风设置）"
    return (
        "请根据用户输入生成长期文风设置结构。\n\n"
        f"项目上下文：\n{project_text}\n\n"
        f"现有文风设置：\n{existing_style_text}\n\n"
        f"用户输入：\n{instruction}\n\n"
        "输出要求：\n"
        "- 只能输出 GeneratedStyleGuide JSON object。\n"
        "- style_sources 记录用户输入中可识别的风格来源或综合方向，每项必须是高层概括。\n"
        "- overall_style、narrative_view、language_rules、dialogue_rules、pacing_rules、avoid 要能直接指导后续写作。\n"
        "- sample_paragraph 必须是原创中文示例段落，只展示综合风格，不引用原文，不仿写具体作者段落。\n"
        "- revision_notes 记录生成依据、保守假设和保存前需要人工确认的事项。\n"
    )


def parse_generated_style_guide(content: str) -> GeneratedStyleGuide:
    return GeneratedStyleGuide.model_validate_json(extract_json_object(content))


def render_generated_style_guide_markdown(guide: GeneratedStyleGuide) -> str:
    parts = [
        "# 文风设置",
        "",
        "## 风格来源",
        _render_bullets(guide.style_sources),
        "",
        "## 整体风格",
        guide.overall_style.strip(),
        "",
        "## 叙事视角",
        guide.narrative_view.strip(),
        "",
        "## 语言要求",
        _render_bullets(guide.language_rules),
        "",
        "## 对白要求",
        _render_bullets(guide.dialogue_rules),
        "",
        "## 节奏",
        _render_bullets(guide.pacing_rules),
        "",
        "## 禁用项",
        _render_bullets(guide.avoid),
        "",
        "## 示例段落",
        guide.sample_paragraph.strip(),
        "",
        "## 修订备注",
        _render_bullets(
            [
                "由 Style Guide Agent 根据用户输入生成，保存前请人工审阅。",
                *guide.revision_notes,
            ]
        ),
        "",
    ]
    return "\n".join(parts)


def default_mock_generated_style_guide_json() -> str:
    return json.dumps(
        {
            "schema_version": 2,
            "style_sources": ["用户提供的作家组合：以高层风格特征综合，不复刻任何具体作者"],
            "overall_style": "整体保持武侠气质、悬念推进和凝练表达，兼顾诗性意象与清晰叙事。",
            "narrative_view": "以贴近主角感知的第三人称为主，关键时刻收窄视角，保留未说破的余味。",
            "language_rules": [
                "句式以短句和中句为主，关键景物描写允许适度拉长。",
                "意象服务人物处境，不堆砌典故或华丽形容。",
                "动作描写清楚、有节制，避免把招式解释成设定说明。",
            ],
            "dialogue_rules": [
                "对白要有留白，人物不把动机一次说尽。",
                "用语保持古意和现代可读性之间的平衡。",
            ],
            "pacing_rules": [
                "冲突场景推进迅速，转折前后留一两处静默或景物落点。",
                "章节结尾保留悬念，但不依赖夸张反转。",
            ],
            "avoid": [
                "不要引用或改写任何真实作品原文。",
                "不要宣称复刻某位作者。",
                "避免网络爽文腔、过度解释和频繁感叹。",
            ],
            "sample_paragraph": "雨停在檐角，像一线将断未断的弦。沈照提灯立在廊下，听见院门外有人轻轻叩了三声。那声音不急，却让他想起十年前雪地里的刀光。",
            "revision_notes": ["这是基于用户输入生成的综合文风草稿，可继续手工删改后保存。"],
        },
        ensure_ascii=False,
    )


def _style_guide_repair_prompt(invalid_output: str, error: str) -> str:
    return (
        "请重新只输出合法 GeneratedStyleGuide JSON object，不要 Markdown、解释或包装语。\n"
        f"解析或校验错误：{error}\n"
        f"上一次输出节选：\n{invalid_output[:4000]}"
    )


def _existing_style_context(root: Path, warnings: list[str]) -> str:
    path = root / "memory" / "style_guide.md"
    if not path.exists():
        warnings.append("memory/style_guide.md is missing; existing style context was not included")
        return ""
    text = path.read_text(encoding="utf-8").strip()
    return _truncate_context(text)


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


def _project_prompt_context(project: ProjectConfig) -> str:
    return _project_context(project)


def _render_bullets(items: list[str]) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return "- 待补充"
    return "\n".join(f"- {item}" for item in cleaned)


def _truncate_context(text: str, max_chars: int = 12000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...（已截断）"
