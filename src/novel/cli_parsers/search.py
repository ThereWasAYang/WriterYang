from __future__ import annotations

from pathlib import Path

from novel.cli_parsers.common import ParserCollection


def register_search_parsers(subparsers: ParserCollection) -> None:
    index_parser = subparsers.add_parser("index", help="Manage the local search index")
    index_subparsers = index_parser.add_subparsers(dest="index_command", required=True)
    index_rebuild = index_subparsers.add_parser("rebuild", help="Rebuild the local search index")
    index_rebuild.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    index_rebuild.add_argument(
        "--embedding-config",
        type=Path,
        default=None,
        help="Embedding config file. Defaults to config/embeddings.yaml in the workspace.",
    )
    index_rebuild.add_argument(
        "--embedding-provider",
        default="config",
        choices=("config", "local_hash", "dashscope", "zhipu", "openai", "openai_compatible"),
        help="Embedding provider to use for vector indexing. Defaults to config active_provider.",
    )
    index_rebuild.add_argument(
        "--with-embeddings",
        action="store_true",
        help="Also build real embedding vectors. This may call an external embedding API.",
    )
    index_refresh = index_subparsers.add_parser("refresh", help="Refresh stale local search index documents")
    index_refresh.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    index_refresh.add_argument(
        "--embedding-config",
        type=Path,
        default=None,
        help="Embedding config file. Defaults to config/embeddings.yaml in the workspace.",
    )
    index_refresh.add_argument(
        "--embedding-provider",
        default="config",
        choices=("config", "local_hash", "dashscope", "zhipu", "openai", "openai_compatible"),
        help="Embedding provider to use when --with-embeddings is set.",
    )
    index_refresh.add_argument(
        "--with-embeddings",
        action="store_true",
        help="Refresh real embedding vectors for changed documents. This may call an external embedding API.",
    )
    index_status = index_subparsers.add_parser("status", help="Show local search index status")
    index_status.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    index_status.add_argument(
        "--embedding-config",
        type=Path,
        default=None,
        help="Embedding config file. Defaults to config/embeddings.yaml in the workspace.",
    )
    index_status.add_argument(
        "--embedding-provider",
        default="config",
        choices=("config", "local_hash", "dashscope", "zhipu", "openai", "openai_compatible"),
        help="Embedding provider to inspect. Defaults to config active_provider.",
    )

    search_parser = subparsers.add_parser("search", help="Search project memory")
    search_parser.add_argument("query", help="Keyword query")
    search_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    search_parser.add_argument(
        "--type",
        default="all",
        choices=("character", "location", "item", "event", "chapter", "chapter_memory", "all"),
        help="Result type to search. Defaults to all.",
    )
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of results. Defaults to 10.",
    )
    search_parser.add_argument(
        "--chapter",
        type=int,
        default=None,
        help="Only return results associated with this chapter number.",
    )
    search_parser.add_argument(
        "--highlight",
        action="store_true",
        help="Include highlighted excerpts with <mark>...</mark> tags.",
    )
    search_parser.add_argument(
        "--use-vector",
        action="store_true",
        help="Use stored embedding vectors to boost lexical search results.",
    )
    search_parser.add_argument(
        "--embedding-config",
        type=Path,
        default=None,
        help="Embedding config file. Defaults to config/embeddings.yaml in the workspace.",
    )
    search_parser.add_argument(
        "--embedding-provider",
        default="config",
        choices=("config", "local_hash", "dashscope", "zhipu", "openai", "openai_compatible"),
        help="Embedding provider for query embedding when --use-vector is enabled.",
    )
    search_parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON.",
    )
