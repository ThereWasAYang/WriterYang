from __future__ import annotations

from novel.core.revision_workflow import (
    RevisionActionOptions,
    RevisionRunOptions,
    RevisionSessionResult,
    RevisionStartOptions,
    accept_revision_session,
    list_revision_blocks,
    run_revision_session,
    show_revision_session,
    start_revision_session,
)

from .common import _required_string, _root_from_body, _root_from_query, _vector_context_mode


def _revision_blocks_api(query: dict[str, str]) -> dict[str, object]:
    root = _root_from_query(query)
    chapter_number = int(_required_string(query.get("chapter"), "chapter"))
    return {"chapter_number": chapter_number, "blocks": list_revision_blocks(root, chapter_number)}


def _revision_show_api(query: dict[str, str]) -> dict[str, object]:
    root = _root_from_query(query)
    result = show_revision_session(root, _required_string(query.get("revision_session_id"), "revision_session_id"))
    return _revision_result_payload(result)


def _revision_start(data: dict[str, object]) -> dict[str, object]:
    result = start_revision_session(
        RevisionStartOptions(
            root=_root_from_body(data),
            chapter_number=int(str(data.get("chapter") or 0)),
            start_block=int(str(data.get("start_block") or 0)),
            end_block=int(str(data.get("end_block") or 0)),
            instruction=_required_string(data.get("instruction"), "instruction"),
        )
    )
    return _revision_result_payload(result)


def _revision_run(data: dict[str, object]) -> dict[str, object]:
    result = run_revision_session(
        RevisionRunOptions(
            root=_root_from_body(data),
            revision_session_id=_required_string(data.get("revision_session_id"), "revision_session_id"),
            provider_name=str(data.get("provider") or "config"),
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
        )
    )
    return _revision_result_payload(result)


def _revision_accept(data: dict[str, object]) -> dict[str, object]:
    result = accept_revision_session(
        RevisionActionOptions(
            root=_root_from_body(data),
            revision_session_id=_required_string(data.get("revision_session_id"), "revision_session_id"),
        )
    )
    return _revision_result_payload(result)


def _revision_result_payload(result: RevisionSessionResult) -> dict[str, object]:
    return {
        "revision_session": result.session.model_dump(mode="json"),
        "session_path": str(result.session_path),
        "message": result.message,
    }
