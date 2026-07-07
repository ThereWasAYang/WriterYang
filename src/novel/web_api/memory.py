# mypy: ignore-errors
# ruff: noqa: F403,F405
from __future__ import annotations

from .deps import *
from .common import *
from .inspection import _management_event_summary
from .session import _session_result_payload

def _inspire(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    provider = load_inspiration_provider(root, str(data.get("provider") or "config"))
    source_text = _optional_string(data.get("text")) or _optional_string(data.get("instruction"))
    if not source_text:
        raise WebAPIError("invalid_request", "inspiration text must not be empty", status=400)
    inspiration_path = root / "memory" / "inspiration.md"
    overwrite = bool(data.get("force")) or is_default_inspiration_placeholder(inspiration_path)
    result = run_inspiration_agent(
        InspirationOptions(
            root=root,
            source_text=source_text,
            source_type="web_text",
            write_json=bool(data.get("write_json")),
            overwrite=overwrite,
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
        ),
        provider,
    )
    return {
        "markdown_path": str(result.markdown_path),
        "json_path": str(result.json_path) if result.json_path else None,
    }


def _canon_suggest(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    provider = load_canon_provider(root, str(data.get("provider") or "config"))
    output = _optional_string(data.get("output"))
    output_path = _safe_workspace_file(root, output) if output else _default_canon_proposal_path(root)
    result = suggest_canon(
        CanonSuggestOptions(
            root=root,
            output_path=output_path,
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
        ),
        provider,
    )
    return {
        "output_path": str(result.output_path) if result.output_path else None,
        "relative_path": _relative(root, result.output_path) if result.output_path else None,
        "proposal": result.proposal.model_dump(mode="json"),
    }


def _canon_apply(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    proposal_file = _optional_string(data.get("proposal_file")) or _optional_string(data.get("proposal_path"))
    if not proposal_file:
        raise WebAPIError("invalid_request", "proposal_file is required", status=400)
    proposal_path = _safe_workspace_file(root, proposal_file)
    result = apply_canon_proposal(root, proposal_path)
    return {
        "proposal_path": str(proposal_path),
        "apply_log": result.apply_log.model_dump(mode="json"),
        "apply_log_path": str(result.apply_log_path),
        "apply_log_relative_path": _relative(root, result.apply_log_path),
        "proposal_snapshot_path": str(result.proposal_snapshot_path),
        "proposal_snapshot_relative_path": _relative(root, result.proposal_snapshot_path),
        "validation_ok": result.validation_report.ok,
        "errors": [message.message for message in result.validation_report.errors],
        "warnings": [message.message for message in result.validation_report.warnings],
    }


def _canon_applied_proposals(query: dict[str, str]) -> dict[str, object]:
    root = _root_from_query(query)
    _require_workspace(root)
    limit = _optional_int(query.get("limit")) or 20
    return {
        "applied_proposals": [
            _canon_applied_proposal_payload(root, record)
            for record in load_canon_applied_proposals(root, limit=limit)
        ]
    }


def _canon_applied_proposal_payload(root: Path, record: CanonAppliedProposalRecord) -> dict[str, object]:
    log = record.apply_log
    return {
        "id": log.id,
        "apply_log_path": _relative(root, record.apply_log_path),
        "original_proposal_path": log.original_proposal_path,
        "proposal_snapshot_path": log.proposal_snapshot_path,
        "target_files": log.target_files,
        "proposal_counts": log.proposal_counts.model_dump(mode="json"),
        "validation_warning_count": log.validation_warning_count,
        "applied_at": log.applied_at.isoformat(),
        "status": log.status,
    }


def _settings_change_suggest(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    request = _optional_string(data.get("request")) or _optional_string(data.get("instruction"))
    if not request:
        raise WebAPIError("invalid_request", "request is required", status=400)
    provider = _optional_string(data.get("provider")) or "config"
    audit_issue_ids = _string_list(data.get("audit_issue_ids"))
    result = suggest_setting_change_interactive(
        root,
        request,
        provider_name=provider,
        stage=_memory_change_stage(data.get("source_stage") or data.get("stage")),
        session_id=_optional_string(data.get("session_id")),
        chapter_number=_optional_int(data.get("chapter_number")) or _optional_int(data.get("chapter")),
        audit_issue_ids=audit_issue_ids,
    )
    return _setting_change_suggestion_payload(root, result)


def _settings_change_answer(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    clarification_id = _optional_string(data.get("clarification_id"))
    answer = _optional_string(data.get("answer")) or _optional_string(data.get("message"))
    if not clarification_id:
        raise WebAPIError("invalid_request", "clarification_id is required", status=400)
    if not answer:
        raise WebAPIError("invalid_request", "answer is required", status=400)
    result = answer_setting_change_clarification(
        root,
        clarification_id,
        answer,
        provider_name=_optional_string(data.get("provider")) or "config",
    )
    return _setting_change_suggestion_payload(root, result)


def _setting_change_suggestion_payload(root: Path, result: SettingChangeSuggestionResult) -> dict[str, object]:
    if result.status == "needs_clarification":
        if result.clarification is None:
            raise WebAPIError("internal_error", "missing clarification result", status=500)
        clarification = result.clarification
        return {
            "status": "needs_clarification",
            "clarification": clarification.model_dump(mode="json"),
            "clarification_id": clarification.clarification_id,
            "questions": clarification.questions,
            "conversation_turns": [turn.model_dump(mode="json") for turn in clarification.conversation_turns],
            "management_events": _management_event_summary(root),
        }
    if result.proposal_result is None:
        raise WebAPIError("internal_error", "missing setting change proposal result", status=500)
    proposal_result = result.proposal_result
    return {
        "status": "proposal_ready",
        "proposal": proposal_result.proposal.model_dump(mode="json"),
        "proposal_path": str(proposal_result.proposal_path),
        "proposal_relative_path": _relative(root, proposal_result.proposal_path),
        "markdown_path": str(proposal_result.markdown_path),
        "markdown_relative_path": _relative(root, proposal_result.markdown_path),
        "management_events": _management_event_summary(root),
    }


def _settings_change_apply(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    proposal_path_text = _optional_string(data.get("proposal_path")) or _optional_string(data.get("proposal_file"))
    if not proposal_path_text:
        raise WebAPIError("invalid_request", "proposal_path is required", status=400)
    proposal_path = _safe_workspace_file(root, proposal_path_text)
    try:
        result = apply_memory_repair(root, proposal_path)
    except MemoryRepairError as exc:
        raise WebAPIError(
            "memory_repair_error",
            str(exc),
            status=400,
            details=_memory_repair_apply_error_details(root, proposal_path),
        ) from exc
    sync_result: dict[str, object] = {"status": "skipped", "reason": "sync_session is false"}
    if bool(data.get("sync_session")):
        sync_result = _sync_setting_change_session(
            root,
            result.proposal,
            session_id=_optional_string(data.get("session_id")),
            provider_name=_optional_string(data.get("provider")) or "config",
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
            polish_mode=_polish_mode(data),
        )
    return {
        "proposal": result.proposal.model_dump(mode="json"),
        "apply_log": result.apply_log.model_dump(mode="json"),
        "apply_log_path": str(result.apply_log_path),
        "apply_log_relative_path": _relative(root, result.apply_log_path),
        "sync_result": sync_result,
        "management_events": _management_event_summary(root),
    }


def _memory_repair_apply_error_details(root: Path, proposal_path: Path) -> dict[str, object]:
    details: dict[str, object] = {}
    try:
        proposal = load_json(proposal_path)
    except Exception:
        return details
    if not isinstance(proposal, dict):
        return details
    repair_id = proposal.get("repair_id")
    if not isinstance(repair_id, str) or not repair_id:
        return details
    details["repair_id"] = repair_id
    apply_log_path = root / "memory" / "repairs" / repair_id / "apply_log.json"
    if not apply_log_path.exists():
        return details
    details["apply_log_path"] = str(apply_log_path)
    details["apply_log_relative_path"] = _relative(root, apply_log_path)
    try:
        apply_log = load_json(apply_log_path)
    except Exception:
        return details
    if isinstance(apply_log, dict):
        if isinstance(apply_log.get("status"), str):
            details["apply_log_status"] = apply_log["status"]
        errors = apply_log.get("errors")
        if isinstance(errors, list):
            details["apply_log_error_count"] = len(errors)
    return details


def _sync_setting_change_session(
    root: Path,
    proposal,
    *,
    session_id: str | None,
    provider_name: str,
    use_search_context: bool,
    use_vector_context: VectorContextMode,
    polish_mode: PolishMode | None,
) -> dict[str, object]:
    if not session_id:
        return {"status": "skipped", "reason": "session_id is missing"}
    try:
        session = load_session(root, session_id)
    except Exception as exc:
        return {"status": "failed", "reason": f"could not load session: {exc}"}
    if session.status in {"accepted", "archived"} or session.content_status in {"accepted", "archived"}:
        return {
            "status": "manual_review",
            "reason": "accepted or archived sessions are not rewritten automatically",
            "session_id": session_id,
        }
    instruction = (
        "设定变更已应用，请基于最新项目 memory 同步当前创作。\n"
        f"原始设定变更请求：{proposal.user_request}\n"
        f"影响分析：{proposal.impact.summary if proposal.impact else '无'}"
    )
    try:
        if session.content_status == "not_started":
            result = revise_outline(
                SessionInstructionOptions(
                    root=root,
                    session_id=session_id,
                    instruction=instruction,
                    provider_name=provider_name,
                    force=True,
                    use_search_context=use_search_context,
                    use_vector_context=use_vector_context,
                    polish_mode=polish_mode,
                )
            )
            return {"status": "synced", "action": "revise_outline", "session": _session_result_payload(result)}
        if session.content_status in {"needs_user_review", "needs_revision"}:
            result = revise_content(
                SessionInstructionOptions(
                    root=root,
                    session_id=session_id,
                    instruction=instruction,
                    provider_name=provider_name,
                    force=True,
                    use_search_context=use_search_context,
                    use_vector_context=use_vector_context,
                    polish_mode=polish_mode,
                )
            )
            return {"status": "synced", "action": "revise_content", "session": _session_result_payload(result)}
        return {
            "status": "manual_review",
            "reason": f"session status {session.status}/{session.content_status} is not safe for automatic sync",
            "session_id": session_id,
        }
    except Exception as exc:
        return {"status": "failed", "reason": str(exc), "session_id": session_id}
