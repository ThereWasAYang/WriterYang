from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Iterable, Literal

from pydantic import ValidationError

from novel.core.agent_output import (
    AgentInvocationContext,
    AgentOutputContract,
    AgentOutputContractError,
    generate_with_output_guard,
)
from novel.core.app_logging import log_app_warning
from novel.core.io import atomic_write_json, atomic_write_model_json, atomic_write_text, backup_file, load_json, load_json_model
from novel.core.json_extract import JsonExtractionError, extract_json_object
from novel.core.management import record_management_event
from novel.core.memory_repair_mock import (
    mock_memory_change_batch_plan,
    mock_memory_change_clarification_decision,
    mock_memory_repair_decision,
)
from novel.core.memory_repair_ops import (
    apply_operations_to_data as _apply_operations_to_data,
    escape_pointer as _escape_pointer,
    pointer_parts as _pointer_parts,
    restore_backups as _restore_backups,
    unescape_pointer as _unescape_pointer,
)
from novel.core.prompts import load_prompt_template
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
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
from novel.core.schemas import (
    MemoryChangeBatch,
    MemoryChangeBatchPlan,
    MemoryChangeDomain,
    MemoryChangeClarificationDecision,
    MemoryChangeClarificationSession,
    MemoryChangeConversationTurn,
    MemoryChangeFollowupAction,
    MemoryChangeImpact,
    MemoryChangeKind,
    MemoryChangeStage,
    MemoryRepairDecision,
    MemoryRepairRiskLevel,
    MemoryRepairApplyLog,
    MemoryRepairOperation,
    MemoryRepairProposal,
)
from novel.core.structured_generation import JsonRepairExhaustedError, generate_json_with_repair
from novel.core.validation import validate_project


class MemoryRepairError(RuntimeError):
    """Raised when a memory repair proposal cannot be created or applied safely."""


@dataclass(frozen=True)
class MemoryRepairSuggestResult:
    proposal: MemoryRepairProposal
    proposal_path: Path
    markdown_path: Path


@dataclass(frozen=True)
class MemoryRepairApplyResult:
    proposal: MemoryRepairProposal
    apply_log: MemoryRepairApplyLog
    apply_log_path: Path


@dataclass(frozen=True)
class SettingChangeSuggestionResult:
    status: Literal["proposal_ready", "needs_clarification"]
    proposal_result: MemoryRepairSuggestResult | None = None
    clarification: MemoryChangeClarificationSession | None = None


@dataclass(frozen=True)
class _PreparedMemoryRepairDecision:
    decision: MemoryRepairDecision
    target_files: list[str]
    operations: list[MemoryRepairOperation]
    notes: list[str]
    change_kind: MemoryChangeKind


def suggest_memory_repair(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    decision: MemoryRepairDecision | None = None,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
    session_id: str | None = None,
    chapter_number: int | None = None,
    audit_issue_ids: list[str] | None = None,
) -> MemoryRepairSuggestResult:
    root = root.resolve()
    request = user_request.strip()
    if not request:
        raise MemoryRepairError("memory repair request must not be empty")
    repair_id = _new_repair_id()
    decision_was_provided = decision is not None
    repair_decision = decision or generate_memory_repair_decision(
        root,
        request,
        provider_name=provider_name,
        provider=provider,
        change_kind=change_kind,
        stage=stage,
    )
    prepared = _prepare_memory_repair_decision(
        root,
        request,
        repair_decision,
        decision_was_provided=decision_was_provided,
        provider_name=provider_name,
        provider=provider,
        change_kind=change_kind,
        stage=stage,
    )
    repair_decision = prepared.decision
    target_files = prepared.target_files
    operations = prepared.operations
    notes = prepared.notes
    resolved_kind = prepared.change_kind
    resolved_stage: MemoryChangeStage = stage or repair_decision.stage or "unknown"
    domains = _dedupe_domains([*repair_decision.domains, *_domains_from_files(target_files)])
    impact = _analyze_memory_change_impact(
        root,
        operations,
        domains=domains,
        session_id=session_id,
        chapter_number=chapter_number,
    )
    followup_actions = _memory_change_followups(
        root,
        impact,
        stage=resolved_stage,
        session_id=session_id,
        chapter_number=chapter_number,
        change_kind=resolved_kind,
    )
    report = validate_project(root)
    proposal = MemoryRepairProposal(
        repair_id=repair_id,
        change_kind=resolved_kind,
        user_request=request,
        target_files=target_files,
        operations=operations,
        domains=domains,
        stage=resolved_stage,
        impact=impact,
        followup_actions=[*repair_decision.followup_actions, *followup_actions],
        risk_level=impact.risk_level if operations else "medium",
        confidence=repair_decision.confidence,
        assumptions=repair_decision.assumptions,
        needs_user_confirmation=True,
        validation_before={
            "ok": report.ok,
            "error_count": len(report.errors),
            "warning_count": len(report.warnings),
        },
        notes=_proposal_notes(target_files, operations, notes, change_kind=resolved_kind, audit_issue_ids=audit_issue_ids or []),
        created_at=_utc_now(),
    )
    repair_dir = _repair_dir(root, repair_id)
    proposal_path = repair_dir / "proposal.json"
    markdown_path = repair_dir / "proposal.md"
    atomic_write_model_json(proposal_path, proposal)
    atomic_write_json(markdown_path.with_suffix(".diff.json"), _preview_operations(root, proposal))
    atomic_write_text(markdown_path, render_memory_repair_markdown(proposal))
    record_management_event(
        root,
        "memory_repair_proposed",
        f"项目管家生成记忆修复建议：{repair_id}",
        source="orchestrator",
        target_files=target_files,
        status="info",
        details={
            "repair_id": repair_id,
            "change_kind": resolved_kind,
            "operation_count": len(operations),
            "affected_chapters": impact.affected_chapters,
            "affected_sessions": impact.affected_sessions,
        },
    )
    return MemoryRepairSuggestResult(proposal=proposal, proposal_path=proposal_path, markdown_path=markdown_path)


def _prepare_memory_repair_decision(
    root: Path,
    request: str,
    repair_decision: MemoryRepairDecision,
    *,
    decision_was_provided: bool,
    provider_name: str,
    provider: ModelProvider | None,
    change_kind: MemoryChangeKind | None,
    stage: MemoryChangeStage | None,
    prompt_target_files: list[str] | None = None,
    batch_label: str | None = None,
) -> _PreparedMemoryRepairDecision:
    resolved_preflight_kind: MemoryChangeKind = change_kind or repair_decision.change_kind
    target_files, operations, notes = _sanitize_repair_decision(repair_decision)
    operations = _drop_unsafe_remove_operations(root, operations, notes)
    preflight_errors = _preflight_memory_repair_operations(root, operations, change_kind=resolved_preflight_kind)
    operations, local_notes, preflight_errors = _auto_repair_setting_change_semantics(
        root,
        operations,
        preflight_errors,
        change_kind=resolved_preflight_kind,
    )
    if local_notes:
        notes.extend(local_notes)
        target_files = sorted({*target_files, *(operation.file for operation in operations)})
    target_schema_repair_attempts = 0
    while preflight_errors and operations and not decision_was_provided and target_schema_repair_attempts < 2:
        target_schema_repair_attempts += 1
        previous_operations = list(operations)
        repair_decision = repair_decision.model_copy(
            update={
                "target_files": target_files,
                "operations": operations,
                "notes": notes,
            }
        )
        try:
            repair_decision = _repair_memory_repair_decision_target_schema(
                root,
                request,
                invalid_decision=repair_decision,
                preflight_errors=preflight_errors,
                provider_name=provider_name,
                provider=provider,
                change_kind=change_kind,
                stage=stage,
                target_files=prompt_target_files,
            )
        except Exception as exc:
            log_app_warning(
                root,
                "memory_repair_target_schema_repair_failed",
                workflow="target_schema_repair",
                stage=stage,
                change_kind=change_kind,
                batch_label=batch_label,
                preflight_error_count=len(preflight_errors),
                error_type=exc.__class__.__name__,
                error=str(exc),
            )
            if batch_label:
                raise MemoryRepairError(f"setting change batch {batch_label} target-schema repair failed: {exc}") from exc
            raise
        resolved_preflight_kind = change_kind or repair_decision.change_kind
        target_files, operations, notes = _sanitize_repair_decision(repair_decision)
        operations = _drop_unsafe_remove_operations(root, operations, notes)
        operations, regression_notes = _restore_regressed_existing_add_operations(root, previous_operations, operations)
        if regression_notes:
            notes.extend(regression_notes)
            target_files = sorted({*target_files, *(operation.file for operation in operations)})
        preflight_errors = _preflight_memory_repair_operations(root, operations, change_kind=resolved_preflight_kind)
        operations, local_notes, preflight_errors = _auto_repair_setting_change_semantics(
            root,
            operations,
            preflight_errors,
            change_kind=resolved_preflight_kind,
        )
        if local_notes:
            notes.extend(local_notes)
            target_files = sorted({*target_files, *(operation.file for operation in operations)})
    if preflight_errors and operations:
        prefix = f"setting change batch {batch_label} " if batch_label else "setting change proposal "
        log_app_warning(
            root,
            "memory_repair_preflight_rejected",
            workflow="proposal_preflight",
            stage=stage,
            change_kind=change_kind or repair_decision.change_kind,
            batch_label=batch_label,
            target_files=target_files,
            operation_count=len(operations),
            preflight_errors=preflight_errors,
        )
        raise MemoryRepairError(
            prefix
            + "failed target schema preflight or semantic preflight: "
            + _format_preflight_errors(preflight_errors)
        )
    target_files = sorted({*target_files, *(operation.file for operation in operations)})
    repair_decision = repair_decision.model_copy(
        update={
            "target_files": target_files,
            "operations": operations,
            "notes": notes,
        }
    )
    return _PreparedMemoryRepairDecision(
        decision=repair_decision,
        target_files=target_files,
        operations=operations,
        notes=notes,
        change_kind=change_kind or repair_decision.change_kind,
    )


def suggest_setting_change(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    stage: MemoryChangeStage = "unknown",
    session_id: str | None = None,
    chapter_number: int | None = None,
    audit_issue_ids: list[str] | None = None,
) -> MemoryRepairSuggestResult:
    root = root.resolve()
    request = user_request.strip()
    if not request:
        raise MemoryRepairError("setting change request must not be empty")
    batch_plan = generate_memory_change_batch_plan(
        root,
        request,
        provider_name=provider_name,
        provider=provider,
        stage=stage,
    )
    prepared_batches: list[_PreparedMemoryRepairDecision] = []
    for batch in batch_plan.batches:
        batch_files = _target_files_for_batch(batch)
        batch_request = _batch_memory_repair_request(request, batch)
        try:
            batch_decision = generate_memory_repair_decision(
                root,
                batch_request,
                provider_name=provider_name,
                provider=provider,
                change_kind="setting_change",
                stage=stage,
                target_files=batch_files,
            )
        except Exception as exc:
            raise MemoryRepairError(f"setting change batch {batch.batch_id} failed: {exc}") from exc
        prepared_batches.append(
            _prepare_memory_repair_decision(
                root,
                batch_request,
                batch_decision,
                decision_was_provided=False,
                provider_name=provider_name,
                provider=provider,
                change_kind="setting_change",
                stage=stage,
                prompt_target_files=batch_files,
                batch_label=batch.batch_id,
            )
        )
    merged_decision = _merge_batched_memory_repair_decisions(batch_plan, prepared_batches, stage=stage)
    return suggest_memory_repair(
        root,
        request,
        provider_name=provider_name,
        provider=provider,
        decision=merged_decision,
        change_kind="setting_change",
        stage=stage,
        session_id=session_id,
        chapter_number=chapter_number,
        audit_issue_ids=audit_issue_ids,
    )


def suggest_setting_change_interactive(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    stage: MemoryChangeStage = "unknown",
    session_id: str | None = None,
    chapter_number: int | None = None,
    audit_issue_ids: list[str] | None = None,
    max_clarification_rounds: int = 3,
) -> SettingChangeSuggestionResult:
    root = root.resolve()
    request = user_request.strip()
    if not request:
        raise MemoryRepairError("setting change request must not be empty")
    decision = generate_memory_change_clarification_decision(
        root,
        request,
        provider_name=provider_name,
        provider=provider,
        stage=stage,
        conversation_turns=[
            MemoryChangeConversationTurn(role="user", content=request, created_at=_utc_now()),
        ],
    )
    if decision.status == "needs_clarification" and max_clarification_rounds > 0:
        clarification = _new_clarification_session(
            root,
            request,
            decision=decision,
            stage=stage,
            session_id=session_id,
            chapter_number=chapter_number,
            audit_issue_ids=audit_issue_ids or [],
        )
        return SettingChangeSuggestionResult(status="needs_clarification", clarification=clarification)
    proposal_result = suggest_setting_change(
        root,
        request,
        provider_name=provider_name,
        provider=provider,
        stage=stage,
        session_id=session_id,
        chapter_number=chapter_number,
        audit_issue_ids=audit_issue_ids,
    )
    return SettingChangeSuggestionResult(status="proposal_ready", proposal_result=proposal_result)


def answer_setting_change_clarification(
    root: Path,
    clarification_id: str,
    answer: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    max_clarification_rounds: int = 3,
) -> SettingChangeSuggestionResult:
    root = root.resolve()
    clean_answer = answer.strip()
    if not clean_answer:
        raise MemoryRepairError("setting change clarification answer must not be empty")
    clarification = load_setting_change_clarification(root, clarification_id)
    if clarification.status != "needs_clarification":
        raise MemoryRepairError(f"setting change clarification is not waiting for input: {clarification_id}")
    now = _utc_now()
    turns = [
        *clarification.conversation_turns,
        MemoryChangeConversationTurn(role="user", content=clean_answer, created_at=now),
    ]
    combined_request = _combined_setting_change_request(clarification.original_request, turns)
    decision = generate_memory_change_clarification_decision(
        root,
        combined_request,
        provider_name=provider_name,
        provider=provider,
        stage=clarification.stage,
        conversation_turns=turns,
    )
    user_answer_count = sum(1 for turn in turns if turn.role == "user") - 1
    if decision.status == "needs_clarification" and user_answer_count < max_clarification_rounds:
        clarification.conversation_turns = [
            *turns,
            MemoryChangeConversationTurn(
                role="agent",
                content="\n".join(decision.questions),
                created_at=_utc_now(),
            ),
        ]
        clarification.questions = decision.questions
        clarification.updated_at = _utc_now()
        _write_clarification_session(root, clarification)
        return SettingChangeSuggestionResult(status="needs_clarification", clarification=clarification)
    if decision.status == "needs_clarification":
        proposal_result = _no_op_setting_change_proposal(
            root,
            combined_request,
            stage=clarification.stage,
            session_id=clarification.session_id,
            chapter_number=clarification.chapter_number,
            audit_issue_ids=clarification.audit_issue_ids,
            notes=[
                "设定变更澄清已达到最大轮数，仍无法安全定位可应用 patch。",
                *decision.questions,
                *decision.notes,
            ],
        )
    else:
        proposal_result = suggest_setting_change(
            root,
            combined_request,
            provider_name=provider_name,
            provider=provider,
            stage=clarification.stage,
            session_id=clarification.session_id,
            chapter_number=clarification.chapter_number,
            audit_issue_ids=clarification.audit_issue_ids,
        )
    clarification.status = "proposal_ready"
    clarification.proposal_path = str(proposal_result.proposal_path.relative_to(root))
    clarification.questions = []
    clarification.updated_at = _utc_now()
    clarification.conversation_turns = turns
    _write_clarification_session(root, clarification)
    return SettingChangeSuggestionResult(status="proposal_ready", proposal_result=proposal_result, clarification=clarification)


def load_setting_change_clarification(root: Path, clarification_id: str) -> MemoryChangeClarificationSession:
    return load_json_model(_clarification_path(root.resolve(), clarification_id), MemoryChangeClarificationSession)


def generate_memory_change_clarification_decision(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    stage: MemoryChangeStage = "unknown",
    conversation_turns: list[MemoryChangeConversationTurn] | None = None,
) -> MemoryChangeClarificationDecision:
    request = user_request.strip()
    if provider is None and provider_name.lower() == "mock":
        return mock_memory_change_clarification_decision(request)
    repair_provider = provider or create_agent_provider(
        default_agent_config_path(root),
        "orchestrator",
        overrides=ProviderOverrides(provider_name=provider_name),
    )
    user_prompt = _memory_change_clarification_user_prompt(
        root,
        request,
        stage=stage,
        conversation_turns=conversation_turns or [],
    )
    model_request = ModelRequest(
        system_prompt=load_prompt_template("memory_change_clarification_system"),
        user_prompt=user_prompt,
        json_schema_name="MemoryChangeClarificationDecision",
    )
    contract = AgentOutputContract(
        output_kind="json",
        target_name="MemoryChangeClarificationDecision",
        json_schema_name="MemoryChangeClarificationDecision",
        allow_user_questions=False,
    )
    try:
        return generate_json_with_repair(
            repair_provider,
            model_request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="orchestrator",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_change_clarification",
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="orchestrator",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_change_clarification_repair",
            ),
            contract=contract,
            parse=parse_memory_change_clarification_decision,
            repair_prompt=lambda invalid_output, error: _structured_decision_repair_prompt(
                schema_name="MemoryChangeClarificationDecision",
                original_prompt=user_prompt,
                invalid_output=invalid_output,
                error=error,
            ),
        )
    except (AgentOutputContractError, JsonRepairExhaustedError) as exc:
        log_app_warning(
            root,
            "memory_repair_fallback",
            workflow="clarification",
            stage=stage,
            error_type=exc.__class__.__name__,
            error=str(exc),
        )
        return _fallback_clarification_decision(f"provider returned invalid clarification decision: {exc}")


def parse_memory_change_clarification_decision(content: str) -> MemoryChangeClarificationDecision:
    try:
        raw = extract_json_object(content)
    except JsonExtractionError as exc:
        raise MemoryRepairError("provider response did not contain a JSON object") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryChangeClarificationDecision JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MemoryRepairError("provider returned MemoryChangeClarificationDecision as a non-object JSON value")
    data = dict(data)
    data["source"] = data.get("source") or "model"
    data["questions"] = _normalize_string_list(data.get("questions"))
    data["assumptions"] = _normalize_string_list(data.get("assumptions"))
    data["notes"] = _normalize_string_list(data.get("notes"))
    try:
        return MemoryChangeClarificationDecision.model_validate(data)
    except ValidationError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryChangeClarificationDecision: {exc}") from exc


def generate_memory_change_batch_plan(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    stage: MemoryChangeStage = "unknown",
) -> MemoryChangeBatchPlan:
    request = user_request.strip()
    if provider is None and provider_name.lower() == "mock":
        return mock_memory_change_batch_plan(request, stage=stage)
    repair_provider = provider or create_agent_provider(
        default_agent_config_path(root),
        "orchestrator",
        overrides=ProviderOverrides(provider_name=provider_name),
    )
    user_prompt = _memory_change_batch_plan_user_prompt(root, request, stage=stage)
    model_request = ModelRequest(
        system_prompt=load_prompt_template("memory_change_batch_plan_system"),
        user_prompt=user_prompt,
        json_schema_name="MemoryChangeBatchPlan",
    )
    contract = AgentOutputContract(
        output_kind="json",
        target_name="MemoryChangeBatchPlan",
        json_schema_name="MemoryChangeBatchPlan",
        allow_user_questions=False,
    )
    try:
        return generate_json_with_repair(
            repair_provider,
            model_request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="orchestrator",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_change_batch_plan",
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="orchestrator",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_change_batch_plan_repair",
            ),
            contract=contract,
            parse=parse_memory_change_batch_plan,
            repair_prompt=lambda invalid_output, error: _structured_decision_repair_prompt(
                schema_name="MemoryChangeBatchPlan",
                original_prompt=user_prompt,
                invalid_output=invalid_output,
                error=error,
            ),
        )
    except JsonRepairExhaustedError as exc:
        raise MemoryRepairError(f"setting change batch planner returned invalid output: {exc}") from exc.second_error
    except Exception as exc:
        raise MemoryRepairError(f"setting change batch planner failed: {exc}") from exc


def parse_memory_change_batch_plan(content: str) -> MemoryChangeBatchPlan:
    try:
        raw = extract_json_object(content)
    except JsonExtractionError as exc:
        raise MemoryRepairError("provider response did not contain a JSON object") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryChangeBatchPlan JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MemoryRepairError("provider returned MemoryChangeBatchPlan as a non-object JSON value")
    if "operations" in data:
        raise MemoryRepairError("MemoryChangeBatchPlan must not include operations")
    batches = data.get("batches")
    if isinstance(batches, list):
        normalized_batches: list[object] = []
        for index, batch in enumerate(batches):
            if isinstance(batch, dict) and "operations" in batch:
                raise MemoryRepairError(f"MemoryChangeBatchPlan batch {index + 1} must not include operations")
            if not isinstance(batch, dict):
                normalized_batches.append(batch)
                continue
            normalized_batches.append(_normalize_memory_change_batch_data(batch, index=index))
        data = dict(data)
        data["batches"] = normalized_batches
    else:
        data = dict(data)
    data["assumptions"] = _normalize_string_list(data.get("assumptions"))
    data["notes"] = _normalize_string_list(data.get("notes"))
    data["source"] = data.get("source") or "model"
    try:
        plan = MemoryChangeBatchPlan.model_validate(data)
    except ValidationError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryChangeBatchPlan: {exc}") from exc
    _validate_memory_change_batch_plan(plan)
    return plan


def _normalize_memory_change_batch_data(batch: dict[str, object], *, index: int) -> dict[str, object]:
    normalized = dict(batch)
    normalized["target_files"] = _normalize_string_list(normalized.get("target_files"))
    normalized["domains"] = _normalize_string_list(normalized.get("domains"))
    instruction = normalized.get("instruction")
    if not isinstance(instruction, str):
        instruction_parts = _normalize_string_list(instruction)
        if instruction_parts:
            normalized["instruction"] = "\n".join(instruction_parts)
    if not isinstance(normalized.get("batch_id"), str) or not str(normalized.get("batch_id")).strip():
        normalized["batch_id"] = f"batch_{index + 1}"
    if not isinstance(normalized.get("reason"), str) or not str(normalized.get("reason")).strip():
        candidates = [
            *_normalize_string_list(normalized.get("notes")),
            *_normalize_string_list(normalized.get("assumptions")),
        ]
        instruction_text = normalized.get("instruction")
        if isinstance(instruction_text, str) and instruction_text.strip():
            candidates.append(instruction_text.strip())
        normalized["reason"] = candidates[0] if candidates else f"按第 {index + 1} 个批次生成设定变更。"
    return normalized


def generate_memory_repair_decision(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
    target_files: list[str] | None = None,
) -> MemoryRepairDecision:
    request = user_request.strip()
    if provider is None and provider_name.lower() == "mock":
        return mock_memory_repair_decision(
            root,
            request,
            change_kind=change_kind,
            stage=stage,
            target_files=target_files,
        )
    repair_provider = provider or create_agent_provider(
        default_agent_config_path(root),
        "orchestrator",
        overrides=ProviderOverrides(provider_name=provider_name),
    )
    user_prompt = _memory_repair_user_prompt(root, request, change_kind=change_kind, stage=stage, target_files=target_files)
    model_request = ModelRequest(
        system_prompt=load_prompt_template("memory_repair_system"),
        user_prompt=user_prompt,
        json_schema_name="MemoryRepairDecision",
    )
    contract = AgentOutputContract(
        output_kind="json",
        target_name="MemoryRepairDecision",
        json_schema_name="MemoryRepairDecision",
        allow_user_questions=False,
    )
    try:
        return generate_json_with_repair(
            repair_provider,
            model_request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="orchestrator",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_repair_decision",
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="orchestrator",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_repair_decision_repair",
            ),
            contract=contract,
            parse=parse_memory_repair_decision,
            repair_prompt=lambda invalid_output, error: _repair_decision_repair_prompt(
                original_prompt=user_prompt,
                invalid_output=invalid_output,
                error=error,
            ),
        )
    except AgentOutputContractError as exc:
        log_app_warning(
            root,
            "memory_repair_fallback",
            workflow="decision",
            stage=stage,
            change_kind=change_kind,
            error_type=exc.__class__.__name__,
            error=str(exc),
        )
        return _empty_memory_repair_decision("provider output violated MemoryRepairDecision contract")
    except JsonRepairExhaustedError as exc:
        log_app_warning(
            root,
            "memory_repair_fallback",
            workflow="decision",
            stage=stage,
            change_kind=change_kind,
            error_type=exc.__class__.__name__,
            error=str(exc.second_error),
        )
        return _empty_memory_repair_decision(f"provider returned invalid MemoryRepairDecision: {exc.second_error}")


def _repair_memory_repair_decision_target_schema(
    root: Path,
    user_request: str,
    *,
    invalid_decision: MemoryRepairDecision,
    preflight_errors: list[str],
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
    target_files: list[str] | None = None,
) -> MemoryRepairDecision:
    request = user_request.strip()
    repair_provider = provider or create_agent_provider(
        default_agent_config_path(root),
        "orchestrator",
        overrides=ProviderOverrides(provider_name=provider_name),
    )
    original_prompt = _memory_repair_user_prompt(root, request, change_kind=change_kind, stage=stage, target_files=target_files)
    try:
        content = generate_with_output_guard(
            repair_provider,
            ModelRequest(
                system_prompt=load_prompt_template("memory_repair_system"),
                user_prompt=_target_schema_repair_prompt(
                    original_prompt=original_prompt,
                    invalid_decision=invalid_decision,
                    preflight_errors=preflight_errors,
                ),
                json_schema_name="MemoryRepairDecision",
            ),
            root=root,
            invocation=AgentInvocationContext(
                agent_name="orchestrator",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_repair_target_schema_repair",
            ),
            contract=AgentOutputContract(
                output_kind="json",
                target_name="MemoryRepairDecision",
                json_schema_name="MemoryRepairDecision",
                allow_user_questions=False,
            ),
        )
    except AgentOutputContractError as exc:
        log_app_warning(
            root,
            "memory_repair_target_schema_repair_failed",
            workflow="target_schema_repair",
            stage=stage,
            change_kind=change_kind,
            preflight_error_count=len(preflight_errors),
            error_type=exc.__class__.__name__,
            error=str(exc),
        )
        raise MemoryRepairError(
            "provider target-schema repair output violated MemoryRepairDecision contract: "
            + ", ".join(exc.reason_codes)
        ) from exc
    try:
        return parse_memory_repair_decision(content)
    except MemoryRepairError as exc:
        log_app_warning(
            root,
            "memory_repair_target_schema_repair_failed",
            workflow="target_schema_repair",
            stage=stage,
            change_kind=change_kind,
            preflight_error_count=len(preflight_errors),
            error_type=exc.__class__.__name__,
            error=str(exc),
        )
        raise MemoryRepairError(f"provider returned invalid target-schema repair decision: {exc}") from exc


def parse_memory_repair_decision(content: str) -> MemoryRepairDecision:
    try:
        raw = extract_json_object(content)
    except JsonExtractionError as exc:
        raise MemoryRepairError("provider response did not contain a JSON object") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryRepairDecision JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MemoryRepairError("provider returned MemoryRepairDecision as a non-object JSON value")
    data = dict(data)
    data = _normalize_memory_repair_decision_data(data)
    data["needs_user_confirmation"] = True
    data["source"] = data.get("source") or "model"
    try:
        return MemoryRepairDecision.model_validate(data)
    except ValidationError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryRepairDecision: {exc}") from exc


def _normalize_memory_repair_decision_data(data: dict[str, object]) -> dict[str, object]:
    data = dict(data)
    data["target_files"] = _normalize_string_list(data.get("target_files"))
    data["assumptions"] = _normalize_string_list(data.get("assumptions"))
    data["notes"] = _normalize_string_list(data.get("notes"))
    operations = data.get("operations")
    if not isinstance(operations, list):
        return data
    normalized_operations: list[object] = []
    for raw_operation in operations:
        if not isinstance(raw_operation, dict):
            normalized_operations.append(raw_operation)
            continue
        normalized_operations.append(_normalize_memory_repair_operation(raw_operation))
    data["operations"] = normalized_operations
    return data


def _normalize_memory_repair_operation(raw_operation: dict[str, object]) -> dict[str, object]:
    operation = dict(raw_operation)
    op = operation.get("op")
    path = operation.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        return operation
    inferred_file = _infer_file_from_pointer_path(path)
    if not isinstance(operation.get("file"), str) or not operation.get("file"):
        if inferred_file:
            operation["file"] = inferred_file
    if op == "add":
        normalized_path = _normalize_add_collection_path(path)
        can_default_reason = normalized_path != path or _is_append_collection_path(normalized_path)
        if normalized_path != path:
            operation["path"] = normalized_path
            if not isinstance(operation.get("file"), str) or not operation.get("file"):
                operation["file"] = _infer_file_from_pointer_path(normalized_path) or inferred_file
        if can_default_reason and (not isinstance(operation.get("reason"), str) or not operation.get("reason")):
            operation["reason"] = "用户要求新增设定；系统根据集合路径补齐操作原因。"
    return operation


def _infer_file_from_pointer_path(path: str) -> str | None:
    parts = [part for part in path.strip("/").split("/") if part]
    if not parts:
        return None
    return POINTER_PATH_FILES.get(_unescape_pointer(parts[0]))


def _normalize_add_collection_path(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) != 2:
        return path
    collection_key = _unescape_pointer(parts[0])
    item_selector = _unescape_pointer(parts[1])
    if collection_key not in COLLECTION_PATH_FILES or item_selector == "-" or item_selector.isdigit():
        return path
    return f"/{_escape_pointer(collection_key)}/-"


def _is_append_collection_path(path: str) -> bool:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) != 2:
        return False
    return _unescape_pointer(parts[0]) in COLLECTION_PATH_FILES and _unescape_pointer(parts[1]) == "-"


def apply_memory_repair(root: Path, proposal_path: Path) -> MemoryRepairApplyResult:
    root = root.resolve()
    proposal = load_json_model(_resolve_proposal_path(root, proposal_path), MemoryRepairProposal)
    backups: list[str] = []
    touched_files: list[str] = []
    apply_log_path = _repair_dir(root, proposal.repair_id) / "apply_log.json"
    try:
        if not proposal.operations:
            raise MemoryRepairError("memory repair proposal has no operations to apply")
        preflight_errors = _preflight_memory_repair_operations(root, proposal.operations, change_kind=proposal.change_kind)
        if preflight_errors:
            log_app_warning(
                root,
                "memory_repair_preflight_rejected",
                workflow="apply_preflight",
                repair_id=proposal.repair_id,
                change_kind=proposal.change_kind,
                target_files=proposal.target_files,
                operation_count=len(proposal.operations),
                preflight_errors=preflight_errors,
            )
            raise MemoryRepairError(
                "memory repair proposal failed target schema preflight or semantic preflight: "
                + _format_preflight_errors(preflight_errors)
            )
        grouped = _group_operations(proposal.operations)
        for rel_path, operations in grouped.items():
            _ensure_allowed_file(rel_path)
            path = root / rel_path
            data = load_json(path)
            updated = _apply_operations_to_data(data, operations)
            _validate_file_model(rel_path, updated)
            backup = backup_file(path, reason="memory_repair")
            backups.append(str(backup.relative_to(root)))
            touched_files.append(rel_path)
            atomic_write_json(path, updated)
        report = validate_project(root)
        if not report.ok:
            raise MemoryRepairError(
                "memory repair produced validation errors: "
                + "; ".join(message.message for message in report.errors)
            )
        apply_log = MemoryRepairApplyLog(
            repair_id=proposal.repair_id,
            applied_at=_utc_now(),
            status="applied",
            target_files=touched_files,
            backups=backups,
        )
        atomic_write_model_json(apply_log_path, apply_log)
        record_management_event(
            root,
            "memory_repair_applied",
            f"项目管家已应用记忆修复：{proposal.repair_id}",
            source="orchestrator",
            target_files=touched_files,
            status="success",
            details={
                "repair_id": proposal.repair_id,
                "change_kind": proposal.change_kind,
                "affected_chapters": proposal.impact.affected_chapters if proposal.impact else [],
                "affected_sessions": proposal.impact.affected_sessions if proposal.impact else [],
                "stale_chapters": proposal.impact.stale_chapters if proposal.impact else [],
            },
        )
        return MemoryRepairApplyResult(proposal=proposal, apply_log=apply_log, apply_log_path=apply_log_path)
    except Exception as exc:
        _restore_backups(root, touched_files, backups)
        log_app_warning(
            root,
            "memory_repair_apply_rolled_back" if backups else "memory_repair_apply_failed",
            workflow="apply",
            repair_id=proposal.repair_id,
            change_kind=proposal.change_kind,
            target_files=touched_files or proposal.target_files,
            backups=backups,
            error_type=exc.__class__.__name__,
            error=str(exc),
        )
        apply_log = MemoryRepairApplyLog(
            repair_id=proposal.repair_id,
            applied_at=_utc_now(),
            status="rolled_back" if backups else "failed",
            target_files=touched_files,
            backups=backups,
            errors=[f"{exc.__class__.__name__}: {exc}"],
        )
        atomic_write_model_json(apply_log_path, apply_log)
        record_management_event(
            root,
            "memory_repair_failed",
            f"项目管家记忆修复失败：{proposal.repair_id}",
            source="orchestrator",
            target_files=touched_files or proposal.target_files,
            status="error",
            details={"repair_id": proposal.repair_id, "error": str(exc)},
        )
        raise MemoryRepairError(str(exc)) from exc


def render_memory_repair_markdown(proposal: MemoryRepairProposal) -> str:
    lines = [
        f"# Memory Repair {proposal.repair_id}",
        "",
        f"- Created by: {proposal.created_by}",
        f"- Change kind: {proposal.change_kind}",
        f"- Stage: {proposal.stage}",
        f"- Risk: {proposal.risk_level}",
        f"- Confidence: {proposal.confidence:.2f}",
        f"- Needs confirmation: {proposal.needs_user_confirmation}",
        f"- Request: {proposal.user_request}",
        "",
        "## Target Files",
        "",
    ]
    lines.extend(f"- {path}" for path in proposal.target_files)
    lines.extend(["", "## Operations", ""])
    if proposal.operations:
        for operation in proposal.operations:
            lines.append(f"- `{operation.op}` `{operation.file}` `{operation.path}`: {operation.reason}")
    else:
        lines.append("- 暂无可安全自动应用的操作；请补充具体 ID 或手动编辑 proposal。")
    if proposal.impact:
        lines.extend(["", "## Impact", ""])
        lines.append(f"- Summary: {proposal.impact.summary}")
        lines.append(f"- Domains: {', '.join(proposal.impact.domains) or 'none'}")
        lines.append(f"- Entity IDs: {', '.join(proposal.impact.entity_ids) or 'none'}")
        lines.append(
            "- Affected chapters: "
            + (", ".join(str(number) for number in proposal.impact.affected_chapters) or "none")
        )
        lines.append(f"- Affected sessions: {', '.join(proposal.impact.affected_sessions) or 'none'}")
        lines.append(
            "- Stale accepted chapters: "
            + (", ".join(str(number) for number in proposal.impact.stale_chapters) or "none")
        )
    if proposal.followup_actions:
        lines.extend(["", "## Follow-up Actions", ""])
        for action in proposal.followup_actions:
            chapters = ",".join(str(number) for number in action.chapter_numbers) or "-"
            lines.append(f"- `{action.action}` session={action.session_id or '-'} chapters={chapters}: {action.reason}")
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in proposal.notes)
    return "\n".join(lines) + "\n"


def _sanitize_repair_decision(decision: MemoryRepairDecision) -> tuple[list[str], list[MemoryRepairOperation], list[str]]:
    notes = list(decision.notes)
    target_files = sorted({path for path in decision.target_files if path in ALLOWED_MEMORY_FILES})
    dropped_targets = sorted({path for path in decision.target_files if path not in ALLOWED_MEMORY_FILES})
    if dropped_targets:
        notes.append("已忽略非白名单目标文件：" + ", ".join(dropped_targets))
    operations: list[MemoryRepairOperation] = []
    for operation in decision.operations:
        if operation.file not in ALLOWED_MEMORY_FILES:
            notes.append(f"已忽略非白名单操作目标：{operation.file} {operation.path}")
            continue
        operations.append(operation)
        if operation.file not in target_files:
            target_files.append(operation.file)
    return sorted(target_files), operations, notes


def _drop_unsafe_remove_operations(
    root: Path,
    operations: list[MemoryRepairOperation],
    notes: list[str],
) -> list[MemoryRepairOperation]:
    safe_operations: list[MemoryRepairOperation] = []
    for operation in operations:
        if operation.op != "remove":
            safe_operations.append(operation)
            continue
        entity_ids = _entity_ids_from_operation_path(root, operation)
        referenced = sorted(
            entity_id
            for entity_id in entity_ids
            if _entity_id_has_references(root, entity_id, exclude_file=operation.file)
        )
        if referenced:
            notes.append(
                "已拒绝删除仍被其他 memory/章节/session 文件引用的实体："
                + ", ".join(referenced)
            )
            continue
        safe_operations.append(operation)
    return safe_operations


def _memory_repair_user_prompt(
    root: Path,
    request: str,
    *,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
    target_files: list[str] | None = None,
) -> str:
    allowed_files = _normalize_allowed_target_files(target_files)
    task_note = ""
    if change_kind == "setting_change":
        task_note = (
            "本次任务是 setting_change：允许根据用户明确请求新增、修改或删除人物/背景设定。\n"
            "新增实体时必须生成稳定小写下划线 id，并填齐目标 schema 必填字段。\n"
            "修改必须定位到明确 ID、exact name 或 exact alias；不要做近似联想匹配。\n"
            "无精确匹配且用户没有明确要求替换/删除/合并时，按新增实体处理。\n"
            "删除被引用实体必须同时安全清理引用，否则 operations 留空。\n"
            "每个 operation 必须包含 file、path、reason；array 新增必须使用 /collection/-，不能使用 /characters/{id} 这类路径。\n"
            "不要要求用户提供文件结构、字段、visibility 或 JSON Pointer；这些由当前结构索引决定。\n"
            f"{SETTING_CHANGE_MAPPING_RULES}"
            f"创作阶段：{stage or 'unknown'}。\n\n"
        )
    return (
        "请生成 MemoryRepairDecision JSON。\n"
        f"{task_note}"
        "允许 target_files：\n"
        + "\n".join(f"- {path}" for path in allowed_files)
        + "\n\n"
        "当前文件结构与 JSON Pointer 路径索引：\n"
        f"{_memory_pointer_index(root, target_files=allowed_files)}\n\n"
        "当前可见 ID 摘要：\n"
        f"{_memory_id_summary(root, target_files=allowed_files)}\n\n"
        f"用户请求：\n{request}\n"
    )


def build_memory_repair_user_prompt(
    root: Path,
    request: str,
    *,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
) -> str:
    return _memory_repair_user_prompt(root, request, change_kind=change_kind, stage=stage)


def _memory_change_batch_plan_user_prompt(
    root: Path,
    request: str,
    *,
    stage: MemoryChangeStage,
) -> str:
    return (
        "请生成 MemoryChangeBatchPlan JSON。\n"
        "本次任务是 setting_change 的分批规划：只拆分批次，不生成 operations。\n"
        f"{SETTING_CHANGE_MAPPING_RULES}"
        f"创作阶段：{stage or 'unknown'}。\n\n"
        "允许 target_files：\n"
        + "\n".join(f"- {path}" for path in sorted(ALLOWED_MEMORY_FILES))
        + "\n\n"
        "当前文件结构与 JSON Pointer 路径索引：\n"
        f"{_memory_pointer_index(root)}\n\n"
        "当前可见 ID 摘要：\n"
        f"{_memory_id_summary(root)}\n\n"
        f"用户请求：\n{request}\n"
    )


def _memory_change_clarification_user_prompt(
    root: Path,
    request: str,
    *,
    stage: MemoryChangeStage,
    conversation_turns: list[MemoryChangeConversationTurn],
) -> str:
    transcript = "\n".join(
        f"- {turn.role}: {turn.content}"
        for turn in conversation_turns
    ) or "- user: " + request
    return (
        "请判断本次 setting_change 是否已经足以生成安全的 MemoryRepairProposal。\n"
        "只要用户的创作意图足够明确，就输出 ready；文件、字段、visibility 和 JSON Pointer 映射是系统责任，不是用户责任。\n"
        "如果只是新实体属于 characters/locations/items/world/hidden_truths/foreshadowing 哪类需要系统判断，不要为此追问用户。\n"
        "如果缺少具体新增/修改内容、用户要求替换/删除但目标不唯一，或存在会改变剧情含义的真实创作歧义，才输出 needs_clarification。\n"
        "不要要求用户提供现有文件完整结构、目标文件、字段名、visibility 或 JSON Pointer；现有文件结构和 JSON Pointer 路径索引已经在本 prompt 中提供。\n"
        "不要把新姓名近似联想到现有角色；只有 exact id、exact name 或 exact alias 匹配才视为已有实体。\n"
        f"{SETTING_CHANGE_MAPPING_RULES}"
        f"创作阶段：{stage or 'unknown'}。\n\n"
        "允许 target_files：\n"
        + "\n".join(f"- {path}" for path in sorted(ALLOWED_MEMORY_FILES))
        + "\n\n"
        "当前文件结构与 JSON Pointer 路径索引：\n"
        f"{_memory_pointer_index(root)}\n\n"
        "当前可见 ID 摘要：\n"
        f"{_memory_id_summary(root)}\n\n"
        "对话记录：\n"
        f"{transcript}\n\n"
        f"合并后的用户请求：\n{request}\n"
    )


def _normalize_allowed_target_files(target_files: list[str] | None) -> list[str]:
    if not target_files:
        return sorted(ALLOWED_MEMORY_FILES)
    allowed = [path for path in target_files if path in ALLOWED_MEMORY_FILES]
    return sorted(dict.fromkeys(allowed)) or sorted(ALLOWED_MEMORY_FILES)


def _memory_pointer_index(root: Path, *, target_files: list[str] | None = None) -> str:
    sections: list[str] = []
    for rel_path in _normalize_allowed_target_files(target_files):
        sections.append(_file_pointer_index(root, rel_path))
    return "\n".join(sections)


def _file_pointer_index(root: Path, rel_path: str) -> str:
    path = root / rel_path
    if not path.exists():
        return f"- {rel_path}: missing"
    try:
        data = load_json(path)
    except Exception as exc:
        return f"- {rel_path}: unreadable ({exc.__class__.__name__})"
    lines = [f"- {rel_path}"]
    if isinstance(data, dict):
        lines.append("  top-level keys: " + ", ".join(sorted(str(key) for key in data)))
    collection_key = FILE_COLLECTION_KEYS.get(rel_path)
    if collection_key and isinstance(data, dict):
        collection = data.get(collection_key)
        fields = COLLECTION_FIELD_HINTS.get(rel_path, [])
        lines.append(f"  collection: /{collection_key}")
        lines.append(f"  add new item path: /{collection_key}/-")
        if fields:
            lines.append("  common item fields: " + ", ".join(fields))
        schema_hint = COLLECTION_SCHEMA_HINTS.get(rel_path)
        if schema_hint:
            lines.extend(f"  {line}" for line in schema_hint.splitlines())
        if isinstance(collection, list) and collection:
            detailed_limit = 20
            for index, item in enumerate(collection[:detailed_limit]):
                if not isinstance(item, dict):
                    lines.append(f"  existing[{index}] path: /{collection_key}/{index}")
                    continue
                item_id = item.get("id") if isinstance(item.get("id"), str) else "-"
                name = item.get("name") or item.get("title") or "-"
                item_fields = sorted(str(key) for key in item)
                examples = [
                    f"/{collection_key}/{index}/{field}"
                    for field in item_fields
                    if field != "id"
                ][:8]
                lines.append(f"  existing[{index}]: id={item_id}; name/title={name}; path=/{collection_key}/{index}")
                lines.append("    fields: " + ", ".join(item_fields))
                if examples:
                    lines.append("    replace paths: " + ", ".join(examples))
            if len(collection) > detailed_limit:
                lines.append("  additional existing id/path index:")
                for index, item in enumerate(collection[detailed_limit:], start=detailed_limit):
                    if not isinstance(item, dict):
                        lines.append(f"  existing[{index}] path: /{collection_key}/{index}")
                        continue
                    item_id = item.get("id") if isinstance(item.get("id"), str) else "-"
                    name = item.get("name") or item.get("title") or "-"
                    lines.append(f"  existing[{index}]: id={item_id}; name/title={name}; path=/{collection_key}/{index}")
        else:
            lines.append(f"  existing items: none; use /{collection_key}/- for add")
        return "\n".join(lines)
    if rel_path == "memory/state/current_state.json" and isinstance(data, dict):
        lines.extend(_state_pointer_index(data))
    elif rel_path == "memory/state/timeline.json" and isinstance(data, dict):
        lines.extend(_timeline_pointer_index(data))
    return "\n".join(lines)


def _state_pointer_index(data: dict[str, object]) -> list[str]:
    lines = [
        "  story position paths: /story_position/latest_chapter, /story_position/current_arc",
        "  add state paths: /character_states/-, /item_states/-, /location_states/-",
    ]
    for key in sorted(STATE_COLLECTION_KEYS):
        collection = data.get(key)
        if not isinstance(collection, list):
            continue
        lines.append(f"  collection: /{key}")
        for index, item in enumerate(collection[:20]):
            if not isinstance(item, dict):
                continue
            entity_id = item.get("entity_id") or item.get("id") or "-"
            fields = sorted(str(field) for field in item)
            examples = [f"/{key}/{index}/{field}" for field in fields if field not in {"entity_id", "id"}][:8]
            lines.append(f"  existing[{index}]: entity_id={entity_id}; path=/{key}/{index}")
            if examples:
                lines.append("    replace paths: " + ", ".join(examples))
    return lines


def _timeline_pointer_index(data: dict[str, object]) -> list[str]:
    events = data.get("events")
    lines = [
        "  collection: /events",
        "  add event path: /events/-",
        "  common event fields: id, chapter, summary, narrative_position, story_position, event_role, certainty, causes, effects, state_change_ids",
    ]
    if not isinstance(events, list) or not events:
        lines.append("  existing events: none; use /events/- for add")
        return lines
    for index, item in enumerate(events[:40]):
        if not isinstance(item, dict):
            continue
        event_id = item.get("id") if isinstance(item.get("id"), str) else "-"
        summary = item.get("summary") if isinstance(item.get("summary"), str) else "-"
        fields = sorted(str(field) for field in item)
        examples = [f"/events/{index}/{field}" for field in fields if field != "id"][:8]
        lines.append(f"  existing[{index}]: id={event_id}; summary={summary}; path=/events/{index}")
        if examples:
            lines.append("    replace paths: " + ", ".join(examples))
    return lines


def _memory_id_summary(root: Path, *, target_files: list[str] | None = None) -> str:
    lines: list[str] = []
    for rel_path in _normalize_allowed_target_files(target_files):
        path = root / rel_path
        if not path.exists():
            lines.append(f"- {rel_path}: missing")
            continue
        try:
            data = load_json(path)
        except Exception as exc:
            lines.append(f"- {rel_path}: unreadable ({exc.__class__.__name__})")
            continue
        ids = _collect_ids(data)
        if ids:
            lines.append(f"- {rel_path}: " + ", ".join(ids[:40]))
        else:
            lines.append(f"- {rel_path}: no explicit ids found")
    return "\n".join(lines)


def _collect_ids(value: object) -> list[str]:
    found: list[str] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            item_id = node.get("id")
            if isinstance(item_id, str) and item_id not in found:
                found.append(item_id)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return found


def _repair_decision_repair_prompt(*, original_prompt: str, invalid_output: str, error: str) -> str:
    return (
        f"{original_prompt}\n\n"
        "上一次输出不能被解析为 MemoryRepairDecision。\n"
        f"错误：{error}\n\n"
        "请重新只输出 JSON object。不要 Markdown 或解释。\n"
        "修复规则：\n"
        "- 每个 operation 必须包含 op、file、path、reason。\n"
        "- 如果是新增数组条目，path 必须使用 /collection/-，例如 /characters/-、/hidden_truths/-、/foreshadowing_threads/-。\n"
        "- 如果上次输出使用 /characters/{id}、/hidden_truths/{id}、/foreshadowing_threads/{id} 这类新增路径，请改成对应 /collection/-，并保留 value.id。\n"
        "- 如果缺少 file，但 path 能唯一映射到允许文件，请补齐 file。\n"
        "- 不要要求用户提供现有文件结构、目标文件、字段、visibility 或 JSON Pointer；原始 prompt 已提供这些结构上下文。\n"
        "- 只有创作意图本身缺失、替换/删除目标不唯一或删除风险无法安全处理时，operations 才能为空。\n"
        f"上一次输出：\n{invalid_output[:3000]}\n"
    )


def _structured_decision_repair_prompt(
    *,
    schema_name: str,
    original_prompt: str,
    invalid_output: str,
    error: str,
) -> str:
    return (
        f"{original_prompt}\n\n"
        f"上一次输出不能被解析为 {schema_name}。\n"
        f"错误：{error}\n\n"
        "请重新只输出 JSON object，不要 Markdown 或解释。\n"
        "不要新增 schema 未定义字段，不要向用户或上游 Agent 提问。\n"
        f"上一次输出：\n{invalid_output[:3000]}\n"
    )


def _target_schema_repair_prompt(
    *,
    original_prompt: str,
    invalid_decision: MemoryRepairDecision,
    preflight_errors: list[str],
) -> str:
    invalid_json = json.dumps(invalid_decision.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return (
        f"{original_prompt}\n\n"
        "上一次输出已经可以解析为 MemoryRepairDecision，但把 operations 应用到目标 memory/canon 文件后没有通过目标文件 schema/semantic preflight。\n"
        "preflight 失败可能来自 file、path 或 value；本次修复必须同时修正非法 path 和非法 value，而不只是补齐 op/file/path/reason。\n"
        "请重新只输出修复后的 MemoryRepairDecision JSON object。不要 Markdown 或解释。\n"
        "修复规则：\n"
        "- 只修改下方 preflight 错误直接涉及的 operation；未被错误涉及的 operation 必须原样保留，包括 file、path、op、value、reason。\n"
        "- 保留用户创作意图；只有安全且存在的 file/path 才能保留，同时修正 value 的字段类型、嵌套对象和 enum。\n"
        "- 如果错误提示 replace path does not exist，说明 path 不存在；必须改到原始 prompt 中列出的 existing replace paths，或清空该 operation 并在 notes 写明原因。\n"
        "- add 到集合时，value 必须是对应集合元素的完整对象，且满足上方 strict add value schema。\n"
        "- visibility 只能是 reader_visible、hidden 或 partially_revealed；importance 只能是 low、medium、high 或 critical。\n"
        "- abilities、secrets、rules、special_properties 必须是对象数组，不要使用字符串数组。\n"
        "- planned_reveal 和 planned_payoff 必须是对象或 null，不要使用字符串。\n"
        "- introduced_in_chapter 必须是整数；如果用户说“开篇”，默认使用 1。\n"
        "- Location 顶层没有 description 字段；地点公开描述写 reader_visible_summary，隐藏/作者私有说明写 private_author_notes，地点规则写 rules[]；不要使用 /locations/{i}/description。\n"
        "- Character.role 只能表示叙事角色；默认使用主角、主要人物、配角、次要人物。家族身份、门派身份、排行、职业/江湖身份必须移入 tags，并可保留在 summary/notes。\n"
        "- 不要把谢家长女、谢家次子、张家幼女、唐门二房之女、江湖散人、武当俗家弟子这类身份短语写入 Character.role。\n"
        "- reader_visible_summary 只能写读者可见信息；如果错误提示 hidden truth appears in reader_visible_summary，必须把隐藏内容移到 private_author_notes 或 hidden_truths.json，不要放在 reader_visible_summary。\n"
        "- 如果错误提示 add would duplicate existing ... at /collection/index 或 duplicate ... id，说明该实体已经存在；"
        "不要保留 add /collection/-，请改成对应已有 path 的 replace（字段级 replace 优先），"
        "或在无法确定时清空 operations 并在 notes 写明原因。\n"
        "- 如果仍无法安全修复，operations 置空并在 notes 中写明 target schema 缺失信息；不要向用户提问。\n\n"
        "目标 schema preflight 错误 / semantic preflight 错误：\n"
        f"{_format_preflight_errors(preflight_errors, max_chars=6000)}\n\n"
        f"上一次 MemoryRepairDecision：\n{invalid_json}\n"
    )


def _empty_memory_repair_decision(note: str) -> MemoryRepairDecision:
    return MemoryRepairDecision(
        target_files=[],
        operations=[],
        confidence=0.0,
        assumptions=[],
        needs_user_confirmation=True,
        notes=[note, "没有生成可安全自动应用的 patch；请提供具体 event/entity id 或手动编辑 proposal。"],
        source="fallback",
    )


def _validate_memory_change_batch_plan(plan: MemoryChangeBatchPlan) -> None:
    if plan.change_kind != "setting_change":
        raise MemoryRepairError("MemoryChangeBatchPlan.change_kind must be setting_change")
    for batch in plan.batches:
        _target_files_for_batch(batch)


def _target_files_for_batch(batch: MemoryChangeBatch) -> list[str]:
    target_files = [path for path in batch.target_files if path in ALLOWED_MEMORY_FILES]
    invalid_files = sorted({path for path in batch.target_files if path not in ALLOWED_MEMORY_FILES})
    if invalid_files:
        raise MemoryRepairError(
            f"MemoryChangeBatch {batch.batch_id} targets non-allowed file(s): " + ", ".join(invalid_files)
        )
    for domain in batch.domains:
        rel_path = DOMAIN_FILES.get(domain)
        if rel_path:
            target_files.append(rel_path)
    target_files = sorted(dict.fromkeys(target_files))
    if not target_files:
        raise MemoryRepairError(f"MemoryChangeBatch {batch.batch_id} has no allowed target_files")
    return target_files


def _batch_memory_repair_request(original_request: str, batch: MemoryChangeBatch) -> str:
    return (
        "原始设定变更请求：\n"
        f"{original_request}\n\n"
        f"当前批次：{batch.batch_id}\n"
        f"批次原因：{batch.reason}\n"
        "本批次只生成下列领域/文件相关 operations；不要处理其他批次内容。\n"
        f"domains: {', '.join(batch.domains) or 'none'}\n"
        f"target_files: {', '.join(_target_files_for_batch(batch))}\n\n"
        "本批次具体指令：\n"
        f"{batch.instruction}\n"
    )


def _merge_batched_memory_repair_decisions(
    plan: MemoryChangeBatchPlan,
    prepared_batches: list[_PreparedMemoryRepairDecision],
    *,
    stage: MemoryChangeStage,
) -> MemoryRepairDecision:
    operations: list[MemoryRepairOperation] = []
    target_files: list[str] = []
    domains: list[MemoryChangeDomain] = [domain for batch in plan.batches for domain in batch.domains]
    notes: list[str] = []
    assumptions: list[str] = list(plan.assumptions)
    followups: list[MemoryChangeFollowupAction] = []
    confidences = [plan.confidence]
    notes.extend(plan.notes)
    notes.append(f"已按 {len(plan.batches)} 个批次生成设定变更建议，并合并为单个 proposal。")
    for batch, prepared in zip(plan.batches, prepared_batches):
        decision = prepared.decision
        batch_prefix = f"批次 {batch.batch_id}"
        operations.extend(prepared.operations)
        target_files.extend(prepared.target_files)
        domains.extend(decision.domains)
        domains.extend(_domains_from_files(prepared.target_files))
        assumptions.extend(decision.assumptions)
        followups.extend(decision.followup_actions)
        confidences.append(decision.confidence)
        if prepared.operations:
            notes.append(f"{batch_prefix} 生成 {len(prepared.operations)} 条 operations。")
        else:
            notes.append(f"{batch_prefix} 未生成可安全自动应用的 operations。")
        notes.extend(prepared.notes)
    confidence_values = [value for value in confidences if value > 0]
    confidence = min(confidence_values) if confidence_values else 0.0
    return MemoryRepairDecision(
        change_kind="setting_change",
        target_files=sorted(set(target_files)),
        operations=operations,
        domains=_dedupe_domains(domains),
        stage=stage or plan.stage or "unknown",
        followup_actions=followups,
        confidence=confidence,
        assumptions=_dedupe_preserve_order(assumptions),
        needs_user_confirmation=True,
        notes=_dedupe_preserve_order(notes),
        source=plan.source,
    )


def _fallback_clarification_decision(note: str) -> MemoryChangeClarificationDecision:
    return MemoryChangeClarificationDecision(
        status="needs_clarification",
        questions=["请补充目标设定的名称或 ID，以及希望改成的具体内容。"],
        confidence=0.0,
        assumptions=[],
        notes=[note],
        source="fallback",
    )


def _new_clarification_session(
    root: Path,
    request: str,
    *,
    decision: MemoryChangeClarificationDecision,
    stage: MemoryChangeStage,
    session_id: str | None,
    chapter_number: int | None,
    audit_issue_ids: list[str],
) -> MemoryChangeClarificationSession:
    now = _utc_now()
    clarification = MemoryChangeClarificationSession(
        clarification_id=_new_clarification_id(),
        original_request=request,
        stage=stage,
        session_id=session_id,
        chapter_number=chapter_number,
        audit_issue_ids=audit_issue_ids,
        status="needs_clarification",
        questions=decision.questions,
        conversation_turns=[
            MemoryChangeConversationTurn(role="user", content=request, created_at=now),
            MemoryChangeConversationTurn(role="agent", content="\n".join(decision.questions), created_at=now),
        ],
        created_at=now,
        updated_at=now,
    )
    _write_clarification_session(root, clarification)
    return clarification


def _write_clarification_session(root: Path, clarification: MemoryChangeClarificationSession) -> None:
    atomic_write_model_json(_clarification_path(root, clarification.clarification_id), clarification)


def _no_op_setting_change_proposal(
    root: Path,
    request: str,
    *,
    stage: MemoryChangeStage,
    session_id: str | None,
    chapter_number: int | None,
    audit_issue_ids: list[str],
    notes: list[str],
) -> MemoryRepairSuggestResult:
    decision = MemoryRepairDecision(
        change_kind="setting_change",
        target_files=[],
        operations=[],
        domains=[],
        stage=stage,
        confidence=0.0,
        assumptions=[],
        needs_user_confirmation=True,
        notes=notes,
        source="fallback",
    )
    return suggest_memory_repair(
        root,
        request,
        provider_name="mock",
        decision=decision,
        change_kind="setting_change",
        stage=stage,
        session_id=session_id,
        chapter_number=chapter_number,
        audit_issue_ids=audit_issue_ids,
    )


def _combined_setting_change_request(original_request: str, turns: list[MemoryChangeConversationTurn]) -> str:
    answer_lines = [
        f"{index}. {turn.content}"
        for index, turn in enumerate(turns, start=1)
        if turn.role == "user" and turn.content != original_request
    ]
    if not answer_lines:
        return original_request
    return (
        "原始设定变更请求：\n"
        f"{original_request}\n\n"
        "用户补充信息：\n"
        + "\n".join(answer_lines)
    )


def _entity_id_has_references(root: Path, entity_id: str, *, exclude_file: str) -> bool:
    for path in _impact_scan_paths(root):
        rel_path = _safe_rel(root, path)
        if rel_path == exclude_file:
            continue
        try:
            if entity_id in path.read_text(encoding="utf-8"):
                return True
        except Exception:
            continue
    return False


def _dedupe_domains(domains: list[MemoryChangeDomain]) -> list[MemoryChangeDomain]:
    return sorted(set(domains))


def _domains_from_files(target_files: list[str]) -> list[MemoryChangeDomain]:
    return [domain for path in target_files if (domain := FILE_DOMAINS.get(path))]


def _analyze_memory_change_impact(
    root: Path,
    operations: list[MemoryRepairOperation],
    *,
    domains: list[MemoryChangeDomain],
    session_id: str | None,
    chapter_number: int | None,
) -> MemoryChangeImpact:
    entity_ids = _affected_entity_ids(root, operations)
    affected_files: set[str] = {operation.file for operation in operations}
    affected_chapters: set[int] = set()
    affected_sessions: set[str] = set()
    reference_count = 0

    if chapter_number and chapter_number > 0:
        affected_chapters.add(chapter_number)
    if session_id:
        affected_sessions.add(session_id)

    for chapter in _chapters_from_timeline_operations(root, operations):
        affected_chapters.add(chapter)

    if entity_ids:
        for path in _impact_scan_paths(root):
            rel_path = _safe_rel(root, path)
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if not any(entity_id in text for entity_id in entity_ids):
                continue
            reference_count += 1
            affected_files.add(rel_path)
            chapter = _chapter_number_from_path(path)
            if chapter:
                affected_chapters.add(chapter)
            session = _session_id_from_path(path)
            if session:
                affected_sessions.add(session)

    for session in _sessions_referencing_chapters(root, affected_chapters):
        affected_sessions.add(session)

    stale_chapters = sorted(chapter for chapter in affected_chapters if _chapter_is_accepted(root, chapter))
    risk_level: MemoryRepairRiskLevel = "low"
    if any(operation.op == "remove" for operation in operations) or stale_chapters:
        risk_level = "high"
    elif affected_chapters or len(affected_files) > len({operation.file for operation in operations}):
        risk_level = "medium"
    summary = _impact_summary(entity_ids, affected_chapters, affected_sessions, stale_chapters)
    return MemoryChangeImpact(
        domains=domains,
        entity_ids=sorted(entity_ids),
        affected_files=sorted(affected_files),
        affected_chapters=sorted(affected_chapters),
        affected_sessions=sorted(affected_sessions),
        stale_chapters=stale_chapters,
        risk_level=risk_level,
        reference_count=reference_count,
        summary=summary,
    )


def _memory_change_followups(
    root: Path,
    impact: MemoryChangeImpact,
    *,
    stage: MemoryChangeStage,
    session_id: str | None,
    chapter_number: int | None,
    change_kind: MemoryChangeKind,
) -> list[MemoryChangeFollowupAction]:
    if change_kind != "setting_change":
        return []
    actions: list[MemoryChangeFollowupAction] = []
    session_data = _safe_session_data(root, session_id) if session_id else None
    session_chapters = _session_chapters(session_data)
    affected_chapters = sorted(set(impact.affected_chapters) | ({chapter_number} if chapter_number else set()))
    if session_data:
        status = str(session_data.get("status") or "")
        outline_status = str(session_data.get("outline_status") or "")
        content_status = str(session_data.get("content_status") or "")
        if status in {"accepted", "archived"} or content_status in {"accepted", "archived"}:
            actions.append(
                MemoryChangeFollowupAction(
                    action="start_revision_session",
                    session_id=session_id,
                    chapter_numbers=session_chapters or affected_chapters,
                    auto=False,
                    reason="当前 session 已认可或归档；设定变更不会静默改写既有章节。",
                )
            )
        elif content_status == "not_started":
            actions.append(
                MemoryChangeFollowupAction(
                    action="revise_outline",
                    session_id=session_id,
                    chapter_numbers=session_chapters or affected_chapters,
                    auto=True,
                    reason="设定变更发生在大纲阶段，需要基于最新 memory 重生成 outline proposal。",
                )
            )
            if outline_status == "approved":
                actions.append(
                    MemoryChangeFollowupAction(
                        action="reapprove_outline",
                        session_id=session_id,
                        chapter_numbers=session_chapters or affected_chapters,
                        auto=False,
                        reason="原大纲已批准，重生成后需要用户重新批准。",
                    )
                )
        elif content_status in {"needs_user_review", "needs_revision"} or stage == "content_review":
            actions.extend(
                [
                    MemoryChangeFollowupAction(
                        action="revise_content",
                        session_id=session_id,
                        chapter_numbers=session_chapters or affected_chapters,
                        auto=True,
                        reason="设定变更影响审查中的正文，需要走现有修订链路。",
                    ),
                    MemoryChangeFollowupAction(
                        action="reaudit_chapters",
                        session_id=session_id,
                        chapter_numbers=session_chapters or affected_chapters,
                        auto=True,
                        reason="正文同步设定变更后必须重新 Audit。",
                    ),
                    MemoryChangeFollowupAction(
                        action="rebuild_state_proposal",
                        session_id=session_id,
                        chapter_numbers=session_chapters or affected_chapters,
                        auto=True,
                        reason="正文重审通过后必须重建 state/timeline proposal。",
                    ),
                ]
            )
        else:
            actions.append(
                MemoryChangeFollowupAction(
                    action="manual_review",
                    session_id=session_id,
                    chapter_numbers=session_chapters or affected_chapters,
                    auto=False,
                    reason="当前 session 状态不适合自动同步设定变更。",
                )
            )
    for stale_chapter in impact.stale_chapters:
        actions.append(
            MemoryChangeFollowupAction(
                action="start_revision_session",
                chapter_numbers=[stale_chapter],
                auto=False,
                reason="已 accepted 的章节只标记为受影响，需要用户显式发起修订 session。",
            )
        )
    if not actions:
        actions.append(
            MemoryChangeFollowupAction(
                action="none",
                chapter_numbers=affected_chapters,
                auto=False,
                reason="未发现需要自动同步的当前 session；后续创作会读取最新设定。",
            )
        )
    return actions


def _affected_entity_ids(root: Path, operations: list[MemoryRepairOperation]) -> set[str]:
    ids: set[str] = set()
    for operation in operations:
        if isinstance(operation.value, dict):
            for key in ("id", "entity_id", "hidden_truth_id"):
                value = operation.value.get(key)
                if isinstance(value, str) and value:
                    ids.add(value)
        if isinstance(operation.value, list):
            ids.update(item for item in operation.value if isinstance(item, str) and _looks_like_entity_id(item))
        ids.update(_entity_ids_from_operation_path(root, operation))
    return ids


def _entity_ids_from_operation_path(root: Path, operation: MemoryRepairOperation) -> set[str]:
    path = root / operation.file
    if not path.exists():
        return set()
    try:
        data = load_json(path)
    except Exception:
        return set()
    parts = [_unescape_pointer(part) for part in operation.path.strip("/").split("/") if part]
    if not parts:
        return set()
    ids: set[str] = set()
    collection_key = FILE_COLLECTION_KEYS.get(operation.file)
    if collection_key and parts[0] == collection_key and len(parts) >= 2:
        entity = _list_entity_at(data, collection_key, parts[1])
        ids.update(_ids_from_entity(entity))
    elif operation.file == "memory/state/current_state.json" and parts[0] in STATE_COLLECTION_KEYS and len(parts) >= 2:
        entity = _list_entity_at(data, parts[0], parts[1])
        ids.update(_ids_from_entity(entity))
    elif operation.file == "memory/state/timeline.json" and parts[0] == "events" and len(parts) >= 2:
        entity = _list_entity_at(data, "events", parts[1])
        ids.update(_ids_from_entity(entity))
    return ids


def _list_entity_at(data: object, collection_key: str, index_text: str) -> object | None:
    if not isinstance(data, dict):
        return None
    collection = data.get(collection_key)
    if not isinstance(collection, list) or not index_text.isdigit():
        return None
    index = int(index_text)
    if index < 0 or index >= len(collection):
        return None
    return collection[index]


def _ids_from_entity(entity: object | None) -> set[str]:
    if not isinstance(entity, dict):
        return set()
    ids: set[str] = set()
    for key in ("id", "entity_id", "hidden_truth_id"):
        value = entity.get(key)
        if isinstance(value, str) and value:
            ids.add(value)
    for key in ("related_entity_ids", "participant_ids", "known_by_character_ids", "state_change_ids"):
        value = entity.get(key)
        if isinstance(value, list):
            ids.update(item for item in value if isinstance(item, str) and _looks_like_entity_id(item))
    return ids


def _chapters_from_timeline_operations(root: Path, operations: list[MemoryRepairOperation]) -> set[int]:
    chapters: set[int] = set()
    timeline_path = root / "memory/state/timeline.json"
    if not timeline_path.exists():
        return chapters
    try:
        timeline = load_json(timeline_path)
    except Exception:
        return chapters
    for operation in operations:
        if operation.file != "memory/state/timeline.json":
            continue
        parts = [_unescape_pointer(part) for part in operation.path.strip("/").split("/") if part]
        if len(parts) < 2 or parts[0] != "events":
            continue
        event = _list_entity_at(timeline, "events", parts[1])
        if isinstance(event, dict):
            chapter = _coerce_positive_int(event.get("chapter"))
            narrative = event.get("narrative_position")
            if not chapter and isinstance(narrative, dict):
                chapter = _coerce_positive_int(narrative.get("chapter"))
            if chapter:
                chapters.add(chapter)
    return chapters


def _impact_scan_paths(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for base in (root / "memory" / "canon", root / "memory" / "state", root / "memory" / "chapters", root / "memory" / "sessions"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in SCANNED_IMPACT_SUFFIXES:
                candidates.append(path)
    return candidates


def _chapter_number_from_path(path: Path) -> int | None:
    for part in path.parts:
        if re.fullmatch(r"[0-9]{3}", part):
            return int(part)
    return None


def _session_id_from_path(path: Path) -> str | None:
    for part in path.parts:
        if re.fullmatch(r"session_[0-9]{8}_[0-9]{6}_[0-9]{6}", part):
            return part
    return None


def _sessions_referencing_chapters(root: Path, chapters: set[int]) -> set[str]:
    if not chapters:
        return set()
    sessions_dir = root / "memory" / "sessions"
    if not sessions_dir.exists():
        return set()
    sessions: set[str] = set()
    for session_json in sessions_dir.glob("session_*/session.json"):
        try:
            data = load_json(session_json)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        chapter_range = {
            number for item in data.get("chapter_range", []) if (number := _coerce_positive_int(item))
        }
        if chapter_range & chapters:
            sessions.add(session_json.parent.name)
    return sessions


def _chapter_is_accepted(root: Path, chapter_number: int) -> bool:
    metadata_path = root / "memory" / "chapters" / f"{chapter_number:03d}" / "metadata.json"
    if not metadata_path.exists():
        return False
    try:
        data = load_json(metadata_path)
    except Exception:
        return False
    return isinstance(data, dict) and data.get("status") == "accepted"


def _safe_session_data(root: Path, session_id: str | None) -> dict[str, object] | None:
    if not session_id:
        return None
    path = root / "memory" / "sessions" / session_id / "session.json"
    if not path.exists():
        return None
    try:
        data = load_json(path)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _session_chapters(session_data: dict[str, object] | None) -> list[int]:
    if not session_data:
        return []
    chapters: list[int] = []
    chapter_range = session_data.get("chapter_range", [])
    if not isinstance(chapter_range, list):
        return []
    for item in chapter_range:
        number = _coerce_positive_int(item)
        if number:
            chapters.append(number)
    return sorted(set(chapters))


def _impact_summary(
    entity_ids: set[str],
    affected_chapters: set[int],
    affected_sessions: set[str],
    stale_chapters: list[int],
) -> str:
    parts = []
    if entity_ids:
        parts.append(f"涉及实体 {', '.join(sorted(entity_ids))}")
    if affected_chapters:
        parts.append("影响章节 " + ", ".join(str(number) for number in sorted(affected_chapters)))
    if affected_sessions:
        parts.append("影响 session " + ", ".join(sorted(affected_sessions)))
    if stale_chapters:
        parts.append("其中已认可章节 " + ", ".join(str(number) for number in stale_chapters))
    return "；".join(parts) if parts else "未发现章节或 session 引用；后续创作会使用最新 memory。"


def _coerce_positive_int(value: object) -> int | None:
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _looks_like_entity_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]*", value))


def _safe_rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _proposal_notes(
    target_files: list[str],
    operations: list[MemoryRepairOperation],
    decision_notes: list[str],
    *,
    change_kind: MemoryChangeKind,
    audit_issue_ids: list[str],
) -> list[str]:
    notes = ["项目管家只生成 proposal，不会静默修改正式 memory 文件。"]
    if change_kind == "setting_change":
        notes.append("设定变更会先进行影响分析；已 accepted/archived 章节不会被静默改写。")
    if audit_issue_ids:
        notes.append("该 proposal 来源于 Audit issue：" + ", ".join(audit_issue_ids))
    notes.extend(decision_notes)
    if not operations:
        notes.append("没有足够信息生成可安全自动应用的 patch；请在请求中提供具体 event/entity id。")
    if "memory/state/timeline.json" in target_files:
        notes.append("timeline 修复应区分 narrative_position 与 story_position。")
    return notes


def _normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    normalized.append(text)
            elif item is not None:
                normalized.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
        return normalized
    return [json.dumps(value, ensure_ascii=False, sort_keys=True)]


def _preview_operations(root: Path, proposal: MemoryRepairProposal) -> dict[str, object]:
    preview: dict[str, object] = {}
    for rel_path, operations in _group_operations(proposal.operations).items():
        try:
            preview[rel_path] = _apply_operations_to_data(load_json(root / rel_path), operations)
        except Exception as exc:
            preview[rel_path] = {"error": str(exc)}
    return preview


def _preflight_memory_repair_operations(
    root: Path,
    operations: list[MemoryRepairOperation],
    *,
    change_kind: MemoryChangeKind | None = None,
) -> list[str]:
    if not operations:
        return []
    contract_errors = _preflight_operation_contract_errors(operations)
    if contract_errors:
        return contract_errors
    errors: list[str] = []
    try:
        grouped = _group_operations(operations)
    except Exception as exc:
        return [str(exc)]
    for rel_path, file_operations in grouped.items():
        try:
            data = load_json(root / rel_path)
            updated = _apply_operations_to_data(data, file_operations)
            _validate_file_model(rel_path, updated)
            errors.extend(_preflight_unique_collection_id_errors(rel_path, updated))
        except Exception as exc:
            errors.append(f"{rel_path}: {exc}")
    if change_kind == "setting_change":
        errors.extend(_preflight_setting_change_add_id_conflicts(root, operations))
        errors.extend(_preflight_setting_change_semantics(operations))
        errors.extend(_preflight_hidden_truth_reader_visible_leaks(root, operations))
    return errors


def _preflight_operation_contract_errors(operations: list[MemoryRepairOperation]) -> list[str]:
    errors: list[str] = []
    for operation in operations:
        if operation.op in {"add", "replace"} and "value" not in operation.model_fields_set:
            errors.append(
                f"{operation.file} {operation.path}: {operation.op} operation must include value; "
                "use explicit null only when null is the intended value"
            )
    return errors


def _restore_regressed_existing_add_operations(
    root: Path,
    previous_operations: list[MemoryRepairOperation],
    operations: list[MemoryRepairOperation],
) -> tuple[list[MemoryRepairOperation], list[str]]:
    previous_replace_operations = _existing_replace_operations_by_entity_id(root, previous_operations)
    if not previous_replace_operations:
        return operations, []
    current_replace_keys = {
        key
        for operation in operations
        if operation.op == "replace"
        for key in [_existing_replace_operation_key(root, operation)]
        if key is not None
    }
    restored: list[MemoryRepairOperation] = []
    restored_keys: set[tuple[str, str]] = set()
    notes: list[str] = []
    for operation in operations:
        add_key = _duplicate_existing_add_operation_key(root, operation)
        if add_key is None or add_key not in previous_replace_operations:
            restored.append(operation)
            continue
        if add_key not in current_replace_keys and add_key not in restored_keys:
            restored.extend(previous_replace_operations[add_key])
        restored_keys.add(add_key)
    if restored_keys:
        restored_labels = ", ".join(f"{rel_path} {entity_id}" for rel_path, entity_id in sorted(restored_keys))
        notes.append("已还原 target-schema repair 退化的重复新增操作：" + restored_labels)
    return restored, notes


def _existing_replace_operations_by_entity_id(
    root: Path,
    operations: list[MemoryRepairOperation],
) -> dict[tuple[str, str], list[MemoryRepairOperation]]:
    grouped: dict[tuple[str, str], list[MemoryRepairOperation]] = {}
    for operation in operations:
        if operation.op != "replace":
            continue
        key = _existing_replace_operation_key(root, operation)
        if key is None:
            continue
        grouped.setdefault(key, []).append(operation)
    return grouped


def _existing_replace_operation_key(root: Path, operation: MemoryRepairOperation) -> tuple[str, str] | None:
    collection_info = UNIQUE_ID_COLLECTIONS.get(operation.file)
    if collection_info is None:
        return None
    collection_key, _label = collection_info
    parts = _pointer_parts(operation.path)
    if len(parts) < 2 or parts[0] != collection_key or not parts[1].isdigit():
        return None
    item_id = _operation_existing_collection_item_id(root, operation, collection_key, int(parts[1]))
    if item_id is None:
        return None
    existing_indexes = _existing_collection_id_index(root, operation.file, collection_key)
    if item_id not in existing_indexes:
        return None
    return (operation.file, item_id)


def _duplicate_existing_add_operation_key(root: Path, operation: MemoryRepairOperation) -> tuple[str, str] | None:
    if operation.op != "add" or not isinstance(operation.value, dict):
        return None
    collection_info = UNIQUE_ID_COLLECTIONS.get(operation.file)
    if collection_info is None:
        return None
    collection_key, _label = collection_info
    if _pointer_parts(operation.path) != [collection_key, "-"]:
        return None
    item_id = operation.value.get("id")
    if not isinstance(item_id, str) or not item_id:
        return None
    existing_indexes = _existing_collection_id_index(root, operation.file, collection_key)
    if item_id not in existing_indexes:
        return None
    return (operation.file, item_id)


def _operation_existing_collection_item_id(
    root: Path,
    operation: MemoryRepairOperation,
    collection_key: str,
    index: int,
) -> str | None:
    if isinstance(operation.value, dict):
        item_id = operation.value.get("id")
        if isinstance(item_id, str) and item_id:
            return item_id
    try:
        data = load_json(root / operation.file)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    collection = data.get(collection_key)
    if not isinstance(collection, list) or index >= len(collection):
        return None
    item = collection[index]
    if not isinstance(item, dict):
        return None
    item_id = item.get("id")
    return item_id if isinstance(item_id, str) and item_id else None


def _existing_collection_id_index(root: Path, rel_path: str, collection_key: str) -> dict[str, int]:
    try:
        data = load_json(root / rel_path)
    except Exception:
        return {}
    return _collection_id_index(data, collection_key)


def _preflight_unique_collection_id_errors(rel_path: str, data: object) -> list[str]:
    collection_info = UNIQUE_ID_COLLECTIONS.get(rel_path)
    if collection_info is None or not isinstance(data, dict):
        return []
    collection_key, label = collection_info
    collection = data.get(collection_key)
    if not isinstance(collection, list):
        return []
    seen: dict[str, int] = {}
    errors: list[str] = []
    for index, item in enumerate(collection):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str):
            continue
        if item_id in seen:
            errors.append(
                f"{rel_path}: duplicate {label}: {item_id} at /{collection_key}/{index}; "
                f"first occurrence at /{collection_key}/{seen[item_id]}"
            )
            continue
        seen[item_id] = index
    return errors


def _preflight_setting_change_add_id_conflicts(root: Path, operations: list[MemoryRepairOperation]) -> list[str]:
    errors: list[str] = []
    cached_existing_indexes: dict[str, dict[str, int]] = {}
    for operation in operations:
        if operation.op != "add" or not isinstance(operation.value, dict):
            continue
        collection_info = UNIQUE_ID_COLLECTIONS.get(operation.file)
        if collection_info is None:
            continue
        collection_key, label = collection_info
        parts = _pointer_parts(operation.path)
        if parts != [collection_key, "-"]:
            continue
        item_id = operation.value.get("id")
        if not isinstance(item_id, str) or not item_id:
            continue
        if operation.file not in cached_existing_indexes:
            try:
                data = load_json(root / operation.file)
            except Exception:
                cached_existing_indexes[operation.file] = {}
            else:
                cached_existing_indexes[operation.file] = _collection_id_index(data, collection_key)
        existing_index = cached_existing_indexes[operation.file].get(item_id)
        if existing_index is None:
            continue
        errors.append(
            f"{operation.file} {operation.path}: add would duplicate existing {label}: {item_id} "
            f"at /{collection_key}/{existing_index}; use replace with the existing path instead of add"
        )
    return errors


def _collection_id_index(data: object, collection_key: str) -> dict[str, int]:
    if not isinstance(data, dict):
        return {}
    collection = data.get(collection_key)
    if not isinstance(collection, list):
        return {}
    indexes: dict[str, int] = {}
    for index, item in enumerate(collection):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id not in indexes:
            indexes[item_id] = index
    return indexes


def _auto_repair_setting_change_semantics(
    root: Path,
    operations: list[MemoryRepairOperation],
    preflight_errors: list[str],
    *,
    change_kind: MemoryChangeKind | None,
) -> tuple[list[MemoryRepairOperation], list[str], list[str]]:
    if change_kind != "setting_change" or not preflight_errors:
        return operations, [], preflight_errors
    operations, notes = _auto_repair_character_identity_tags(operations, preflight_errors)
    if not notes:
        return operations, [], preflight_errors
    updated_errors = _preflight_memory_repair_operations(root, operations, change_kind=change_kind)
    return operations, notes, updated_errors


def _auto_repair_character_identity_tags(
    operations: list[MemoryRepairOperation],
    preflight_errors: list[str],
) -> tuple[list[MemoryRepairOperation], list[str]]:
    if not any("Character identity phrase(s) must be in tags" in error for error in preflight_errors):
        return operations, []
    repaired: list[MemoryRepairOperation] = []
    note_details: list[str] = []
    for operation in operations:
        parts = _pointer_parts(operation.path)
        if (
            operation.file != "memory/canon/characters.json"
            or operation.op not in {"add", "replace"}
            or len(parts) != 2
            or parts[0] != "characters"
            or not isinstance(operation.value, dict)
        ):
            repaired.append(operation)
            continue
        value = json.loads(json.dumps(operation.value, ensure_ascii=False))
        tags = _string_values(value.get("tags"))
        missing_tags = [
            phrase
            for phrase in _character_identity_phrases_from_fields(value)
            if phrase not in tags
        ]
        if not missing_tags:
            repaired.append(operation)
            continue
        value["tags"] = [*tags, *missing_tags]
        repaired.append(operation.model_copy(update={"value": value}))
        label = _operation_semantic_location(operation)
        note_details.append(f"{label}: " + ", ".join(missing_tags))
    if not note_details:
        return operations, []
    return repaired, ["已本地补齐 Character.tags 中缺失的身份短语：" + "；".join(note_details)]


def _preflight_hidden_truth_reader_visible_leaks(
    root: Path,
    operations: list[MemoryRepairOperation],
) -> list[str]:
    try:
        data_by_file = _memory_data_after_operations(root, operations)
    except Exception:
        return []
    hidden_truths = _collection_items(data_by_file.get("memory/canon/hidden_truths.json"), "hidden_truths")
    if not hidden_truths:
        return []
    visible_sources: list[tuple[str, str, str]] = []
    for rel_path, collection_key in (
        ("memory/canon/characters.json", "characters"),
        ("memory/canon/locations.json", "locations"),
        ("memory/canon/items.json", "items"),
    ):
        for item in _collection_items(data_by_file.get(rel_path), collection_key):
            item_id = item.get("id")
            summary = item.get("reader_visible_summary")
            if isinstance(item_id, str) and isinstance(summary, str):
                visible_sources.append((rel_path, item_id, summary))
    errors: list[str] = []
    for truth in hidden_truths:
        truth_id = truth.get("id")
        fragments = [
            fragment.strip()
            for fragment in (truth.get("description"), truth.get("title"))
            if isinstance(fragment, str) and fragment.strip()
        ]
        if not isinstance(truth_id, str) or not fragments:
            continue
        for rel_path, entity_id, summary in visible_sources:
            for fragment in fragments:
                if fragment in summary:
                    errors.append(
                        f"{rel_path}: hidden truth {truth_id} appears in reader_visible_summary for {entity_id}. "
                        "Move hidden information into private_author_notes or hidden_truths.json only."
                    )
                    break
    return errors


def _memory_data_after_operations(root: Path, operations: list[MemoryRepairOperation]) -> dict[str, object]:
    data_by_file: dict[str, object] = {
        rel_path: load_json(root / rel_path)
        for rel_path in ALLOWED_MEMORY_FILES
        if (root / rel_path).exists()
    }
    for rel_path, file_operations in _group_operations(operations).items():
        data_by_file[rel_path] = _apply_operations_to_data(data_by_file[rel_path], file_operations)
    return data_by_file


def _collection_items(data: object, collection_key: str) -> list[dict[str, object]]:
    if not isinstance(data, dict):
        return []
    collection = data.get(collection_key)
    if not isinstance(collection, list):
        return []
    return [item for item in collection if isinstance(item, dict)]


def _preflight_setting_change_semantics(operations: list[MemoryRepairOperation]) -> list[str]:
    errors: list[str] = []
    for operation in operations:
        parts = _pointer_parts(operation.path)
        if operation.file == "memory/canon/characters.json":
            errors.extend(_preflight_character_setting_change_semantics(operation, parts))
        elif operation.file == "memory/canon/locations.json":
            errors.extend(_preflight_location_setting_change_semantics(operation, parts))
    return errors


def _preflight_character_setting_change_semantics(
    operation: MemoryRepairOperation,
    parts: list[str],
) -> list[str]:
    if len(parts) < 2 or parts[0] != "characters":
        return []
    location = _operation_semantic_location(operation)
    if operation.op in {"add", "replace"} and len(parts) == 2 and isinstance(operation.value, dict):
        return _preflight_character_role_semantics(operation.value, location)
    if operation.op in {"add", "replace"} and len(parts) == 3 and parts[2] == "role" and isinstance(operation.value, str):
        return _preflight_character_role_value(operation.value, location)
    return []


def _preflight_location_setting_change_semantics(
    operation: MemoryRepairOperation,
    parts: list[str],
) -> list[str]:
    if len(parts) == 3 and parts[0] == "locations" and parts[2] == "description":
        base_path = f"/locations/{parts[1]}"
        return [
            f"{_operation_semantic_location(operation)}: Location has no top-level description field. "
            f"Use {base_path}/reader_visible_summary for public location description, "
            f"{base_path}/private_author_notes for hidden/author-only notes, or {base_path}/rules for explicit rules."
        ]
    return []


def _preflight_character_role_semantics(character: dict[str, object], location: str) -> list[str]:
    errors = _preflight_character_role_value(character.get("role"), location)
    tags = _string_values(character.get("tags"))
    identity_phrases = _character_identity_phrases_from_fields(character)
    missing_tags = [phrase for phrase in identity_phrases if phrase not in tags]
    if missing_tags:
        errors.append(
            f"{location}: Character identity phrase(s) must be in tags, not only summary/notes/role: "
            + ", ".join(missing_tags[:8])
        )
    return errors


def _preflight_character_role_value(value: object, location: str) -> list[str]:
    if not isinstance(value, str):
        return []
    role = value.strip()
    if not role:
        return []
    if role.lower() in NARRATIVE_CHARACTER_ROLES:
        return []
    phrases = _identity_phrases(role)
    if not phrases:
        return []
    return [
        f"{location}: Character.role semantic preflight failed: role={role!r} looks like identity/rank/profession, "
        "but role must be narrative role only. Use 主角/主要人物/配角/次要人物 or compatible legacy protagonist/supporting/minor/antagonist, "
        "and move identity phrase(s) into tags: "
        + ", ".join(phrases[:8])
    ]


def _character_identity_phrases_from_fields(character: dict[str, object]) -> list[str]:
    phrases: list[str] = []
    for key in ("role", "reader_visible_summary", "private_author_notes"):
        value = character.get(key)
        if isinstance(value, str):
            phrases.extend(_identity_phrases(value))
    return _dedupe_preserve_order(phrases)


def _identity_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for pattern in CHARACTER_ROLE_IDENTITY_PATTERNS:
        for match in pattern.finditer(text):
            phrase = next((group for group in reversed(match.groups()) if group), match.group(0)).strip()
            phrases.append(_trim_identity_phrase(phrase))
    return _dedupe_preserve_order(phrase for phrase in phrases if phrase and phrase.lower() not in NARRATIVE_CHARACTER_ROLES)


def _trim_identity_phrase(phrase: str) -> str:
    cleaned = phrase.strip()
    for marker in ("身为", "作为", "是", "为", "乃"):
        if marker in cleaned:
            cleaned = cleaned.rsplit(marker, 1)[-1].strip()
    return cleaned


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _operation_semantic_location(operation: MemoryRepairOperation) -> str:
    label = f"{operation.file} {operation.path}"
    if isinstance(operation.value, dict):
        item_id = operation.value.get("id")
        name = operation.value.get("name")
        details = [str(value) for value in (item_id, name) if isinstance(value, str) and value]
        if details:
            label += f" ({'/'.join(details)})"
    return label


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _format_preflight_errors(errors: list[str], *, max_chars: int = 10000) -> str:
    text = "\n".join(f"- {error}" for error in errors)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n... truncated ..."


def _group_operations(operations: list[MemoryRepairOperation]) -> dict[str, list[MemoryRepairOperation]]:
    grouped: dict[str, list[MemoryRepairOperation]] = {}
    for operation in operations:
        _ensure_allowed_file(operation.file)
        grouped.setdefault(operation.file, []).append(operation)
    return grouped


def _ensure_allowed_file(rel_path: str) -> None:
    if rel_path not in ALLOWED_MEMORY_FILES:
        raise MemoryRepairError(f"memory repair target is not allowed: {rel_path}")


def _validate_file_model(rel_path: str, data: object) -> None:
    model = ALLOWED_MEMORY_FILES[rel_path]
    try:
        model.model_validate(data)
    except ValidationError as exc:
        raise MemoryRepairError(f"schema validation failed for {rel_path}: {exc}") from exc


def _resolve_proposal_path(root: Path, proposal_path: Path) -> Path:
    path = proposal_path if proposal_path.is_absolute() else root / proposal_path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise MemoryRepairError("repair proposal must be inside the project workspace") from exc
    if path.name != "proposal.json" or "memory/repairs" not in str(path.relative_to(root)):
        raise MemoryRepairError("repair proposal must be memory/repairs/{repair_id}/proposal.json")
    return path


def _repair_dir(root: Path, repair_id: str) -> Path:
    return root / "memory" / "repairs" / repair_id


def _clarification_dir(root: Path, clarification_id: str) -> Path:
    if not re.fullmatch(r"clarify_[0-9]{8}_[0-9]{6}_[0-9]{6}", clarification_id):
        raise MemoryRepairError("invalid setting change clarification id")
    return root / "memory" / "repairs" / "clarifications" / clarification_id


def _clarification_path(root: Path, clarification_id: str) -> Path:
    path = _clarification_dir(root, clarification_id) / "session.json"
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise MemoryRepairError("setting change clarification must be inside the project workspace") from exc
    return resolved


def _new_repair_id() -> str:
    return "repair_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _new_clarification_id() -> str:
    return "clarify_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
