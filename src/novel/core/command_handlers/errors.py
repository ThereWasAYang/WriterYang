from __future__ import annotations

from novel.core import web_launcher
from novel.core.canon import CanonError
from novel.core.chapter_memory import ChapterMemoryError
from novel.core.command_bus import DomainError
from novel.core.config_mutations import ConfigMutationError
from novel.core.exporting import ExportError
from novel.core.inspection import ProjectReadError
from novel.core.inspiration import InspirationError
from novel.core.memory_repair import MemoryRepairError
from novel.core.previewing import PreviewError
from novel.core.revision_workflow import RevisionWorkflowError
from novel.core.search import SearchError
from novel.core.session import CreationSessionError
from novel.core.setup_guide import SetupGuideError
from novel.core.style_guide import StyleGuideGenerationError
from novel.core.workspace import WorkspaceExistsError
from novel.core.workspace_mutations import WorkspaceMutationError


def map_domain_error(error: Exception) -> DomainError | None:
    """Translate application errors at the handler boundary without coupling the dispatcher to domains."""

    if isinstance(error, WorkspaceExistsError):
        return DomainError("workspace_exists", str(error), recoverable=True)
    if isinstance(error, CreationSessionError):
        return DomainError("session_error", str(error), recoverable=True)
    if isinstance(error, RevisionWorkflowError):
        return DomainError("revision_error", str(error), recoverable=True)
    if isinstance(error, InspirationError):
        return DomainError("inspiration_error", str(error), recoverable=True)
    if isinstance(error, CanonError):
        return DomainError("canon_error", str(error), recoverable=True)
    if isinstance(error, ChapterMemoryError):
        return DomainError("chapter_memory_error", str(error), recoverable=True)
    if isinstance(error, StyleGuideGenerationError):
        return DomainError("style_guide_error", str(error), recoverable=True)
    if isinstance(error, (WorkspaceMutationError, ConfigMutationError)):
        return DomainError(error.code, error.message, recoverable=True)
    if isinstance(error, SetupGuideError):
        return DomainError("setup_guide_error", str(error), recoverable=True)
    if isinstance(error, web_launcher.PortUnavailableError):
        return DomainError("port_unavailable", str(error), recoverable=True)
    if isinstance(error, web_launcher.WebLauncherError):
        return DomainError("web_launcher_error", str(error), recoverable=True)
    if isinstance(error, MemoryRepairError):
        return DomainError("memory_repair_error", str(error), recoverable=True)
    if isinstance(error, ExportError):
        return DomainError("export_error", str(error), recoverable=True)
    if isinstance(error, PreviewError):
        return DomainError("preview_error", str(error), recoverable=True)
    if isinstance(error, SearchError):
        return DomainError("search_error", str(error), recoverable=True)
    if isinstance(error, ProjectReadError):
        return DomainError("project_read_error", str(error), recoverable=True)
    return None


__all__ = ["map_domain_error"]
