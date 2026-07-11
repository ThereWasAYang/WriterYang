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
    parse_chapter_plan,
    plan_chapter,
)
from novel.core.providers import MockProvider
from novel.core.search import (
    rebuild_search_index,
    refresh_search_index,
    retrieve_context,
    retrieve_context_bundle,
    resolve_vector_context_mode,
    search_index_status,
    search_project,
    write_context_report,
)
from novel.core.session import (
    SessionActionOptions,
    SessionRunOptions,
    SessionStartOptions,
    accept_session,
    approve_outline,
    run_session,
    start_session,
)
from novel.core.schemas import ContextBundle
from novel.core.workspace import InitOptions, init_workspace


def test_index_rebuild_creates_search_index(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)

    result = rebuild_search_index(root)

    assert result.index_path == root / "memory" / "search_index.json"
    assert result.manifest_path == root / "memory" / "search_index_manifest.json"
    assert result.index_path.is_file()
    assert result.manifest_path.is_file()
    assert (root / "memory" / "search_index.sqlite").is_file()
    assert result.document_count > 0
    with sqlite3.connect(root / "memory" / "search_index.sqlite") as conn:
        vector_count = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
    assert vector_count == 0
    assert fts_count == result.document_count
    status = search_index_status(root)
    assert status.fts_status == "indexed"
    assert status.embedding_status in {"env_missing", "missing", "not_configured", "test_only"}
    payload = json.loads(result.index_path.read_text(encoding="utf-8"))
    for document in payload["documents"]:
        assert document["authority"] in {"canonical", "approved_plan", "accepted_chapter", "chapter_memory"}
        assert document["lifecycle_status"] in {"current", "accepted", "fresh", "working"}
        assert document["visibility"] in {"reader_visible", "author_only", "hidden_truth", "audit_only"}
        assert len(document["source_sha256"]) == 64


def test_index_allowlist_excludes_archive_rejection_backup_and_working_candidates(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    excluded_files = (
        root / "memory" / "archive" / "session_x" / "archived.md",
        root / "memory" / "sessions" / "session_x" / "rejections" / "rejected.md",
        root / "memory" / "backups" / "backup.md",
        root / "memory" / "chapters" / "001" / "polished.md",
    )
    for path in excluded_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("不应召回的唯一毒化文本 poison_archive_7788", encoding="utf-8")

    rebuild_search_index(root)

    assert search_project(root, "poison_archive_7788", limit=20) == []


def test_index_refresh_updates_only_changed_documents(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    rebuild_search_index(root)
    first_status = search_index_status(root)
    assert first_status.fts_status == "indexed"

    characters_path = root / "memory" / "canon" / "characters.json"
    characters = json.loads(characters_path.read_text(encoding="utf-8"))
    characters["characters"][0]["reader_visible_summary"] += " 他握着蓝色伞柄。"
    characters_path.write_text(json.dumps(characters, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    stale_status = search_index_status(root)
    assert stale_status.fts_status == "stale"
    assert stale_status.stale_document_count >= 1

    result = refresh_search_index(root)

    assert result.refreshed_count >= 1
    assert result.deleted_count == 0
    assert search_index_status(root).fts_status == "indexed"
    assert search_project(root, "蓝色伞柄", limit=5)


def test_index_refresh_counts_deleted_documents(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    rebuild_search_index(root)
    characters_path = root / "memory" / "canon" / "characters.json"
    characters = json.loads(characters_path.read_text(encoding="utf-8"))
    characters["characters"] = []
    characters_path.write_text(json.dumps(characters, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = refresh_search_index(root)

    assert result.deleted_count >= 1
    assert search_index_status(root).fts_status == "indexed"


def test_search_finds_character(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    rebuild_search_index(root)

    results = search_project(root, "林澈", search_type="character", limit=5)

    assert results
    assert results[0].type == "character"
    assert "char_lin_che" in results[0].id
    assert "林澈" in results[0].excerpt


def test_search_can_use_vector_scores(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    rebuild_search_index(root, embedding_provider_name="local_hash", with_embeddings=True)

    results = search_project(
        root,
        "修复旧物的人",
        search_type="all",
        limit=5,
        use_vector=True,
        embedding_provider_name="local_hash",
    )

    assert results
    assert any("vector_score" in result.metadata for result in results)


def test_vector_search_auto_refreshes_stale_embedding_index(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    rebuild_search_index(root)

    results = search_project(
        root,
        "修复旧物的人",
        search_type="all",
        limit=5,
        use_vector=True,
        embedding_provider_name="local_hash",
    )

    assert results
    assert any("vector_score" in result.metadata for result in results)
    with sqlite3.connect(root / "memory" / "search_index.sqlite") as conn:
        vector_count = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    assert vector_count > 0


def test_vector_search_without_real_embedding_index_fails_clearly(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    rebuild_search_index(root)

    code, stdout, stderr = _run_cli(["search", "林澈", "--path", str(root), "--use-vector"])

    assert code != 0
    assert stdout == ""
    assert "embedding vector" in stderr


def test_vector_context_auto_enables_only_when_embedding_config_is_complete(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)

    enabled, warnings = resolve_vector_context_mode(root, "auto")
    assert enabled is False
    assert any("missing embedding environment" in warning for warning in warnings)

    (root / ".env").write_text('DASHSCOPE_API_KEY="test-key"\n', encoding="utf-8")
    enabled, warnings = resolve_vector_context_mode(root, "auto")
    assert enabled is True
    assert warnings == []


def test_vector_context_auto_disables_test_only_local_hash(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    embeddings_path = root / "config" / "embeddings.yaml"
    embeddings_path.write_text(
        embeddings_path.read_text(encoding="utf-8").replace(
            'active_provider: "dashscope"',
            'active_provider: "test_local_hash"',
        ),
        encoding="utf-8",
    )

    enabled, warnings = resolve_vector_context_mode(root, "auto")

    assert enabled is False
    assert any("local_hash" in warning for warning in warnings)


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


def test_context_bundle_schema_round_trips() -> None:
    bundle = ContextBundle(
        chapter_number=1,
        task="write",
        query="chapter 1 林澈",
        included=[
            {
                "id": "char_lin_che",
                "type": "character",
                "source": "memory/canon/characters.json",
                "visibility": "reader_visible",
                "reason": "directly referenced by ChapterPlan",
                "priority": 100,
                "content": {"name": "林澈"},
            }
        ],
        excluded=[
            {
                "id": "truth_station_overlap",
                "type": "hidden_truth",
                "source": "memory/canon/hidden_truths.json",
                "visibility": "hidden_truth",
                "reason": "protected from drafting output",
            }
        ],
        created_at="2026-05-24T00:00:00Z",
    )

    reloaded = ContextBundle.model_validate_json(bundle.model_dump_json())

    assert reloaded.included[0].visibility == "reader_visible"
    assert reloaded.excluded[0].reason == "protected from drafting output"


def test_project_level_context_bundle_writes_run_report(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)

    bundle = retrieve_context_bundle(
        root,
        chapter_number=None,
        task="canon",
        instruction="旧车站广播",
        limit=5,
    )
    path = write_context_report(root, bundle)

    assert path.is_file()
    assert path.parent == root / "runs"
    assert "context_report.canon" in path.name
    assert bundle.chapter_number is None


def test_context_bundle_expands_chapter_plan_entities_and_state(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    _write_current_state(root)
    _write_timeline_event(root)
    payload = json.loads(default_mock_chapter_plan_json(1))
    payload["required_context"]["timeline_event_ids"] = ["event_broadcast"]
    plan = parse_chapter_plan(json.dumps(payload, ensure_ascii=False))

    bundle = retrieve_context_bundle(
        root,
        chapter_number=1,
        task="write",
        instruction="林澈调查广播",
        plan=plan,
        limit=20,
    )

    included = {(item.type, item.id): item for item in bundle.included}
    assert ("character", "char_lin_che") in included
    assert ("location", "loc_old_station") in included
    assert ("item", "item_broken_ticket") in included
    assert ("character_state", "state_char_lin_che") in included
    assert ("item_state", "state_item_broken_ticket") in included
    assert ("timeline_event", "event_broadcast") in included
    assert included[("character", "char_lin_che")].priority == 100
    assert "建立追查动机" in bundle.query


def test_context_bundle_recalls_key_timeline_events_for_plan_focus(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    _write_timeline_events(
        root,
        [
            {
                "id": "event_old_backstory",
                "chapter": 1,
                "in_story_time": "七年前",
                "summary": "早年广播协议留下未解影响",
                "reader_visible": True,
                "event_role": "backstory",
                "location_id": "loc_other",
                "participant_ids": ["char_lin_che"],
            },
            {
                "id": "event_old_current",
                "chapter": 1,
                "in_story_time": "七年前",
                "summary": "普通巡查记录",
                "reader_visible": True,
                "event_role": "current_action",
                "location_id": "loc_other",
                "participant_ids": ["char_lin_che"],
            },
            {
                "id": "event_other_backstory",
                "chapter": 1,
                "in_story_time": "七年前",
                "summary": "旁支角色旧事",
                "reader_visible": True,
                "event_role": "backstory",
                "location_id": "loc_other",
                "participant_ids": ["char_other"],
            },
        ],
    )
    payload = json.loads(default_mock_chapter_plan_json(8))
    payload["required_context"]["timeline_event_ids"] = []
    plan = parse_chapter_plan(json.dumps(payload, ensure_ascii=False))

    bundle = retrieve_context_bundle(
        root,
        chapter_number=8,
        task="write",
        instruction="继续调查",
        plan=plan,
        limit=20,
    )

    included = {(item.type, item.id): item for item in bundle.included}
    assert ("timeline_event", "event_old_backstory") in included
    assert included[("timeline_event", "event_old_backstory")].priority == 88
    assert ("timeline_event", "event_old_current") not in included
    assert ("timeline_event", "event_other_backstory") not in included


def test_context_bundle_protects_hidden_truth_for_write(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    plan = parse_chapter_plan(default_mock_chapter_plan_json(1))

    bundle = retrieve_context_bundle(
        root,
        chapter_number=1,
        task="write",
        instruction="揭示隐藏真相",
        plan=plan,
        limit=20,
    )
    rendered = bundle.render_for_prompt()

    assert "旧车站在特定雨夜会短暂连接过去的时间层" not in rendered
    assert "广播来自过去的时间层" not in rendered
    assert any(item.id == "truth_station_overlap" for item in bundle.excluded)
    assert any("protected" in item.reason for item in bundle.excluded)
    assert bundle.warnings


def test_context_bundle_allows_hidden_truth_for_audit(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    plan = parse_chapter_plan(default_mock_chapter_plan_json(1))

    bundle = retrieve_context_bundle(
        root,
        chapter_number=1,
        task="audit",
        instruction="检查是否提前揭示",
        plan=plan,
        limit=20,
    )
    rendered = bundle.render_for_prompt()

    assert any(item.type == "hidden_truth" for item in bundle.included)
    assert "旧车站在特定雨夜会短暂连接过去的时间层" in rendered


def test_context_bundle_allows_only_explicitly_authorized_hidden_truth_for_writer(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    payload = json.loads(default_mock_chapter_plan_json(1))
    payload["reveal_authorizations"] = [
        {
            "hidden_truth_id": "truth_station_overlap",
            "chapter_number": 1,
            "method": "通过广播日期揭示",
            "reason": "本章已批准的核心揭示",
        }
    ]
    plan = parse_chapter_plan(json.dumps(payload, ensure_ascii=False))

    bundle = retrieve_context_bundle(
        root,
        chapter_number=1,
        task="write",
        instruction="按已批准计划写作",
        plan=plan,
        limit=20,
    )
    rendered = bundle.render_for_prompt()

    assert "旧车站在特定雨夜会短暂连接过去的时间层" in rendered
    assert not any(item.id == "truth_station_overlap" for item in bundle.excluded)
    assert "BEGIN UNTRUSTED_WORKSPACE_DATA" in rendered


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
    assert result.context_report_path is not None
    assert result.context_report_path.is_file()
    assert provider.requests
    assert "Context bundle" in provider.requests[0].user_prompt


def test_creation_session_uses_search_context_by_default(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    started = start_session(
        SessionStartOptions(root=root, user_intent="写第1章", chapter_range=(1,), provider_name="mock")
    )
    approve_outline(SessionActionOptions(root=root, session_id=started.session.session_id))
    result = run_session(SessionRunOptions(root=root, session_id=started.session.session_id, provider_name="mock"))

    assert result.session.content_status == "needs_user_review"
    reports = list((root / "memory" / "chapters" / "001").glob("context_report*.json"))
    assert reports


def test_cli_search_json_output(tmp_path: Path) -> None:
    root = _workspace_ready_for_search(tmp_path)
    _run_cli(["index", "rebuild", "--path", str(root), "--embedding-provider", "local_hash", "--with-embeddings"])

    code, stdout, stderr = _run_cli(
        [
            "search",
            "林澈",
            "--path",
            str(root),
            "--type",
            "character",
            "--use-vector",
            "--embedding-provider",
            "local_hash",
            "--json",
        ]
    )

    assert code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["results"][0]["type"] == "character"
    assert "vector_score" in payload["results"][0]["metadata"]


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
    assert payload["results"][0]["metadata"]["chapter"] == 1
    assert "<mark>" in payload["results"][0]["highlighted_excerpt"]


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
    started = start_session(
        SessionStartOptions(
            root=root,
            user_intent="写第一章并保留旧车站广播线索。",
            chapter_range=(1,),
            provider_name="mock",
        )
    )
    session_id = started.session.session_id
    approve_outline(SessionActionOptions(root=root, session_id=session_id))
    run_session(SessionRunOptions(root=root, session_id=session_id, provider_name="mock"))
    accept_session(SessionActionOptions(root=root, session_id=session_id, provider_name="mock"))
    return root


def _write_timeline_event(root: Path) -> None:
    _write_timeline_events(
        root,
        [
            {
                "id": "event_broadcast",
                "chapter": 1,
                "in_story_time": "雨夜",
                "summary": "旧车站广播响起",
                "reader_visible": True,
                "location_id": "loc_old_station",
                "participant_ids": ["char_lin_che"],
            }
        ],
    )


def _write_timeline_events(root: Path, events: list[dict[str, object]]) -> None:
    normalized_events: list[dict[str, object]] = []
    for event in events:
        chapter = event.get("chapter", 1)
        event_data = {key: value for key, value in event.items() if key not in {"chapter", "scene", "in_story_time"}}
        normalized_events.append(
            {
                **event_data,
                "narrative_position": {"chapter": chapter},
                "story_position": {"time_label": str(event.get("in_story_time", "未知"))},
            }
        )
    (root / "memory" / "state" / "timeline.json").write_text(
        json.dumps(
            {"events": normalized_events},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_current_state(root: Path) -> None:
    (root / "memory" / "state" / "current_state.json").write_text(
        json.dumps(
            {
                "story_position": {"latest_chapter": 0},
                "character_states": [
                    {
                        "entity_id": "char_lin_che",
                        "location_id": "loc_old_station",
                        "health": "疲惫",
                        "mental_state": "警觉",
                        "knowledge": [],
                        "goals": ["调查广播"],
                        "possessions": [],
                        "last_updated_chapter": 0,
                    }
                ],
                "item_states": [
                    {
                        "entity_id": "item_broken_ticket",
                        "holder_id": None,
                        "location_id": "loc_old_station",
                        "condition": "潮湿",
                        "known_properties": [],
                        "last_updated_chapter": 0,
                    }
                ],
                "location_states": [
                    {
                        "entity_id": "loc_old_station",
                        "accessibility": "可进入",
                        "condition": "废弃",
                        "active_events": [],
                        "last_updated_chapter": 0,
                    }
                ],
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
