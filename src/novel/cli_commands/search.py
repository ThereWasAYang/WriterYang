from __future__ import annotations

# ruff: noqa: F401

import argparse
import getpass
import importlib.util
import json
import os
import re
import sys
import webbrowser
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import cast

from novel.core.auditing import (
    AuditError,
    ChapterAuditOptions,
    audit_chapter,
    load_audit_provider,
    read_audit_instruction,
)
from novel.core.canon import (
    CanonError,
    CanonSuggestOptions,
    apply_canon_proposal,
    format_canon_validation_report,
    load_canon_provider,
    suggest_canon,
)
from novel.core.chapter_memory import (
    ChapterMemoryError,
    ChapterMemoryOptions,
    accepted_chapter_numbers,
    chapter_memory_path,
    generate_chapter_memory,
    load_chapter_memory_provider,
)
from novel.core.env import load_project_env
from novel.core.drafting import (
    ChapterDraftingOptions,
    DraftingError,
    load_drafting_provider,
    read_drafting_instruction,
    write_chapter_draft,
)
from novel.core.inspiration import (
    InspirationError,
    InspirationOptions,
    load_inspiration_provider,
    read_inspiration_input,
    run_inspiration_agent,
)
from novel.core.planning import (
    ChapterPlanningOptions,
    PlanningError,
    load_planning_provider,
    plan_chapter,
    read_planning_instruction,
)
from novel.core.polishing import (
    ChapterPolishingOptions,
    PolishingError,
    load_polishing_provider,
    polish_chapter,
    read_polishing_instruction,
    resolve_edit_mode,
)
from novel.core.revision import (
    ChapterRevisionOptions,
    RevisionError,
    RevisionLoopOptions,
    load_revision_provider,
    read_revision_instruction,
    revise_chapter,
    revise_chapter_loop,
)
from novel.core.management import load_management_events
from novel.core.memory_repair import (
    MemoryRepairError,
    SettingChangeSuggestionResult,
    answer_setting_change_clarification,
    apply_memory_repair,
    suggest_memory_repair,
    suggest_setting_change_interactive,
)
from novel.core.search import SearchError, rebuild_search_index, refresh_search_index, search_index_status, search_project
from novel.core.session import (
    CreationSessionError,
    SessionActionOptions,
    SessionInstructionOptions,
    SessionResult,
    SessionRunOptions,
    SessionStartOptions,
    SessionRewriteControlOptions,
    accept_session,
    approve_outline,
    archive_session,
    load_rewrite_events,
    parse_range,
    retry_rewrite,
    revise_audit,
    revise_content,
    revise_outline,
    run_session,
    show_session,
    start_session,
    undo_rewrite,
)
from novel.core.state_update import (
    AcceptChapterOptions,
    StateUpdateApplyOptions,
    StateUpdateError,
    StateUpdateProposeOptions,
    accept_chapter,
    apply_state_update,
    load_state_update_provider,
    propose_state_update,
    read_state_update_instruction,
)
from novel.core.exporting import (
    DocxExportOptions,
    ExportError,
    MarkdownExportOptions,
    export_docx,
    export_markdown,
    parse_chapter_selector,
)
from novel.core.inspection import (
    ProjectReadError,
    format_characters,
    format_canon,
    format_state,
    format_status,
    format_timeline,
    get_project_status,
)
from novel.core.json_schema import export_json_schemas
from novel.core.io import load_json_model, load_yaml, load_yaml_model
from novel.core.locking import ProjectLock, ProjectLockError
from novel.core.migration import MigrationError, migrate_project
from novel.core.orchestrator import (
    OrchestratorError,
    OrchestratorOptions,
    decide_ask_intent,
    format_orchestrator_plan,
    handoff_rules_text,
    orchestrate,
)
from novel.core.provider_config import ProviderOverrides, describe_agent_provider, default_agent_config_path
from novel.core.security import scan_security
from novel.core.setup_guide import (
    SetupGuideError,
    configure_default_provider,
    configure_embedding_provider,
    configure_web_port,
    find_available_port,
    is_port_available,
)
from novel.core.schemas import (
    AgentsConfig,
    AuditReport,
    CreationSession,
    MemoryChangeStage,
    PolishMode,
    ProjectConfig,
    VectorContextMode,
)
from novel.core.usage import UsageError, summarize_provider_usage
from novel.core.workspace import InitOptions, WorkspaceExistsError, init_workspace
from novel.core.validation import validate_canon, validate_project
from novel.core.workflow import (
    GenerateChapterOptions,
    WorkflowError,
    generate_chapter,
    read_workflow_instruction,
)
from novel import __version__
from novel.cli_shared import (
    _add_agent_runtime_args,
    _add_search_context_args,
    _add_polish_mode_arg,
    _vector_context_mode_from_args,
    _polish_mode_from_arg,
    _audit_issue_lines,
    _management_event_payload,
    _management_event_lines,
    _severity_rank,
    _extract_chapter_from_text,
    _extract_repair_id,
    _resolve_memory_repair_proposal_arg,
    _print_dry_run_provider,
    _add_integration_args,
    _add_integration_args_recursive,
    _apply_project_alias,
    _wants_json,
    _quiet,
    _success,
    _failure,
    _print_json,
    _safe_message,
    _command_lock,
    _validation_payload,
    _status_payload,
    _format_usage_summary,
    _resolve_web_port,
    _should_run_init_guide,
    _run_init_setup_guide,
    _prompt_text,
    _prompt_yes_no,
    completion_script,
    run_doctor,
    format_doctor_result,
    _doctor_check,
    _doctor_env_checks,
    _doctor_agent_config_checks,
    _collect_env_names,
    _repo_root,
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
