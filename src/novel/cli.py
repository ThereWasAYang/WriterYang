from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from novel.core.auditing import (
    AuditError,
    ChapterAuditOptions,
    audit_chapter,
    load_audit_provider,
    read_audit_instruction,
)
from novel.core.canon import (
    CanonError,
    CanonSuggestOptions,
    apply_canon_proposal,
    format_canon_validation_report,
    load_canon_provider,
    suggest_canon,
)
from novel.core.drafting import (
    ChapterDraftingOptions,
    DraftingError,
    load_drafting_provider,
    read_drafting_instruction,
    write_chapter_draft,
)
from novel.core.inspiration import (
    InspirationError,
    InspirationOptions,
    load_inspiration_provider,
    read_inspiration_input,
    run_inspiration_agent,
)
from novel.core.planning import (
    ChapterPlanningOptions,
    PlanningError,
    load_planning_provider,
    plan_chapter,
    read_planning_instruction,
)
from novel.core.polishing import (
    ChapterPolishingOptions,
    PolishingError,
    load_polishing_provider,
    polish_chapter,
    read_polishing_instruction,
    resolve_edit_mode,
)
from novel.core.revision import (
    ChapterRevisionOptions,
    RevisionError,
    load_revision_provider,
    read_revision_instruction,
    revise_chapter,
)
from novel.core.search import SearchError, rebuild_search_index, search_project
from novel.core.state_update import (
    AcceptChapterOptions,
    StateUpdateApplyOptions,
    StateUpdateError,
    StateUpdateProposeOptions,
    accept_chapter,
    apply_state_update,
    load_state_update_provider,
    propose_state_update,
    read_state_update_instruction,
)
from novel.core.exporting import (
    DocxExportOptions,
    ExportError,
    MarkdownExportOptions,
    export_docx,
    export_markdown,
    parse_chapter_selector,
)
from novel.core.inspection import (
    ProjectReadError,
    format_characters,
    format_canon,
    format_state,
    format_status,
    format_timeline,
    get_project_status,
)
from novel.core.orchestrator import (
    OrchestratorError,
    OrchestratorOptions,
    format_orchestrator_plan,
    handoff_rules_text,
    orchestrate,
)
from novel.core.provider_config import ProviderOverrides, describe_agent_provider, default_agent_config_path
from novel.core.workspace import InitOptions, WorkspaceExistsError, init_workspace
from novel.core.validation import validate_canon, validate_project
from novel.core.workflow import (
    GenerateChapterOptions,
    WorkflowError,
    generate_chapter,
    read_workflow_instruction,
)
from novel import __version__


def _add_agent_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent-config",
        type=Path,
        default=None,
        help="Agent model config file. Defaults to config/agents.yaml in the workspace.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Temporarily override the configured model name.",
    )
    parser.add_argument(
        "--dry-run-provider",
        action="store_true",
        help="Show the provider configuration that would be used without calling the provider.",
    )


def _print_dry_run_provider(
    root: Path,
    agent_config: Path | None,
    provider_name: str,
    model_name: str | None,
    agents: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    path = agent_config or default_agent_config_path(root)
    overrides = ProviderOverrides(provider_name=provider_name, model_name=model_name)
    for index, (agent_name, fallback_agents) in enumerate(agents):
        if index:
            print("")
        print(
            describe_agent_provider(
                path,
                agent_name,
                fallback_agents=fallback_agents,
                overrides=overrides,
            ).format()
        )


def _add_integration_args(parser: argparse.ArgumentParser) -> None:
    option_strings = {
        option
        for action in parser._actions
        for option in getattr(action, "option_strings", ())
    }
    if "--project" not in option_strings:
        parser.add_argument(
            "--project",
            default=None,
            help="Stable alias for --path, intended for external agent integrations.",
        )
    if "--quiet" not in option_strings:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress human-readable success output.",
        )
    if "--json" not in option_strings:
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output machine-readable JSON.",
        )


def _add_integration_args_recursive(parser: argparse.ArgumentParser) -> None:
    _add_integration_args(parser)
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for subparser in choices.values():
                _add_integration_args_recursive(subparser)


def _apply_project_alias(args: argparse.Namespace) -> None:
    project = getattr(args, "project", None)
    if project is not None and hasattr(args, "path"):
        args.path = project


def _wants_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _quiet(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "quiet", False))


def _success(args: argparse.Namespace, payload: dict[str, object], lines: list[str] | None = None) -> int:
    if _wants_json(args):
        response = {"ok": True, **payload}
        if lines:
            response["messages"] = lines
        _print_json(response)
    elif not _quiet(args):
        for line in lines or []:
            print(line)
    return 0


def _failure(args: argparse.Namespace, message: str, *, code: int = 1, error_type: str = "error") -> int:
    safe = _safe_message(message)
    if _wants_json(args):
        _print_json({"ok": False, "error": {"type": error_type, "message": safe, "code": code}})
    else:
        print(f"error: {safe}", file=sys.stderr)
    return code


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _safe_message(message: str) -> str:
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[redacted-api-key]", message)
    for key, value in os.environ.items():
        if value and ("KEY" in key or "TOKEN" in key or "SECRET" in key):
            redacted = redacted.replace(value, "[redacted]")
    return redacted


def _validation_payload(report) -> dict[str, object]:
    return {
        "root": str(report.root),
        "ok": report.ok,
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "messages": [
            {
                "level": message.level,
                "path": str(message.path),
                "message": message.message,
            }
            for message in report.messages
        ],
    }


def _status_payload(status) -> dict[str, object]:
    return {
        "title": status.title,
        "latest_chapter": status.latest_chapter,
        "inspiration_exists": status.inspiration_exists,
        "character_count": status.character_count,
        "location_count": status.location_count,
        "item_count": status.item_count,
        "timeline_event_count": status.timeline_event_count,
        "latest_run_log": str(status.latest_run_log) if status.latest_run_log else None,
        "latest_run_summary": status.latest_run_summary,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel", description="Novel workspace CLI")
    parser.add_argument(
        "--version",
        action="version",
        version=f"novel {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a new novel project workspace")
    init_parser.add_argument("title", help="Novel title")
    init_parser.add_argument(
        "--path",
        default="novel-project",
        help="Workspace directory to create. Defaults to ./novel-project",
    )
    init_parser.add_argument(
        "--project-id",
        default=None,
        help="Stable project id. Defaults to a generated id based on the title.",
    )
    init_parser.add_argument("--language", default="zh-CN", help="Project language")
    init_parser.add_argument(
        "--genre",
        action="append",
        default=[],
        help="Genre label. Can be provided multiple times.",
    )

    validate_parser = subparsers.add_parser("validate", help="Validate a novel project workspace")
    validate_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to validate. Defaults to the current directory.",
    )

    index_parser = subparsers.add_parser("index", help="Manage the local search index")
    index_subparsers = index_parser.add_subparsers(dest="index_command", required=True)
    index_rebuild = index_subparsers.add_parser("rebuild", help="Rebuild the local search index")
    index_rebuild.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )

    search_parser = subparsers.add_parser("search", help="Search project memory")
    search_parser.add_argument("query", help="Keyword query")
    search_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    search_parser.add_argument(
        "--type",
        default="all",
        choices=("character", "location", "item", "event", "chapter", "all"),
        help="Result type to search. Defaults to all.",
    )
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of results. Defaults to 10.",
    )
    search_parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON.",
    )

    ask_parser = subparsers.add_parser("ask", help="Ask the controlled orchestrator to run a task")
    ask_parser.add_argument("request", help="Natural language task request")
    ask_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    ask_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible"),
        help="Provider to use for selected agents.",
    )
    ask_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the execution plan without calling agents or writing files.",
    )
    ask_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow selected services to overwrite their normal target files.",
    )
    ask_parser.add_argument(
        "--max-steps",
        type=int,
        default=8,
        help="Maximum handoff steps. Defaults to 8.",
    )
    ask_parser.add_argument(
        "--max-retries",
        type=int,
        default=0,
        help="Maximum retries per task. Defaults to 0.",
    )
    ask_parser.add_argument(
        "--max-agent-calls",
        type=int,
        default=8,
        help="Maximum agent calls. Defaults to 8.",
    )
    ask_parser.add_argument(
        "--show-handoff-rules",
        action="store_true",
        help="Print allowed handoff rules before the plan.",
    )

    status_parser = subparsers.add_parser("status", help="Show project status")
    status_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to inspect. Defaults to the current directory.",
    )

    show_parser = subparsers.add_parser("show", help="Show project data")
    show_parser.add_argument(
        "target",
        choices=("characters", "timeline", "state", "canon"),
        help="Project data to display.",
    )
    show_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to inspect. Defaults to the current directory.",
    )

    inspire_parser = subparsers.add_parser("inspire", help="Generate an inspiration weak outline")
    inspire_parser.add_argument(
        "text",
        nargs="?",
        help="Raw inspiration text. Use --input to read from a file instead.",
    )
    inspire_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read raw inspiration text from a file.",
    )
    inspire_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    inspire_parser.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible"),
        help="Provider to use. Defaults to the inspiration agent config.",
    )
    _add_agent_runtime_args(inspire_parser)
    inspire_parser.add_argument(
        "--json",
        action="store_true",
        help="Also write memory/inspiration.json.",
    )
    inspire_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing inspiration files.",
    )

    canon_parser = subparsers.add_parser("canon", help="Manage canon data")
    canon_subparsers = canon_parser.add_subparsers(dest="canon_command", required=True)

    canon_suggest = canon_subparsers.add_parser("suggest", help="Generate a canon proposal")
    canon_suggest.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    canon_suggest.add_argument(
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible"),
        help="Provider to use. Defaults to config/agents.yaml.",
    )
    _add_agent_runtime_args(canon_suggest)
    canon_suggest.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Save proposal JSON to this file. Refuses to overwrite.",
    )

    canon_apply = canon_subparsers.add_parser("apply", help="Apply a canon proposal")
    canon_apply.add_argument("proposal_file", type=Path, help="Canon proposal JSON file")
    canon_apply.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )

    canon_validate = canon_subparsers.add_parser("validate", help="Validate canon files only")
    canon_validate.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    canon_show = canon_subparsers.add_parser("show", help="Show canon summary")
    canon_show.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )

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
        choices=("config", "mock", "openai", "openai_compatible"),
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
        choices=("config", "mock", "openai", "openai_compatible"),
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
        choices=("config", "mock", "openai", "openai_compatible"),
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
        choices=("config", "mock", "openai", "openai_compatible"),
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
        "--provider",
        default="config",
        choices=("config", "mock", "openai", "openai_compatible"),
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
        choices=("config", "mock", "openai", "openai_compatible"),
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
        help="Allow proposal generation when audit has high or critical issues.",
    )

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
        help="Allow acceptance when audit has high or critical issues.",
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
        choices=("config", "mock", "openai", "openai_compatible"),
        help="Provider to use when --propose is set.",
    )
    _add_agent_runtime_args(accept_parser)
    accept_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing state_update_proposal.json when used with --propose.",
    )

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
        choices=("config", "mock", "openai", "openai_compatible"),
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
        "--skip-polish",
        action="store_true",
        help="Stop after draft generation; polished.md and audit.json are not generated.",
    )
    generate_parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Generate through polished.md but skip audit.json.",
    )
    generate_parser.add_argument(
        "--stop-after",
        choices=("plan", "write", "polish", "audit"),
        default=None,
        help="Stop after the selected pipeline step.",
    )

    export_parser = subparsers.add_parser("export", help="Export project content")
    export_subparsers = export_parser.add_subparsers(dest="export_command", required=True)
    export_markdown_parser = export_subparsers.add_parser("markdown", help="Export chapters as Markdown")
    export_markdown_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    export_markdown_parser.add_argument(
        "--chapters",
        default=None,
        help="Comma-separated chapter numbers, for example 1,2,3.",
    )
    export_markdown_parser.add_argument(
        "--from",
        dest="from_chapter",
        type=int,
        default=None,
        help="First chapter number to export.",
    )
    export_markdown_parser.add_argument(
        "--to",
        dest="to_chapter",
        type=int,
        default=None,
        help="Last chapter number to export.",
    )
    export_markdown_parser.add_argument(
        "--include-unaccepted",
        action="store_true",
        help="Include chapters whose polished.md is not marked accepted.",
    )
    export_markdown_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output Markdown path. Defaults to exports/novel.md.",
    )
    export_markdown_parser.add_argument(
        "--title",
        default=None,
        help="Override exported work title.",
    )
    export_markdown_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing output Markdown.",
    )
    export_docx_parser = export_subparsers.add_parser("docx", help="Export chapters as Word DOCX")
    export_docx_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory. Defaults to the current directory.",
    )
    export_docx_parser.add_argument(
        "--chapters",
        default=None,
        help="Comma-separated chapter numbers, for example 1,2,3.",
    )
    export_docx_parser.add_argument(
        "--from",
        dest="from_chapter",
        type=int,
        default=None,
        help="First chapter number to export.",
    )
    export_docx_parser.add_argument(
        "--to",
        dest="to_chapter",
        type=int,
        default=None,
        help="Last chapter number to export.",
    )
    export_docx_parser.add_argument(
        "--include-unaccepted",
        action="store_true",
        help="Include chapters whose polished.md is not marked accepted.",
    )
    export_docx_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output DOCX path. Defaults to exports/novel.docx.",
    )
    export_docx_parser.add_argument(
        "--title",
        default=None,
        help="Override exported work title.",
    )
    export_docx_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing output DOCX.",
    )

    web_parser = subparsers.add_parser("web", help="Run the local Web UI")
    web_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind. Defaults to 127.0.0.1.",
    )
    web_parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to bind. Defaults to 8765.",
    )

    _add_integration_args_recursive(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_project_alias(args)

    if args.command == "init":
        options = InitOptions(
            title=args.title,
            root=Path(args.path),
            project_id=args.project_id,
            language=args.language,
            genre=args.genre,
        )
        try:
            result = init_workspace(options)
        except WorkspaceExistsError as exc:
            return _failure(args, str(exc), error_type="workspace_exists")

        return _success(
            args,
            {
                "command": "init",
                "root": str(result.root),
                "project_file": str(result.root / "project.yaml"),
            },
            [
                f"Created novel workspace: {result.root}",
                f"Project file: {result.root / 'project.yaml'}",
            ],
        )

    if args.command == "validate":
        report = validate_project(Path(args.path))
        payload = _validation_payload(report)
        if _wants_json(args):
            _print_json({"ok": report.ok, "command": "validate", "validation": payload})
            return 0 if report.ok else 1
        if _quiet(args):
            return 0 if report.ok else 1
        for message in report.messages:
            path = message.path
            try:
                path = path.relative_to(report.root)
            except ValueError:
                pass
            print(f"{message.level}: {path}: {message.message}")

        if report.ok:
            print(f"Validation passed: {len(report.warnings)} warning(s)")
            return 0

        print(
            f"Validation failed: {len(report.errors)} error(s), "
            f"{len(report.warnings)} warning(s)",
            file=sys.stderr,
        )
        return 1

    if args.command == "index":
        if args.index_command == "rebuild":
            try:
                result = rebuild_search_index(Path(args.path))
            except SearchError as exc:
                return _failure(args, str(exc), error_type="search_error")
            return _success(
                args,
                {
                    "command": "index rebuild",
                    "index_path": str(result.index_path),
                    "document_count": result.document_count,
                },
                [f"Rebuilt search index: {result.index_path}", f"Documents: {result.document_count}"],
            )

    if args.command == "search":
        try:
            results = search_project(
                Path(args.path),
                args.query,
                search_type=args.type,
                limit=args.limit,
            )
        except SearchError as exc:
            return _failure(args, str(exc), error_type="search_error")
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "id": result.id,
                            "type": result.type,
                            "path": result.path,
                            "title": result.title,
                            "score": result.score,
                            "matched_terms": list(result.matched_terms),
                            "excerpt": result.excerpt,
                            "metadata": result.metadata,
                        }
                        for result in results
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if not results:
            print("No results.")
            return 0
        for index, result in enumerate(results, start=1):
            terms = ", ".join(result.matched_terms) if result.matched_terms else "none"
            print(f"{index}. [{result.type}] {result.title}")
            print(f"   path: {result.path}")
            print(f"   score: {result.score}; matched_terms: {terms}")
            print(f"   excerpt: {result.excerpt}")
        return 0

    if args.command == "ask":
        try:
            result = orchestrate(
                OrchestratorOptions(
                    root=Path(args.path),
                    request=args.request,
                    provider_name=args.provider,
                    dry_run=args.dry_run,
                    force=args.force,
                    max_steps=args.max_steps,
                    max_retries=args.max_retries,
                    max_agent_calls=args.max_agent_calls,
                )
            )
        except OrchestratorError as exc:
            return _failure(args, str(exc), error_type="orchestrator_error")
        payload = {
            "command": "ask",
            "task": result.plan.task,
            "chapter_number": result.plan.chapter_number,
            "message": result.message,
            "run_log_path": str(result.run_log_path) if result.run_log_path else None,
            "handoff_trace": [entry.as_dict() for entry in result.plan.handoff_trace],
        }
        if _wants_json(args):
            _print_json({"ok": True, **payload})
            return 0
        if _quiet(args):
            return 0
        if args.show_handoff_rules:
            print(handoff_rules_text())
            print("")
        print(format_orchestrator_plan(result.plan))
        print(result.message)
        if result.run_log_path:
            print(f"Run log: {result.run_log_path}")
        return 0

    if args.command == "status":
        try:
            status = get_project_status(Path(args.path))
        except ProjectReadError as exc:
            return _failure(args, str(exc), error_type="project_read_error")
        return _success(
            args,
            {"command": "status", "status": _status_payload(status)},
            [format_status(status, Path(args.path))],
        )

    if args.command == "show":
        try:
            if args.target == "characters":
                output = format_characters(Path(args.path))
            elif args.target == "timeline":
                output = format_timeline(Path(args.path))
            elif args.target == "canon":
                output = format_canon(Path(args.path))
            else:
                output = format_state(Path(args.path))
        except ProjectReadError as exc:
            return _failure(args, str(exc), error_type="project_read_error")
        return _success(
            args,
            {"command": "show", "target": args.target, "output": output},
            [output],
        )

    if args.command == "inspire":
        root = Path(args.path)
        try:
            if args.dry_run_provider:
                _print_dry_run_provider(
                    root,
                    args.agent_config,
                    args.provider,
                    args.model,
                    (("inspiration", ()),),
                )
                return 0
            source_text, source_type = read_inspiration_input(args.text, args.input)
            provider = load_inspiration_provider(
                root,
                args.provider,
                agent_config_path=args.agent_config,
                model_name=args.model,
            )
            result = run_inspiration_agent(
                InspirationOptions(
                    root=root,
                    source_text=source_text,
                    source_type=source_type,
                    write_json=args.json,
                    overwrite=args.overwrite,
                ),
                provider,
            )
        except InspirationError as exc:
            return _failure(args, str(exc), error_type="inspiration_error")
        except Exception as exc:
            return _failure(args, f"inspiration generation failed: {exc}", error_type="inspiration_error")

        lines = [f"Wrote inspiration markdown: {result.markdown_path}"]
        if result.json_path:
            lines.append(f"Wrote inspiration JSON: {result.json_path}")
        return _success(
            args,
            {
                "command": "inspire",
                "markdown_path": str(result.markdown_path),
                "json_path": str(result.json_path) if result.json_path else None,
            },
            lines,
        )

    if args.command == "canon":
        root = Path(args.path)
        if args.canon_command == "suggest":
            try:
                if args.dry_run_provider:
                    _print_dry_run_provider(
                        root,
                        args.agent_config,
                        args.provider,
                        args.model,
                        (("canon", ("inspiration",)),),
                    )
                    return 0
                provider = load_canon_provider(
                    root,
                    args.provider,
                    agent_config_path=args.agent_config,
                    model_name=args.model,
                )
                result = suggest_canon(
                    CanonSuggestOptions(root=root, output_path=args.output),
                    provider,
                )
            except CanonError as exc:
                return _failure(args, str(exc), error_type="canon_error")
            except Exception as exc:
                return _failure(args, f"canon suggestion failed: {exc}", error_type="canon_error")

            if _wants_json(args):
                _print_json(
                    {
                        "ok": True,
                        "command": "canon suggest",
                        "output_path": str(result.output_path) if result.output_path else None,
                        "proposal": json.loads(result.proposal_json),
                    }
                )
                return 0
            if _quiet(args):
                return 0
            if result.output_path:
                print(f"Wrote canon proposal: {result.output_path}")
            else:
                print(result.proposal_json, end="")
            return 0

        if args.canon_command == "apply":
            try:
                result = apply_canon_proposal(root, args.proposal_file)
            except CanonError as exc:
                return _failure(args, str(exc), error_type="canon_error")
            if _wants_json(args):
                _print_json(
                    {
                        "ok": result.validation_report.ok,
                        "command": "canon apply",
                        "validation": _validation_payload(result.validation_report),
                    }
                )
                return 0 if result.validation_report.ok else 1
            if not _quiet(args):
                print(format_canon_validation_report(result.validation_report))
            return 0 if result.validation_report.ok else 1

        if args.canon_command == "validate":
            report = validate_canon(root)
            if _wants_json(args):
                _print_json({"ok": report.ok, "command": "canon validate", "validation": _validation_payload(report)})
                return 0 if report.ok else 1
            if not _quiet(args):
                print(format_canon_validation_report(report))
            return 0 if report.ok else 1

        if args.canon_command == "show":
            try:
                output = format_canon(root)
            except ProjectReadError as exc:
                return _failure(args, str(exc), error_type="project_read_error")
            return _success(args, {"command": "canon show", "output": output}, [output])

    if args.command == "plan-chapter":
        root = Path(args.path)
        try:
            if args.dry_run_provider:
                _print_dry_run_provider(
                    root,
                    args.agent_config,
                    args.provider,
                    args.model,
                    (("plot", ()),),
                )
                return 0
            instruction = read_planning_instruction(args.instruction, args.input)
            provider = load_planning_provider(
                root,
                args.provider,
                chapter_number=args.chapter_number,
                agent_config_path=args.agent_config,
                model_name=args.model,
            )
            result = plan_chapter(
                ChapterPlanningOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    instruction=instruction,
                    force=args.force,
                    use_search_context=args.use_search_context,
                ),
                provider,
            )
        except PlanningError as exc:
            return _failure(args, str(exc), error_type="planning_error")
        except Exception as exc:
            return _failure(args, f"chapter planning failed: {exc}", error_type="planning_error")

        payload = {
            "command": "plan-chapter",
            "chapter_number": result.plan.chapter_number,
            "plan_json_path": str(result.plan_json_path),
            "plan_markdown_path": str(result.plan_markdown_path),
            "validation": _validation_payload(result.validation_report),
        }
        if _wants_json(args):
            _print_json({"ok": result.validation_report.ok, **payload})
            return 0 if result.validation_report.ok else 1
        if not _quiet(args):
            print(f"Wrote chapter plan JSON: {result.plan_json_path}")
            print(f"Wrote chapter plan Markdown: {result.plan_markdown_path}")
        if not result.validation_report.ok:
            if not _quiet(args):
                print(
                    f"Validation failed after planning: {len(result.validation_report.errors)} error(s), "
                    f"{len(result.validation_report.warnings)} warning(s)",
                    file=sys.stderr,
                )
            return 1
        if not _quiet(args):
            print(f"Validation passed: {len(result.validation_report.warnings)} warning(s)")
        return 0

    if args.command == "write-chapter":
        root = Path(args.path)
        try:
            if args.dry_run_provider:
                _print_dry_run_provider(
                    root,
                    args.agent_config,
                    args.provider,
                    args.model,
                    (("writer", ()),),
                )
                return 0
            instruction = read_drafting_instruction(args.instruction, args.input)
            provider = load_drafting_provider(
                root,
                args.provider,
                agent_config_path=args.agent_config,
                model_name=args.model,
            )
            result = write_chapter_draft(
                ChapterDraftingOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    instruction=instruction,
                    force=args.force,
                    target_words=args.target_words,
                    style_note=args.style_note,
                    use_search_context=args.use_search_context,
                ),
                provider,
            )
        except DraftingError as exc:
            return _failure(args, str(exc), error_type="drafting_error")
        except Exception as exc:
            return _failure(args, f"chapter drafting failed: {exc}", error_type="drafting_error")

        lines = [*(f"warning: {warning}" for warning in result.warnings), f"Wrote chapter draft: {result.draft_path}"]
        return _success(
            args,
            {
                "command": "write-chapter",
                "draft_path": str(result.draft_path),
                "warnings": list(result.warnings),
            },
            lines,
        )

    if args.command == "polish-chapter":
        root = Path(args.path)
        try:
            if args.dry_run_provider:
                _print_dry_run_provider(
                    root,
                    args.agent_config,
                    args.provider,
                    args.model,
                    (("polish", ()),),
                )
                return 0
            instruction = read_polishing_instruction(args.instruction, args.input)
            edit_mode = resolve_edit_mode(
                light_edit=args.light_edit,
                deep_edit=args.deep_edit,
            )
            provider = load_polishing_provider(
                root,
                args.provider,
                agent_config_path=args.agent_config,
                model_name=args.model,
            )
            result = polish_chapter(
                ChapterPolishingOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    instruction=instruction,
                    force=args.force,
                    style_note=args.style_note,
                    keep_length=args.keep_length,
                    edit_mode=edit_mode,
                ),
                provider,
            )
        except PolishingError as exc:
            return _failure(args, str(exc), error_type="polishing_error")
        except Exception as exc:
            return _failure(args, f"chapter polishing failed: {exc}", error_type="polishing_error")

        lines = [*(f"warning: {warning}" for warning in result.warnings), f"Wrote polished chapter: {result.polished_path}"]
        return _success(
            args,
            {
                "command": "polish-chapter",
                "polished_path": str(result.polished_path),
                "warnings": list(result.warnings),
            },
            lines,
        )

    if args.command == "audit-chapter":
        root = Path(args.path)
        try:
            if args.dry_run_provider:
                _print_dry_run_provider(
                    root,
                    args.agent_config,
                    args.provider,
                    args.model,
                    (("audit", ()),),
                )
                return 0
            instruction = read_audit_instruction(args.instruction, args.input)
            provider = load_audit_provider(
                root,
                args.provider,
                chapter_number=args.chapter_number,
                audited_file=args.audited_file,
                agent_config_path=args.agent_config,
                model_name=args.model,
            )
            result = audit_chapter(
                ChapterAuditOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    instruction=instruction,
                    force=args.force,
                    strict=args.strict,
                    focus=tuple(args.focus),
                    audited_file=args.audited_file,
                    use_search_context=args.use_search_context,
                ),
                provider,
            )
        except AuditError as exc:
            return _failure(args, str(exc), error_type="audit_error")
        except Exception as exc:
            return _failure(args, f"chapter audit failed: {exc}", error_type="audit_error")

        lines = [
            *(f"warning: {warning}" for warning in result.warnings),
            f"Wrote chapter audit: {result.audit_path}",
            f"Audit status: {result.report.overall_status}",
            f"Issues: {len(result.report.issues)}",
        ]
        return _success(
            args,
            {
                "command": "audit-chapter",
                "audit_path": str(result.audit_path),
                "overall_status": result.report.overall_status,
                "issue_count": len(result.report.issues),
                "warnings": list(result.warnings),
            },
            lines,
        )

    if args.command == "revise-chapter":
        root = Path(args.path)
        try:
            if args.dry_run_provider:
                agent = "writer" if args.target == "draft" else "polish"
                _print_dry_run_provider(
                    root,
                    args.agent_config,
                    args.provider,
                    args.model,
                    ((agent, ()),),
                )
                return 0
            instruction = read_revision_instruction(args.instruction, args.input)
            provider = load_revision_provider(
                root,
                args.provider,
                target=args.target,
                agent_config_path=args.agent_config,
                model_name=args.model,
            )
            result = revise_chapter(
                ChapterRevisionOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    instruction=instruction,
                    from_audit=args.from_audit,
                    target=args.target,
                    force=args.force,
                    save_as_version=args.save_as_version,
                ),
                provider,
                provider_name=args.provider,
            )
        except RevisionError as exc:
            return _failure(args, str(exc), error_type="revision_error")
        except Exception as exc:
            return _failure(args, f"chapter revision failed: {exc}", error_type="revision_error")

        lines = [
            *(f"warning: {warning}" for warning in result.warnings),
            f"Wrote chapter revision: {result.output_path}",
            f"Updated revision log: {result.revision_log_path}",
        ]
        return _success(
            args,
            {
                "command": "revise-chapter",
                "output_path": str(result.output_path),
                "revision_log_path": str(result.revision_log_path),
                "revision_id": result.record.id,
                "warnings": list(result.warnings),
            },
            lines,
        )

    if args.command == "propose-state-update":
        root = Path(args.path)
        try:
            if args.dry_run_provider:
                _print_dry_run_provider(
                    root,
                    args.agent_config,
                    args.provider,
                    args.model,
                    (("state_update", ("audit",)),),
                )
                return 0
            instruction = read_state_update_instruction(args.instruction, args.input)
            provider = load_state_update_provider(
                root,
                args.provider,
                chapter_number=args.chapter_number,
                agent_config_path=args.agent_config,
                model_name=args.model,
            )
            result = propose_state_update(
                StateUpdateProposeOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    instruction=instruction,
                    force=args.force,
                    allow_unresolved_audit=args.allow_unresolved_audit,
                ),
                provider,
            )
        except StateUpdateError as exc:
            return _failure(args, str(exc), error_type="state_update_error")
        except Exception as exc:
            return _failure(args, f"state update proposal failed: {exc}", error_type="state_update_error")

        lines = [
            *(f"warning: {warning}" for warning in result.warnings),
            f"Wrote state update proposal: {result.proposal_path}",
            f"State changes: {len(result.proposal.state_changes)}",
            f"Timeline events: {len(result.proposal.timeline_events)}",
        ]
        return _success(
            args,
            {
                "command": "propose-state-update",
                "proposal_path": str(result.proposal_path),
                "state_change_count": len(result.proposal.state_changes),
                "timeline_event_count": len(result.proposal.timeline_events),
                "warnings": list(result.warnings),
            },
            lines,
        )

    if args.command == "apply-state-update":
        root = Path(args.path)
        try:
            result = apply_state_update(
                StateUpdateApplyOptions(root=root, chapter_number=args.chapter_number)
            )
        except StateUpdateError as exc:
            return _failure(args, str(exc), error_type="state_update_error")
        except Exception as exc:
            return _failure(args, f"state update application failed: {exc}", error_type="state_update_error")

        return _success(
            args,
            {
                "command": "apply-state-update",
                "state_backup_path": str(result.state_backup_path),
                "timeline_backup_path": str(result.timeline_backup_path),
                "state_path": str(result.state_path),
                "timeline_path": str(result.timeline_path),
            },
            [
                f"Backed up current state: {result.state_backup_path}",
                f"Backed up timeline: {result.timeline_backup_path}",
                f"Updated current state: {result.state_path}",
                f"Updated timeline: {result.timeline_path}",
            ],
        )

    if args.command == "accept-chapter":
        root = Path(args.path)
        try:
            if args.dry_run_provider:
                _print_dry_run_provider(
                    root,
                    args.agent_config,
                    args.provider,
                    args.model,
                    (("state_update", ("audit",)),),
                )
                return 0
            instruction = read_state_update_instruction(args.instruction, args.input)
            provider = (
                load_state_update_provider(
                    root,
                    args.provider,
                    chapter_number=args.chapter_number,
                    agent_config_path=args.agent_config,
                    model_name=args.model,
                )
                if args.propose
                else None
            )
            result = accept_chapter(
                AcceptChapterOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    allow_issues=args.allow_issues,
                    propose=args.propose,
                    instruction=instruction,
                    force_proposal=args.force,
                ),
                provider,
            )
        except StateUpdateError as exc:
            return _failure(args, str(exc), error_type="state_update_error")
        except Exception as exc:
            return _failure(args, f"chapter acceptance failed: {exc}", error_type="state_update_error")

        lines = []
        if result.proposal_result:
            lines.append(f"Wrote state update proposal: {result.proposal_result.proposal_path}")
        lines.extend(
            [
                f"Accepted chapter: {result.accepted_path}",
                f"Updated current state: {result.apply_result.state_path}",
                f"Updated timeline: {result.apply_result.timeline_path}",
            ]
        )
        return _success(
            args,
            {
                "command": "accept-chapter",
                "accepted_path": str(result.accepted_path),
                "state_path": str(result.apply_result.state_path),
                "timeline_path": str(result.apply_result.timeline_path),
                "proposal_path": str(result.proposal_result.proposal_path)
                if result.proposal_result
                else None,
            },
            lines,
        )

    if args.command == "generate-chapter":
        root = Path(args.path)
        try:
            if args.dry_run_provider:
                _print_dry_run_provider(
                    root,
                    args.agent_config,
                    args.provider,
                    args.model,
                    (
                        ("plot", ()),
                        ("writer", ()),
                        ("polish", ()),
                        ("audit", ()),
                    ),
                )
                return 0
            instruction = read_workflow_instruction(args.instruction, args.input)
            result = generate_chapter(
                GenerateChapterOptions(
                    root=root,
                    chapter_number=args.chapter_number,
                    instruction=instruction,
                    force=args.force,
                    provider_name=args.provider,
                    agent_config_path=args.agent_config,
                    model_name=args.model,
                    target_words=args.target_words,
                    style_note=args.style_note,
                    skip_polish=args.skip_polish,
                    skip_audit=args.skip_audit,
                    stop_after=args.stop_after,
                )
            )
        except WorkflowError as exc:
            return _failure(args, str(exc), error_type="workflow_error")
        except Exception as exc:
            return _failure(args, f"chapter generation failed: {exc}", error_type="workflow_error")

        lines = [result.message, f"Run log: {result.run_log_path}"]
        lines.extend(f"{step.step_id} {step.agent}: {step.status}" for step in result.run_log.steps)
        return _success(
            args,
            {
                "command": "generate-chapter",
                "message": result.message,
                "run_log_path": str(result.run_log_path),
                "status": result.run_log.status,
                "steps": [
                    {
                        "step_id": step.step_id,
                        "agent": step.agent,
                        "status": step.status,
                        "output_files": step.output_files,
                        "error": step.error,
                    }
                    for step in result.run_log.steps
                ],
            },
            lines,
        )

    if args.command == "export":
        root = Path(args.path)
        if args.export_command == "markdown":
            try:
                chapters = parse_chapter_selector(args.chapters)
                result = export_markdown(
                    MarkdownExportOptions(
                        root=root,
                        chapters=chapters,
                        from_chapter=args.from_chapter,
                        to_chapter=args.to_chapter,
                        include_unaccepted=args.include_unaccepted,
                        output_path=args.output,
                        title=args.title,
                        force=args.force,
                    )
                )
            except ExportError as exc:
                return _failure(args, str(exc), error_type="export_error")
            except Exception as exc:
                return _failure(args, f"markdown export failed: {exc}", error_type="export_error")

            lines = [
                *(f"warning: {warning}" for warning in result.warnings),
                f"Wrote Markdown export: {result.output_path}",
                f"Updated export manifest: {result.manifest_path}",
                f"Chapters: {', '.join(str(number) for number in result.exported_chapters)}",
            ]
            return _success(
                args,
                {
                    "command": "export markdown",
                    "output_path": str(result.output_path),
                    "manifest_path": str(result.manifest_path),
                    "chapters": list(result.exported_chapters),
                    "warnings": list(result.warnings),
                },
                lines,
            )
        if args.export_command == "docx":
            try:
                chapters = parse_chapter_selector(args.chapters)
                result = export_docx(
                    DocxExportOptions(
                        root=root,
                        chapters=chapters,
                        from_chapter=args.from_chapter,
                        to_chapter=args.to_chapter,
                        include_unaccepted=args.include_unaccepted,
                        output_path=args.output,
                        title=args.title,
                        force=args.force,
                    )
                )
            except ExportError as exc:
                return _failure(args, str(exc), error_type="export_error")
            except Exception as exc:
                return _failure(args, f"docx export failed: {exc}", error_type="export_error")

            lines = [
                *(f"warning: {warning}" for warning in result.warnings),
                f"Wrote DOCX export: {result.output_path}",
                f"Updated export manifest: {result.manifest_path}",
                f"Chapters: {', '.join(str(number) for number in result.exported_chapters)}",
            ]
            return _success(
                args,
                {
                    "command": "export docx",
                    "output_path": str(result.output_path),
                    "manifest_path": str(result.manifest_path),
                    "chapters": list(result.exported_chapters),
                    "warnings": list(result.warnings),
                },
                lines,
            )

    if args.command == "web":
        from novel.web_server import run_web_server

        run_web_server(host=args.host, port=args.port)
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2
