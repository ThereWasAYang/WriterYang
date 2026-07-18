from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


def schema_text(name: str) -> str:
    resource_name = name if name.endswith(".schema.json") else f"{name}.schema.json"
    return files(__package__).joinpath(resource_name).read_text(encoding="utf-8")


def schema_payload(name: str) -> dict[str, Any]:
    payload = json.loads(schema_text(name))
    if not isinstance(payload, dict):
        raise ValueError(f"packaged schema {name} must contain a JSON object")
    return payload


__all__ = ["schema_payload", "schema_text"]
