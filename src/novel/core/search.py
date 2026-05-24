from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal

from novel.core.embeddings import (
    EmbeddingError,
    EmbeddingProvider,
    create_embedding_provider,
    local_embedding_vector,
)
from novel.core.io import atomic_write_json, atomic_write_model_json, backup_if_exists, load_json
from novel.core.schemas import (
    ChapterPlan,
    ContextBundle,
    ContextExclusion,
    ContextItem,
    ContextTask,
    ContextVisibility,
)


SearchType = Literal["character", "location", "item", "event", "chapter", "all"]


class SearchError(RuntimeError):
    """Raised when search indexing or lookup cannot proceed."""


@dataclass(frozen=True)
class SearchDocument:
    id: str
    type: str
    path: str
    title: str
    text: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    id: str
    type: str
    path: str
    title: str
    score: int
    matched_terms: tuple[str, ...]
    excerpt: str
    highlighted_excerpt: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchIndexResult:
    index_path: Path
    document_count: int


@dataclass(frozen=True)
class RetrievedContext:
    query: str
    chapter_number: int
    results: tuple[SearchResult, ...]

    def render_for_prompt(self) -> str:
        if not self.results:
            return (
                "Search context: no indexed results matched the instruction. "
                "Do not invent missing context; rely on loaded canon/state/timeline.\n"
            )
        lines = [
            "Search context (explainable keyword retrieval):",
            f"- query: {self.query}",
            f"- chapter_number: {self.chapter_number}",
            "- results:",
        ]
        for index, result in enumerate(self.results, start=1):
            terms = ", ".join(result.matched_terms) if result.matched_terms else "none"
            lines.extend(
                [
                    f"  {index}. [{result.type}] {result.title} ({result.path})",
                    f"     score: {result.score}; matched_terms: {terms}",
                    f"     excerpt: {result.excerpt}",
                ]
            )
        return "\n".join(lines) + "\n"


def rebuild_search_index(
    root: Path,
    *,
    embedding_provider_name: str = "config",
    embedding_config_path: Path | None = None,
) -> SearchIndexResult:
    root = root.resolve()
    if not (root / "project.yaml").exists():
        raise SearchError(f"{root} does not look like a novel workspace")
    documents = _collect_documents(root)
    index_path = search_index_path(root)
    payload = {
        "version": 1,
        "documents": [_document_to_dict(document) for document in documents],
    }
    backup_if_exists(index_path, reason="index_rebuild")
    atomic_write_json(index_path, payload)
    provider = _load_embedding_provider(root, embedding_provider_name, embedding_config_path)
    _write_sqlite_index(sqlite_search_index_path(root), documents, provider)
    return SearchIndexResult(index_path=index_path, document_count=len(documents))


def search_project(
    root: Path,
    query: str,
    *,
    search_type: SearchType = "all",
    limit: int = 10,
    chapter_number: int | None = None,
    highlight: bool = False,
    rebuild_if_missing: bool = True,
    use_vector: bool = False,
    embedding_provider_name: str = "config",
    embedding_config_path: Path | None = None,
) -> list[SearchResult]:
    root = root.resolve()
    if not query.strip():
        raise SearchError("search query must not be empty")
    if limit < 1:
        raise SearchError("--limit must be a positive integer")
    index_path = search_index_path(root)
    sqlite_path = sqlite_search_index_path(root)
    if not index_path.exists():
        if not rebuild_if_missing:
            raise SearchError(f"{index_path} is missing; run novel index rebuild first")
        rebuild_search_index(
            root,
            embedding_provider_name=embedding_provider_name,
            embedding_config_path=embedding_config_path,
        )
    elif not sqlite_path.exists():
        documents = _load_index(index_path)
        provider = _load_embedding_provider(root, embedding_provider_name, embedding_config_path)
        _write_sqlite_index(sqlite_path, documents, provider)
    documents = _load_index(index_path)
    terms = _query_terms(query)
    candidate_ids = _sqlite_candidate_ids(sqlite_path, terms)
    vector_scores = (
        _vector_scores(sqlite_path, query, _load_embedding_provider(root, embedding_provider_name, embedding_config_path))
        if use_vector
        else {}
    )
    results = [
        _result_with_vector_score(document, result, vector_scores.get(document.id, 0.0))
        for document in documents
        if not candidate_ids or document.id in candidate_ids or document.id in vector_scores
        if _type_matches(document.type, search_type)
        if _chapter_matches(document.metadata, chapter_number)
        for result in [_score_document(document, query, terms)]
        if result is not None or (use_vector and document.id in vector_scores)
    ]
    sorted_results = sorted(results, key=lambda result: (-result.score, result.path, result.id))[:limit]
    if not highlight:
        return sorted_results
    return [_with_highlight(result) for result in sorted_results]


def retrieve_context(
    root: Path,
    *,
    chapter_number: int,
    instruction: str | None,
    limit: int = 8,
    use_vector: bool = True,
) -> RetrievedContext:
    query_parts = [f"chapter {chapter_number}"]
    if instruction and instruction.strip():
        query_parts.append(instruction.strip())
    query = " ".join(query_parts)
    try:
        results = tuple(
            _diverse_context_results(
                search_project(
                    root,
                    query,
                    search_type="all",
                    limit=max(limit * 3, limit),
                    highlight=True,
                    use_vector=use_vector,
                ),
                limit=limit,
                chapter_number=chapter_number,
            )
        )
    except SearchError:
        rebuild_search_index(root)
        results = tuple(
            _diverse_context_results(
                search_project(
                    root,
                    query,
                    search_type="all",
                    limit=max(limit * 3, limit),
                    highlight=True,
                    use_vector=use_vector,
                ),
                limit=limit,
                chapter_number=chapter_number,
            )
        )
    return RetrievedContext(query=query, chapter_number=chapter_number, results=results)


def retrieve_context_bundle(
    root: Path,
    *,
    chapter_number: int,
    task: ContextTask,
    instruction: str | None,
    plan: ChapterPlan | None = None,
    limit: int = 12,
    use_vector: bool = True,
) -> ContextBundle:
    root = root.resolve()
    query_parts = [f"chapter {chapter_number}"]
    if instruction and instruction.strip():
        query_parts.append(instruction.strip())
    query = " ".join(query_parts)
    included: dict[tuple[str, str], ContextItem] = {}
    excluded: dict[tuple[str, str], ContextExclusion] = {}
    warnings: list[str] = []

    data = _load_context_data(root)
    direct_ids = _plan_entity_ids(plan)
    direct_event_ids = _plan_timeline_event_ids(plan)
    for entity_id in sorted(direct_ids):
        _include_entity_context(
            root=root,
            data=data,
            entity_id=entity_id,
            task=task,
            included=included,
            excluded=excluded,
        )
    for event_id in sorted(direct_event_ids):
        event = data["events_by_id"].get(event_id)
        if event:
            _put_context_item(
                included,
                ContextItem(
                    id=event_id,
                    type="timeline_event",
                    source="memory/state/timeline.json",
                    visibility="reader_visible" if event.get("reader_visible") else "author_only",
                    reason="referenced by ChapterPlan.required_context.timeline_event_ids",
                    priority=92,
                    content=_safe_content(event),
                ),
            )

    if _instruction_requests_hidden_reveal(instruction) and task in {"write", "polish"}:
        warnings.append(
            "instruction appears to request revealing hidden truth; hidden_truth content is still protected for this task"
        )

    for truth in data["hidden_truths"]:
        _maybe_include_hidden_truth(
            truth=truth,
            task=task,
            included=included,
            excluded=excluded,
            reason="hidden truth relevant to canon; protected by task visibility policy",
        )
    for thread in data["foreshadowing_threads"]:
        _maybe_include_foreshadowing(
            thread=thread,
            task=task,
            included=included,
            excluded=excluded,
            direct_ids=direct_ids,
        )

    search_results = _safe_retrieve_search_results(
        root,
        query=query,
        chapter_number=chapter_number,
        limit=limit,
        use_vector=use_vector,
    )
    for result in search_results:
        _include_search_result(result, task=task, included=included, excluded=excluded)

    selected = sorted(included.values(), key=lambda item: (-item.priority, item.type, item.id))[:limit]
    return ContextBundle(
        chapter_number=chapter_number,
        task=task,
        query=query,
        included=selected,
        excluded=sorted(excluded.values(), key=lambda item: (item.type, item.id)),
        warnings=warnings,
        created_at=_utc_now(),
    )


def write_context_report(root: Path, bundle: ContextBundle, *, force: bool = False) -> Path:
    chapter_dir = root.resolve() / "memory" / "chapters" / f"{bundle.chapter_number:03d}"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    target = chapter_dir / "context_report.json"
    if target.exists() and not force:
        target = chapter_dir / f"context_report.{bundle.task}.json"
        counter = 1
        while target.exists():
            target = chapter_dir / f"context_report.{bundle.task}.{counter}.json"
            counter += 1
    elif target.exists() and force:
        backup_if_exists(target, reason="force")
    atomic_write_model_json(target, bundle)
    return target


def _load_context_data(root: Path) -> dict[str, Any]:
    canon_dir = root / "memory" / "canon"
    state_dir = root / "memory" / "state"
    characters = _list_from_json(canon_dir / "characters.json", "characters")
    locations = _list_from_json(canon_dir / "locations.json", "locations")
    items = _list_from_json(canon_dir / "items.json", "items")
    world_rules = _list_from_json(canon_dir / "world.json", "world_rules")
    hidden_truths = _list_from_json(canon_dir / "hidden_truths.json", "hidden_truths")
    foreshadowing_threads = _list_from_json(canon_dir / "foreshadowing.json", "foreshadowing_threads")
    character_states = _list_from_json(state_dir / "current_state.json", "character_states")
    item_states = _list_from_json(state_dir / "current_state.json", "item_states")
    location_states = _list_from_json(state_dir / "current_state.json", "location_states")
    events = _list_from_json(state_dir / "timeline.json", "events")
    return {
        "characters_by_id": _by_id(characters),
        "locations_by_id": _by_id(locations),
        "items_by_id": _by_id(items),
        "world_rules_by_id": _by_id(world_rules),
        "character_states_by_id": _by_key(character_states, "entity_id"),
        "item_states_by_id": _by_key(item_states, "entity_id"),
        "location_states_by_id": _by_key(location_states, "entity_id"),
        "events_by_id": _by_id(events),
        "events": events,
        "hidden_truths": hidden_truths,
        "foreshadowing_threads": foreshadowing_threads,
    }


def _list_from_json(path: Path, key: str) -> list[dict[str, Any]]:
    data = _safe_load_json(path)
    values = data.get(key) if isinstance(data, dict) else None
    return [value for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def _by_id(values: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(value["id"]): value for value in values if isinstance(value.get("id"), str)}


def _by_key(values: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(value[key]): value for value in values if isinstance(value.get(key), str)}


def _plan_entity_ids(plan: ChapterPlan | None) -> set[str]:
    if plan is None:
        return set()
    entity_ids = set(plan.required_context.canon_entity_ids)
    entity_ids.update(plan.required_context.state_entity_ids)
    for scene in plan.scenes:
        entity_ids.add(scene.location_id)
        entity_ids.update(scene.participant_ids)
    return {entity_id for entity_id in entity_ids if entity_id}


def _plan_timeline_event_ids(plan: ChapterPlan | None) -> set[str]:
    if plan is None:
        return set()
    return {event_id for event_id in plan.required_context.timeline_event_ids if event_id}


def _include_entity_context(
    *,
    root: Path,
    data: dict[str, Any],
    entity_id: str,
    task: ContextTask,
    included: dict[tuple[str, str], ContextItem],
    excluded: dict[tuple[str, str], ContextExclusion],
) -> None:
    mappings = (
        ("character", "memory/canon/characters.json", "characters_by_id", "character_states_by_id", "character_state"),
        ("location", "memory/canon/locations.json", "locations_by_id", "location_states_by_id", "location_state"),
        ("item", "memory/canon/items.json", "items_by_id", "item_states_by_id", "item_state"),
        ("world_rule", "memory/canon/world.json", "world_rules_by_id", "", ""),
    )
    for entity_type, source, map_key, state_map_key, state_type in mappings:
        value = data[map_key].get(entity_id)
        if value:
            _put_context_item(
                included,
                ContextItem(
                    id=entity_id,
                    type=entity_type,
                    source=source,
                    visibility=_visibility_for_canon(value),
                    reason="directly referenced by ChapterPlan",
                    priority=100,
                    content=_safe_content(value),
                ),
            )
            if state_map_key:
                state_value = data[state_map_key].get(entity_id)
                if state_value:
                    _put_context_item(
                        included,
                        ContextItem(
                            id=f"state_{entity_id}",
                            type=state_type,
                            source="memory/state/current_state.json",
                            visibility="author_only",
                            reason=f"current state for directly referenced {entity_type}",
                            priority=95,
                            content=_safe_content(state_value),
                        ),
                    )
            _include_related_events(entity_id, data, included)
            _include_related_hidden_material(entity_id, data, task, included, excluded)
            return


def _include_related_events(
    entity_id: str,
    data: dict[str, Any],
    included: dict[tuple[str, str], ContextItem],
) -> None:
    for event in data["events"]:
        event_id = event.get("id")
        if not isinstance(event_id, str):
            continue
        if event.get("location_id") != entity_id and entity_id not in _string_list(event.get("participant_ids")):
            continue
        _put_context_item(
            included,
            ContextItem(
                id=event_id,
                type="timeline_event",
                source="memory/state/timeline.json",
                visibility="reader_visible" if event.get("reader_visible") else "author_only",
                reason=f"timeline event references {entity_id}",
                priority=84,
                content=_safe_content(event),
            ),
        )


def _include_related_hidden_material(
    entity_id: str,
    data: dict[str, Any],
    task: ContextTask,
    included: dict[tuple[str, str], ContextItem],
    excluded: dict[tuple[str, str], ContextExclusion],
) -> None:
    for truth in data["hidden_truths"]:
        if entity_id in _string_list(truth.get("related_entity_ids")):
            _maybe_include_hidden_truth(
                truth=truth,
                task=task,
                included=included,
                excluded=excluded,
                reason=f"hidden truth references {entity_id}",
            )
    for thread in data["foreshadowing_threads"]:
        if entity_id in _string_list(thread.get("related_entity_ids")):
            _maybe_include_foreshadowing(
                thread=thread,
                task=task,
                included=included,
                excluded=excluded,
                direct_ids={entity_id},
            )


def _maybe_include_hidden_truth(
    *,
    truth: dict[str, Any],
    task: ContextTask,
    included: dict[tuple[str, str], ContextItem],
    excluded: dict[tuple[str, str], ContextExclusion],
    reason: str,
) -> None:
    truth_id = truth.get("id")
    if not isinstance(truth_id, str):
        return
    if task in {"write", "polish"}:
        _put_exclusion(
            excluded,
            ContextExclusion(
                id=truth_id,
                type="hidden_truth",
                source="memory/canon/hidden_truths.json",
                visibility="hidden_truth",
                reason="protected from drafting output",
            ),
        )
        return
    _put_context_item(
        included,
        ContextItem(
            id=truth_id,
            type="hidden_truth",
            source="memory/canon/hidden_truths.json",
            visibility="hidden_truth" if task == "plan" else "audit_only",
            reason=reason,
            priority=78 if task == "plan" else 96,
            content=_safe_content(truth),
        ),
    )


def _maybe_include_foreshadowing(
    *,
    thread: dict[str, Any],
    task: ContextTask,
    included: dict[tuple[str, str], ContextItem],
    excluded: dict[tuple[str, str], ContextExclusion],
    direct_ids: set[str],
) -> None:
    thread_id = thread.get("id")
    if not isinstance(thread_id, str):
        return
    has_hidden = bool(thread.get("hidden_truth") or thread.get("hidden_truth_id"))
    is_related = bool(direct_ids.intersection(_string_list(thread.get("related_entity_ids")))) or not direct_ids
    if not is_related:
        return
    if task in {"write", "polish"} and has_hidden:
        safe_thread = dict(thread)
        safe_thread.pop("hidden_truth", None)
        _put_context_item(
            included,
            ContextItem(
                id=thread_id,
                type="foreshadowing",
                source="memory/canon/foreshadowing.json",
                visibility="author_only",
                reason="related foreshadowing with hidden fields redacted",
                priority=72,
                content=_safe_content(safe_thread),
            ),
        )
        _put_exclusion(
            excluded,
            ContextExclusion(
                id=thread_id,
                type="foreshadowing_hidden_detail",
                source="memory/canon/foreshadowing.json",
                visibility="hidden_truth",
                reason="protected from drafting output",
            ),
        )
        return
    _put_context_item(
        included,
        ContextItem(
            id=thread_id,
            type="foreshadowing",
            source="memory/canon/foreshadowing.json",
            visibility="audit_only" if task == "audit" and has_hidden else "author_only",
            reason="related foreshadowing thread",
            priority=74,
            content=_safe_content(thread),
        ),
    )


def _safe_retrieve_search_results(
    root: Path,
    *,
    query: str,
    chapter_number: int,
    limit: int,
    use_vector: bool,
) -> list[SearchResult]:
    try:
        return _diverse_context_results(
            search_project(
                root,
                query,
                search_type="all",
                limit=max(limit * 3, limit),
                highlight=True,
                use_vector=use_vector,
            ),
            limit=limit,
            chapter_number=chapter_number,
        )
    except SearchError:
        rebuild_search_index(root)
        return _diverse_context_results(
            search_project(
                root,
                query,
                search_type="all",
                limit=max(limit * 3, limit),
                highlight=True,
                use_vector=use_vector,
            ),
            limit=limit,
            chapter_number=chapter_number,
        )


def _include_search_result(
    result: SearchResult,
    *,
    task: ContextTask,
    included: dict[tuple[str, str], ContextItem],
    excluded: dict[tuple[str, str], ContextExclusion],
) -> None:
    if task in {"write", "polish"} and result.path.endswith("hidden_truths.json"):
        _put_exclusion(
            excluded,
            ContextExclusion(
                id=result.id,
                type="search_result",
                source=result.path,
                visibility="hidden_truth",
                reason="protected from drafting output",
            ),
        )
        return
    _put_context_item(
        included,
        ContextItem(
            id=result.id,
            type=f"search_{result.type}",
            source=result.path,
            visibility="reader_visible" if result.type in {"character", "location", "item", "chapter", "event"} else "author_only",
            reason=f"search match: {', '.join(result.matched_terms) if result.matched_terms else 'vector similarity'}",
            priority=min(70 + result.score, 89),
            content={
                "title": result.title,
                "excerpt": result.excerpt,
                "matched_terms": list(result.matched_terms),
                "metadata": result.metadata,
            },
        ),
    )


def _put_context_item(items: dict[tuple[str, str], ContextItem], item: ContextItem) -> None:
    key = (item.type, item.id)
    existing = items.get(key)
    if existing is None or item.priority > existing.priority:
        items[key] = item


def _put_exclusion(items: dict[tuple[str, str], ContextExclusion], item: ContextExclusion) -> None:
    items[(item.type, item.id)] = item


def _visibility_for_canon(value: dict[str, Any]) -> ContextVisibility:
    raw_visibility = value.get("visibility")
    if raw_visibility == "hidden":
        return "hidden_truth"
    if value.get("private_author_notes"):
        return "author_only"
    return "reader_visible"


def _safe_content(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _string_list(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _instruction_requests_hidden_reveal(instruction: str | None) -> bool:
    if not instruction:
        return False
    lowered = instruction.lower()
    return any(marker in lowered for marker in ("揭示", "暴露隐藏真相", "隐藏真相", "reveal", "hidden truth"))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def search_index_path(root: Path) -> Path:
    return root.resolve() / "memory" / "search_index.json"


def sqlite_search_index_path(root: Path) -> Path:
    return root.resolve() / "memory" / "search_index.sqlite"


def _collect_documents(root: Path) -> list[SearchDocument]:
    documents: list[SearchDocument] = []
    documents.extend(_canon_documents(root))
    documents.extend(_timeline_documents(root))
    documents.extend(_markdown_documents(root))
    documents.extend(_chapter_json_documents(root))
    return documents


def _canon_documents(root: Path) -> list[SearchDocument]:
    canon_dir = root / "memory" / "canon"
    documents: list[SearchDocument] = []
    mapping = (
        ("characters.json", "characters", "character", "name"),
        ("locations.json", "locations", "location", "name"),
        ("items.json", "items", "item", "name"),
    )
    for filename, collection_key, document_type, title_key in mapping:
        path = canon_dir / filename
        if not path.exists():
            continue
        data = _safe_load_json(path)
        values = data.get(collection_key) if isinstance(data, dict) else None
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            entity_id = str(value.get("id") or f"{document_type}_{len(documents) + 1}")
            documents.append(
                SearchDocument(
                    id=entity_id,
                    type=document_type,
                    path=_rel(root, path),
                    title=str(value.get(title_key) or entity_id),
                    text=_json_text(value),
                    metadata={"entity_id": entity_id, "entity_type": document_type},
                )
            )
    return documents


def _timeline_documents(root: Path) -> list[SearchDocument]:
    path = root / "memory" / "state" / "timeline.json"
    if not path.exists():
        return []
    data = _safe_load_json(path)
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    documents: list[SearchDocument] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or f"event_{len(documents) + 1}")
        documents.append(
            SearchDocument(
                id=event_id,
                type="event",
                path=_rel(root, path),
                title=str(event.get("summary") or event_id),
                text=_json_text(event),
                metadata={
                    "event_id": event_id,
                    "chapter": event.get("chapter"),
                    "location_id": event.get("location_id"),
                    "participant_ids": event.get("participant_ids", []),
                },
            )
        )
    return documents


def _markdown_documents(root: Path) -> list[SearchDocument]:
    documents: list[SearchDocument] = []
    memory = root / "memory"
    if not memory.exists():
        return documents
    for path in sorted(memory.rglob("*.md")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        rel = _rel(root, path)
        document_type = "chapter" if "/chapters/" in rel else "markdown"
        title = _markdown_title(text) or path.stem
        documents.append(
            SearchDocument(
                id=_safe_id(rel),
                type=document_type,
                path=rel,
                title=title,
                text=text,
                metadata=_chapter_metadata_from_path(rel),
            )
        )
    return documents


def _chapter_json_documents(root: Path) -> list[SearchDocument]:
    chapters_dir = root / "memory" / "chapters"
    if not chapters_dir.exists():
        return []
    documents: list[SearchDocument] = []
    for path in sorted(chapters_dir.rglob("*.json")):
        if path.name in {"revision_log.json"}:
            continue
        data = _safe_load_json(path)
        text = _json_text(data)
        documents.append(
            SearchDocument(
                id=_safe_id(_rel(root, path)),
                type="chapter",
                path=_rel(root, path),
                title=path.name,
                text=text,
                metadata=_chapter_metadata_from_path(_rel(root, path)),
            )
        )
    return documents


def _score_document(
    document: SearchDocument,
    raw_query: str,
    terms: list[str],
) -> SearchResult | None:
    weighted_fields = (
        (document.id, 8),
        (document.title, 6),
        (document.type, 3),
        (document.path, 2),
        (document.text, 1),
        (_token_text(document), 1),
    )
    haystack = " ".join(value for value, _ in weighted_fields).lower()
    matched: list[str] = []
    score = 0
    raw = raw_query.strip().lower()
    if raw and raw in haystack:
        matched.append(raw_query.strip())
        score += 12
    for term in terms:
        term_score = 0
        for value, weight in weighted_fields:
            count = value.lower().count(term.lower())
            if count:
                term_score += count * weight
        if term_score:
            matched.append(term)
            score += term_score
    if score <= 0:
        return None
    unique_terms = tuple(dict.fromkeys(matched))
    return SearchResult(
        id=document.id,
        type=document.type,
        path=document.path,
        title=document.title,
        score=score,
        matched_terms=unique_terms,
        excerpt=_excerpt(document.text, unique_terms),
        highlighted_excerpt=_highlight(_excerpt(document.text, unique_terms), unique_terms),
        metadata=document.metadata,
    )


def _query_terms(query: str) -> list[str]:
    terms = [part.lower() for part in re.findall(r"[A-Za-z0-9_]+", query) if len(part) > 1]
    terms.extend(_chinese_terms(query))
    for chunk in re.split(r"\s+", query.strip()):
        cleaned = chunk.strip().lower()
        if cleaned and cleaned not in terms:
            terms.append(cleaned)
    return terms or [query.strip().lower()]


def _chinese_terms(text: str) -> list[str]:
    chunks = re.findall(r"[\u4e00-\u9fff]+", text)
    terms: list[str] = []
    for chunk in chunks:
        if len(chunk) <= 4:
            terms.append(chunk)
        for size in (2, 3):
            for index in range(0, max(len(chunk) - size + 1, 0)):
                terms.append(chunk[index : index + size])
    return list(dict.fromkeys(terms))


def _type_matches(document_type: str, search_type: SearchType) -> bool:
    return search_type == "all" or document_type == search_type


def _chapter_matches(metadata: dict[str, object], chapter_number: int | None) -> bool:
    if chapter_number is None:
        return True
    return metadata.get("chapter_number") == chapter_number or metadata.get("chapter") == chapter_number


def _excerpt(text: str, terms: tuple[str, ...]) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""
    lower = compact.lower()
    positions = [lower.find(term.lower()) for term in terms if term and lower.find(term.lower()) >= 0]
    start = max((min(positions) if positions else 0) - 60, 0)
    end = min(start + 220, len(compact))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return prefix + compact[start:end] + suffix


def _highlight(text: str, terms: tuple[str, ...]) -> str:
    highlighted = text
    for term in sorted((term for term in terms if term), key=len, reverse=True):
        highlighted = re.sub(re.escape(term), lambda match: f"<mark>{match.group(0)}</mark>", highlighted, flags=re.IGNORECASE)
    return highlighted


def _with_highlight(result: SearchResult) -> SearchResult:
    return SearchResult(
        id=result.id,
        type=result.type,
        path=result.path,
        title=result.title,
        score=result.score,
        matched_terms=result.matched_terms,
        excerpt=result.excerpt,
        highlighted_excerpt=_highlight(result.excerpt, result.matched_terms),
        metadata=result.metadata,
    )


def _diverse_context_results(
    results: list[SearchResult],
    *,
    limit: int,
    chapter_number: int,
) -> list[SearchResult]:
    def priority(result: SearchResult) -> tuple[int, int]:
        chapter = result.metadata.get("chapter_number") or result.metadata.get("chapter")
        near_chapter = isinstance(chapter, int) and abs(chapter - chapter_number) <= 1
        type_priority = {"character": 0, "location": 1, "item": 2, "event": 3, "chapter": 4}.get(result.type, 5)
        return (0 if near_chapter else 1, type_priority)

    selected: list[SearchResult] = []
    type_counts: dict[str, int] = {}
    for result in sorted(results, key=lambda item: (priority(item), -item.score)):
        if type_counts.get(result.type, 0) >= 3 and len(selected) < limit - 1:
            continue
        selected.append(result)
        type_counts[result.type] = type_counts.get(result.type, 0) + 1
        if len(selected) >= limit:
            break
    return selected


def _load_index(path: Path) -> list[SearchDocument]:
    data = load_json(path)
    if not isinstance(data, dict):
        raise SearchError(f"{path} is not a valid search index")
    documents = data.get("documents")
    if not isinstance(documents, list):
        raise SearchError(f"{path} is missing documents")
    return [_document_from_dict(item) for item in documents if isinstance(item, dict)]


def _document_to_dict(document: SearchDocument) -> dict[str, object]:
    return {
        "id": document.id,
        "type": document.type,
        "path": document.path,
        "title": document.title,
        "text": document.text,
        "metadata": document.metadata,
    }


def _document_from_dict(data: dict[str, object]) -> SearchDocument:
    metadata = data.get("metadata")
    return SearchDocument(
        id=str(data.get("id") or ""),
        type=str(data.get("type") or ""),
        path=str(data.get("path") or ""),
        title=str(data.get("title") or ""),
        text=str(data.get("text") or ""),
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )


def _safe_load_json(path: Path) -> object:
    try:
        return load_json(path)
    except Exception:
        return {}


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _token_text(document: SearchDocument) -> str:
    return " ".join(_query_terms(" ".join([document.id, document.title, document.text])))


def _write_sqlite_index(path: Path, documents: list[SearchDocument], provider: EmbeddingProvider | None = None) -> None:
    provider = provider or create_embedding_provider(path.parent.parent, provider_name="local_hash")
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE IF EXISTS documents")
        conn.execute("DROP TABLE IF EXISTS vectors")
        conn.execute("DROP TABLE IF EXISTS documents_fts")
        conn.execute(
            "CREATE TABLE documents (id TEXT PRIMARY KEY, type TEXT, path TEXT, title TEXT, text TEXT, metadata TEXT)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE documents_fts USING fts5(id, type, title, body, token_text)"
        )
        conn.execute(
            "CREATE TABLE vectors (id TEXT PRIMARY KEY, provider TEXT, model TEXT, dimensions INTEGER, vector TEXT)"
        )
        vectors = _embed_documents(provider, documents)
        for document in documents:
            conn.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
                (
                    document.id,
                    document.type,
                    document.path,
                    document.title,
                    document.text,
                    json.dumps(document.metadata, ensure_ascii=False, sort_keys=True),
                ),
            )
            conn.execute(
                "INSERT INTO documents_fts (id, type, title, body, token_text) VALUES (?, ?, ?, ?, ?)",
                (document.id, document.type, document.title, document.text, _token_text(document)),
            )
            vector = vectors.get(document.id) or local_embedding_vector(" ".join([document.title, document.text]))
            conn.execute(
                "INSERT INTO vectors VALUES (?, ?, ?, ?, ?)",
                (document.id, provider.provider_name, provider.model, len(vector), json.dumps(vector)),
            )


def _sqlite_candidate_ids(path: Path, terms: list[str]) -> set[str]:
    if not path.exists() or not terms:
        return set()
    query_terms = [term.replace('"', '""') for term in terms[:12] if term]
    if not query_terms:
        return set()
    match_query = " OR ".join(f'"{term}"' for term in query_terms)
    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT id FROM documents_fts WHERE documents_fts MATCH ? LIMIT 200",
                (match_query,),
            ).fetchall()
    except sqlite3.Error:
        return set()
    return {str(row[0]) for row in rows}


def _load_embedding_provider(
    root: Path,
    provider_name: str,
    config_path: Path | None,
) -> EmbeddingProvider:
    try:
        return create_embedding_provider(
            root,
            provider_name=provider_name,
            config_path=config_path,
        )
    except EmbeddingError as exc:
        raise SearchError(str(exc)) from exc


def _embed_documents(provider: EmbeddingProvider, documents: list[SearchDocument]) -> dict[str, list[float]]:
    if not documents:
        return {}
    texts = [" ".join([document.title, document.text]) for document in documents]
    try:
        response = provider.embed_texts(texts)
    except EmbeddingError as exc:
        raise SearchError(str(exc)) from exc
    if len(response.vectors) != len(texts):
        raise SearchError("embedding provider returned the wrong number of vectors")
    return {document.id: vector for document, vector in zip(documents, response.vectors)}


def _vector_scores(path: Path, query: str, provider: EmbeddingProvider) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        query_response = provider.embed_texts([query])
        query_vector = query_response.vectors[0]
    except (EmbeddingError, IndexError) as exc:
        raise SearchError(str(exc)) from exc
    scores: dict[str, float] = {}
    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute("SELECT id, vector FROM vectors").fetchall()
    except sqlite3.Error:
        return scores
    for document_id, raw_vector in rows:
        try:
            vector = json.loads(raw_vector)
        except json.JSONDecodeError:
            continue
        if isinstance(vector, list):
            score = _cosine_similarity(query_vector, [float(value) for value in vector])
            if score > 0:
                scores[str(document_id)] = score
    return scores


def _result_with_vector_score(
    document: SearchDocument,
    result: SearchResult | None,
    vector_score: float,
) -> SearchResult:
    if result is None:
        matched_terms: tuple[str, ...] = ()
        result = SearchResult(
            id=document.id,
            type=document.type,
            path=document.path,
            title=document.title,
            score=0,
            matched_terms=matched_terms,
            excerpt=_excerpt(document.text, matched_terms),
            highlighted_excerpt="",
            metadata=document.metadata,
        )
    if vector_score <= 0:
        return result
    metadata = dict(result.metadata)
    metadata["vector_score"] = round(vector_score, 4)
    return SearchResult(
        id=result.id,
        type=result.type,
        path=result.path,
        title=result.title,
        score=result.score + int(vector_score * 20),
        matched_terms=result.matched_terms,
        excerpt=result.excerpt,
        highlighted_excerpt=result.highlighted_excerpt,
        metadata=metadata,
    )


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _markdown_title(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None


def _chapter_metadata_from_path(path: str) -> dict[str, object]:
    match = re.search(r"memory/chapters/([0-9]+)", path)
    if not match:
        return {}
    return {"chapter_number": int(match.group(1))}


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    return cleaned or "document"


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
