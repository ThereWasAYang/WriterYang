from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

from novel.cli import main
from novel.core import state_update as state_update_module
from novel.core.auditing import ChapterAuditOptions, audit_chapter, default_mock_audit_report_json
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.chapter_memory import load_chapter_memories, render_chapter_memory_prompt_text, validate_chapter_memory
from novel.core.drafting import ChapterDraftingOptions, write_chapter_draft
from novel.core.planning import ChapterPlanningOptions, default_mock_chapter_plan_json, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, polish_chapter
from novel.core.providers import MockProvider
from novel.core.search import rebuild_search_index, retrieve_context_bundle, search_project
from novel.core.schemas import ChapterMemory, ChapterMemoryItem, ProjectConfig
from novel.core.state_update import AcceptChapterOptions, accept_chapter
from novel.core.io import atomic_write_model_json, load_json_model, load_yaml_model
from novel.core.workspace import InitOptions, init_workspace


def test_accept_chapter_creates_chapter_memory(tmp_path: Path) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    code, stdout, stderr = _run_cli(["accept-chapter", "1", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert "Wrote chapter memory:" in stdout
    memory_path = root / "memory" / "chapters" / "001" / "chapter_memory.json"
    memory = load_json_model(memory_path, ChapterMemory)
    assert memory.chapter_number == 1
    assert memory.source.polished_path == "memory/chapters/001/polished.md"
    assert memory.source.polished_sha256 != "0" * 64
    metadata = json.loads((root / "memory" / "chapters" / "001" / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["chapter_memory_path"] == "memory/chapters/001/chapter_memory.json"
    events = (root / "memory" / "management_events.jsonl").read_text(encoding="utf-8")
    assert "chapter_memory_generated" in events


def test_chapter_memory_failure_does_not_block_accept(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    def fail_memory(*args, **kwargs):
        raise RuntimeError("simulated memory failure")

    monkeypatch.setattr(state_update_module, "generate_chapter_memory", fail_memory)

    result = accept_chapter(AcceptChapterOptions(root=root, chapter_number=1))

    assert result.metadata.status == "accepted"
    assert any("chapter memory generation skipped" in warning for warning in result.warnings)
    events = (root / "memory" / "management_events.jsonl").read_text(encoding="utf-8")
    assert "chapter_memory_failed" in events


def test_chapter_memory_provider_failure_uses_deterministic_fallback(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])

    def fail_provider(*args, **kwargs):
        raise RuntimeError("provider config missing")

    monkeypatch.setattr(state_update_module, "load_chapter_memory_provider", fail_provider)

    result = accept_chapter(AcceptChapterOptions(root=root, chapter_number=1))

    assert result.chapter_memory_result is not None
    memory = result.chapter_memory_result.memory
    assert memory.generation_status == "deterministic_fallback"
    assert any("provider unavailable" in warning for warning in memory.warnings)
    assert any("deterministic fallback" in warning for warning in memory.warnings)
    events = (root / "memory" / "management_events.jsonl").read_text(encoding="utf-8")
    assert "chapter_memory_generated" in events
    assert "chapter_memory_failed" not in events


def test_chapter_memory_stale_sha_is_detected(tmp_path: Path) -> None:
    root = _workspace_with_accepted_memory(tmp_path)
    memory_path = root / "memory" / "chapters" / "001" / "chapter_memory.json"
    memory = load_json_model(memory_path, ChapterMemory)
    polished_path = root / "memory" / "chapters" / "001" / "polished.md"
    polished_path.write_text(polished_path.read_text(encoding="utf-8") + "\n补写一句。\n", encoding="utf-8")

    warnings = validate_chapter_memory(root, memory)
    memories, load_warnings = load_chapter_memories(root, before_chapter_number=2)

    assert any("stale chapter memory" in warning for warning in warnings)
    assert memories == []
    assert any("stale chapter memory" in warning for warning in load_warnings)


def test_chapter_memory_prompt_redacts_hidden_items_for_writer(tmp_path: Path) -> None:
    root = _workspace_with_accepted_memory(tmp_path)
    memory_path = root / "memory" / "chapters" / "001" / "chapter_memory.json"
    memory = load_json_model(memory_path, ChapterMemory)
    hidden = ChapterMemoryItem(
        summary="SECRET_DO_NOT_LEAK",
        visibility="hidden_truth",
        source_refs=[{"path": "memory/chapters/001/polished.md", "kind": "accepted_polished"}],
    )
    safe = ChapterMemoryItem(
        summary="SAFE_CONTINUITY_NOTE",
        visibility="reader_visible",
        source_refs=[{"path": "memory/chapters/001/polished.md", "kind": "accepted_polished"}],
    )
    memory = memory.model_copy(update={"foreshadowing": [hidden], "continuity_notes": [safe]})
    atomic_write_model_json(memory_path, memory)
    project = load_yaml_model(root / "project.yaml", ProjectConfig)

    plot_context = render_chapter_memory_prompt_text(root, project=project, chapter_number=2, task="plan")
    writer_context = render_chapter_memory_prompt_text(root, project=project, chapter_number=2, task="write")

    assert "SECRET_DO_NOT_LEAK" in plot_context
    assert "SECRET_DO_NOT_LEAK" not in writer_context
    assert "SAFE_CONTINUITY_NOTE" in writer_context
    assert "not a source of truth" in writer_context


def test_context_bundle_redacts_chapter_memory_excerpt_for_writer(tmp_path: Path) -> None:
    root = _workspace_with_accepted_memory(tmp_path)
    memory_path = root / "memory" / "chapters" / "001" / "chapter_memory.json"
    memory = load_json_model(memory_path, ChapterMemory)
    hidden = ChapterMemoryItem(
        summary="SECRET_DO_NOT_LEAK",
        visibility="hidden_truth",
        source_refs=[{"path": "memory/chapters/001/polished.md", "kind": "accepted_polished"}],
    )
    atomic_write_model_json(memory_path, memory.model_copy(update={"foreshadowing": [hidden]}))
    rebuild_search_index(root)

    bundle = retrieve_context_bundle(root, chapter_number=2, task="write", instruction="chapter_memory", limit=20)
    rendered = bundle.render_for_prompt()

    assert any(item.type == "search_chapter_memory" for item in bundle.included)
    assert "SECRET_DO_NOT_LEAK" not in rendered
    assert "Use it only as a pointer" in rendered


def test_search_indexes_chapter_memory(tmp_path: Path) -> None:
    root = _workspace_with_accepted_memory(tmp_path)
    rebuild_search_index(root)

    results = search_project(root, "chapter_memory", search_type="chapter_memory", limit=5)

    assert results
    assert results[0].type == "chapter_memory"
    assert results[0].path == "memory/chapters/001/chapter_memory.json"


def _workspace_with_accepted_memory(tmp_path: Path) -> Path:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])
    code, _, stderr = _run_cli(["accept-chapter", "1", "--path", str(root), "--provider", "mock"])
    assert code == 0, stderr
    return root


def _workspace_with_audit(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# Inspiration\n\n## Weak Outline\n\n雨夜旧车站传来停播多年的广播声。\n",
        encoding="utf-8",
    )
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    plan_chapter(
        ChapterPlanningOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_chapter_plan_json(1)),
    )
    write_chapter_draft(
        ChapterDraftingOptions(root=root, chapter_number=1),
        MockProvider(fake_response="雨落在旧车站。林澈听见广播，拾起半张车票。"),
    )
    polish_chapter(
        ChapterPolishingOptions(root=root, chapter_number=1),
        MockProvider(fake_response="雨声更深，旧车站像在夜里醒来。林澈收起车票。"),
    )
    audit_chapter(
        ChapterAuditOptions(root=root, chapter_number=1),
        MockProvider(fake_response=default_mock_audit_report_json(1, "polished.md")),
    )
    return root


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
