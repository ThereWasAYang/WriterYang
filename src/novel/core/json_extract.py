from __future__ import annotations

import re


class JsonExtractionError(ValueError):
    """Raised when provider text does not contain a JSON object."""


def strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def extract_json_object(content: str, *, error_message: str = "provider response did not contain a JSON object") -> str:
    stripped = strip_code_fence(content)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise JsonExtractionError(error_message)
    return stripped[start : end + 1]
