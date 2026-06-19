from __future__ import annotations


DEFAULT_STYLE_GUIDANCE = "# 文风设置\n\n## 整体风格\n\n保持清晰、克制、连贯，避免过度解释。\n"


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
