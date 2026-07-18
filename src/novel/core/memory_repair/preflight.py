from __future__ import annotations

from .deps import (
    ALLOWED_MEMORY_FILES,
    CHARACTER_ROLE_IDENTITY_PATTERNS,
    NARRATIVE_CHARACTER_ROLES,
    UNIQUE_ID_COLLECTIONS,
    Iterable,
    MemoryChangeKind,
    MemoryRepairOperation,
    MemoryRepairProposal,
    Path,
    _apply_operations_to_data,
    _pointer_parts,
    canonical_gender,
    infer_gender_from_character_payload,
    json,
    load_json,
    re,
    strip_explicit_gender_tags,
)
from .validation import (
    _group_operations,
    _validate_file_model,
)


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"-?\d+", text):
            return int(text)
    return None


def _preview_operations(root: Path, proposal: MemoryRepairProposal) -> dict[str, object]:
    preview: dict[str, object] = {}
    for rel_path, operations in _group_operations(proposal.operations).items():
        try:
            preview[rel_path] = _apply_operations_to_data(load_json(root / rel_path), operations)
        except Exception as exc:
            preview[rel_path] = {"error": str(exc)}
    return preview


def _preflight_memory_repair_operations(
    root: Path,
    operations: list[MemoryRepairOperation],
    *,
    change_kind: MemoryChangeKind | None = None,
) -> list[str]:
    if not operations:
        return []
    contract_errors = _preflight_operation_contract_errors(operations)
    if contract_errors:
        return contract_errors
    errors: list[str] = []
    try:
        grouped = _group_operations(operations)
    except Exception as exc:
        return [str(exc)]
    for rel_path, file_operations in grouped.items():
        try:
            data = load_json(root / rel_path)
            updated = _apply_operations_to_data(data, file_operations)
            _validate_file_model(rel_path, updated)
            errors.extend(_preflight_unique_collection_id_errors(rel_path, updated))
        except Exception as exc:
            errors.append(_preflight_error_message(rel_path, exc))
    if change_kind == "setting_change":
        errors.extend(_preflight_setting_change_add_id_conflicts(root, operations))
        errors.extend(_preflight_setting_change_semantics(operations))
        errors.extend(_preflight_hidden_truth_reader_visible_leaks(root, operations))
    return errors


def _preflight_error_message(rel_path: str, exc: Exception) -> str:
    message = str(exc)
    if message.startswith(f"{rel_path}:") or f"for {rel_path}:" in message:
        return message
    return f"{rel_path}: {message}"


def _preflight_operation_contract_errors(operations: list[MemoryRepairOperation]) -> list[str]:
    errors: list[str] = []
    for operation in operations:
        if operation.op in {"add", "replace"} and "value" not in operation.model_fields_set:
            errors.append(
                f"{operation.file} {operation.path}: {operation.op} operation must include value; "
                "use explicit null only when null is the intended value"
            )
    return errors


def _restore_regressed_existing_add_operations(
    root: Path,
    previous_operations: list[MemoryRepairOperation],
    operations: list[MemoryRepairOperation],
) -> tuple[list[MemoryRepairOperation], list[str]]:
    previous_replace_operations = _existing_replace_operations_by_entity_id(root, previous_operations)
    if not previous_replace_operations:
        return operations, []
    current_replace_keys = {
        key
        for operation in operations
        if operation.op == "replace"
        for key in [_existing_replace_operation_key(root, operation)]
        if key is not None
    }
    restored: list[MemoryRepairOperation] = []
    restored_keys: set[tuple[str, str]] = set()
    notes: list[str] = []
    for operation in operations:
        add_key = _duplicate_existing_add_operation_key(root, operation)
        if add_key is None or add_key not in previous_replace_operations:
            restored.append(operation)
            continue
        if add_key not in current_replace_keys and add_key not in restored_keys:
            restored.extend(previous_replace_operations[add_key])
        restored_keys.add(add_key)
    if restored_keys:
        restored_labels = ", ".join(f"{rel_path} {entity_id}" for rel_path, entity_id in sorted(restored_keys))
        notes.append("已还原 target-schema repair 退化的重复新增操作：" + restored_labels)
    return restored, notes


def _existing_replace_operations_by_entity_id(
    root: Path,
    operations: list[MemoryRepairOperation],
) -> dict[tuple[str, str], list[MemoryRepairOperation]]:
    grouped: dict[tuple[str, str], list[MemoryRepairOperation]] = {}
    for operation in operations:
        if operation.op != "replace":
            continue
        key = _existing_replace_operation_key(root, operation)
        if key is None:
            continue
        grouped.setdefault(key, []).append(operation)
    return grouped


def _existing_replace_operation_key(root: Path, operation: MemoryRepairOperation) -> tuple[str, str] | None:
    collection_info = UNIQUE_ID_COLLECTIONS.get(operation.file)
    if collection_info is None:
        return None
    collection_key, _label = collection_info
    parts = _pointer_parts(operation.path)
    if len(parts) < 2 or parts[0] != collection_key or not parts[1].isdigit():
        return None
    item_id = _operation_existing_collection_item_id(root, operation, collection_key, int(parts[1]))
    if item_id is None:
        return None
    existing_indexes = _existing_collection_id_index(root, operation.file, collection_key)
    if item_id not in existing_indexes:
        return None
    return (operation.file, item_id)


def _duplicate_existing_add_operation_key(root: Path, operation: MemoryRepairOperation) -> tuple[str, str] | None:
    if operation.op != "add" or not isinstance(operation.value, dict):
        return None
    collection_info = UNIQUE_ID_COLLECTIONS.get(operation.file)
    if collection_info is None:
        return None
    collection_key, _label = collection_info
    if _pointer_parts(operation.path) != [collection_key, "-"]:
        return None
    item_id = operation.value.get("id")
    if not isinstance(item_id, str) or not item_id:
        return None
    existing_indexes = _existing_collection_id_index(root, operation.file, collection_key)
    if item_id not in existing_indexes:
        return None
    return (operation.file, item_id)


def _operation_existing_collection_item_id(
    root: Path,
    operation: MemoryRepairOperation,
    collection_key: str,
    index: int,
) -> str | None:
    if isinstance(operation.value, dict):
        item_id = operation.value.get("id")
        if isinstance(item_id, str) and item_id:
            return item_id
    try:
        data = load_json(root / operation.file)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    collection = data.get(collection_key)
    if not isinstance(collection, list) or index >= len(collection):
        return None
    item = collection[index]
    if not isinstance(item, dict):
        return None
    item_id = item.get("id")
    return item_id if isinstance(item_id, str) and item_id else None


def _existing_collection_id_index(root: Path, rel_path: str, collection_key: str) -> dict[str, int]:
    try:
        data = load_json(root / rel_path)
    except Exception:
        return {}
    return _collection_id_index(data, collection_key)


def _preflight_unique_collection_id_errors(rel_path: str, data: object) -> list[str]:
    collection_info = UNIQUE_ID_COLLECTIONS.get(rel_path)
    if collection_info is None or not isinstance(data, dict):
        return []
    collection_key, label = collection_info
    collection = data.get(collection_key)
    if not isinstance(collection, list):
        return []
    seen: dict[str, int] = {}
    errors: list[str] = []
    for index, item in enumerate(collection):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str):
            continue
        if item_id in seen:
            errors.append(
                f"{rel_path}: duplicate {label}: {item_id} at /{collection_key}/{index}; "
                f"first occurrence at /{collection_key}/{seen[item_id]}"
            )
            continue
        seen[item_id] = index
    return errors


def _preflight_setting_change_add_id_conflicts(root: Path, operations: list[MemoryRepairOperation]) -> list[str]:
    errors: list[str] = []
    cached_existing_indexes: dict[str, dict[str, int]] = {}
    for operation in operations:
        if operation.op != "add" or not isinstance(operation.value, dict):
            continue
        collection_info = UNIQUE_ID_COLLECTIONS.get(operation.file)
        if collection_info is None:
            continue
        collection_key, label = collection_info
        parts = _pointer_parts(operation.path)
        if parts != [collection_key, "-"]:
            continue
        item_id = operation.value.get("id")
        if not isinstance(item_id, str) or not item_id:
            continue
        if operation.file not in cached_existing_indexes:
            try:
                data = load_json(root / operation.file)
            except Exception:
                cached_existing_indexes[operation.file] = {}
            else:
                cached_existing_indexes[operation.file] = _collection_id_index(data, collection_key)
        existing_index = cached_existing_indexes[operation.file].get(item_id)
        if existing_index is None:
            continue
        errors.append(
            f"{operation.file} {operation.path}: add would duplicate existing {label}: {item_id} "
            f"at /{collection_key}/{existing_index}; use replace with the existing path instead of add"
        )
    return errors


def _collection_id_index(data: object, collection_key: str) -> dict[str, int]:
    if not isinstance(data, dict):
        return {}
    collection = data.get(collection_key)
    if not isinstance(collection, list):
        return {}
    indexes: dict[str, int] = {}
    for index, item in enumerate(collection):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id not in indexes:
            indexes[item_id] = index
    return indexes


def _normalize_setting_change_gender_operations(
    root: Path,
    operations: list[MemoryRepairOperation],
    *,
    change_kind: MemoryChangeKind | None,
) -> tuple[list[MemoryRepairOperation], list[str]]:
    if change_kind != "setting_change":
        return operations, []
    normalized: list[MemoryRepairOperation] = []
    notes: list[str] = []
    for operation in operations:
        converted = _normalize_character_gender_tag_operation(root, operation)
        if converted is not None:
            normalized.append(converted)
            notes.append(f"{converted.path}={converted.value}")
            continue
        converted = _normalize_character_gender_in_object(operation)
        if converted is not None:
            normalized.append(converted)
            notes.append(_operation_semantic_location(converted))
            continue
        normalized.append(operation)
    if not notes:
        return operations, []
    return normalized, ["已将角色性别设定归一化为 Character.gender：" + "；".join(notes)]


def _normalize_character_gender_tag_operation(
    root: Path,
    operation: MemoryRepairOperation,
) -> MemoryRepairOperation | None:
    parts = _pointer_parts(operation.path)
    if (
        operation.file != "memory/canon/characters.json"
        or operation.op not in {"add", "replace"}
        or len(parts) != 4
        or parts[0] != "characters"
        or not parts[1].isdigit()
        or parts[2] != "tags"
    ):
        return None
    gender = canonical_gender(operation.value)
    if gender is None:
        return None
    character_index = int(parts[1])
    op = "replace" if _character_field_exists(root, character_index, "gender") else "add"
    return operation.model_copy(
        update={
            "op": op,
            "path": f"/characters/{character_index}/gender",
            "value": gender,
            "reason": "将性别标签归一化为 Character.gender；" + operation.reason,
        }
    )


def _normalize_character_gender_in_object(operation: MemoryRepairOperation) -> MemoryRepairOperation | None:
    parts = _pointer_parts(operation.path)
    if (
        operation.file != "memory/canon/characters.json"
        or operation.op not in {"add", "replace"}
        or len(parts) != 2
        or parts[0] != "characters"
        or not isinstance(operation.value, dict)
    ):
        return None
    value = json.loads(json.dumps(operation.value, ensure_ascii=False))
    existing_gender = canonical_gender(value.get("gender"))
    inferred_gender = infer_gender_from_character_payload(value)

    if existing_gender is not None:
        gender_changed = value.get("gender") != existing_gender
        value["gender"] = existing_gender
        stripped_tags, tags_changed = strip_explicit_gender_tags(value.get("tags"))
        if stripped_tags is not None and tags_changed:
            value["tags"] = stripped_tags
        if gender_changed or tags_changed:
            return operation.model_copy(update={"value": value})
        return None

    if inferred_gender is None:
        return None
    value["gender"] = inferred_gender
    stripped_tags, tags_changed = strip_explicit_gender_tags(value.get("tags"))
    if stripped_tags is not None and tags_changed:
        value["tags"] = stripped_tags
    return operation.model_copy(update={"value": value})


def _character_field_exists(root: Path, index: int, field: str) -> bool:
    try:
        data = load_json(root / "memory/canon/characters.json")
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    characters = data.get("characters")
    if not isinstance(characters, list) or index >= len(characters):
        return False
    character = characters[index]
    return isinstance(character, dict) and field in character


def _auto_repair_setting_change_semantics(
    root: Path,
    operations: list[MemoryRepairOperation],
    preflight_errors: list[str],
    *,
    change_kind: MemoryChangeKind | None,
) -> tuple[list[MemoryRepairOperation], list[str], list[str]]:
    if change_kind != "setting_change" or not preflight_errors:
        return operations, [], preflight_errors
    notes: list[str] = []
    operations, local_notes = _auto_repair_character_identity_tags(operations, preflight_errors)
    notes.extend(local_notes)
    operations, local_notes = _auto_repair_timeline_unanchored_backstory(operations, preflight_errors)
    notes.extend(local_notes)
    if not notes:
        return operations, [], preflight_errors
    updated_errors = _preflight_memory_repair_operations(root, operations, change_kind=change_kind)
    return operations, notes, updated_errors


def _auto_repair_character_identity_tags(
    operations: list[MemoryRepairOperation],
    preflight_errors: list[str],
) -> tuple[list[MemoryRepairOperation], list[str]]:
    if not any("Character identity phrase(s) must be in tags" in error for error in preflight_errors):
        return operations, []
    repaired: list[MemoryRepairOperation] = []
    note_details: list[str] = []
    for operation in operations:
        parts = _pointer_parts(operation.path)
        if (
            operation.file != "memory/canon/characters.json"
            or operation.op not in {"add", "replace"}
            or len(parts) != 2
            or parts[0] != "characters"
            or not isinstance(operation.value, dict)
        ):
            repaired.append(operation)
            continue
        value = json.loads(json.dumps(operation.value, ensure_ascii=False))
        tags = _string_values(value.get("tags"))
        missing_tags = [
            phrase
            for phrase in _character_identity_phrases_from_fields(value)
            if phrase not in tags
        ]
        if not missing_tags:
            repaired.append(operation)
            continue
        value["tags"] = [*tags, *missing_tags]
        repaired.append(operation.model_copy(update={"value": value}))
        label = _operation_semantic_location(operation)
        note_details.append(f"{label}: " + ", ".join(missing_tags))
    if not note_details:
        return operations, []
    return repaired, ["已本地补齐 Character.tags 中缺失的身份短语：" + "；".join(note_details)]


def _auto_repair_timeline_unanchored_backstory(
    operations: list[MemoryRepairOperation],
    preflight_errors: list[str],
) -> tuple[list[MemoryRepairOperation], list[str]]:
    if not any("narrative_position.chapter" in error for error in preflight_errors):
        return operations, []
    repaired: list[MemoryRepairOperation] = []
    note_details: list[str] = []
    for operation in operations:
        parts = _pointer_parts(operation.path)
        if (
            operation.file != "memory/state/timeline.json"
            or operation.op not in {"add", "replace"}
            or len(parts) != 2
            or parts[0] != "events"
            or not isinstance(operation.value, dict)
        ):
            repaired.append(operation)
            continue
        value = json.loads(json.dumps(operation.value, ensure_ascii=False))
        narrative = value.get("narrative_position")
        story_position = value.get("story_position")
        if not isinstance(narrative, dict) or not isinstance(story_position, dict):
            repaired.append(operation)
            continue
        chapter = _coerce_int(narrative.get("chapter"))
        time_label = story_position.get("time_label")
        if chapter is None or chapter > 0 or not isinstance(time_label, str) or not time_label.strip():
            repaired.append(operation)
            continue
        value.pop("narrative_position", None)
        repaired.append(operation.model_copy(update={"value": value}))
        event_id = value.get("id")
        label = event_id if isinstance(event_id, str) and event_id else _operation_semantic_location(operation)
        note_details.append(str(label))
    if not note_details:
        return operations, []
    return repaired, [
        "已将未在正文揭示的 timeline 背景事件改为省略 narrative_position，而不是使用 chapter=0："
        + "；".join(note_details)
    ]


def _preflight_hidden_truth_reader_visible_leaks(
    root: Path,
    operations: list[MemoryRepairOperation],
) -> list[str]:
    try:
        data_by_file = _memory_data_after_operations(root, operations)
    except Exception:
        return []
    hidden_truths = _collection_items(data_by_file.get("memory/canon/hidden_truths.json"), "hidden_truths")
    if not hidden_truths:
        return []
    visible_sources: list[tuple[str, str, str]] = []
    for rel_path, collection_key in (
        ("memory/canon/characters.json", "characters"),
        ("memory/canon/locations.json", "locations"),
        ("memory/canon/items.json", "items"),
    ):
        for item in _collection_items(data_by_file.get(rel_path), collection_key):
            item_id = item.get("id")
            summary = item.get("reader_visible_summary")
            if isinstance(item_id, str) and isinstance(summary, str):
                visible_sources.append((rel_path, item_id, summary))
    errors: list[str] = []
    for truth in hidden_truths:
        truth_id = truth.get("id")
        fragments = [
            fragment.strip()
            for fragment in (truth.get("description"), truth.get("title"))
            if isinstance(fragment, str) and fragment.strip()
        ]
        if not isinstance(truth_id, str) or not fragments:
            continue
        for rel_path, entity_id, summary in visible_sources:
            for fragment in fragments:
                if fragment in summary:
                    errors.append(
                        f"{rel_path}: hidden truth {truth_id} appears in reader_visible_summary for {entity_id}. "
                        "Move hidden information into private_author_notes or hidden_truths.json only."
                    )
                    break
    return errors


def _memory_data_after_operations(root: Path, operations: list[MemoryRepairOperation]) -> dict[str, object]:
    data_by_file: dict[str, object] = {
        rel_path: load_json(root / rel_path)
        for rel_path in ALLOWED_MEMORY_FILES
        if (root / rel_path).exists()
    }
    for rel_path, file_operations in _group_operations(operations).items():
        data_by_file[rel_path] = _apply_operations_to_data(data_by_file[rel_path], file_operations)
    return data_by_file


def _collection_items(data: object, collection_key: str) -> list[dict[str, object]]:
    if not isinstance(data, dict):
        return []
    collection = data.get(collection_key)
    if not isinstance(collection, list):
        return []
    return [item for item in collection if isinstance(item, dict)]


def _preflight_setting_change_semantics(operations: list[MemoryRepairOperation]) -> list[str]:
    errors: list[str] = []
    for operation in operations:
        parts = _pointer_parts(operation.path)
        if operation.file == "memory/canon/characters.json":
            errors.extend(_preflight_character_setting_change_semantics(operation, parts))
        elif operation.file == "memory/canon/locations.json":
            errors.extend(_preflight_location_setting_change_semantics(operation, parts))
    return errors


def _preflight_character_setting_change_semantics(
    operation: MemoryRepairOperation,
    parts: list[str],
) -> list[str]:
    if len(parts) < 2 or parts[0] != "characters":
        return []
    location = _operation_semantic_location(operation)
    if operation.op in {"add", "replace"} and len(parts) == 2 and isinstance(operation.value, dict):
        return _preflight_character_role_semantics(operation.value, location)
    if operation.op in {"add", "replace"} and len(parts) == 3 and parts[2] == "role" and isinstance(operation.value, str):
        return _preflight_character_role_value(operation.value, location)
    return []


def _preflight_location_setting_change_semantics(
    operation: MemoryRepairOperation,
    parts: list[str],
) -> list[str]:
    if len(parts) == 3 and parts[0] == "locations" and parts[2] == "description":
        base_path = f"/locations/{parts[1]}"
        return [
            f"{_operation_semantic_location(operation)}: Location has no top-level description field. "
            f"Use {base_path}/reader_visible_summary for public location description, "
            f"{base_path}/private_author_notes for hidden/author-only notes, or {base_path}/rules for explicit rules."
        ]
    return []


def _preflight_character_role_semantics(character: dict[str, object], location: str) -> list[str]:
    errors = _preflight_character_role_value(character.get("role"), location)
    tags = _string_values(character.get("tags"))
    identity_phrases = _character_identity_phrases_from_fields(character)
    missing_tags = [phrase for phrase in identity_phrases if phrase not in tags]
    if missing_tags:
        errors.append(
            f"{location}: Character identity phrase(s) must be in tags, not only summary/notes/role: "
            + ", ".join(missing_tags[:8])
        )
    return errors


def _preflight_character_role_value(value: object, location: str) -> list[str]:
    if not isinstance(value, str):
        return []
    role = value.strip()
    if not role:
        return []
    if role.lower() in NARRATIVE_CHARACTER_ROLES:
        return []
    phrases = _identity_phrases(role)
    if phrases:
        return [
            f"{location}: Character.role semantic preflight failed: role={role!r} looks like identity/rank/profession, "
            "but role must be narrative role only. Use Chinese narrative roles such as 主角/主要人物/配角/次要人物, "
            "and move identity phrase(s) into tags: "
            + ", ".join(phrases[:8])
        ]
    allowed = "、".join(sorted(NARRATIVE_CHARACTER_ROLES))
    return [
        f"{location}: Character.role semantic preflight failed: role={role!r} is not a supported Chinese narrative role. "
        f"Use one of: {allowed}"
    ]


def _character_identity_phrases_from_fields(character: dict[str, object]) -> list[str]:
    phrases: list[str] = []
    for key in ("role", "reader_visible_summary", "private_author_notes"):
        value = character.get(key)
        if isinstance(value, str):
            phrases.extend(_identity_phrases(value))
    return _dedupe_preserve_order(phrases)


def _identity_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for pattern in CHARACTER_ROLE_IDENTITY_PATTERNS:
        for match in pattern.finditer(text):
            phrase = next((group for group in reversed(match.groups()) if group), match.group(0)).strip()
            phrases.append(_trim_identity_phrase(phrase))
    return _dedupe_preserve_order(phrase for phrase in phrases if phrase and phrase.lower() not in NARRATIVE_CHARACTER_ROLES)


def _trim_identity_phrase(phrase: str) -> str:
    cleaned = phrase.strip()
    for marker in ("身为", "作为", "是", "为", "乃"):
        if marker in cleaned:
            cleaned = cleaned.rsplit(marker, 1)[-1].strip()
    return cleaned


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _operation_semantic_location(operation: MemoryRepairOperation) -> str:
    label = f"{operation.file} {operation.path}"
    if isinstance(operation.value, dict):
        item_id = operation.value.get("id")
        name = operation.value.get("name")
        details = [str(value) for value in (item_id, name) if isinstance(value, str) and value]
        if details:
            label += f" ({'/'.join(details)})"
    return label


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result

__all__ = [
    "_coerce_int",
    "_preview_operations",
    "_preflight_memory_repair_operations",
    "_preflight_error_message",
    "_preflight_operation_contract_errors",
    "_restore_regressed_existing_add_operations",
    "_existing_replace_operations_by_entity_id",
    "_existing_replace_operation_key",
    "_duplicate_existing_add_operation_key",
    "_operation_existing_collection_item_id",
    "_existing_collection_id_index",
    "_preflight_unique_collection_id_errors",
    "_preflight_setting_change_add_id_conflicts",
    "_collection_id_index",
    "_normalize_setting_change_gender_operations",
    "_normalize_character_gender_tag_operation",
    "_normalize_character_gender_in_object",
    "_character_field_exists",
    "_auto_repair_setting_change_semantics",
    "_auto_repair_character_identity_tags",
    "_auto_repair_timeline_unanchored_backstory",
    "_preflight_hidden_truth_reader_visible_leaks",
    "_memory_data_after_operations",
    "_collection_items",
    "_preflight_setting_change_semantics",
    "_preflight_character_setting_change_semantics",
    "_preflight_location_setting_change_semantics",
    "_preflight_character_role_semantics",
    "_preflight_character_role_value",
    "_character_identity_phrases_from_fields",
    "_identity_phrases",
    "_trim_identity_phrase",
    "_string_values",
    "_operation_semantic_location",
    "_dedupe_preserve_order",
]
