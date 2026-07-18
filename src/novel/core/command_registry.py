from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args, get_origin

from pydantic import BaseModel

from novel.core.contracts.commands import PUBLIC_COMMAND_MODELS, CommandResult

CommandAccess = Literal["read", "write", "unlocked_write"]

_READ_ONLY = {
    "project.status",
    "project.validate",
    "project.show",
    "search",
    "session.show",
    "revision.blocks",
    "revision.show",
}
_UNLOCKED_WRITE = {"project.init", "schema.export", "session.cancel"}
_CONFIRMATION_REQUIRED = {
    "canon.apply",
    "session.accept",
    "session.archive",
    "revision.accept",
    "memory_repair.apply",
    "setting_change.apply",
    "export.markdown",
    "export.docx",
}


@dataclass(frozen=True)
class CommandSpec:
    """单一公开 command 的输入、输出与横切策略。"""

    command_type: str
    request_model: type[BaseModel]
    response_model: type[BaseModel]
    access: CommandAccess
    confirmation_required: bool
    error_codes: tuple[str, ...]

    @property
    def lock_required(self) -> bool:
        return self.access == "write"


def _command_types(model: type[BaseModel]) -> tuple[str, ...]:
    annotation = model.model_fields["type"].annotation
    if get_origin(annotation) is not Literal:
        raise RuntimeError(f"{model.__name__}.type must use Literal")
    values = get_args(annotation)
    if not values or not all(isinstance(value, str) for value in values):
        raise RuntimeError(f"{model.__name__}.type must contain string literals")
    return tuple(values)


def _build_registry() -> dict[str, CommandSpec]:
    registry: dict[str, CommandSpec] = {}
    for model in PUBLIC_COMMAND_MODELS:
        for command_type in _command_types(model):
            if command_type in registry:
                raise RuntimeError(f"duplicate command spec: {command_type}")
            access: CommandAccess = (
                "read"
                if command_type in _READ_ONLY
                else "unlocked_write"
                if command_type in _UNLOCKED_WRITE
                else "write"
            )
            registry[command_type] = CommandSpec(
                command_type=command_type,
                request_model=model,
                response_model=CommandResult,
                access=access,
                confirmation_required=command_type in _CONFIRMATION_REQUIRED,
                error_codes=(
                    "invalid_command",
                    "confirmation_required",
                    "project_locked",
                    "budget_exceeded",
                    "internal_error",
                ),
            )
    return registry


COMMAND_SPECS = _build_registry()


def command_spec(command_type: str) -> CommandSpec:
    try:
        return COMMAND_SPECS[command_type]
    except KeyError as exc:
        raise KeyError(f"unknown public command: {command_type}") from exc


def command_catalog() -> dict[str, object]:
    return {
        command_type: {
            "request_schema": spec.request_model.model_json_schema(),
            "response_schema": spec.response_model.model_json_schema(),
            "access": spec.access,
            "confirmation_required": spec.confirmation_required,
            "error_codes": list(spec.error_codes),
        }
        for command_type, spec in sorted(COMMAND_SPECS.items())
    }
