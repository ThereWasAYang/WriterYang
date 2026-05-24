from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import sqlite3
from pathlib import Path

from novel.cli import main
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.planning import (
    ChapterPlanningOptions,
    default_mock_chapter_plan_json,
    plan_chapter,
)
from novel.core.providers import MockProvider
from novel.core.search import rebuild_search_index, retrieve_context, search_project
from novel.core.workflow import GenerateChapterOptions, generate_chapter
from novel.core.workspace import InitOptions, init_workspace


def test_index_rebuild_creates_search_index(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)

    result = rebuild_search_index(root)

    assert result.index_path == root / "memory" / "search_index.json"
    assert result.index_path.is_file()
    assert (root / "memory" / "search_index.sqlite").is_file()
    assert result.document_count > 0
    with sqlite3.connect(root / "memory" / "search_index.sqlite") as conn:
        vector_count = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
    assert vector_count == result.document_count
    assert fts_count == result.document_count


def test_search_finds_character(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    rebuild_search_index(root)

    results = search_project(root, "林澈", search_type="character", limit=5)

    assert results
    assert results[0].type == "character"
    assert "char_lin_che" in results[0].id
    assert "林澈" in results[0].excerpt


def test_search_supports_chinese_tokenization_and_highlight(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    rebuild_search_index(root)

    results = search_project(root, "旧物修复师", search_type="character", limit=5, highlight=True)

    assert results
    assert results[0].type == "character"
    assert "<mark>" in results[0].highlighted_excerpt


def test_search_finds_timeline_event(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    _write_timeline_event(root)
    rebuild_search_index(root)

    results = search_project(root, "广播响起", search_type="event", limit=5)

    assert results
    assert results[0].id == "event_broadcast"
    assert results[0].metadata["chapter"] == 1


def test_search_filters_by_chapter(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    _write_timeline_event(root)
    rebuild_search_index(root)

    assert search_project(root, "广播", search_type="event", chapter_number=1, limit=5)
    assert search_project(root, "广播", search_type="event", chapter_number=2, limit=5) == []


def test_search_finds_chapter_text(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)
    rebuild_search_index(root)

    results = search_project(root, "旧车站广播", search_type="chapter", limit=5)

    assert results
    assert any("memory/chapters/001" in result.path for result in results)


def test_retriever_returns_explainable_sources(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)

    context = retrieve_context(root, chapter_number=1, instruction="林澈调查广播", limit=5)
    rendered = context.render_for_prompt()

    assert context.results
    assert context.query == "chapter 1 林澈调查广播"
    assert "matched_terms" in rendered
    assert "memory/" in rendered


def test_use_search_context_does_not_break_planning_prompt(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    provider = MockProvider(fake_response=default_mock_chapter_plan_json(1))

    result = plan_chapter(
        ChapterPlanningOptions(
            root=root,
            chapter_number=1,
            instruction="林澈调查广播",
            use_search_context=True,
        ),
        provider,
    )

    assert result.plan.chapter_number == 1
    assert provider.requests
    assert "Search context" in provider.requests[0].user_prompt


def test_cli_search_json_output(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    _run_cli(["index", "rebuild", "--path", str(root)])

    code, stdout, stderr = _run_cli(
        ["search", "林澈", "--path", str(root), "--type", "character", "--json"]
    )

    assert code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload[0]["type"] == "character"


def test_cli_search_supports_highlight_and_chapter_filter(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    _write_timeline_event(root)
    _run_cli(["index", "rebuild", "--path", str(root)])

    code, stdout, stderr = _run_cli(
        [
            "search",
            "广播",
            "--path",
            str(root),
            "--type",
            "event",
            "--chapter",
            "1",
            "--highlight",
            "--json",
        ]
    )

    assert code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload[0]["metadata"]["chapter"] == 1
    assert "<mark>" in payload[0]["highlighted_excerpt"]


def _workspace_ready_for_search(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# Inspiration\n\n## Weak Outline\n\n雨夜旧车站传来停播多年的广播声。\n",
        encoding="utf-8",
    )
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    return root


def _workspace_with_generated_chapter(tmp_path: Path) -> Path:
    root = _workspace_ready_for_search(tmp_path)
    result = generate_chapter(
        GenerateChapterOptions(root=root, chapter_number=1, provider_name="mock")
    )
    assert result.run_log.status == "completed"
    return root


def _write_timeline_event(root: Path) -> None:
    (root / "memory" / "state" / "timeline.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "event_broadcast",
                        "chapter": 1,
                        "in_story_time": "雨夜",
                        "summary": "旧车站广播响起",
                        "reader_visible": True,
                        "location_id": "loc_old_station",
                        "participant_ids": ["char_lin_che"],
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
