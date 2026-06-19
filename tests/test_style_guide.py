from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel.core.providers import MockProvider
from novel.core.style_guide import (
    StyleGuideGenerationError,
    StyleGuideGenerationOptions,
    generate_style_guide,
    render_generated_style_guide_markdown,
)
from novel.core.schemas import GeneratedStyleGuide
from novel.core.workspace import InitOptions, init_workspace


def test_render_generated_style_guide_markdown_uses_project_template_sections() -> None:
    guide = GeneratedStyleGuide.model_validate(
        {
            "style_sources": ["凝练对白", "诗性意象"],
            "overall_style": "保持江湖气、悬念和克制抒情。",
            "narrative_view": "第三人称贴近主角感知。",
            "language_rules": ["短句为主。"],
            "dialogue_rules": ["对白留白。"],
            "pacing_rules": ["动作场景快速推进。"],
            "avoid": ["不要复刻任何具体作者。"],
            "sample_paragraph": "雨声停在檐角，他才听见门外那一声轻叩。",
            "revision_notes": ["保存前人工审阅。"],
        }
    )

    markdown = render_generated_style_guide_markdown(guide)

    assert markdown.startswith("# 文风设置")
    assert "## 风格来源" in markdown
    assert "- 凝练对白" in markdown
    assert "## 整体风格\n保持江湖气、悬念和克制抒情。" in markdown
    assert "## 禁用项\n- 不要复刻任何具体作者。" in markdown
    assert "## 示例段落\n雨声停在檐角" in markdown


def test_generate_style_guide_with_mock_provider_returns_markdown(tmp_path: Path) -> None:
    root = _style_workspace(tmp_path)
    provider = MockProvider(fake_response=_style_guide_json())

    result = generate_style_guide(
        StyleGuideGenerationOptions(root=root, instruction="结合古典武侠和克制悬疑。"),
        provider,
    )

    assert "# 文风设置" in result.content
    assert "凝练、清晰、有留白" in result.content
    assert provider.requests[0].json_schema_name == "GeneratedStyleGuide"
    assert "现有文风设置" in provider.requests[0].user_prompt
    assert "结合古典武侠和克制悬疑" in provider.requests[0].user_prompt


def test_generate_style_guide_rejects_empty_instruction(tmp_path: Path) -> None:
    root = _style_workspace(tmp_path)

    with pytest.raises(StyleGuideGenerationError):
        generate_style_guide(StyleGuideGenerationOptions(root=root, instruction="   "), MockProvider())


def test_generate_style_guide_repairs_invalid_json(tmp_path: Path) -> None:
    root = _style_workspace(tmp_path)
    provider = MockProvider(fake_response=["{not valid json", _style_guide_json()])

    result = generate_style_guide(
        StyleGuideGenerationOptions(root=root, instruction="生成一版文风设置。"),
        provider,
    )

    assert "凝练、清晰、有留白" in result.content
    assert len(provider.requests) == 2
    assert "上一次结构化输出没有通过解析或校验" in provider.requests[1].user_prompt


def _style_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "style_guide.md").write_text("# 文风设置\n\n保持克制。\n", encoding="utf-8")
    return root


def _style_guide_json() -> str:
    return json.dumps(
        {
            "schema_version": 2,
            "style_sources": ["古典武侠气质", "克制悬疑节奏"],
            "overall_style": "凝练、清晰、有留白，保留江湖气和悬念。",
            "narrative_view": "第三人称贴近主角感知。",
            "language_rules": ["中短句为主。"],
            "dialogue_rules": ["对白少解释，多含蓄。"],
            "pacing_rules": ["动作快，转折后留静默。"],
            "avoid": ["不要引用原文。"],
            "sample_paragraph": "雨停后，旧车站的灯仍亮着，像有人在黑暗里等一句迟来的答复。",
            "revision_notes": ["保存前确认是否符合项目长期方向。"],
        },
        ensure_ascii=False,
    )
