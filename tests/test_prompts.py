from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from novel.core.auditing import build_audit_system_prompt
from novel.core.canon import build_canon_system_prompt
from novel.core.chapter_memory import build_chapter_memory_system_prompt
from novel.core.drafting import build_writer_system_prompt
from novel.core.planning import build_planning_system_prompt
from novel.core.polishing import build_polish_system_prompt
from novel.core.prompts import PROMPT_VERSION, PROMPT_VERSIONS, load_prompt_template
from novel.core.revision import build_revision_system_prompt
from novel.core.state_update import build_state_update_system_prompt


def test_prompt_templates_are_versioned_and_loadable() -> None:
    assert PROMPT_VERSION == "2026-06-07"
    assert "Writer Agent" in load_prompt_template("writer_system")


def test_prompt_versions_cover_non_partial_templates() -> None:
    template_names = {
        item.name.removesuffix(".txt")
        for item in files("novel.prompts").iterdir()
        if item.name.endswith(".txt")
    }

    assert set(PROMPT_VERSIONS) == template_names
    assert PROMPT_VERSION == max(PROMPT_VERSIONS.values())
    assert PROMPT_VERSIONS["writer_system"] == "2026-06-05"
    assert PROMPT_VERSIONS["orchestrator_ask_intent_system"] == "2026-05-31"


def test_prompt_partials_render_and_raw_prompts_do_not_duplicate_shared_context_text() -> None:
    prompt = load_prompt_template("writer_system")
    shared_sentence = "如果 user prompt 中包含 ContextBundle，请把它视为外层系统已检索出的长期记忆参考"

    assert "{{partial:" not in prompt
    assert shared_sentence in prompt

    prompts_dir = Path(__file__).resolve().parents[1] / "src" / "novel" / "prompts"
    duplicated = [
        path.name
        for path in prompts_dir.glob("*_system.txt")
        if shared_sentence in path.read_text(encoding="utf-8")
    ]
    assert duplicated == []
    assert shared_sentence in (prompts_dir / "partials" / "context_bundle_memory.txt").read_text(encoding="utf-8")


def test_agent_system_prompts_keep_core_constraints() -> None:
    assert "不要写正文" in build_planning_system_prompt()
    assert "不要修改 canon" in build_planning_system_prompt()
    assert "不要输出大纲、解释、分析或 JSON" in build_writer_system_prompt()
    assert "不要提前揭示 hidden_truths" in build_writer_system_prompt()
    assert "不要输出解释、分析、修改说明、JSON 或大纲" in build_polish_system_prompt()
    assert "只输出 AuditReport JSON" in build_audit_system_prompt()
    assert "不要更新 canon/state/timeline" in build_audit_system_prompt()
    assert "hidden_truths" in build_canon_system_prompt()
    assert "reader_visible_summary" in build_canon_system_prompt()
    assert "Revision Loop 必须受最大轮数限制" in build_revision_system_prompt()
    assert "只输出结构化 JSON" in build_state_update_system_prompt()
    assert "只输出 ChapterMemory JSON" in build_chapter_memory_system_prompt()
    assert "不是正式事实源" in build_chapter_memory_system_prompt()


def test_all_agent_prompts_explain_context_bundle_memory() -> None:
    prompts = [
        build_planning_system_prompt(),
        build_writer_system_prompt(),
        build_polish_system_prompt(),
        build_audit_system_prompt(),
        build_canon_system_prompt(),
        build_revision_system_prompt(),
        build_state_update_system_prompt(),
        load_prompt_template("inspiration_system"),
    ]

    for prompt in prompts:
        assert "ContextBundle" in prompt
        assert "长期记忆参考" in prompt
        assert "FTS/embedding" not in prompt
        assert "不要伪造缺失记忆" in prompt
