from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
import os
from pathlib import Path

from novel.cli import main
from novel.core import chapter_memory as chapter_memory_module
from novel.core import state_update as state_update_module
from novel.core.auditing import ChapterAuditOptions, audit_chapter, default_mock_audit_report_json
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.chapter_memory import (
    accepted_chapter_numbers as _accepted_chapter_numbers,
    default_mock_chapter_memory_json,
    load_chapter_memories,
    load_chapter_memory_context,
    parse_chapter_memory,
    render_chapter_memory_prompt_text,
    validate_chapter_memory,
)
from novel.core.drafting import ChapterDraftingOptions, write_chapter_draft
from novel.core.migration import CURRENT_SCHEMA_VERSION
from novel.core.planning import ChapterPlanningOptions, default_mock_chapter_plan_json, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, polish_chapter
from novel.core.providers import MockProvider
from novel.core.search import rebuild_search_index, retrieve_context_bundle, search_project
from novel.core.schemas import ChapterMemory, ChapterMemoryItem, ChapterPlan, ProjectConfig
from novel.core.state_update import AcceptChapterOptions, accept_chapter
from novel.core.io import atomic_write_model_json, atomic_write_yaml, load_json_model, load_yaml, load_yaml_model
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
    plan_path = root / "memory" / "chapters" / "001" / "plan.json"
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_data["summary"] = "SECRET_PLAN_SUMMARY_DO_NOT_LEAK"
    plan_path.write_text(json.dumps(plan_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def fail_provider(*args, **kwargs):
        raise RuntimeError("provider config missing")

    monkeypatch.setattr(state_update_module, "load_chapter_memory_provider", fail_provider)

    result = accept_chapter(AcceptChapterOptions(root=root, chapter_number=1))

    assert result.chapter_memory_result is not None
    memory = result.chapter_memory_result.memory
    assert memory.generation_status == "deterministic_fallback"
    assert "SECRET_PLAN_SUMMARY_DO_NOT_LEAK" not in memory.reader_visible_summary
    assert "雨声更深" in memory.reader_visible_summary
    assert all(item.visibility == "author_only" for item in memory.plot_beats)
    assert any("provider unavailable" in warning for warning in memory.warnings)
    assert any("deterministic fallback" in warning for warning in memory.warnings)
    events = (root / "memory" / "management_events.jsonl").read_text(encoding="utf-8")
    assert "chapter_memory_generated" in events
    assert "chapter_memory_failed" not in events


def test_parse_chapter_memory_overwrites_stale_schema_version(tmp_path: Path) -> None:
    root = _workspace_with_accepted_memory(tmp_path)
    context = load_chapter_memory_context(root, 1)
    payload = json.loads(default_mock_chapter_memory_json(1))
    payload["schema_version"] = 1

    memory = parse_chapter_memory(json.dumps(payload, ensure_ascii=False), context)

    assert memory.schema_version == CURRENT_SCHEMA_VERSION


def test_chapter_memory_stale_sha_is_detected(tmp_path: Path) -> None:
    root = _workspace_with_accepted_memory(tmp_path)
    memory_path = root / "memory" / "chapters" / "001" / "chapter_memory.json"
    memory = load_json_model(memory_path, ChapterMemory)
    polished_path = root / "memory" / "chapters" / "001" / "polished.md"
    polished_path.write_text(polished_path.read_text(encoding="utf-8") + "\n补写一句。\n", encoding="utf-8")
    newer_time = memory_path.stat().st_mtime + 10
    os.utime(polished_path, (newer_time, newer_time))

    warnings = validate_chapter_memory(root, memory)
    memories, load_warnings = load_chapter_memories(root, before_chapter_number=2)

    assert any("stale chapter memory" in warning for warning in warnings)
    assert memories == []
    assert any("stale chapter memory" in warning for warning in load_warnings)


def test_chapter_memory_hot_path_skips_hash_when_memory_is_newer(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_with_accepted_memory(tmp_path)
    memory_path = root / "memory" / "chapters" / "001" / "chapter_memory.json"
    polished_path = root / "memory" / "chapters" / "001" / "polished.md"
    memory = load_json_model(memory_path, ChapterMemory)
    newer_time = polished_path.stat().st_mtime + 10
    os.utime(memory_path, (newer_time, newer_time))
    hash_calls: list[Path] = []

    def fake_sha256(path: Path) -> str:
        hash_calls.append(path)
        return memory.source.polished_sha256

    monkeypatch.setattr(chapter_memory_module, "_sha256", fake_sha256)

    memories, load_warnings = load_chapter_memories(root, before_chapter_number=2)
    validation_warnings = validate_chapter_memory(root, memory)

    assert len(memories) == 1
    assert load_warnings == []
    assert validation_warnings == []
    assert hash_calls == [polished_path]


def test_chapter_memory_prompt_redacts_hidden_items_for_writer(tmp_path: Path) -> None:
    root = _workspace_with_accepted_memory(tmp_path)
    memory_path = root / "memory" / "chapters" / "001" / "chapter_memory.json"
    memory = load_json_model(memory_path, ChapterMemory)
    hidden_note = ChapterMemoryItem(
        summary="SECRET_NOTE_DO_NOT_LEAK",
        visibility="hidden_truth",
        source_refs=[{"path": "memory/chapters/001/polished.md", "kind": "accepted_polished"}],
    )
    hidden_hint = ChapterMemoryItem(
        summary="SECRET_HINT_DO_NOT_LEAK",
        visibility="author_only",
        source_refs=[{"path": "memory/chapters/001/polished.md", "kind": "accepted_polished"}],
    )
    safe_note = ChapterMemoryItem(
        summary="SAFE_CONTINUITY_NOTE",
        visibility="reader_visible",
        source_refs=[{"path": "memory/chapters/001/polished.md", "kind": "accepted_polished"}],
    )
    safe_hint = ChapterMemoryItem(
        summary="SAFE_RETRIEVAL_HINT",
        visibility="reader_visible",
        source_refs=[{"path": "memory/chapters/001/polished.md", "kind": "accepted_polished"}],
    )
    memory = memory.model_copy(
        update={
            "continuity_notes": [hidden_note, safe_note],
            "retrieval_hints": [hidden_hint, safe_hint],
        }
    )
    atomic_write_model_json(memory_path, memory)
    project = load_yaml_model(root / "project.yaml", ProjectConfig)

    plot_context = render_chapter_memory_prompt_text(root, project=project, chapter_number=2, task="plan")
    writer_context = render_chapter_memory_prompt_text(root, project=project, chapter_number=2, task="write")

    assert "SECRET_NOTE_DO_NOT_LEAK" in plot_context
    assert "SECRET_HINT_DO_NOT_LEAK" in plot_context
    assert "SECRET_NOTE_DO_NOT_LEAK" not in writer_context
    assert "SECRET_HINT_DO_NOT_LEAK" not in writer_context
    assert "SAFE_CONTINUITY_NOTE" in writer_context
    assert "SAFE_RETRIEVAL_HINT" in writer_context
    assert "not a source of truth" in writer_context


def test_chapter_memory_overview_is_budgeted_and_keeps_selected_memory(tmp_path: Path) -> None:
    root = _workspace_with_accepted_memory(tmp_path)
    base_memory = load_json_model(root / "memory" / "chapters" / "001" / "chapter_memory.json", ChapterMemory)
    base_polished = root / "memory" / "chapters" / "001" / "polished.md"
    base_body = base_polished.read_text(encoding="utf-8")
    source_ref = {"path": "memory/chapters/001/polished.md", "kind": "accepted_polished"}
    focused_item = ChapterMemoryItem(
        summary="FOCUSED_OLD_MEMORY",
        visibility="author_only",
        related_entity_ids=["char_lin_che"],
        source_refs=[source_ref],
    )
    for chapter_number in range(2, 31):
        chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
        chapter_dir.mkdir(parents=True, exist_ok=True)
        polished_path = chapter_dir / "polished.md"
        polished_path.write_text(
            base_body.replace("chapter_number: 1", f"chapter_number: {chapter_number}", 1),
            encoding="utf-8",
        )
        source = base_memory.source.model_copy(
            update={
                "polished_path": f"memory/chapters/{chapter_number:03d}/polished.md",
                "polished_sha256": _sha256(polished_path),
            }
        )
        atomic_write_model_json(
            chapter_dir / "chapter_memory.json",
            base_memory.model_copy(
                update={
                    "chapter_number": chapter_number,
                    "title": f"第 {chapter_number} 章",
                    "source": source,
                    "reader_visible_summary": f"SUMMARY_{chapter_number:03d}",
                }
            ),
        )
    memory_one = base_memory.model_copy(update={"continuity_notes": [focused_item]})
    atomic_write_model_json(root / "memory" / "chapters" / "001" / "chapter_memory.json", memory_one)
    project = load_yaml_model(root / "project.yaml", ProjectConfig)
    plan = load_json_model(root / "memory" / "chapters" / "001" / "plan.json", ChapterPlan)

    context = render_chapter_memory_prompt_text(root, project=project, chapter_number=31, task="plan", plan=plan)
    overview_lines = [line for line in context.splitlines() if line.startswith("  - chapter ")]

    assert len(overview_lines) <= 20
    assert any(line.startswith("  - chapter 1 ") for line in overview_lines)
    assert "older ChapterMemory entries omitted" in context


def test_strict_chapter_memory_failure_still_writes_metadata(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_with_audit(tmp_path)
    _run_cli(["propose-state-update", "1", "--path", str(root), "--provider", "mock"])
    project_data = load_yaml(root / "project.yaml")
    assert isinstance(project_data, dict)
    chapter_memory = dict(project_data.get("chapter_memory") or {})
    chapter_memory["strict_accept"] = True
    project_data["chapter_memory"] = chapter_memory
    atomic_write_yaml(root / "project.yaml", project_data)

    def fail_memory(*args, **kwargs):
        raise RuntimeError("simulated strict failure")

    monkeypatch.setattr(state_update_module, "generate_chapter_memory", fail_memory)

    result = accept_chapter(AcceptChapterOptions(root=root, chapter_number=1))

    assert result.metadata.status == "accepted"
    assert result.metadata_path.exists()
    assert "strict chapter memory generation failed" in "\n".join(result.warnings)
    events = [
        json.loads(line)
        for line in (root / "memory" / "management_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    memory_events = [event for event in events if event["event_type"] == "chapter_memory_failed"]
    assert memory_events
    assert memory_events[-1]["status"] == "error"


def test_accepted_chapter_numbers_do_not_match_body_status_text(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    chapter_dir = root / "memory" / "chapters" / "001"
    chapter_dir.mkdir(parents=True)
    (chapter_dir / "polished.md").write_text(
        "---\nchapter_number: 1\ntitle: 雨夜旧车站\nstatus: polished\n---\n\n正文写着 status: accepted 只是引用。\n",
        encoding="utf-8",
    )

    assert _accepted_chapter_numbers(root) == []


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
