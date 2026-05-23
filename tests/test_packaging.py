from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from importlib import metadata
from io import StringIO
import subprocess
import sys
from pathlib import Path

import novel
from novel.cli import build_parser, main
from novel.core.validation import validate_project


def test_novel_version_command_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "novel", "--version"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == f"novel {novel.__version__}"
    assert result.stderr == ""


def test_example_project_validates() -> None:
    report = validate_project(Path("examples/rain_station"))

    assert report.ok, [message.message for message in report.messages]


def test_example_project_validates_from_cli() -> None:
    code, stdout, stderr = _run_cli(["validate", "--path", "examples/rain_station", "--json"])

    assert code == 0
    assert stderr == ""
    assert '"ok": true' in stdout


def test_readme_core_commands_match_cli() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    parser_help = build_parser().format_help()

    for command in (
        "novel init",
        "novel validate",
        "novel status",
        "novel inspire",
        "novel canon suggest",
        "novel plan-chapter",
        "novel write-chapter",
        "novel polish-chapter",
        "novel audit-chapter",
        "novel accept-chapter",
        "novel export markdown",
        "novel --version",
    ):
        assert command in readme
    for parser_command in (
        "init",
        "validate",
        "status",
        "inspire",
        "canon",
        "plan-chapter",
        "write-chapter",
        "polish-chapter",
        "audit-chapter",
        "generate-chapter",
        "export",
    ):
        assert parser_command in parser_help


def test_package_console_script_entry_point_is_declared() -> None:
    entry_points = metadata.entry_points(group="console_scripts")
    novel_points = [entry for entry in entry_points if entry.name == "novel"]

    assert novel_points
    assert novel_points[0].value == "novel.cli:main"


def test_packaging_metadata_version_matches_package() -> None:
    assert metadata.version("writeryang") == novel.__version__


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
