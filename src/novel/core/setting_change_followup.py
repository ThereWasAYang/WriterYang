from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from novel.core.schemas import MemoryRepairProposal, PolishMode, VectorContextMode
from novel.core.session import (
    SessionInstructionOptions,
    load_session,
    revise_content,
    revise_outline,
)


@dataclass(frozen=True)
class SettingChangeFollowupOptions:
    root: Path
    proposal: MemoryRepairProposal
    session_id: str | None
    provider_name: str = "config"
    use_search_context: bool = True
    use_vector_context: VectorContextMode = "auto"
    polish_mode: PolishMode | None = None


def sync_setting_change_session(options: SettingChangeFollowupOptions) -> dict[str, object]:
    if not options.session_id:
        return {"status": "skipped", "reason": "session_id is missing"}
    try:
        session = load_session(options.root, options.session_id)
    except Exception as exc:
        return {
            "status": "failed_recoverable",
            "reason": f"could not load session: {exc}",
            "session_id": options.session_id,
        }
    if session.status in {"accepted", "archived"} or session.content_status in {"accepted", "archived"}:
        return {
            "status": "manual_review",
            "reason": "accepted or archived sessions are not rewritten automatically",
            "session_id": options.session_id,
        }
    instruction = (
        "设定变更已应用，请基于最新项目 memory 同步当前创作。\n"
        f"原始设定变更请求：{options.proposal.user_request}\n"
        f"影响分析：{options.proposal.impact.summary if options.proposal.impact else '无'}"
    )
    try:
        if session.content_status == "not_started":
            result = revise_outline(_instruction_options(options, instruction))
            return {
                "status": "synced",
                "action": "revise_outline",
                "session": result.session.model_dump(mode="json"),
                "session_path": str(result.session_path),
            }
        if session.content_status in {"needs_user_review", "needs_revision"}:
            result = revise_content(_instruction_options(options, instruction))
            return {
                "status": "synced",
                "action": "revise_content",
                "session": result.session.model_dump(mode="json"),
                "session_path": str(result.session_path),
            }
        return {
            "status": "manual_review",
            "reason": f"session status {session.status}/{session.content_status} is not safe for automatic sync",
            "session_id": options.session_id,
        }
    except Exception as exc:
        return {
            "status": "failed_recoverable",
            "reason": str(exc),
            "session_id": options.session_id,
        }


def _instruction_options(
    options: SettingChangeFollowupOptions,
    instruction: str,
) -> SessionInstructionOptions:
    if not options.session_id:
        raise ValueError("session_id is required")
    return SessionInstructionOptions(
        root=options.root,
        session_id=options.session_id,
        instruction=instruction,
        provider_name=options.provider_name,
        force=True,
        use_search_context=options.use_search_context,
        use_vector_context=options.use_vector_context,
        polish_mode=options.polish_mode,
    )
