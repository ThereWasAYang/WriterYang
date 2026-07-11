from __future__ import annotations

import argparse
from pathlib import Path

from novel.cli_commands.generation import (
    _cmd_accept_chapter,
    _cmd_apply_state_update,
    _cmd_audit_chapter,
    _cmd_generate_chapter,
    _cmd_plan_chapter,
    _cmd_polish_chapter,
    _cmd_propose_state_update,
    _cmd_revise_chapter,
    _cmd_write_chapter,
)
from novel.cli_shared import (
    _add_agent_runtime_args,
    _add_integration_args_recursive,
    _add_search_context_args,
    _apply_project_alias,
)
from novel.cli import main


_INTERNAL_HANDLERS = {
    "plan-chapter": _cmd_plan_chapter,
    "write-chapter": _cmd_write_chapter,
    "polish-chapter": _cmd_polish_chapter,
    "audit-chapter": _cmd_audit_chapter,
    "revise-chapter": _cmd_revise_chapter,
    "propose-state-update": _cmd_propose_state_update,
    "apply-state-update": _cmd_apply_state_update,
    "accept-chapter": _cmd_accept_chapter,
    "generate-chapter": _cmd_generate_chapter,
}


def run_test_cli(argv: list[str]) -> int:
    if argv and argv[0] in _INTERNAL_HANDLERS:
        return run_internal_task_command(argv)
    return main(argv)


def run_internal_task_command(argv: list[str]) -> int:
    """Exercise runtime-internal Tasks without exposing them through the product CLI."""
    parser = argparse.ArgumentParser(prog="internal-task")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan-chapter", help="Generate a chapter plan")
    plan_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    plan_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    plan_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra planning instruction for this chapter.",
    )
    plan_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra planning instruction from a file.",
    )
    plan_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the plot agent config.",
    )
    _add_agent_runtime_args(plan_parser)
    plan_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing plan files.",
    )
    plan_parser.add_argument(
        "--use-search-context",
        action="store_true",
        help="Add explainable search results to the planning prompt.",
    )
    plan_parser.add_argument(
        "--vector-context",
        choices=("auto", "on", "off"),
        default="auto",
        help="Embedding semantic context mode for agent memory retrieval. Defaults to auto.",
    )
    plan_parser.add_argument(
        "--use-vector-context",
        action="store_true",
        help="Compatibility alias for --vector-context on.",
    )

    write_parser = subparsers.add_parser("write-chapter", help="Generate a chapter draft")
    write_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    write_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    write_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra writing instruction for this chapter.",
    )
    write_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra writing instruction from a file.",
    )
    write_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the writer agent config.",
    )
    _add_agent_runtime_args(write_parser)
    write_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing draft.md.",
    )
    write_parser.add_argument(
        "--target-words",
        type=int,
        default=None,
        help="Optional target word count.",
    )
    write_parser.add_argument(
        "--style-note",
        default=None,
        help="Temporary style guidance for this draft.",
    )
    write_parser.add_argument(
        "--use-search-context",
        action="store_true",
        help="Add explainable search results to the writing prompt.",
    )
    write_parser.add_argument(
        "--vector-context",
        choices=("auto", "on", "off"),
        default="auto",
        help="Embedding semantic context mode for agent memory retrieval. Defaults to auto.",
    )
    write_parser.add_argument(
        "--use-vector-context",
        action="store_true",
        help="Compatibility alias for --vector-context on.",
    )

    polish_parser = subparsers.add_parser("polish-chapter", help="Polish a chapter draft")
    polish_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    polish_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    polish_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra polishing instruction for this chapter.",
    )
    polish_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra polishing instruction from a file.",
    )
    polish_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the polish agent config.",
    )
    _add_agent_runtime_args(polish_parser)
    polish_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing polished.md.",
    )
    polish_parser.add_argument(
        "--style-note",
        default=None,
        help="Temporary style guidance for this polish pass.",
    )
    polish_parser.add_argument(
        "--keep-length",
        action="store_true",
        help="Try to keep the original length and paragraph scale.",
    )
    polish_parser.add_argument(
        "--light-edit",
        action="store_true",
        help="Light edit: language cleanup only.",
    )
    polish_parser.add_argument(
        "--deep-edit",
        action="store_true",
        help="Deep edit: improve rhythm, dialogue, and description without changing facts.",
    )
    _add_search_context_args(polish_parser)

    audit_parser = subparsers.add_parser("audit-chapter", help="Audit a chapter for consistency")
    audit_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    audit_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    audit_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra audit instruction for this chapter.",
    )
    audit_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra audit instruction from a file.",
    )
    audit_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the audit agent config.",
    )
    _add_agent_runtime_args(audit_parser)
    audit_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing audit.json.",
    )
    audit_parser.add_argument(
        "--strict",
        action="store_true",
        help="Use stricter audit criteria.",
    )
    audit_parser.add_argument(
        "--focus",
        action="append",
        default=[],
        choices=("canon", "state", "timeline", "style", "plot", "character_voice", "premature_reveal"),
        help="Audit focus area. Can be provided multiple times.",
    )
    audit_parser.add_argument(
        "--audited-file",
        default="polished.md",
        choices=("draft.md", "polished.md"),
        help="Chapter file to audit. Defaults to polished.md.",
    )
    audit_parser.add_argument(
        "--use-search-context",
        action="store_true",
        help="Add explainable search results to the audit prompt.",
    )
    audit_parser.add_argument(
        "--vector-context",
        choices=("auto", "on", "off"),
        default="auto",
        help="Embedding semantic context mode for agent memory retrieval. Defaults to auto.",
    )
    audit_parser.add_argument(
        "--use-vector-context",
        action="store_true",
        help="Compatibility alias for --vector-context on.",
    )
    audit_parser.add_argument(
        "--no-audit-recall",
        action="store_true",
        help="Disable bounded audit context recall for this run.",
    )

    revise_parser = subparsers.add_parser("revise-chapter", help="Revise a chapter from instructions or audit")
    revise_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    revise_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    revise_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra revision instruction for this chapter.",
    )
    revise_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra revision instruction from a file.",
    )
    revise_parser.add_argument(
        "--from-audit",
        action="store_true",
        help="Use audit.json issues as the main revision target.",
    )
    revise_parser.add_argument(
        "--target",
        default="polished",
        choices=("draft", "polished"),
        help="Source and output version family to revise. Defaults to polished.",
    )
    revise_parser.add_argument(
        "--source-file",
        default=None,
        help="Specific source version to revise, such as polished.md or polished.v2.md. Defaults to the latest version.",
    )
    revise_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to writer/polish agent config based on target.",
    )
    _add_agent_runtime_args(revise_parser)
    revise_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the selected revision version file if it already exists.",
    )
    revise_parser.add_argument(
        "--save-as-version",
        action="store_true",
        default=True,
        help="Save as draft.vN.md or polished.vN.md. This is the default.",
    )
    revise_parser.add_argument(
        "--max-rounds",
        type=int,
        default=1,
        help="Maximum revision loop rounds. Defaults to 1.",
    )
    revise_parser.add_argument(
        "--confirm-loop",
        action="store_true",
        help="Explicitly allow more than one revision round.",
    )
    _add_search_context_args(revise_parser)

    propose_state_parser = subparsers.add_parser(
        "propose-state-update",
        help="Generate a state and timeline update proposal",
    )
    propose_state_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    propose_state_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    propose_state_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra state update instruction for this chapter.",
    )
    propose_state_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra state update instruction from a file.",
    )
    propose_state_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to the state update agent config.",
    )
    _add_agent_runtime_args(propose_state_parser)
    propose_state_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing state_update_proposal.json.",
    )
    propose_state_parser.add_argument(
        "--allow-unresolved-audit",
        action="store_true",
        help="Allow proposal generation when audit has medium, high, or critical issues.",
    )
    _add_search_context_args(propose_state_parser)

    apply_state_parser = subparsers.add_parser(
        "apply-state-update",
        help="Apply a chapter state update proposal",
    )
    apply_state_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    apply_state_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )

    accept_parser = subparsers.add_parser(
        "accept-chapter",
        help="Accept a chapter and apply state/timeline updates",
    )
    accept_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    accept_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    accept_parser.add_argument(
        "--allow-issues",
        action="store_true",
        help="Allow acceptance when audit has medium, high, or critical issues.",
    )
    accept_parser.add_argument(
        "--propose",
        action="store_true",
        help="Generate state_update_proposal.json first if it is missing.",
    )
    accept_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra state update instruction when used with --propose.",
    )
    accept_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra state update instruction from a file when used with --propose.",
    )
    accept_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for --propose and canon drift proposal checks.",
    )
    _add_agent_runtime_args(accept_parser)
    accept_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing state_update_proposal.json when used with --propose.",
    )
    _add_search_context_args(accept_parser, default_enabled=True)

    generate_parser = subparsers.add_parser(
        "generate-chapter",
        help="Run the chapter generation pipeline",
    )
    generate_parser.add_argument("chapter_number", type=int, help="Positive chapter number")
    generate_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    generate_parser.add_argument(
        "--instruction",
        default=None,
        help="Extra instruction shared by planning, writing, polishing, and audit.",
    )
    generate_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read extra instruction from a file.",
    )
    generate_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use for each pipeline step.",
    )
    _add_agent_runtime_args(generate_parser)
    generate_parser.add_argument(
        "--target-words",
        type=int,
        default=None,
        help="Optional target word count for writing.",
    )
    generate_parser.add_argument(
        "--style-note",
        default=None,
        help="Temporary style guidance for writing and polishing.",
    )
    generate_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite files generated by pipeline steps.",
    )
    generate_parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse already generated step outputs and continue the pipeline.",
    )
    generate_parser.add_argument(
        "--polish-mode",
        choices=("single-pass", "auto", "review-gate"),
        default=None,
        help="Finalization mode. Defaults to project polish.mode or single-pass.",
    )
    generate_parser.add_argument(
        "--stop-after",
        choices=("plan", "write", "polish", "audit"),
        default=None,
        help="Stop after the selected pipeline step.",
    )
    _add_search_context_args(generate_parser, default_enabled=True)


    _add_integration_args_recursive(parser)
    args = parser.parse_args(argv)
    _apply_project_alias(args)
    return _INTERNAL_HANDLERS[args.command](args)
