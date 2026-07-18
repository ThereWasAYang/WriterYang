from __future__ import annotations

from novel.cli_parsers.common import ParserCollection
from novel.cli_shared import (
    _add_polish_mode_arg,
    _add_search_context_args,
)


def register_session_parsers(subparsers: ParserCollection) -> None:
    session_parser = subparsers.add_parser("session", help="Manage collaborative creation sessions")
    session_subparsers = session_parser.add_subparsers(dest="session_command", required=True)
    session_start = session_subparsers.add_parser("start", help="Start a collaborative creation session")
    session_start.add_argument("intent", help="User intent for this creation session")
    session_start.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_start.add_argument("--chapters", required=True, help="Chapter range, for example 3 or 3-4.")
    session_start.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for outline generation.",
    )
    session_start.add_argument("--force", action="store_true", help="Overwrite outline artifacts if needed.")
    _add_search_context_args(session_start, default_enabled=True)

    session_show = session_subparsers.add_parser("show", help="Show a creation session")
    session_show.add_argument("session_id", help="Session id")
    session_show.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")

    session_revise_outline = session_subparsers.add_parser("revise-outline", help="Revise a session outline proposal")
    session_revise_outline.add_argument("session_id", help="Session id")
    session_revise_outline.add_argument(
        "--path", default=".", help="Workspace directory. Defaults to the current directory."
    )
    session_revise_outline.add_argument("--instruction", required=True, help="Outline revision instruction.")
    session_revise_outline.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for outline revision.",
    )
    session_revise_outline.add_argument("--force", action="store_true", help="Overwrite outline artifacts if needed.")
    _add_search_context_args(session_revise_outline, default_enabled=True)

    session_approve = session_subparsers.add_parser("approve-outline", help="Approve a session outline")
    session_approve.add_argument("session_id", help="Session id")
    session_approve.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_approve.add_argument("--force", action="store_true", help="Overwrite approved outline artifacts.")

    session_run = session_subparsers.add_parser("run", help="Run a session after outline approval")
    session_run.add_argument("session_id", help="Session id")
    session_run.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_run.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for session generation.",
    )
    session_run.add_argument("--force", action="store_true", help="Overwrite generated artifacts.")
    session_run.add_argument(
        "--max-auto-revision-rounds",
        type=int,
        default=None,
        help="Maximum automatic repair rounds. Defaults to session setting.",
    )
    _add_polish_mode_arg(session_run)
    _add_search_context_args(session_run, default_enabled=True)

    session_revise_content = session_subparsers.add_parser("revise-content", help="Revise generated session content")
    session_revise_content.add_argument("session_id", help="Session id")
    session_revise_content.add_argument(
        "--path", default=".", help="Workspace directory. Defaults to the current directory."
    )
    session_revise_content.add_argument("--instruction", default=None, help="User feedback for content revision.")
    session_revise_content.add_argument(
        "--from-audit",
        action="store_true",
        help="Use current audit.json issues as the revision target. Useful when choosing to fix low issues.",
    )
    session_revise_content.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for content revision.",
    )
    session_revise_content.add_argument("--force", action="store_true", help="Overwrite selected revision artifacts.")
    _add_search_context_args(session_revise_content, default_enabled=True)

    session_revise_audit = session_subparsers.add_parser(
        "revise-audit", help="Correct Audit understanding and rerun audit for a rewrite event"
    )
    session_revise_audit.add_argument("session_id", help="Session id")
    session_revise_audit.add_argument("event_id", help="Rewrite event id")
    session_revise_audit.add_argument(
        "--path", default=".", help="Workspace directory. Defaults to the current directory."
    )
    session_revise_audit.add_argument("--instruction", required=True, help="Correction instruction for Audit Agent.")
    session_revise_audit.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for audit revision.",
    )
    session_revise_audit.add_argument("--force", action="store_true", help="Overwrite audit artifacts if needed.")
    _add_search_context_args(session_revise_audit, default_enabled=True)

    session_retry_rewrite = session_subparsers.add_parser(
        "retry-rewrite", help="Retry a rewrite event from the latest audit"
    )
    session_retry_rewrite.add_argument("session_id", help="Session id")
    session_retry_rewrite.add_argument("event_id", help="Rewrite event id")
    session_retry_rewrite.add_argument(
        "--path", default=".", help="Workspace directory. Defaults to the current directory."
    )
    session_retry_rewrite.add_argument("--instruction", default=None, help="Optional extra rewrite instruction.")
    session_retry_rewrite.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for rewrite retry.",
    )
    session_retry_rewrite.add_argument("--force", action="store_true", help="Overwrite generated artifacts if needed.")
    _add_polish_mode_arg(session_retry_rewrite)
    _add_search_context_args(session_retry_rewrite, default_enabled=True)

    session_undo_rewrite = session_subparsers.add_parser(
        "undo-rewrite", help="Restore rejected text snapshot for a rewrite event"
    )
    session_undo_rewrite.add_argument("session_id", help="Session id")
    session_undo_rewrite.add_argument("event_id", help="Rewrite event id")
    session_undo_rewrite.add_argument(
        "--path", default=".", help="Workspace directory. Defaults to the current directory."
    )
    session_undo_rewrite.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for post-restore audit.",
    )
    _add_search_context_args(session_undo_rewrite, default_enabled=True)

    session_accept = session_subparsers.add_parser("accept", help="Accept generated session content")
    session_accept.add_argument("session_id", help="Session id")
    session_accept.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_accept.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for missing state proposals.",
    )
    session_accept.add_argument("--force", action="store_true", help="Overwrite missing state proposals if needed.")

    session_archive = session_subparsers.add_parser("archive", help="Archive accepted session content")
    session_archive.add_argument("session_id", help="Session id")
    session_archive.add_argument("--path", default=".", help="Workspace directory. Defaults to the current directory.")
    session_archive.add_argument("--force", action="store_true", help="Overwrite archive files.")

    revision_session_parser = subparsers.add_parser(
        "revision-session", help="Manage scoped revisions of accepted chapters"
    )
    revision_session_subparsers = revision_session_parser.add_subparsers(dest="revision_session_command", required=True)
    revision_blocks = revision_session_subparsers.add_parser("blocks", help="List stable Markdown blocks")
    revision_blocks.add_argument("chapter", type=int, help="Accepted chapter number")
    revision_blocks.add_argument("--path", default=".", help="Workspace directory.")
    revision_start = revision_session_subparsers.add_parser("start", help="Create a scoped revision session")
    revision_start.add_argument("chapter", type=int, help="Accepted chapter number")
    revision_start.add_argument("--blocks", required=True, help="Inclusive block range, for example 2-4.")
    revision_start.add_argument("--instruction", required=True, help="Scoped revision instruction.")
    revision_start.add_argument("--path", default=".", help="Workspace directory.")
    revision_show = revision_session_subparsers.add_parser("show", help="Show a scoped revision session")
    revision_show.add_argument("revision_session_id", help="Revision session id")
    revision_show.add_argument("--path", default=".", help="Workspace directory.")
    revision_run = revision_session_subparsers.add_parser("run", help="Generate and audit the scoped candidate")
    revision_run.add_argument("revision_session_id", help="Revision session id")
    revision_run.add_argument("--path", default=".", help="Workspace directory.")
    revision_run.add_argument("--provider", default="config", help="Provider name.")
    _add_search_context_args(revision_run, default_enabled=True)
    revision_accept = revision_session_subparsers.add_parser("accept", help="Commit the audited scoped revision")
    revision_accept.add_argument("revision_session_id", help="Revision session id")
    revision_accept.add_argument("--path", default=".", help="Workspace directory.")
    revision_cancel = revision_session_subparsers.add_parser(
        "cancel", help="Cancel the revision and restore the accepted working files"
    )
    revision_cancel.add_argument("revision_session_id", help="Revision session id")
    revision_cancel.add_argument("--path", default=".", help="Workspace directory.")
