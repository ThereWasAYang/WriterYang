from __future__ import annotations

from novel.core.contracts import RevisionBlocksCommand, RevisionCommand, RevisionStartCommand

from .common import (
    _dispatch_web_command,
    _dispatch_web_query_command,
    _required_string,
    _vector_context_mode,
)


def _revision_blocks_api(query: dict[str, str]) -> dict[str, object]:
    return _dispatch_web_query_command(
        query,
        RevisionBlocksCommand(chapter_number=int(_required_string(query.get("chapter"), "chapter"))),
    )


def _revision_show_api(query: dict[str, str]) -> dict[str, object]:
    return _dispatch_web_query_command(
        query,
        RevisionCommand(
            type="revision.show",
            revision_session_id=_required_string(query.get("revision_session_id"), "revision_session_id"),
        ),
    )


def _revision_start(data: dict[str, object]) -> dict[str, object]:
    return _dispatch_web_command(
        data,
        RevisionStartCommand(
            chapter_number=int(str(data.get("chapter") or 0)),
            start_block=int(str(data.get("start_block") or 0)),
            end_block=int(str(data.get("end_block") or 0)),
            instruction=_required_string(data.get("instruction"), "instruction"),
        ),
    )


def _revision_run(data: dict[str, object]) -> dict[str, object]:
    return _dispatch_web_command(
        data,
        RevisionCommand(
            type="revision.run",
            revision_session_id=_required_string(data.get("revision_session_id"), "revision_session_id"),
            provider_name=str(data.get("provider") or "config"),
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
        ),
    )


def _revision_accept(data: dict[str, object]) -> dict[str, object]:
    return _dispatch_web_command(
        data,
        RevisionCommand(
            type="revision.accept",
            revision_session_id=_required_string(data.get("revision_session_id"), "revision_session_id"),
        ),
        confirmed=True,
    )


def _revision_cancel(data: dict[str, object]) -> dict[str, object]:
    return _dispatch_web_command(
        data,
        RevisionCommand(
            type="revision.cancel",
            revision_session_id=_required_string(data.get("revision_session_id"), "revision_session_id"),
        ),
    )
