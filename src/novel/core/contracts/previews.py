from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from novel.core.contracts.artifacts import ArtifactRef
from novel.core.contracts.common import SchemaV3Model, Sha256


class PreviewSourceChapter(SchemaV3Model):
    chapter_number: int = Field(ge=1)
    title: str = Field(min_length=1)
    source_kind: Literal["draft", "polished"]
    path: str = Field(min_length=1)
    sha256: Sha256
    artifact_ref: ArtifactRef | None = None


class PreviewManifest(SchemaV3Model):
    preview_id: str = Field(pattern=r"^preview_[0-9]{8}_[0-9]{6}_[0-9]{6}$")
    package_kind: Literal["preview"] = "preview"
    production_eligible: Literal[False] = False
    title: str = Field(min_length=1)
    source_kind: Literal["draft", "polished"]
    source_chapters: list[PreviewSourceChapter] = Field(min_length=1)
    content_path: str = Field(min_length=1)
    content_sha256: Sha256
    warning: str = Field(min_length=1)
    created_at: datetime
