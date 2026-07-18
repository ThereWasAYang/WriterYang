from __future__ import annotations

from novel.cli_parsers.common import ParserCollection
from novel.cli_shared import (
    _add_agent_runtime_args,
    _add_search_context_args,
)


def register_memory_parsers(subparsers: ParserCollection) -> None:
    ask_parser = subparsers.add_parser("ask", help="Ask the controlled orchestrator to run a task")
    ask_parser.add_argument(
        "request",
        nargs="?",
        help="Natural language task request. Omit when using --confirm.",
    )
    ask_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    ask_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for selected agents.",
    )
    ask_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build a command proposal without executing it or writing domain artifacts.",
    )
    ask_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow selected services to overwrite their normal target files.",
    )
    ask_parser.add_argument(
        "--confirm",
        metavar="WORKFLOW_RUN_ID",
        help="Execute the exact persisted proposal from the selected workflow run.",
    )
    ask_parser.add_argument(
        "--max-agent-calls",
        type=int,
        default=8,
        help="Maximum agent calls. Defaults to 8.",
    )
    ask_parser.add_argument("--max-chapters", type=int, default=20)
    ask_parser.add_argument("--max-provider-attempts", type=int, default=24)
    ask_parser.add_argument("--max-auto-revision-rounds", type=int, default=3)
    ask_parser.add_argument("--max-input-tokens", type=int, default=None)
    ask_parser.add_argument("--max-output-tokens", type=int, default=None)
    _add_search_context_args(ask_parser, default_enabled=True)

    memory_repair_parser = subparsers.add_parser(
        "memory-repair", help="Suggest or apply project memory repair proposals"
    )
    memory_repair_subparsers = memory_repair_parser.add_subparsers(dest="memory_repair_command", required=True)
    memory_repair_suggest = memory_repair_subparsers.add_parser("suggest", help="Create a memory repair proposal")
    memory_repair_suggest.add_argument("request", help="Natural language description of the memory problem")
    memory_repair_suggest.add_argument(
        "--path", default=".", help="Workspace directory. Defaults to the current directory."
    )
    memory_repair_suggest.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for structured repair proposal generation.",
    )
    memory_repair_suggest.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    memory_repair_suggest.add_argument("--quiet", action="store_true", help="Suppress normal output.")
    memory_repair_apply = memory_repair_subparsers.add_parser("apply", help="Apply a memory repair proposal explicitly")
    memory_repair_apply.add_argument("proposal", help="repair_id or path to memory/repairs/{repair_id}/proposal.json")
    memory_repair_apply.add_argument(
        "--path", default=".", help="Workspace directory. Defaults to the current directory."
    )
    memory_repair_apply.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    memory_repair_apply.add_argument("--quiet", action="store_true", help="Suppress normal output.")

    setting_change_parser = subparsers.add_parser(
        "setting-change",
        help="Suggest or apply natural-language character/background setting changes",
    )
    setting_change_subparsers = setting_change_parser.add_subparsers(dest="setting_change_command", required=True)
    setting_change_suggest = setting_change_subparsers.add_parser("suggest", help="Create a setting change proposal")
    setting_change_suggest.add_argument("request", help="Natural language setting change request")
    setting_change_suggest.add_argument(
        "--path", default=".", help="Workspace directory. Defaults to the current directory."
    )
    setting_change_suggest.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for structured setting change proposal generation.",
    )
    setting_change_suggest.add_argument(
        "--stage",
        default="unknown",
        choices=("pre_creation", "outline_discussion", "content_review", "post_chapter", "unknown"),
        help="Current creative stage for impact/follow-up analysis.",
    )
    setting_change_suggest.add_argument("--session-id", help="Active session id, if any.")
    setting_change_suggest.add_argument("--chapter", type=int, help="Current chapter number, if any.")
    setting_change_suggest.add_argument(
        "--audit-issue-id",
        action="append",
        default=[],
        help="Audit issue id that triggered this setting change. Can be repeated.",
    )
    setting_change_suggest.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    setting_change_suggest.add_argument("--quiet", action="store_true", help="Suppress normal output.")
    setting_change_answer = setting_change_subparsers.add_parser(
        "answer",
        help="Answer a setting change clarification question and continue proposal generation",
    )
    setting_change_answer.add_argument("clarification_id", help="clarify_... id returned by setting-change suggest")
    setting_change_answer.add_argument("--answer", required=True, help="User clarification answer")
    setting_change_answer.add_argument(
        "--path", default=".", help="Workspace directory. Defaults to the current directory."
    )
    setting_change_answer.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for structured setting change proposal generation.",
    )
    setting_change_answer.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    setting_change_answer.add_argument("--quiet", action="store_true", help="Suppress normal output.")
    setting_change_apply = setting_change_subparsers.add_parser(
        "apply", help="Apply a setting change proposal explicitly"
    )
    setting_change_apply.add_argument("proposal", help="repair_id or path to memory/repairs/{repair_id}/proposal.json")
    setting_change_apply.add_argument(
        "--path", default=".", help="Workspace directory. Defaults to the current directory."
    )
    setting_change_apply.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    setting_change_apply.add_argument("--quiet", action="store_true", help="Suppress normal output.")

    chapter_memory_parser = subparsers.add_parser("chapter-memory", help="Manage accepted chapter memory")
    chapter_memory_subparsers = chapter_memory_parser.add_subparsers(dest="chapter_memory_command", required=True)
    chapter_memory_show = chapter_memory_subparsers.add_parser("show", help="Show a chapter_memory.json file")
    chapter_memory_show.add_argument("chapter_number", type=int, help="Chapter number")
    chapter_memory_show.add_argument(
        "--path", default=".", help="Workspace directory. Defaults to the current directory."
    )
    chapter_memory_generate = chapter_memory_subparsers.add_parser("generate", help="Generate chapter_memory.json")
    chapter_memory_generate.add_argument("chapter_number", type=int, help="Chapter number")
    chapter_memory_generate.add_argument(
        "--path", default=".", help="Workspace directory. Defaults to the current directory."
    )
    chapter_memory_generate.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for structured ChapterMemory generation.",
    )
    chapter_memory_generate.add_argument("--force", action="store_true", help="Overwrite existing chapter_memory.json.")
    _add_agent_runtime_args(chapter_memory_generate)
    chapter_memory_rebuild = chapter_memory_subparsers.add_parser("rebuild", help="Rebuild chapter memories")
    chapter_memory_rebuild.add_argument(
        "--path", default=".", help="Workspace directory. Defaults to the current directory."
    )
    chapter_memory_rebuild.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for structured ChapterMemory generation.",
    )
    chapter_memory_rebuild.add_argument(
        "--force", action="store_true", help="Overwrite existing chapter_memory.json files."
    )
    chapter_memory_rebuild.add_argument(
        "--missing-only",
        action="store_true",
        help="Only generate ChapterMemory for accepted chapters missing chapter_memory.json.",
    )
    _add_agent_runtime_args(chapter_memory_rebuild)
