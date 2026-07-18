from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from novel.core.command_bus import DomainError, _handler, _result
from novel.core.contracts import (
    CommandEnvelope,
    CommandResult,
    ProjectInitCommand,
    ProjectShowCommand,
    ProjectStatusCommand,
    ProjectValidateCommand,
    SchemaExportCommand,
)
from novel.core.inspection import (
    format_canon,
    format_characters,
    format_state,
    format_timeline,
    get_project_status,
)
from novel.core.io import load_json
from novel.core.json_schema import export_json_schemas
from novel.core.validation import validate_project
from novel.core.workspace import (
    InitOptions,
    init_workspace,
)


@_handler("project.status")
def _handle_project_status(envelope: CommandEnvelope, root: Path) -> CommandResult:
    if not isinstance(envelope.command, ProjectStatusCommand):
        raise DomainError("invalid_command", "project.status payload type mismatch")
    status = get_project_status(root)
    payload = asdict(status)
    payload["latest_run_log"] = str(status.latest_run_log) if status.latest_run_log else None
    return _result(envelope, result={"status": payload})


@_handler("project.init")
def _handle_project_init(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, ProjectInitCommand):
        raise DomainError("invalid_command", "project.init payload type mismatch")
    value = init_workspace(
        InitOptions(
            title=command.title,
            root=root,
            project_id=command.project_id,
            language=command.language,
            genre=command.genre,
        )
    )
    return _result(
        envelope,
        result={
            "root": str(value.root),
            "created_files": [str(path) for path in value.created_files],
            "created_dirs": [str(path) for path in value.created_dirs],
        },
        changed_paths=[path.relative_to(value.root).as_posix() for path in value.created_files],
    )

@_handler("project.validate")
def _handle_project_validate(envelope: CommandEnvelope, root: Path) -> CommandResult:
    if not isinstance(envelope.command, ProjectValidateCommand):
        raise DomainError("invalid_command", "project.validate payload type mismatch")
    report = validate_project(root)
    return _result(
        envelope,
        result={
            "root": str(report.root),
            "valid": report.ok,
            "error_count": len(report.errors),
            "warning_count": len(report.warnings),
            "messages": [
                {
                    "level": message.level,
                    "path": (
                        message.path.relative_to(report.root).as_posix()
                        if message.path.is_relative_to(report.root)
                        else str(message.path)
                    ),
                    "message": message.message,
                }
                for message in report.messages
            ],
        },
    )


@_handler("schema.export")
def _handle_schema_export(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, SchemaExportCommand):
        raise DomainError("invalid_command", "schema.export payload type mismatch")
    output = Path(command.output_path).expanduser().resolve()
    paths = export_json_schemas(output)
    return _result(
        envelope,
        result={
            "output": str(output),
            "schema_count": len(paths),
            "files": [str(path) for path in paths],
        },
        changed_paths=[str(path) for path in paths],
    )


@_handler("project.show")
def _handle_project_show(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, ProjectShowCommand):
        raise DomainError("invalid_command", "project.show payload type mismatch")
    paths = {
        "characters": root / "memory" / "canon" / "characters.json",
        "timeline": root / "memory" / "state" / "timeline.json",
        "state": root / "memory" / "state" / "current_state.json",
        "canon": root / "memory" / "canon" / "characters.json",
    }
    path = paths[command.target]
    output = {
        "characters": format_characters,
        "timeline": format_timeline,
        "state": format_state,
        "canon": format_canon,
    }[command.target](root)
    return _result(
        envelope,
        result={
            "target": command.target,
            "path": str(path),
            "data": load_json(path),
            "output": output,
        },
    )
