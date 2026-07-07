# mypy: ignore-errors
# ruff: noqa: F403,F405
from __future__ import annotations

from .deps import *

class MemoryRepairError(RuntimeError):
    """Raised when a memory repair proposal cannot be created or applied safely."""


@dataclass(frozen=True)
class MemoryRepairSuggestResult:
    proposal: MemoryRepairProposal
    proposal_path: Path
    markdown_path: Path


@dataclass(frozen=True)
class MemoryRepairApplyResult:
    proposal: MemoryRepairProposal
    apply_log: MemoryRepairApplyLog
    apply_log_path: Path


@dataclass(frozen=True)
class SettingChangeSuggestionResult:
    status: Literal["proposal_ready", "needs_clarification"]
    proposal_result: MemoryRepairSuggestResult | None = None
    clarification: MemoryChangeClarificationSession | None = None


@dataclass(frozen=True)
class _PreparedMemoryRepairDecision:
    decision: MemoryRepairDecision
    target_files: list[str]
    operations: list[MemoryRepairOperation]
    notes: list[str]
    change_kind: MemoryChangeKind

__all__ = [name for name in globals() if not name.startswith("__")]
