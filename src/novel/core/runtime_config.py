from __future__ import annotations

from novel.core.schemas import PolishMode, ProjectConfig


def normalize_polish_mode(value: str | None) -> PolishMode:
    if value is None or not str(value).strip():
        raise ValueError("polish mode must not be empty")
    normalized = str(value).strip().lower().replace("-", "_")
    if normalized in {"single_pass", "single"}:
        return "single_pass"
    if normalized == "auto":
        return "auto"
    if normalized in {"review_gate", "review"}:
        return "review_gate"
    raise ValueError(f"unsupported polish mode: {value}")


def project_polish_mode(project: ProjectConfig) -> PolishMode:
    if project.polish is None:
        return "single_pass"
    return normalize_polish_mode(project.polish.mode)
