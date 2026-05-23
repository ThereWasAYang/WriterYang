from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Literal

from novel.core.io import load_json


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


def rebuild_search_index(root: Path) -> SearchIndexResult:
    root = root.resolve()
    if not (root / "project.yaml").exists():
        raise SearchError(f"{root} does not look like a novel workspace")
    documents = _collect_documents(root)
    index_path = search_index_path(root)
    payload = {
        "version": 1,
        "documents": [_document_to_dict(document) for document in documents],
    }
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return SearchIndexResult(index_path=index_path, document_count=len(documents))


def search_project(
    root: Path,
    query: str,
    *,
    search_type: SearchType = "all",
    limit: int = 10,
    rebuild_if_missing: bool = True,
) -> list[SearchResult]:
    root = root.resolve()
    if not query.strip():
        raise SearchError("search query must not be empty")
    if limit < 1:
        raise SearchError("--limit must be a positive integer")
    index_path = search_index_path(root)
    if not index_path.exists():
        if not rebuild_if_missing:
            raise SearchError(f"{index_path} is missing; run novel index rebuild first")
        rebuild_search_index(root)
    documents = _load_index(index_path)
    terms = _query_terms(query)
    results = [
        result
        for document in documents
        if _type_matches(document.type, search_type)
        for result in [_score_document(document, query, terms)]
        if result is not None
    ]
    return sorted(results, key=lambda result: (-result.score, result.path, result.id))[:limit]


def retrieve_context(
    root: Path,
    *,
    chapter_number: int,
    instruction: str | None,
    limit: int = 8,
) -> RetrievedContext:
    query_parts = [f"chapter {chapter_number}"]
    if instruction and instruction.strip():
        query_parts.append(instruction.strip())
    query = " ".join(query_parts)
    try:
        results = tuple(search_project(root, query, search_type="all", limit=limit))
    except SearchError:
        rebuild_search_index(root)
        results = tuple(search_project(root, query, search_type="all", limit=limit))
    return RetrievedContext(query=query, chapter_number=chapter_number, results=results)


def search_index_path(root: Path) -> Path:
    return root.resolve() / "memory" / "search_index.json"


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
                    metadata={"entity_id": entity_id},
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
    haystack = " ".join(
        [document.id, document.type, document.path, document.title, document.text]
    ).lower()
    matched: list[str] = []
    score = 0
    raw = raw_query.strip().lower()
    if raw and raw in haystack:
        matched.append(raw_query.strip())
        score += 8
    for term in terms:
        count = haystack.count(term.lower())
        if count:
            matched.append(term)
            score += count
            if term.lower() in document.title.lower() or term.lower() == document.id.lower():
                score += 4
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
        metadata=document.metadata,
    )


def _query_terms(query: str) -> list[str]:
    terms = [part.lower() for part in re.findall(r"[A-Za-z0-9_]+", query) if len(part) > 1]
    for chunk in re.split(r"\s+", query.strip()):
        cleaned = chunk.strip().lower()
        if cleaned and cleaned not in terms:
            terms.append(cleaned)
    return terms or [query.strip().lower()]


def _type_matches(document_type: str, search_type: SearchType) -> bool:
    return search_type == "all" or document_type == search_type


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
