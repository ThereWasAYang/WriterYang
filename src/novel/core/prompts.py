from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


PROMPT_VERSION = "2026-05-24"


@lru_cache(maxsize=None)
def load_prompt_template(name: str) -> str:
    if not name.endswith(".txt"):
        name = f"{name}.txt"
    return files("novel.prompts").joinpath(name).read_text(encoding="utf-8").strip()


def render_prompt_template(name: str, **values: object) -> str:
    template = load_prompt_template(name)
    return template.format(**{key: str(value) for key, value in values.items()})
