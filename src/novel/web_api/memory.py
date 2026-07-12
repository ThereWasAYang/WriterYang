from __future__ import annotations

from .deps import (
    Path,
    CanonAppliedProposalRecord,
    load_canon_applied_proposals,
)
from novel.core.contracts import (
    CanonApplyCommand,
    CanonSuggestCommand,
    InspirationGenerateCommand,
    SettingChangeAnswerCommand,
    SettingChangeApplyCommand,
    SettingChangeSuggestCommand,
)

from .common import (
    WebAPIError,
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
    source_text = _optional_string(data.get("text")) or _optional_string(data.get("instruction"))
    if not source_text:
        raise WebAPIError("invalid_request", "inspiration text must not be empty", status=400)
    return _dispatch_web_command(
        data,
        InspirationGenerateCommand(
            source_text=source_text,
            source_type="web_text",
            write_json=bool(data.get("write_json")),
            overwrite=bool(data.get("force")),
            allow_default_placeholder=True,
            provider_name=_optional_string(data.get("provider")) or "config",
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
        ),
    )


def _canon_suggest(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    output = _optional_string(data.get("output"))
    output_path = output or _relative(root, _default_canon_proposal_path(root))
    return _dispatch_web_command(
        data,
        CanonSuggestCommand(
            output_path=output_path,
            provider_name=_optional_string(data.get("provider")) or "config",
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
        ),
    )


def _canon_apply(data: dict[str, object]) -> dict[str, object]:
    proposal_file = _optional_string(data.get("proposal_file")) or _optional_string(data.get("proposal_path"))
    if not proposal_file:
        raise WebAPIError("invalid_request", "proposal_file is required", status=400)
    return _dispatch_web_command(
        data,
        CanonApplyCommand(proposal_path=proposal_file),
        confirmed=True,
    )


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
