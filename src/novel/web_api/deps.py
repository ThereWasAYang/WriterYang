# mypy: ignore-errors
# ruff: noqa: F401
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import difflib
import json
import os
from pathlib import Path
import re
import sys
import traceback
from typing import Literal, Mapping, cast
from urllib.parse import parse_qs

from pydantic import BaseModel, Field
import yaml

from novel import __version__
from novel.core.agent_defaults import (
    DEFAULT_AGENT_MAX_CONTEXT_TOKENS,
    DEFAULT_AGENT_MAX_TOKENS,
    DEFAULT_AGENT_TIMEOUT_SECONDS,
    PROFILE_NAMES,
    TASK_ONLY_CONFIG_FIELDS,
    TASK_TO_PROFILE,
    drop_legacy_profile_default_patch,
    inherited_profile_config_patch,
    profile_for_task,
    profile_inherited_patch_fields,
)
from novel.core.app_logging import log_app_warning
from novel.core.audit_localization import (
    localize_audit_issue_for_author,
    localize_session_rewrite_issue_for_author,
)
from novel.core.auditing import ChapterAuditOptions, audit_chapter, load_audit_provider
from novel.core.canon import (
    CanonAppliedProposalRecord,
    CanonError,
    CanonSuggestOptions,
    apply_canon_proposal,
    load_canon_applied_proposals,
    load_canon_provider,
    suggest_canon,
)
from novel.core.chapter_memory import (
    ChapterMemoryOptions,
    accepted_chapter_numbers,
    chapter_memory_freshness_warnings,
    chapter_memory_path,
    generate_chapter_memory,
    load_chapter_memory_provider,
)
from novel.core.chapter_versions import (
    is_allowed_chapter_version_name,
    latest_chapter_version_path,
    next_chapter_version_path,
)
from novel.core.drafting import ChapterDraftingOptions, load_drafting_provider, write_chapter_draft
from novel.core.env import load_project_env
from novel.core.exporting import DocxExportOptions, MarkdownExportOptions, export_docx, export_markdown, parse_chapter_selector
from novel.core.inspiration import InspirationOptions, load_inspiration_provider, run_inspiration_agent
from novel.core.inspection import format_canon, get_project_status
from novel.core.io import atomic_write_model_json, atomic_write_text, atomic_write_yaml, backup_if_exists, load_json, load_json_model, load_yaml
from novel.core.locking import ProjectLock, ProjectLockError
from novel.core.management import load_management_events
from novel.core.memory_repair import (
    MemoryRepairError,
    SettingChangeSuggestionResult,
    answer_setting_change_clarification,
    apply_memory_repair,
    suggest_setting_change_interactive,
)
from novel.core.planning import ChapterPlanningOptions, load_planning_provider, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, load_polishing_provider, polish_chapter
from novel.core.provider_config import resolve_agent_config_source, resolve_profile_config_source
from novel.core.search import SearchError, refresh_search_index, search_index_status, search_project
from novel.core.embeddings import EmbeddingError, resolve_embedding_parameters
from novel.core.setup_guide import (
    SetupGuideError,
    configure_default_provider,
    configure_embedding_provider,
)
from novel.core.style_guide import (
    StyleGuideGenerationOptions,
    generate_style_guide,
    load_style_guide_provider,
)
from novel.core.timeutil import new_request_id, utc_now, utc_timestamp
import novel.core.web_launcher as web_launcher
from novel.core.schemas import (
    AgentConfig,
    AgentsConfig,
    AuditReport,
    ChapterMemory,
    ChapterPlan,
    CreationSession,
    EmbeddingsConfig,
    PolishMode,
    RevisionLog,
    RevisionRecord,
    SessionProgress,
    VectorContextMode,
    MemoryChangeStage,
)
from novel.core.security import redact_secret_text, validate_secret_config_file
from novel.core.session import (
    CreationSessionError,
    SessionActionOptions,
    SessionInstructionOptions,
    SessionRunOptions,
    SessionStartOptions,
    SessionRewriteControlOptions,
    accept_session,
    approve_outline,
    archive_session,
    find_latest_active_session,
    load_session_progress,
    load_session,
    load_rewrite_events,
    parse_range,
    request_session_cancel,
    retry_rewrite,
    revise_audit,
    revise_content,
    revise_outline,
    run_session,
    start_session,
    undo_rewrite,
)
from novel.core.providers import (
    ProviderContextLimitError,
    ProviderError,
    ProviderFactory,
    provider_parameter_capabilities,
    resolve_json_response_format,
)
from novel.core.usage import summarize_provider_usage
from novel.core.validation import ValidationMessage, validate_project
from novel.core.workflow import GenerateChapterOptions, ProviderName, generate_chapter
from novel.core.workspace import (
    InitOptions,
    WorkspaceExistsError,
    default_style_guide_markdown,
    init_workspace,
    is_default_inspiration_placeholder,
)


__all__ = [name for name in globals() if not name.startswith("__") or name == "__version__"]
