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
    inherited_profile_config_patch,
    profile_for_task,
    profile_inherited_patch_fields,
)
from novel.core.app_logging import log_app_warning
from novel.core.audit_localization import (
    localize_audit_issue_for_author,
    localize_session_rewrite_issue_for_author,
)
from novel.core.canon import (
    CanonAppliedProposalRecord,
    CanonError,
    load_canon_applied_proposals,
)
from novel.core.chapter_memory import (
    accepted_chapter_numbers,
    chapter_memory_freshness_warnings,
    chapter_memory_path,
)
from novel.core.env import load_project_env
from novel.core.exporting import parse_chapter_selector
from novel.core.inspection import format_canon, get_project_status
from novel.core.io import load_json, load_json_model, load_yaml
from novel.core.locking import ProjectLock, ProjectLockError
from novel.core.management import load_management_events
from novel.core.memory_repair import (
    MemoryRepairError,
    SettingChangeSuggestionResult,
)
from novel.core.provider_config import resolve_agent_config_source, resolve_profile_config_source
from novel.core.search import SearchError, search_index_status, search_project
from novel.core.embeddings import EmbeddingError, resolve_embedding_parameters
from novel.core.setup_guide import SetupGuideError
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
    find_latest_active_session,
    load_session_progress,
    load_session,
    load_rewrite_events,
    parse_range,
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
from novel.core.workspace import (
    InitOptions,
    WorkspaceExistsError,
    default_style_guide_markdown,
    is_default_inspiration_placeholder,
)


__all__ = [
    "Callable",
    "asdict",
    "difflib",
    "json",
    "os",
    "Path",
    "re",
    "sys",
    "traceback",
    "Literal",
    "Mapping",
    "cast",
    "parse_qs",
    "BaseModel",
    "Field",
    "yaml",
    "__version__",
    "DEFAULT_AGENT_MAX_CONTEXT_TOKENS",
    "DEFAULT_AGENT_MAX_TOKENS",
    "DEFAULT_AGENT_TIMEOUT_SECONDS",
    "PROFILE_NAMES",
    "TASK_ONLY_CONFIG_FIELDS",
    "TASK_TO_PROFILE",
    "inherited_profile_config_patch",
    "profile_for_task",
    "profile_inherited_patch_fields",
    "log_app_warning",
    "localize_audit_issue_for_author",
    "localize_session_rewrite_issue_for_author",
    "CanonAppliedProposalRecord",
    "CanonError",
    "load_canon_applied_proposals",
    "accepted_chapter_numbers",
    "chapter_memory_freshness_warnings",
    "chapter_memory_path",
    "load_project_env",
    "parse_chapter_selector",
    "format_canon",
    "get_project_status",
    "load_json",
    "load_json_model",
    "load_yaml",
    "ProjectLock",
    "ProjectLockError",
    "load_management_events",
    "MemoryRepairError",
    "SettingChangeSuggestionResult",
    "resolve_agent_config_source",
    "resolve_profile_config_source",
    "SearchError",
    "search_index_status",
    "search_project",
    "EmbeddingError",
    "resolve_embedding_parameters",
    "SetupGuideError",
    "new_request_id",
    "utc_now",
    "utc_timestamp",
    "web_launcher",
    "AgentConfig",
    "AgentsConfig",
    "AuditReport",
    "ChapterMemory",
    "ChapterPlan",
    "CreationSession",
    "EmbeddingsConfig",
    "PolishMode",
    "RevisionLog",
    "RevisionRecord",
    "SessionProgress",
    "VectorContextMode",
    "MemoryChangeStage",
    "redact_secret_text",
    "validate_secret_config_file",
    "CreationSessionError",
    "find_latest_active_session",
    "load_session_progress",
    "load_session",
    "load_rewrite_events",
    "parse_range",
    "ProviderContextLimitError",
    "ProviderError",
    "ProviderFactory",
    "provider_parameter_capabilities",
    "resolve_json_response_format",
    "summarize_provider_usage",
    "ValidationMessage",
    "validate_project",
    "InitOptions",
    "WorkspaceExistsError",
    "default_style_guide_markdown",
    "is_default_inspiration_placeholder",
]
