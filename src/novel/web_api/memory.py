from __future__ import annotations

from .deps import (
    Path,
    CanonAppliedProposalRecord,
    CanonSuggestOptions,
    apply_canon_proposal,
    load_canon_applied_proposals,
    load_canon_provider,
    suggest_canon,
    InspirationOptions,
    load_inspiration_provider,
    run_inspiration_agent,
    is_default_inspiration_placeholder,
)
from novel.core.contracts import (
    SettingChangeAnswerCommand,
    SettingChangeApplyCommand,
    SettingChangeSuggestCommand,
)

from .common import (
    WebAPIError,
    _safe_workspace_file,
    _require_workspace,
    _root_from_query,
    _root_from_body,
    _optional_string,
    _string_list,
    _memory_change_stage,
    _vector_context_mode,
    _polish_mode,
    _optional_int,
    _default_canon_proposal_path,
    _dispatch_web_command,
    _relative,
)

from .inspection import _management_event_summary
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
    request = _optional_string(data.get("request")) or _optional_string(data.get("instruction"))
    if not request:
        raise WebAPIError("invalid_request", "request is required", status=400)
    return _enrich_setting_change_payload(
        data,
        _dispatch_web_command(
            data,
            SettingChangeSuggestCommand(
                request=request,
                provider_name=_optional_string(data.get("provider")) or "config",
                stage=_memory_change_stage(data.get("source_stage") or data.get("stage")),
                session_id=_optional_string(data.get("session_id")),
                chapter_number=_optional_int(data.get("chapter_number")) or _optional_int(data.get("chapter")),
                audit_issue_ids=_string_list(data.get("audit_issue_ids")),
            ),
        ),
    )


def _settings_change_answer(data: dict[str, object]) -> dict[str, object]:
    clarification_id = _optional_string(data.get("clarification_id"))
    answer = _optional_string(data.get("answer")) or _optional_string(data.get("message"))
    if not clarification_id:
        raise WebAPIError("invalid_request", "clarification_id is required", status=400)
    if not answer:
        raise WebAPIError("invalid_request", "answer is required", status=400)
    return _enrich_setting_change_payload(
        data,
        _dispatch_web_command(
            data,
            SettingChangeAnswerCommand(
                clarification_id=clarification_id,
                answer=answer,
                provider_name=_optional_string(data.get("provider")) or "config",
            ),
        ),
    )


def _enrich_setting_change_payload(
    data: dict[str, object],
    result: dict[str, object],
) -> dict[str, object]:
    root = _root_from_body(data)
    result["management_events"] = _management_event_summary(root)
    for field in ("proposal_path", "markdown_path", "apply_log_path"):
        value = result.get(field)
        if isinstance(value, str):
            result[f"{field.removesuffix('_path')}_relative_path"] = _relative(root, Path(value))
    return result


def _settings_change_apply(data: dict[str, object]) -> dict[str, object]:
    proposal_path_text = _optional_string(data.get("proposal_path")) or _optional_string(data.get("proposal_file"))
    if not proposal_path_text:
        raise WebAPIError("invalid_request", "proposal_path is required", status=400)
    return _enrich_setting_change_payload(
        data,
        _dispatch_web_command(
            data,
            SettingChangeApplyCommand(
                proposal_path=proposal_path_text,
                sync_session=bool(data.get("sync_session")),
                session_id=_optional_string(data.get("session_id")),
                provider_name=_optional_string(data.get("provider")) or "config",
                use_search_context=bool(data.get("use_search_context", True)),
                use_vector_context=_vector_context_mode(data),
                polish_mode=_polish_mode(data),
            ),
            confirmed=True,
        ),
    )
