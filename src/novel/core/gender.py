from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Literal


CharacterGenderValue = Literal["男", "女"]

_MALE_MARKERS = ("男性", "男子", "长子", "次子", "幼子", "少子", "庶子", "嫡子")
_FEMALE_MARKERS = ("女性", "女子", "长女", "次女", "幼女", "少女", "姑娘", "庶女", "嫡女")
_MALE_RANK_PATTERN = re.compile(r"[\u4e00-\u9fff]{1,8}(?:家|氏)[长次二三四五六七八九十幼少庶嫡]?子")
_FEMALE_RANK_PATTERN = re.compile(r"[\u4e00-\u9fff]{1,8}(?:家|氏)[长次二三四五六七八九十幼少庶嫡]?女")


def canonical_gender(value: object) -> CharacterGenderValue | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"男", "男性", "male", "m"}:
        return "男"
    if normalized in {"女", "女性", "female", "f"}:
        return "女"
    return None


def has_male_marker(text: str) -> bool:
    if any(marker in text for marker in _MALE_MARKERS):
        return True
    return bool(_MALE_RANK_PATTERN.search(text))


def has_female_marker(text: str) -> bool:
    if any(marker in text for marker in _FEMALE_MARKERS):
        return True
    return bool(_FEMALE_RANK_PATTERN.search(text))


def infer_gender_from_texts(values: list[str]) -> CharacterGenderValue | None:
    text = " ".join(values)
    has_male = has_male_marker(text)
    has_female = has_female_marker(text)
    if has_male and not has_female:
        return "男"
    if has_female and not has_male:
        return "女"
    return None


def infer_gender_from_character_payload(value: Mapping[str, object]) -> CharacterGenderValue | None:
    explicit_genders: set[CharacterGenderValue] = set()
    text_values: list[str] = []
    for item in _string_values(value.get("tags")):
        gender = canonical_gender(item)
        if gender is not None:
            explicit_genders.add(gender)
        else:
            text_values.append(item)
    if len(explicit_genders) == 1:
        return next(iter(explicit_genders))
    if len(explicit_genders) > 1:
        return None

    for field in ("reader_visible_summary", "private_author_notes"):
        field_value = value.get(field)
        if isinstance(field_value, str):
            text_values.append(field_value)
    return infer_gender_from_texts(text_values)


def infer_character_gender(character: Any) -> CharacterGenderValue | None:
    gender = canonical_gender(getattr(character, "gender", None))
    if gender is not None:
        return gender

    appearance = getattr(character, "appearance", None)
    if isinstance(appearance, Mapping):
        gender = canonical_gender(appearance.get("gender"))
        if gender is not None:
            return gender

    values: list[str] = []
    for value in (
        getattr(character, "reader_visible_summary", None),
        getattr(character, "private_author_notes", None),
    ):
        if isinstance(value, str):
            values.append(value)
    tags = getattr(character, "tags", None)
    if isinstance(tags, list):
        values.extend(item for item in tags if isinstance(item, str))
    return infer_gender_from_texts(values)


def strip_explicit_gender_tags(tags: object) -> tuple[list[object] | None, bool]:
    if not isinstance(tags, list):
        return None, False
    stripped = [item for item in tags if canonical_gender(item) is None]
    return stripped, len(stripped) != len(tags)


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
