from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath

from pydantic import Field, field_validator

from novel.core.contracts.common import SchemaV3Model


class ProseArtifactKind(StrEnum):
    INSPIRATION = "inspiration"
    CHAPTER_DRAFT = "chapter_draft"
    POLISHED_CHAPTER = "polished_chapter"
    CHAPTER_REVISION = "chapter_revision"


class ProseArtifactPayload(SchemaV3Model):
    """Strict inter-agent envelope for prose that is rendered to editable Markdown."""

    artifact_kind: ProseArtifactKind
    chapter_number: int | None
    body_markdown: str = Field(min_length=1)
    source_artifact_refs: list[str]
    assumptions: list[str]
    warnings: list[str]
    change_summary: str = Field(min_length=1)

    @field_validator("chapter_number")
    @classmethod
    def positive_chapter_number(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("chapter_number must be positive")
        return value

    @field_validator("body_markdown", "change_summary")
    @classmethod
    def non_blank_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("text must not be blank")
        return text

    @field_validator("source_artifact_refs")
    @classmethod
    def safe_source_refs(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            text = value.strip().replace("\\", "/")
            path = PurePosixPath(text)
            if not text or path.is_absolute() or ".." in path.parts:
                raise ValueError("source_artifact_refs must be safe project-relative paths")
            if text not in normalized:
                normalized.append(text)
        if not normalized:
            raise ValueError("source_artifact_refs must not be empty")
        return normalized

    @field_validator("assumptions", "warnings")
    @classmethod
    def clean_notes(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))
