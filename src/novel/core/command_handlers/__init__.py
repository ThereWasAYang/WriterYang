from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novel.core.command_bus import DomainError

_REGISTERED = False


def register_builtin_handlers() -> None:
    """Import bounded handler modules exactly once so their decorators register commands."""

    global _REGISTERED
    if _REGISTERED:
        return
    from novel.core.command_handlers import generation, project, publishing, session  # noqa: F401

    _REGISTERED = True


def map_domain_error(error: Exception) -> DomainError | None:
    from novel.core.command_handlers.errors import map_domain_error as map_error

    return map_error(error)


__all__ = ["map_domain_error", "register_builtin_handlers"]
