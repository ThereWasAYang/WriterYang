from __future__ import annotations

import argparse
from pathlib import Path

from novel.core.search import SearchError, rebuild_search_index, refresh_search_index, search_index_status
from novel.core.command_bus import DomainError
from novel.core.contracts import SearchCommand
from novel.core.locking import ProjectLockError
from novel.cli_shared import (
    _success,
    _failure,
    _command_lock,
    _dispatch_cli_command,
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
        payload = _dispatch_cli_command(
            args,
            Path(args.path).expanduser().resolve(),
            SearchCommand(
                query=args.query,
                search_type=args.type,
                limit=args.limit,
                chapter_number=args.chapter,
                highlight=args.highlight,
                use_vector=args.use_vector,
                embedding_provider_name=args.embedding_provider,
                embedding_config_path=str(args.embedding_config) if args.embedding_config else None,
            ),
        )
    except DomainError as exc:
        return _failure(args, exc.message, error_type=exc.code)
    results = payload.get("results")
    if not isinstance(results, list):
        return _failure(args, "command result is missing results", error_type="internal_error")
    if not results:
        return _success(args, {**payload, "command": "search"}, ["No results."])
    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        if not isinstance(result, dict):
            continue
        terms_value = result.get("matched_terms")
        terms = ", ".join(str(item) for item in terms_value) if isinstance(terms_value, list) else "none"
        lines.extend(
            [
                f"{index}. [{result.get('type')}] {result.get('title')}",
                f"   path: {result.get('path')}",
                f"   score: {result.get('score')}; matched_terms: {terms}",
                f"   excerpt: {result.get('highlighted_excerpt') if args.highlight else result.get('excerpt')}",
            ]
        )
    return _success(args, {**payload, "command": "search"}, lines)
