# mypy: ignore-errors
# ruff: noqa: F403,F405
from __future__ import annotations

from .deps import *
from .models import *
from .validation import *
from .preflight import *
from .impact import *
from .generation import *
from .apply import *

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
        created_at=utc_now(),
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
        source="memory_repair",
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
    operations, local_notes = _normalize_setting_change_gender_operations(
        root,
        operations,
        change_kind=resolved_preflight_kind,
    )
    if local_notes:
        notes.extend(local_notes)
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
        operations, local_notes = _normalize_setting_change_gender_operations(
            root,
            operations,
            change_kind=resolved_preflight_kind,
        )
        if local_notes:
            notes.extend(local_notes)
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
            MemoryChangeConversationTurn(role="user", content=request, created_at=utc_now()),
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
    now = utc_now()
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
                created_at=utc_now(),
            ),
        ]
        clarification.questions = decision.questions
        clarification.updated_at = utc_now()
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
    clarification.updated_at = utc_now()
    clarification.conversation_turns = turns
    _write_clarification_session(root, clarification)
    return SettingChangeSuggestionResult(status="proposal_ready", proposal_result=proposal_result, clarification=clarification)


def load_setting_change_clarification(root: Path, clarification_id: str) -> MemoryChangeClarificationSession:
    return load_json_model(_clarification_path(root.resolve(), clarification_id), MemoryChangeClarificationSession)

__all__ = [name for name in globals() if not name.startswith("__")]
