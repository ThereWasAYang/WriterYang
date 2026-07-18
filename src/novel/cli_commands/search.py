from __future__ import annotations

import argparse
from pathlib import Path

from novel.cli_shared import (
    _dispatch_cli_command,
    _failure,
    _success,
)
from novel.core.command_bus import DomainError
from novel.core.contracts import IndexUpdateCommand, SearchCommand
from novel.core.search import search_index_status


def _cmd_index(args: argparse.Namespace) -> int:
    if args.index_command in {"rebuild", "refresh"}:
        try:
            payload = _dispatch_cli_command(
                args,
                Path(args.path),
                IndexUpdateCommand(
                    type=f"index.{args.index_command}",  # type: ignore[arg-type]
                    embedding_provider_name=args.embedding_provider,
                    embedding_config_path=str(args.embedding_config) if args.embedding_config else None,
                    with_embeddings=args.with_embeddings,
                ),
            )
        except DomainError as exc:
            return _failure(args, exc.message, error_type=exc.code)
        action = "Rebuilt" if args.index_command == "rebuild" else "Refreshed"
        details = []
        if args.index_command == "refresh":
            details.append(
                f"Changed: {payload.get('refreshed_count')}; deleted: {payload.get('deleted_count')}"
            )
        return _success(
            args,
            {
                **payload,
                "command": f"index {args.index_command}",
            },
            [
                f"{action} search index: {payload.get('index_path')}",
                f"Documents: {payload.get('document_count')}",
                *details,
                f"Embedding vectors: {payload.get('embedding_document_count')}",
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
