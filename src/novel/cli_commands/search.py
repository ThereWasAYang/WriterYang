from __future__ import annotations

import argparse
import json
from pathlib import Path

from novel.core.search import SearchError, rebuild_search_index, refresh_search_index, search_index_status, search_project
from novel.core.locking import ProjectLockError
from novel.cli_shared import (
    _success,
    _failure,
    _command_lock,
)

def _cmd_index(args: argparse.Namespace) -> int:
    if args.index_command == "rebuild":
        try:
            with _command_lock(args, Path(args.path), "index rebuild"):
                result = rebuild_search_index(
                    Path(args.path),
                    embedding_provider_name=args.embedding_provider,
                    embedding_config_path=args.embedding_config,
                    with_embeddings=args.with_embeddings,
                )
        except ProjectLockError as exc:
            return _failure(args, str(exc), error_type="project_locked")
        except SearchError as exc:
            return _failure(args, str(exc), error_type="search_error")
        return _success(
            args,
            {
                "command": "index rebuild",
                "index_path": str(result.index_path),
                "sqlite_path": str(result.sqlite_path),
                "manifest_path": str(result.manifest_path),
                "document_count": result.document_count,
                "embedding_document_count": result.embedding_document_count,
                "with_embeddings": result.with_embeddings,
            },
            [
                f"Rebuilt search index: {result.index_path}",
                f"Documents: {result.document_count}",
                f"Embedding vectors: {result.embedding_document_count}",
            ],
        )
    if args.index_command == "refresh":
        try:
            with _command_lock(args, Path(args.path), "index refresh"):
                result = refresh_search_index(
                    Path(args.path),
                    embedding_provider_name=args.embedding_provider,
                    embedding_config_path=args.embedding_config,
                    with_embeddings=args.with_embeddings,
                )
        except ProjectLockError as exc:
            return _failure(args, str(exc), error_type="project_locked")
        except SearchError as exc:
            return _failure(args, str(exc), error_type="search_error")
        return _success(
            args,
            {
                "command": "index refresh",
                "index_path": str(result.index_path),
                "sqlite_path": str(result.sqlite_path),
                "manifest_path": str(result.manifest_path),
                "document_count": result.document_count,
                "refreshed_count": result.refreshed_count,
                "deleted_count": result.deleted_count,
                "embedding_document_count": result.embedding_document_count,
                "with_embeddings": result.with_embeddings,
            },
            [
                f"Refreshed search index: {result.index_path}",
                f"Documents: {result.document_count}",
                f"Changed: {result.refreshed_count}; deleted: {result.deleted_count}",
                f"Embedding vectors: {result.embedding_document_count}",
            ],
        )
    if args.index_command == "status":
        status = search_index_status(
            Path(args.path),
            embedding_provider_name=args.embedding_provider,
            embedding_config_path=args.embedding_config,
        )
        return _success(
            args,
            {"command": "index status", **status.as_dict()},
            [
                f"FTS: {status.fts_status}",
                f"Embedding: {status.embedding_status}",
                status.message,
            ],
        )
    return _failure(args, f"unknown index command: {args.index_command}", code=2)

def _cmd_search(args: argparse.Namespace) -> int:
    try:
        results = search_project(
            Path(args.path),
            args.query,
            search_type=args.type,
            limit=args.limit,
            chapter_number=args.chapter,
            highlight=args.highlight,
            use_vector=args.use_vector,
            embedding_provider_name=args.embedding_provider,
            embedding_config_path=args.embedding_config,
        )
    except SearchError as exc:
        return _failure(args, str(exc), error_type="search_error")
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": result.id,
                        "type": result.type,
                        "path": result.path,
                        "title": result.title,
                        "score": result.score,
                        "matched_terms": list(result.matched_terms),
                        "excerpt": result.excerpt,
                        "highlighted_excerpt": result.highlighted_excerpt,
                        "metadata": result.metadata,
                    }
                    for result in results
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not results:
        print("No results.")
        return 0
    for index, result in enumerate(results, start=1):
        terms = ", ".join(result.matched_terms) if result.matched_terms else "none"
        print(f"{index}. [{result.type}] {result.title}")
        print(f"   path: {result.path}")
        print(f"   score: {result.score}; matched_terms: {terms}")
        print(f"   excerpt: {result.highlighted_excerpt if args.highlight else result.excerpt}")
    return 0
