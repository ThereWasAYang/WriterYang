from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from novel import __version__
from novel.cli_commands.generation import (
    _cmd_accept_chapter,
    _cmd_apply_state_update,
    _cmd_audit_chapter,
    _cmd_canon,
    _cmd_export,
    _cmd_generate_chapter,
    _cmd_inspire,
    _cmd_plan_chapter,
    _cmd_polish_chapter,
    _cmd_propose_state_update,
    _cmd_revise_chapter,
    _cmd_write_chapter,
)
from novel.cli_commands.memory import (
    _cmd_chapter_memory,
    _cmd_memory_repair,
    _cmd_setting_change,
)
from novel.cli_commands.orchestrator import _cmd_ask
from novel.cli_commands.project_system import (
    _cmd_completion,
    _cmd_doctor,
    _cmd_init,
    _cmd_schema,
    _cmd_show,
    _cmd_status,
    _cmd_usage,
    _cmd_validate,
    _cmd_web,
    _cmd_web_launch,
)
from novel.cli_commands.search import _cmd_index, _cmd_search
from novel.cli_commands.session import _cmd_session
from novel.cli_shared import (
    _add_agent_runtime_args,
    _add_integration_args_recursive,
    _add_polish_mode_arg,
    _add_search_context_args,
    _apply_project_alias,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel", description="Novel workspace CLI")
    parser.add_argument(
        "--version",
        action="version",
        version=f"novel {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a new novel project workspace")
    init_parser.add_argument("title", help="Novel title")
    init_parser.add_argument(
        "--path",
        default="novel-project",
        help="Workspace directory to create. Defaults to ./novel-project",
    )
    init_parser.add_argument(
        "--project-id",
        default=None,
        help="Stable project id. Defaults to a generated id based on the title.",
    )
    init_parser.add_argument("--language", default="zh-CN", help="Project language")
    init_parser.add_argument(
        "--genre",
        action="append",
        default=[],
        help="Genre label. Can be provided multiple times.",
    )
    guide_group = init_parser.add_mutually_exclusive_group()
    guide_group.add_argument(
        "--guide",
        action="store_true",
        help="Run the interactive initial setup guide after creating the workspace.",
    )
    guide_group.add_argument(
        "--no-guide",
        action="store_true",
        help="Skip the interactive initial setup guide.",
    )

    validate_parser = subparsers.add_parser("validate", help="Validate a novel project workspace")
    validate_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to validate. Defaults to the current directory.",
    )

    schema_parser = subparsers.add_parser("schema", help="Export JSON Schema files")
    schema_subparsers = schema_parser.add_subparsers(dest="schema_command", required=True)
    schema_export_parser = schema_subparsers.add_parser("export", help="Export JSON Schema files")
    schema_export_parser.add_argument(
        "--output",
        type=Path,
        default=Path("schemas"),
        help="Output directory. Defaults to ./schemas.",
    )

    completion_parser = subparsers.add_parser("completion", help="Print shell completion script")
    completion_parser.add_argument(
        "shell",
        choices=("bash", "zsh", "fish"),
        help="Shell to generate completion for.",
    )

    doctor_parser = subparsers.add_parser("doctor", help="Check local environment and project health")
    doctor_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to check. Defaults to the current directory.",
    )

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

    ask_parser = subparsers.add_parser("ask", help="Ask the controlled orchestrator to run a task")
    ask_parser.add_argument("request", help="Natural language task request")
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
        help="Show the execution plan without calling agents or writing files.",
    )
    ask_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow selected services to overwrite their normal target files.",
    )
    ask_parser.add_argument(
        "--max-steps",
        type=int,
        default=8,
        help="Maximum handoff steps. Defaults to 8.",
    )
    ask_parser.add_argument(
        "--max-retries",
        type=int,
        default=0,
        help="Maximum retries per task. Defaults to 0.",
    )
    ask_parser.add_argument(
        "--max-agent-calls",
        type=int,
        default=8,
        help="Maximum agent calls. Defaults to 8.",
    )
    ask_parser.add_argument(
        "--show-handoff-rules",
        action="store_true",
        help="Print allowed handoff rules before the plan.",
    )
    _add_search_context_args(ask_parser, default_enabled=True)

    memory_repair_parser = subparsers.add_parser("memory-repair", help="Suggest or apply project memory repair proposals")
    memory_repair_subparsers = memory_repair_parser.add_subparsers(dest="memory_repair_command", required=True)
    memory_repair_suggest = memory_repair_subparsers.add_parser("suggest", help="Create a memory repair proposal")
    memory_repair_suggest.add_argument("request", help="Natural language description of the memory problem")
    memory_repair_suggest.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
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
    memory_repair_apply.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    memory_repair_apply.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    memory_repair_apply.add_argument("--quiet", action="store_true", help="Suppress normal output.")

    setting_change_parser = subparsers.add_parser(
        "setting-change",
        help="Suggest or apply natural-language character/background setting changes",
    )
    setting_change_subparsers = setting_change_parser.add_subparsers(dest="setting_change_command", required=True)
    setting_change_suggest = setting_change_subparsers.add_parser("suggest", help="Create a setting change proposal")
    setting_change_suggest.add_argument("request", help="Natural language setting change request")
    setting_change_suggest.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
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
    setting_change_answer.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    setting_change_answer.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for structured setting change proposal generation.",
    )
    setting_change_answer.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    setting_change_answer.add_argument("--quiet", action="store_true", help="Suppress normal output.")
    setting_change_apply = setting_change_subparsers.add_parser("apply", help="Apply a setting change proposal explicitly")
    setting_change_apply.add_argument("proposal", help="repair_id or path to memory/repairs/{repair_id}/proposal.json")
    setting_change_apply.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    setting_change_apply.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    setting_change_apply.add_argument("--quiet", action="store_true", help="Suppress normal output.")

    chapter_memory_parser = subparsers.add_parser("chapter-memory", help="Manage accepted chapter memory")
    chapter_memory_subparsers = chapter_memory_parser.add_subparsers(dest="chapter_memory_command", required=True)
    chapter_memory_show = chapter_memory_subparsers.add_parser("show", help="Show a chapter_memory.json file")
    chapter_memory_show.add_argument("chapter_number", type=int, help="Chapter number")
    chapter_memory_show.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    chapter_memory_generate = chapter_memory_subparsers.add_parser("generate", help="Generate chapter_memory.json")
    chapter_memory_generate.add_argument("chapter_number", type=int, help="Chapter number")
    chapter_memory_generate.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    chapter_memory_generate.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for structured ChapterMemory generation.",
    )
    chapter_memory_generate.add_argument("--force", action="store_true", help="Overwrite existing chapter_memory.json.")
    _add_agent_runtime_args(chapter_memory_generate)
    chapter_memory_rebuild = chapter_memory_subparsers.add_parser("rebuild", help="Rebuild chapter memories")
    chapter_memory_rebuild.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    chapter_memory_rebuild.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for structured ChapterMemory generation.",
    )
    chapter_memory_rebuild.add_argument("--force", action="store_true", help="Overwrite existing chapter_memory.json files.")
    chapter_memory_rebuild.add_argument(
        "--missing-only",
        action="store_true",
        help="Only generate ChapterMemory for accepted chapters missing chapter_memory.json.",
    )
    _add_agent_runtime_args(chapter_memory_rebuild)

    session_parser = subparsers.add_parser("session", help="Manage collaborative creation sessions")
    session_subparsers = session_parser.add_subparsers(dest="session_command", required=True)
    session_start = session_subparsers.add_parser("start", help="Start a collaborative creation session")
    session_start.add_argument("intent", help="User intent for this creation session")
    session_start.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_start.add_argument("--chapters", default=None, help="Chapter range, for example 3 or 3-4.")
    session_start.add_argument("--chapter", type=int, default=None, help="Single chapter for segment sessions.")
    session_start.add_argument("--segments", default=None, help="Segment range for a chapter, for example 8-10.")
    session_start.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for outline generation.",
    )
    session_start.add_argument("--force", action="store_true", help="Overwrite outline artifacts if needed.")
    _add_search_context_args(session_start, default_enabled=True)

    session_show = session_subparsers.add_parser("show", help="Show a creation session")
    session_show.add_argument("session_id", help="Session id")
    session_show.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")

    session_revise_outline = session_subparsers.add_parser("revise-outline", help="Revise a session outline proposal")
    session_revise_outline.add_argument("session_id", help="Session id")
    session_revise_outline.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_revise_outline.add_argument("--instruction", required=True, help="Outline revision instruction.")
    session_revise_outline.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for outline revision.",
    )
    session_revise_outline.add_argument("--force", action="store_true", help="Overwrite outline artifacts if needed.")
    _add_search_context_args(session_revise_outline, default_enabled=True)

    session_approve = session_subparsers.add_parser("approve-outline", help="Approve a session outline")
    session_approve.add_argument("session_id", help="Session id")
    session_approve.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_approve.add_argument("--force", action="store_true", help="Overwrite approved outline artifacts.")

    session_run = session_subparsers.add_parser("run", help="Run a session after outline approval")
    session_run.add_argument("session_id", help="Session id")
    session_run.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_run.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for session generation.",
    )
    session_run.add_argument("--force", action="store_true", help="Overwrite generated artifacts.")
    session_run.add_argument(
        "--max-auto-revision-rounds",
        type=int,
        default=None,
        help="Maximum automatic repair rounds. Defaults to session setting.",
    )
    _add_polish_mode_arg(session_run)
    _add_search_context_args(session_run, default_enabled=True)

    session_revise_content = session_subparsers.add_parser("revise-content", help="Revise generated session content")
    session_revise_content.add_argument("session_id", help="Session id")
    session_revise_content.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_revise_content.add_argument("--instruction", default=None, help="User feedback for content revision.")
    session_revise_content.add_argument(
        "--from-audit",
        action="store_true",
        help="Use current audit.json issues as the revision target. Useful when choosing to fix low issues.",
    )
    session_revise_content.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for content revision.",
    )
    session_revise_content.add_argument("--force", action="store_true", help="Overwrite selected revision artifacts.")
    _add_search_context_args(session_revise_content, default_enabled=True)

    session_revise_audit = session_subparsers.add_parser("revise-audit", help="Correct Audit understanding and rerun audit for a rewrite event")
    session_revise_audit.add_argument("session_id", help="Session id")
    session_revise_audit.add_argument("event_id", help="Rewrite event id")
    session_revise_audit.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_revise_audit.add_argument("--instruction", required=True, help="Correction instruction for Audit Agent.")
    session_revise_audit.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for audit revision.",
    )
    session_revise_audit.add_argument("--force", action="store_true", help="Overwrite audit artifacts if needed.")
    _add_search_context_args(session_revise_audit, default_enabled=True)

    session_retry_rewrite = session_subparsers.add_parser("retry-rewrite", help="Retry a rewrite event from the latest audit")
    session_retry_rewrite.add_argument("session_id", help="Session id")
    session_retry_rewrite.add_argument("event_id", help="Rewrite event id")
    session_retry_rewrite.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_retry_rewrite.add_argument("--instruction", default=None, help="Optional extra rewrite instruction.")
    session_retry_rewrite.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for rewrite retry.",
    )
    session_retry_rewrite.add_argument("--force", action="store_true", help="Overwrite generated artifacts if needed.")
    _add_polish_mode_arg(session_retry_rewrite)
    _add_search_context_args(session_retry_rewrite, default_enabled=True)

    session_undo_rewrite = session_subparsers.add_parser("undo-rewrite", help="Restore rejected text snapshot for a rewrite event")
    session_undo_rewrite.add_argument("session_id", help="Session id")
    session_undo_rewrite.add_argument("event_id", help="Rewrite event id")
    session_undo_rewrite.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_undo_rewrite.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for post-restore audit.",
    )
    _add_search_context_args(session_undo_rewrite, default_enabled=True)

    session_accept = session_subparsers.add_parser("accept", help="Accept generated session content")
    session_accept.add_argument("session_id", help="Session id")
    session_accept.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_accept.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for missing state proposals.",
    )
    session_accept.add_argument("--force", action="store_true", help="Overwrite missing state proposals if needed.")

    session_archive = session_subparsers.add_parser("archive", help="Archive accepted session content")
    session_archive.add_argument("session_id", help="Session id")
    session_archive.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_archive.add_argument("--force", action="store_true", help="Overwrite archive files.")

    status_parser = subparsers.add_parser("status", help="Show project status")
    status_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to inspect. Defaults to the current directory.",
    )

    usage_parser = subparsers.add_parser("usage", help="Show provider token usage statistics")
    usage_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to inspect. Defaults to the current directory.",
    )

    show_parser = subparsers.add_parser("show", help="Show project data")
    show_parser.add_argument(
        "target",
        choices=("characters", "timeline", "state", "canon"),
        help="Project data to display.",
    )
    show_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to inspect. Defaults to the current directory.",
    )

    inspire_parser = subparsers.add_parser("inspire", help="Generate an inspiration weak outline")
    inspire_parser.add_argument(
        "text",
        nargs="?",
        help="Raw inspiration text. Use --input to read from a file instead.",
    )
    inspire_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read raw inspiration text from a file.",
    )
    inspire_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    inspire_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the inspiration agent config.",
    )
    _add_agent_runtime_args(inspire_parser)
    inspire_parser.add_argument(
        "--json",
        action="store_true",
        help="Also write memory/inspiration.json.",
    )
    inspire_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing inspiration files.",
    )
    _add_search_context_args(inspire_parser)

    canon_parser = subparsers.add_parser("canon", help="Manage canon data")
    canon_subparsers = canon_parser.add_subparsers(dest="canon_command", required=True)

    canon_suggest = canon_subparsers.add_parser("suggest", help="Generate a canon proposal")
    canon_suggest.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    canon_suggest.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to config/agents.yaml.",
    )
    _add_agent_runtime_args(canon_suggest)
    canon_suggest.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Save proposal JSON to this file. Refuses to overwrite.",
    )
    _add_search_context_args(canon_suggest)

    canon_apply = canon_subparsers.add_parser("apply", help="Apply a canon proposal")
    canon_apply.add_argument("proposal_file", type=Path, help="Canon proposal JSON file")
    canon_apply.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )

    canon_validate = canon_subparsers.add_parser("validate", help="Validate canon files only")
    canon_validate.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    canon_show = canon_subparsers.add_parser("show", help="Show canon summary")
    canon_show.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )

    plan_parser = subparsers.add_parser("plan-chapter", help="Generate a chapter plan")
    plan_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    plan_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    plan_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra planning instruction for this chapter.",
    )
    plan_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra planning instruction from a file.",
    )
    plan_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the plot agent config.",
    )
    _add_agent_runtime_args(plan_parser)
    plan_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing plan files.",
    )
    plan_parser.add_argument(
        "--use-search-context",
        action="store_true",
        help="Add explainable search results to the planning prompt.",
    )
    plan_parser.add_argument(
        "--vector-context",
        choices=("auto", "on", "off"),
        default="auto",
        help="Embedding semantic context mode for agent memory retrieval. Defaults to auto.",
    )
    plan_parser.add_argument(
        "--use-vector-context",
        action="store_true",
        help="Compatibility alias for --vector-context on.",
    )

    write_parser = subparsers.add_parser("write-chapter", help="Generate a chapter draft")
    write_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    write_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    write_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra writing instruction for this chapter.",
    )
    write_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra writing instruction from a file.",
    )
    write_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the writer agent config.",
    )
    _add_agent_runtime_args(write_parser)
    write_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing draft.md.",
    )
    write_parser.add_argument(
        "--target-words",
        type=int,
        default=None,
        help="Optional target word count.",
    )
    write_parser.add_argument(
        "--style-note",
        default=None,
        help="Temporary style guidance for this draft.",
    )
    write_parser.add_argument(
        "--use-search-context",
        action="store_true",
        help="Add explainable search results to the writing prompt.",
    )
    write_parser.add_argument(
        "--vector-context",
        choices=("auto", "on", "off"),
        default="auto",
        help="Embedding semantic context mode for agent memory retrieval. Defaults to auto.",
    )
    write_parser.add_argument(
        "--use-vector-context",
        action="store_true",
        help="Compatibility alias for --vector-context on.",
    )

    polish_parser = subparsers.add_parser("polish-chapter", help="Polish a chapter draft")
    polish_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    polish_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    polish_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra polishing instruction for this chapter.",
    )
    polish_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra polishing instruction from a file.",
    )
    polish_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the polish agent config.",
    )
    _add_agent_runtime_args(polish_parser)
    polish_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing polished.md.",
    )
    polish_parser.add_argument(
        "--style-note",
        default=None,
        help="Temporary style guidance for this polish pass.",
    )
    polish_parser.add_argument(
        "--keep-length",
        action="store_true",
        help="Try to keep the original length and paragraph scale.",
    )
    polish_parser.add_argument(
        "--light-edit",
        action="store_true",
        help="Light edit: language cleanup only.",
    )
    polish_parser.add_argument(
        "--deep-edit",
        action="store_true",
        help="Deep edit: improve rhythm, dialogue, and description without changing facts.",
    )
    _add_search_context_args(polish_parser)

    audit_parser = subparsers.add_parser("audit-chapter", help="Audit a chapter for consistency")
    audit_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    audit_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    audit_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra audit instruction for this chapter.",
    )
    audit_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra audit instruction from a file.",
    )
    audit_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the audit agent config.",
    )
    _add_agent_runtime_args(audit_parser)
    audit_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing audit.json.",
    )
    audit_parser.add_argument(
        "--strict",
        action="store_true",
        help="Use stricter audit criteria.",
    )
    audit_parser.add_argument(
        "--focus",
        action="append",
        default=[],
        choices=("canon", "state", "timeline", "style", "plot", "character_voice", "premature_reveal"),
        help="Audit focus area. Can be provided multiple times.",
    )
    audit_parser.add_argument(
        "--audited-file",
        default="polished.md",
        choices=("draft.md", "polished.md"),
        help="Chapter file to audit. Defaults to polished.md.",
    )
    audit_parser.add_argument(
        "--use-search-context",
        action="store_true",
        help="Add explainable search results to the audit prompt.",
    )
    audit_parser.add_argument(
        "--vector-context",
        choices=("auto", "on", "off"),
        default="auto",
        help="Embedding semantic context mode for agent memory retrieval. Defaults to auto.",
    )
    audit_parser.add_argument(
        "--use-vector-context",
        action="store_true",
        help="Compatibility alias for --vector-context on.",
    )
    audit_parser.add_argument(
        "--no-audit-recall",
        action="store_true",
        help="Disable bounded audit context recall for this run.",
    )

    revise_parser = subparsers.add_parser("revise-chapter", help="Revise a chapter from instructions or audit")
    revise_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    revise_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    revise_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra revision instruction for this chapter.",
    )
    revise_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra revision instruction from a file.",
    )
    revise_parser.add_argument(
        "--from-audit",
        action="store_true",
        help="Use audit.json issues as the main revision target.",
    )
    revise_parser.add_argument(
        "--target",
        default="polished",
        choices=("draft", "polished"),
        help="Source and output version family to revise. Defaults to polished.",
    )
    revise_parser.add_argument(
        "--source-file",
        default=None,
        help="Specific source version to revise, such as polished.md or polished.v2.md. Defaults to the latest version.",
    )
    revise_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to writer/polish agent config based on target.",
    )
    _add_agent_runtime_args(revise_parser)
    revise_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the selected revision version file if it already exists.",
    )
    revise_parser.add_argument(
        "--save-as-version",
        action="store_true",
        default=True,
        help="Save as draft.vN.md or polished.vN.md. This is the default.",
    )
    revise_parser.add_argument(
        "--max-rounds",
        type=int,
        default=1,
        help="Maximum revision loop rounds. Defaults to 1.",
    )
    revise_parser.add_argument(
        "--confirm-loop",
        action="store_true",
        help="Explicitly allow more than one revision round.",
    )
    _add_search_context_args(revise_parser)

    propose_state_parser = subparsers.add_parser(
        "propose-state-update",
        help="Generate a state and timeline update proposal",
    )
    propose_state_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    propose_state_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    propose_state_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra state update instruction for this chapter.",
    )
    propose_state_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra state update instruction from a file.",
    )
    propose_state_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the state update agent config.",
    )
    _add_agent_runtime_args(propose_state_parser)
    propose_state_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing state_update_proposal.json.",
    )
    propose_state_parser.add_argument(
        "--allow-unresolved-audit",
        action="store_true",
        help="Allow proposal generation when audit has medium, high, or critical issues.",
    )
    _add_search_context_args(propose_state_parser)

    apply_state_parser = subparsers.add_parser(
        "apply-state-update",
        help="Apply a chapter state update proposal",
    )
    apply_state_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    apply_state_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )

    accept_parser = subparsers.add_parser(
        "accept-chapter",
        help="Accept a chapter and apply state/timeline updates",
    )
    accept_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    accept_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    accept_parser.add_argument(
        "--allow-issues",
        action="store_true",
        help="Allow acceptance when audit has medium, high, or critical issues.",
    )
    accept_parser.add_argument(
        "--propose",
        action="store_true",
        help="Generate state_update_proposal.json first if it is missing.",
    )
    accept_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra state update instruction when used with --propose.",
    )
    accept_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra state update instruction from a file when used with --propose.",
    )
    accept_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for --propose and canon drift proposal checks.",
    )
    _add_agent_runtime_args(accept_parser)
    accept_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing state_update_proposal.json when used with --propose.",
    )
    _add_search_context_args(accept_parser, default_enabled=True)

    generate_parser = subparsers.add_parser(
        "generate-chapter",
        help="Run the chapter generation pipeline",
    )
    generate_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    generate_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    generate_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra instruction shared by planning, writing, polishing, and audit.",
    )
    generate_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra instruction from a file.",
    )
    generate_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for each pipeline step.",
    )
    _add_agent_runtime_args(generate_parser)
    generate_parser.add_argument(
        "--target-words",
        type=int,
        default=None,
        help="Optional target word count for writing.",
    )
    generate_parser.add_argument(
        "--style-note",
        default=None,
        help="Temporary style guidance for writing and polishing.",
    )
    generate_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite files generated by pipeline steps.",
    )
    generate_parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse already generated step outputs and continue the pipeline.",
    )
    generate_parser.add_argument(
        "--polish-mode",
        choices=("single-pass", "auto", "review-gate"),
        default=None,
        help="Finalization mode. Defaults to project polish.mode or single-pass.",
    )
    generate_parser.add_argument(
        "--stop-after",
        choices=("plan", "write", "polish", "audit"),
        default=None,
        help="Stop after the selected pipeline step.",
    )
    _add_search_context_args(generate_parser, default_enabled=True)

    export_parser = subparsers.add_parser("export", help="Export project content")
    export_subparsers = export_parser.add_subparsers(dest="export_command", required=True)
    export_markdown_parser = export_subparsers.add_parser("markdown", help="Export chapters as Markdown")
    export_markdown_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    export_markdown_parser.add_argument(
        "--chapters",
        default=None,
        help="Comma-separated chapter numbers, for example 1,2,3.",
    )
    export_markdown_parser.add_argument(
        "--from",
        dest="from_chapter",
        type=int,
        default=None,
        help="First chapter number to export.",
    )
    export_markdown_parser.add_argument(
        "--to",
        dest="to_chapter",
        type=int,
        default=None,
        help="Last chapter number to export.",
    )
    export_markdown_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output Markdown path. Defaults to exports/novel.md.",
    )
    export_markdown_parser.add_argument(
        "--title",
        default=None,
        help="Override exported work title.",
    )
    export_markdown_parser.add_argument(
        "--toc",
        action="store_true",
        help="Include a Markdown table of contents.",
    )
    export_markdown_parser.add_argument(
        "--volume-title",
        default=None,
        help="Optional volume title inserted before chapters and inside the table of contents.",
    )
    export_markdown_parser.add_argument(
        "--chapter-number-style",
        choices=("chinese", "arabic", "chapter", "plain"),
        default="chinese",
        help="Chapter heading style. Defaults to chinese, for example 第一章.",
    )
    export_markdown_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing output Markdown.",
    )
    export_docx_parser = export_subparsers.add_parser("docx", help="Export chapters as Word DOCX")
    export_docx_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    export_docx_parser.add_argument(
        "--chapters",
        default=None,
        help="Comma-separated chapter numbers, for example 1,2,3.",
    )
    export_docx_parser.add_argument(
        "--from",
        dest="from_chapter",
        type=int,
        default=None,
        help="First chapter number to export.",
    )
    export_docx_parser.add_argument(
        "--to",
        dest="to_chapter",
        type=int,
        default=None,
        help="Last chapter number to export.",
    )
    export_docx_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output DOCX path. Defaults to exports/novel.docx.",
    )
    export_docx_parser.add_argument(
        "--title",
        default=None,
        help="Override exported work title.",
    )
    export_docx_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing output DOCX.",
    )

    web_parser = subparsers.add_parser("web", help="Run the local Web UI")
    web_parser.add_argument(
        "--path",
        default=".",
        help="Novel project directory whose project.yaml may define web.default_port. Defaults to current directory.",
    )
    web_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind. Defaults to 127.0.0.1.",
    )
    web_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind. Overrides project.yaml web.default_port. Defaults to 8765.",
    )
    web_open_group = web_parser.add_mutually_exclusive_group()
    web_open_group.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the Web UI URL in the default browser after the server starts.",
    )
    web_open_group.add_argument(
        "--no-open",
        dest="open_browser",
        action="store_false",
        help="Do not open a browser automatically. This is the default.",
    )
    web_parser.set_defaults(open_browser=False)

    web_launch_parser = subparsers.add_parser("web-launch", help="Run the Web UI from launcher config")
    web_launch_parser.add_argument(
        "--config",
        type=Path,
        default=Path("WriterYang_WebUI.config.json"),
        help="Web UI launcher config path. Defaults to WriterYang_WebUI.config.json.",
    )
    web_launch_open_group = web_launch_parser.add_mutually_exclusive_group()
    web_launch_open_group.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the Web UI URL in the default browser after the server starts. This is the default.",
    )
    web_launch_open_group.add_argument(
        "--no-open",
        dest="open_browser",
        action="store_false",
        help="Do not open a browser automatically.",
    )
    web_launch_parser.set_defaults(open_browser=True)

    _add_integration_args_recursive(parser)
    return parser

_COMMAND_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "init": _cmd_init,
    "validate": _cmd_validate,
    "schema": _cmd_schema,
    "completion": _cmd_completion,
    "doctor": _cmd_doctor,
    "index": _cmd_index,
    "search": _cmd_search,
    "memory-repair": _cmd_memory_repair,
    "setting-change": _cmd_setting_change,
    "chapter-memory": _cmd_chapter_memory,
    "ask": _cmd_ask,
    "session": _cmd_session,
    "status": _cmd_status,
    "usage": _cmd_usage,
    "show": _cmd_show,
    "inspire": _cmd_inspire,
    "canon": _cmd_canon,
    "plan-chapter": _cmd_plan_chapter,
    "write-chapter": _cmd_write_chapter,
    "polish-chapter": _cmd_polish_chapter,
    "audit-chapter": _cmd_audit_chapter,
    "revise-chapter": _cmd_revise_chapter,
    "propose-state-update": _cmd_propose_state_update,
    "apply-state-update": _cmd_apply_state_update,
    "accept-chapter": _cmd_accept_chapter,
    "generate-chapter": _cmd_generate_chapter,
    "export": _cmd_export,
    "web": _cmd_web,
    "web-launch": _cmd_web_launch,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_project_alias(args)

    handler = _COMMAND_HANDLERS.get(args.command)
    if handler is not None:
        return handler(args)

    parser.error(f"unknown command: {args.command}")
    return 2
