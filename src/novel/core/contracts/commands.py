from __future__ import annotations

from datetime import datetime

from pydantic import Field

from novel.core.contracts.common import SchemaV3Model, Surface


class CommandEnvelope(SchemaV3Model):
    command_id: str = Field(pattern=r"^cmd_[0-9a-f]{32}$")
    command_type: str = Field(min_length=1)
    surface: Surface
    project_root: str = Field(min_length=1)
    issued_at: datetime
