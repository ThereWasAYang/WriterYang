from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from novel.core.agent_output import (
    AgentInvocationContext,
    AgentOutputContract,
    AgentOutputContractError,
    generate_with_output_guard,
)
from novel.core.app_logging import log_app_warning
from novel.core.gender import canonical_gender, infer_gender_from_character_payload, strip_explicit_gender_tags
from novel.core.io import (
    atomic_write_json,
    atomic_write_model_json,
    atomic_write_text,
    backup_file,
    load_json,
    load_json_model,
)
from novel.core.json_extract import JsonExtractionError, extract_json_object
from novel.core.management import record_management_event
from novel.core.memory_repair_mock import (
    mock_memory_change_batch_plan,
    mock_memory_change_clarification_decision,
    mock_memory_repair_decision,
)
from novel.core.memory_repair_ops import (
    apply_operations_to_data as _apply_operations_to_data,
)
from novel.core.memory_repair_ops import (
    escape_pointer as _escape_pointer,
)
from novel.core.memory_repair_ops import (
    pointer_parts as _pointer_parts,
)
from novel.core.memory_repair_ops import (
    restore_backups as _restore_backups,
)
from novel.core.memory_repair_ops import (
    unescape_pointer as _unescape_pointer,
)
from novel.core.memory_repair_rules import (
    ALLOWED_MEMORY_FILES,
    CHARACTER_ROLE_IDENTITY_PATTERNS,
    COLLECTION_FIELD_HINTS,
    COLLECTION_PATH_FILES,
    COLLECTION_SCHEMA_HINTS,
    DOMAIN_FILES,
    FILE_COLLECTION_KEYS,
    FILE_DOMAINS,
    NARRATIVE_CHARACTER_ROLES,
    POINTER_PATH_FILES,
    SCANNED_IMPACT_SUFFIXES,
    SETTING_CHANGE_MAPPING_RULES,
    STATE_COLLECTION_KEYS,
    UNIQUE_ID_COLLECTIONS,
)
from novel.core.prompts import load_prompt_template, prompt_template_version
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.schemas import (
    MemoryChangeBatch,
    MemoryChangeBatchPlan,
    MemoryChangeClarificationDecision,
    MemoryChangeClarificationSession,
    MemoryChangeConversationTurn,
    MemoryChangeDomain,
    MemoryChangeFollowupAction,
    MemoryChangeImpact,
    MemoryChangeKind,
    MemoryChangeStage,
    MemoryRepairApplyLog,
    MemoryRepairDecision,
    MemoryRepairOperation,
    MemoryRepairProposal,
    MemoryRepairRiskLevel,
)
from novel.core.structured_generation import (
    REPAIR_ERROR_LIMIT,
    REPAIR_INVALID_OUTPUT_LIMIT,
    JsonRepairExhaustedError,
    generate_json_with_repair,
)
from novel.core.timeutil import new_request_id, utc_now
from novel.core.validation import validate_project

__all__ = [
    "dataclass",
    "json",
    "Path",
    "re",
    "Iterable",
    "Literal",
    "Mapping",
    "ValidationError",
    "AgentInvocationContext",
    "AgentOutputContract",
    "AgentOutputContractError",
    "generate_with_output_guard",
    "log_app_warning",
    "canonical_gender",
    "infer_gender_from_character_payload",
    "strip_explicit_gender_tags",
    "atomic_write_json",
    "atomic_write_model_json",
    "atomic_write_text",
    "backup_file",
    "load_json",
    "load_json_model",
    "JsonExtractionError",
    "extract_json_object",
    "record_management_event",
    "mock_memory_change_batch_plan",
    "mock_memory_change_clarification_decision",
    "mock_memory_repair_decision",
    "_apply_operations_to_data",
    "_escape_pointer",
    "_pointer_parts",
    "_restore_backups",
    "_unescape_pointer",
    "load_prompt_template",
    "prompt_template_version",
    "ProviderOverrides",
    "create_agent_provider",
    "default_agent_config_path",
    "ModelProvider",
    "ModelRequest",
    "ALLOWED_MEMORY_FILES",
    "CHARACTER_ROLE_IDENTITY_PATTERNS",
    "COLLECTION_FIELD_HINTS",
    "COLLECTION_PATH_FILES",
    "COLLECTION_SCHEMA_HINTS",
    "DOMAIN_FILES",
    "FILE_COLLECTION_KEYS",
    "FILE_DOMAINS",
    "NARRATIVE_CHARACTER_ROLES",
    "POINTER_PATH_FILES",
    "SCANNED_IMPACT_SUFFIXES",
    "SETTING_CHANGE_MAPPING_RULES",
    "STATE_COLLECTION_KEYS",
    "UNIQUE_ID_COLLECTIONS",
    "MemoryChangeBatch",
    "MemoryChangeBatchPlan",
    "MemoryChangeDomain",
    "MemoryChangeClarificationDecision",
    "MemoryChangeClarificationSession",
    "MemoryChangeConversationTurn",
    "MemoryChangeFollowupAction",
    "MemoryChangeImpact",
    "MemoryChangeKind",
    "MemoryChangeStage",
    "MemoryRepairDecision",
    "MemoryRepairRiskLevel",
    "MemoryRepairApplyLog",
    "MemoryRepairOperation",
    "MemoryRepairProposal",
    "REPAIR_ERROR_LIMIT",
    "REPAIR_INVALID_OUTPUT_LIMIT",
    "JsonRepairExhaustedError",
    "generate_json_with_repair",
    "new_request_id",
    "utc_now",
    "validate_project",
]
