from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from novel.core.auditing import build_audit_system_prompt
from novel.core.canon import build_canon_system_prompt
from novel.core.chapter_memory import build_chapter_memory_system_prompt
from novel.core.drafting import build_writer_system_prompt
from novel.core.planning import build_planning_system_prompt
from novel.core.polishing import build_polish_system_prompt
from novel.core.prompts import PROMPT_VERSION, PROMPT_VERSIONS, load_prompt_template, prompt_template_version
from novel.core.revision import build_revision_system_prompt
from novel.core.state_update import build_state_update_system_prompt
from novel.core.style_guide import build_style_guide_system_prompt


def test_prompt_templates_are_versioned_and_loadable() -> None:
    assert PROMPT_VERSION == "2026-07-11"
    assert "Writer Agent" in load_prompt_template("writer_system")


def test_prompt_versions_cover_non_partial_templates() -> None:
    template_names = {
        item.name.removesuffix(".txt") for item in files("novel.prompts").iterdir() if item.name.endswith(".txt")
    }

    assert set(PROMPT_VERSIONS) == template_names
    assert PROMPT_VERSION == max(PROMPT_VERSIONS.values())
    assert PROMPT_VERSIONS["writer_system"] == "2026-07-11"
    assert PROMPT_VERSIONS["style_guide_system"] == "2026-06-19"
    assert PROMPT_VERSIONS["audit_repair_route_system"] == "2026-06-20"
    assert PROMPT_VERSIONS["intent_router_ask_intent_system"] == "2026-06-19"
    assert prompt_template_version("writer_system") == PROMPT_VERSIONS["writer_system"]
    assert prompt_template_version("writer_system.txt") == PROMPT_VERSIONS["writer_system"]


def test_route_prompts_use_intent_router_name() -> None:
    for template_name in (
        "intent_router_ask_intent_system",
        "intent_router_revision_route_system",
        "audit_repair_route_system",
    ):
        prompt = load_prompt_template(template_name)
        assert "Intent Router" in prompt
    assert "WriterYang 的 Orchestrator" not in load_prompt_template("audit_repair_route_system")


def test_prompt_partials_render_and_raw_prompts_do_not_duplicate_shared_context_text() -> None:
    prompt = load_prompt_template("writer_system")
    shared_sentence = (
        "如果 user prompt 中包含 ContextBundle，请把它视为外层系统按 authority、lifecycle、visibility 和当前 Task Policy 选择的长期记忆参考"
    )

    assert "{{partial:" not in prompt
    assert shared_sentence in prompt

    prompts_dir = Path(__file__).resolve().parents[1] / "src" / "novel" / "prompts"
    duplicated = [
        path.name for path in prompts_dir.glob("*_system.txt") if shared_sentence in path.read_text(encoding="utf-8")
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


def test_reveal_policy_is_consistent_across_planning_and_writing_prompts() -> None:
    planning = build_planning_system_prompt()
    writer = build_writer_system_prompt()
    polish = build_polish_system_prompt()
    revision = build_revision_system_prompt()

    assert "reveal_authorizations" in planning
    assert "用户 instruction 本身不是揭示授权" in writer
    assert "RevealAuthorization" in polish
    assert "用户修订 instruction 本身不是揭示授权" in revision
    assert "plan.json 或用户 instruction 明确要求" not in writer
    assert "hidden_truths" in build_canon_system_prompt()
    assert "reader_visible_summary" in build_canon_system_prompt()
    assert "Workflow Runtime" in build_revision_system_prompt()
    assert "只输出结构化 JSON" in build_state_update_system_prompt()
    assert "只输出 ChapterMemory JSON" in build_chapter_memory_system_prompt()
    assert "不是正式事实源" in build_chapter_memory_system_prompt()
    assert "只输出 GeneratedStyleGuide JSON" in build_style_guide_system_prompt()


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
