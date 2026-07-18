from __future__ import annotations

from pathlib import Path

from novel.cli_parsers.common import ParserCollection


def register_project_parsers(subparsers: ParserCollection) -> None:
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
    guide_group = init_parser.add_mutually_exclusive_group()
    guide_group.add_argument(
        "--guide",
        action="store_true",
        help="Run the interactive initial setup guide after creating the workspace.",
    )
    guide_group.add_argument(
        "--no-guide",
        action="store_true",
        help="Skip the interactive initial setup guide.",
    )

    validate_parser = subparsers.add_parser("validate", help="Validate a novel project workspace")
    validate_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to validate. Defaults to the current directory.",
    )

    schema_parser = subparsers.add_parser("schema", help="Export JSON Schema files")
    schema_subparsers = schema_parser.add_subparsers(dest="schema_command", required=True)
    schema_export_parser = schema_subparsers.add_parser("export", help="Export JSON Schema files")
    schema_export_parser.add_argument(
        "--output",
        type=Path,
        default=Path("schemas"),
        help="Output directory. Defaults to ./schemas.",
    )

    completion_parser = subparsers.add_parser("completion", help="Print shell completion script")
    completion_parser.add_argument(
        "shell",
        choices=("bash", "zsh", "fish"),
        help="Shell to generate completion for.",
    )

    doctor_parser = subparsers.add_parser("doctor", help="Check local environment and project health")
    doctor_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to check. Defaults to the current directory.",
    )

    status_parser = subparsers.add_parser("status", help="Show project status")
    status_parser.add_argument(
        "--path",
        default=".",
        help="Workspace directory to inspect. Defaults to the current directory.",
    )

    usage_parser = subparsers.add_parser("usage", help="Show provider token usage statistics")
    usage_parser.add_argument(
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

    web_parser = subparsers.add_parser("web", help="Run the local Web UI")
    web_parser.add_argument(
        "--path",
        default=".",
        help="Novel project directory whose project.yaml may define web.default_port. Defaults to current directory.",
    )
    web_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind. Defaults to 127.0.0.1.",
    )
    web_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind. Overrides project.yaml web.default_port. Defaults to 8765.",
    )
    web_open_group = web_parser.add_mutually_exclusive_group()
    web_open_group.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the Web UI URL in the default browser after the server starts.",
    )
    web_open_group.add_argument(
        "--no-open",
        dest="open_browser",
        action="store_false",
        help="Do not open a browser automatically. This is the default.",
    )
    web_parser.set_defaults(open_browser=False)

    web_launch_parser = subparsers.add_parser("web-launch", help="Run the Web UI from launcher config")
    web_launch_parser.add_argument(
        "--config",
        type=Path,
        default=Path("WriterYang_WebUI.config.json"),
        help="Web UI launcher config path. Defaults to WriterYang_WebUI.config.json.",
    )
    web_launch_open_group = web_launch_parser.add_mutually_exclusive_group()
    web_launch_open_group.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the Web UI URL in the default browser after the server starts. This is the default.",
    )
    web_launch_open_group.add_argument(
        "--no-open",
        dest="open_browser",
        action="store_false",
        help="Do not open a browser automatically.",
    )
    web_launch_parser.set_defaults(open_browser=True)
