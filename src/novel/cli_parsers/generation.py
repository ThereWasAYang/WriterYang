from __future__ import annotations

from pathlib import Path

from novel.cli_parsers.common import ParserCollection
from novel.cli_shared import (
    _add_agent_runtime_args,
    _add_search_context_args,
)


def register_generation_parsers(subparsers: ParserCollection) -> None:
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
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
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
    _add_search_context_args(inspire_parser)

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
        choices=("config", "mock", "openai", "openai_compatible", "deepseek", "zai"),
        help="Provider to use. Defaults to config/agents.yaml.",
    )
    _add_agent_runtime_args(canon_suggest)
    canon_suggest.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Save proposal JSON to this file. Refuses to overwrite.",
    )
    _add_search_context_args(canon_suggest)

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
        "--toc",
        action="store_true",
        help="Include a Markdown table of contents.",
    )
    export_markdown_parser.add_argument(
        "--volume-title",
        default=None,
        help="Optional volume title inserted before chapters and inside the table of contents.",
    )
    export_markdown_parser.add_argument(
        "--chapter-number-style",
        choices=("chinese", "arabic", "chapter", "plain"),
        default="chinese",
        help="Chapter heading style. Defaults to chinese, for example 第一章.",
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

    preview_parser = subparsers.add_parser("preview", help="Build non-production preview packages")
    preview_subparsers = preview_parser.add_subparsers(dest="preview_command", required=True)
    preview_package = preview_subparsers.add_parser("package", help="Package working chapter candidates")
    preview_package.add_argument("--path", default=".", help="Workspace directory.")
    preview_package.add_argument("--chapters", default=None, help="Comma-separated chapter numbers.")
    preview_package.add_argument("--from", dest="from_chapter", type=int, default=None, help="First chapter.")
    preview_package.add_argument("--to", dest="to_chapter", type=int, default=None, help="Last chapter.")
    preview_package.add_argument("--source", choices=("draft", "polished"), default="polished")
    preview_package.add_argument("--title", default=None, help="Optional preview title.")
