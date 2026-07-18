from __future__ import annotations

import re
from pathlib import Path

from novel.core.io import load_json
from novel.core.memory_repair_rules import ALLOWED_MEMORY_FILES, FILE_DOMAINS
from novel.core.schemas import (
    MemoryChangeBatch,
    MemoryChangeBatchPlan,
    MemoryChangeClarificationDecision,
    MemoryChangeDomain,
    MemoryChangeKind,
    MemoryChangeStage,
    MemoryRepairDecision,
    MemoryRepairOperation,
)


def mock_memory_change_clarification_decision(request: str) -> MemoryChangeClarificationDecision:
    normalized = request.strip()
    if not normalized:
        return _fallback_clarification_decision("empty request")
    unclear_patterns = (
        "还没想好",
        "随便",
        "某个",
        "某人",
        "一个人物",
        "一个角色",
        "改一下",
        "优化一下",
    )
    has_specific_target = bool(re.search(r"\b(char|loc|item|world|truth|thread)_[a-z0-9_]+\b", normalized)) or any(
        marker in normalized for marker in ("沈微", "林澈", "world_")
    )
    has_specific_change = any(marker in normalized for marker in ("新增", "删除", "设定为", "改成", "规则为", "背景是"))
    if any(pattern in normalized for pattern in unclear_patterns) and not (has_specific_target and has_specific_change):
        return MemoryChangeClarificationDecision(
            status="needs_clarification",
            questions=["请补充目标设定的名称或 ID，以及希望新增/修改后的具体内容。"],
            confidence=0.35,
            assumptions=["mock provider fixture only; not used as real business inference"],
            notes=[],
            source="mock",
        )
    return MemoryChangeClarificationDecision(
        status="ready",
        questions=[],
        confidence=0.8,
        assumptions=["mock provider fixture only; not used as real business inference"],
        notes=[],
        source="mock",
    )


def mock_memory_change_batch_plan(request: str, *, stage: MemoryChangeStage) -> MemoryChangeBatchPlan:
    target_files = _mock_infer_target_files(request) or ["memory/canon/characters.json"]
    batches = [
        MemoryChangeBatch(
            batch_id=f"batch_{FILE_DOMAINS.get(rel_path, 'memory')}",
            instruction=request,
            target_files=[rel_path],
            domains=_domains_from_files([rel_path]),
            reason="mock provider fixture only; not used as real business inference",
        )
        for rel_path in target_files
    ]
    return MemoryChangeBatchPlan(
        stage=stage,
        batches=batches,
        confidence=0.8,
        assumptions=["mock provider fixture only; not used as real business inference"],
        notes=[],
        source="mock",
    )


def mock_memory_repair_decision(
    root: Path,
    request: str,
    *,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
    target_files: list[str] | None = None,
) -> MemoryRepairDecision:
    request = _mock_effective_memory_repair_request(request)
    resolved_target_files = _normalize_allowed_target_files(target_files) if target_files else _mock_infer_target_files(request)
    operations = _mock_infer_operations(root, request, resolved_target_files)
    return MemoryRepairDecision(
        change_kind=change_kind or ("setting_change" if _looks_like_setting_change(request) else "memory_repair"),
        target_files=resolved_target_files,
        operations=operations,
        domains=_domains_from_files(resolved_target_files),
        stage=stage or "unknown",
        confidence=0.8 if operations else 0.2,
        assumptions=["mock provider fixture only; not used as real business inference"],
        needs_user_confirmation=True,
        notes=["mock provider generated deterministic repair proposal for tests."],
        source="mock",
    )


def _fallback_clarification_decision(note: str) -> MemoryChangeClarificationDecision:
    return MemoryChangeClarificationDecision(
        status="needs_clarification",
        questions=["请补充目标设定的名称或 ID，以及希望改成的具体内容。"],
        confidence=0.0,
        assumptions=[],
        notes=[note],
        source="fallback",
    )


def _mock_effective_memory_repair_request(request: str) -> str:
    marker = "本批次具体指令：\n"
    if marker not in request:
        return request
    return request.rsplit(marker, 1)[-1].strip() or request


def _mock_infer_target_files(request: str) -> list[str]:
    text = request.lower()
    targets: list[str] = []
    if any(token in text for token in ("timeline", "时间线", "事件", "回忆", "插叙", "倒序")):
        targets.append("memory/state/timeline.json")
    if any(token in text for token in ("state", "状态", "位置", "持有人", "知道", "知识")):
        targets.append("memory/state/current_state.json")
    if any(token in text for token in ("canon", "设定", "角色", "人物", "地点", "物品", "世界观", "背景")):
        targets.extend(["memory/canon/characters.json", "memory/canon/locations.json", "memory/canon/items.json"])
    if any(token in text for token in ("世界", "世界观", "规则", "背景")):
        targets.append("memory/canon/world.json")
    if any(token in text for token in ("隐藏", "真相", "秘密")):
        targets.append("memory/canon/hidden_truths.json")
    if any(token in text for token in ("伏笔", "铺垫")):
        targets.append("memory/canon/foreshadowing.json")
    return sorted(set(targets or ["memory/state/timeline.json"]))


def _mock_infer_operations(root: Path, request: str, target_files: list[str]) -> list[MemoryRepairOperation]:
    operations: list[MemoryRepairOperation] = []
    operations.extend(_mock_infer_setting_operations(root, request, target_files))
    if operations or "memory/state/timeline.json" not in target_files:
        return operations
    event_id = _extract_event_id(request)
    event_role = _mock_infer_event_role(request)
    if not event_id or not event_role:
        return operations
    timeline_path = root / "memory" / "state" / "timeline.json"
    if not timeline_path.exists():
        return operations
    timeline = load_json(timeline_path)
    events = timeline.get("events") if isinstance(timeline, dict) else None
    if not isinstance(events, list):
        return operations
    for index, event in enumerate(events):
        if isinstance(event, dict) and event.get("id") == event_id:
            operations.append(
                MemoryRepairOperation(
                    op="replace" if "event_role" in event else "add",
                    file="memory/state/timeline.json",
                    path=f"/events/{index}/event_role",
                    value=event_role,
                    reason=f"用户指出 timeline event {event_id} 的叙事类型应为 {event_role}",
                )
            )
            break
    return operations


def _looks_like_setting_change(request: str) -> bool:
    return any(token in request for token in ("设定", "人物", "角色", "背景", "世界观", "新增", "增加", "删除", "改成", "修改"))


def _mock_infer_setting_operations(root: Path, request: str, target_files: list[str]) -> list[MemoryRepairOperation]:
    operations: list[MemoryRepairOperation] = []
    if "memory/canon/characters.json" in target_files:
        operations.extend(_mock_character_operations(root, request))
    if "memory/canon/world.json" in target_files:
        operations.extend(_mock_world_operations(root, request))
    return operations


def _mock_character_operations(root: Path, request: str) -> list[MemoryRepairOperation]:
    path = root / "memory/canon/characters.json"
    if not path.exists():
        return []
    characters_data = load_json(path)
    characters = characters_data.get("characters") if isinstance(characters_data, dict) else None
    if not isinstance(characters, list):
        return []
    character_id = _extract_entity_id(request, "char_") or _match_entity_id_by_name(characters, request)
    if any(token in request for token in ("新增", "增加", "添加", "新人物", "新角色")):
        name = _extract_named_value(request) or "测试人物"
        new_id = character_id or f"char_{_slugify_name(name)}"
        if any(isinstance(item, dict) and item.get("id") == new_id for item in characters):
            return []
        return [
            MemoryRepairOperation(
                op="add",
                file="memory/canon/characters.json",
                path="/characters/-",
                value={
                    "id": new_id,
                    "name": name,
                    "role": "配角",
                    "reader_visible_summary": f"{name}是用户新增的人物设定。",
                    "aliases": [],
                    "private_author_notes": "由 setting-change mock proposal 新增。",
                    "relationships": [],
                    "abilities": [],
                    "secrets": [],
                    "tags": ["setting_change"],
                },
                reason=f"用户要求新增人物设定：{name}",
            )
        ]
    if not character_id:
        return []
    index = _find_entity_index(characters, character_id)
    if index is None:
        return []
    if any(token in request for token in ("删除", "移除", "删掉")):
        return [
            MemoryRepairOperation(
                op="remove",
                file="memory/canon/characters.json",
                path=f"/characters/{index}",
                reason=f"用户要求删除未被引用的人物设定：{character_id}",
            )
        ]
    new_summary = _extract_after_tokens(request, ("总结为", "摘要为", "设定为", "改成", "修改为"))
    if new_summary:
        return [
            MemoryRepairOperation(
                op="replace",
                file="memory/canon/characters.json",
                path=f"/characters/{index}/reader_visible_summary",
                value=new_summary,
                reason=f"用户要求修改人物 {character_id} 的读者可见设定摘要。",
            )
        ]
    return []


def _mock_world_operations(root: Path, request: str) -> list[MemoryRepairOperation]:
    path = root / "memory/canon/world.json"
    if not path.exists():
        return []
    world_data = load_json(path)
    rules = world_data.get("world_rules") if isinstance(world_data, dict) else None
    if not isinstance(rules, list):
        return []
    rule_id = _extract_entity_id(request, "world_") or _match_entity_id_by_name(rules, request)
    if not rule_id and any(token in request for token in ("新增", "增加", "添加")):
        name = _extract_named_value(request) or "新世界规则"
        new_id = f"world_{_slugify_name(name)}"
        if any(isinstance(item, dict) and item.get("id") == new_id for item in rules):
            return []
        return [
            MemoryRepairOperation(
                op="add",
                file="memory/canon/world.json",
                path="/world_rules/-",
                value={
                    "id": new_id,
                    "name": name,
                    "description": _extract_after_tokens(request, ("规则为", "设定为", "：", ":")) or f"{name}。",
                    "visibility": "reader_visible",
                    "limitations": [],
                    "known_by_character_ids": [],
                },
                reason=f"用户要求新增世界规则：{name}",
            )
        ]
    if not rule_id:
        return []
    index = _find_entity_index(rules, rule_id)
    if index is None:
        return []
    new_description = _extract_after_tokens(request, ("描述为", "规则为", "设定为", "改成", "修改为"))
    if new_description:
        return [
            MemoryRepairOperation(
                op="replace",
                file="memory/canon/world.json",
                path=f"/world_rules/{index}/description",
                value=new_description,
                reason=f"用户要求修改世界规则 {rule_id}。",
            )
        ]
    return []


def _extract_entity_id(request: str, prefix: str) -> str | None:
    match = re.search(rf"\b({re.escape(prefix)}[a-zA-Z0-9_]+)\b", request)
    return match.group(1) if match else None


def _match_entity_id_by_name(entities: list[object], request: str) -> str | None:
    matches: list[str] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_id = entity.get("id")
        name = entity.get("name") or entity.get("title")
        if isinstance(entity_id, str) and isinstance(name, str) and name and name in request:
            matches.append(entity_id)
    return matches[0] if len(matches) == 1 else None


def _find_entity_index(entities: list[object], entity_id: str) -> int | None:
    for index, entity in enumerate(entities):
        if isinstance(entity, dict) and entity.get("id") == entity_id:
            return index
    return None


def _extract_named_value(request: str) -> str | None:
    patterns = [
        r"(?:新增|增加|添加)(?:一个|一名|人物|角色|设定|世界规则|规则)?[：:\s]*([\u4e00-\u9fffA-Za-z0-9_]{2,24})",
        r"(?:名叫|叫做|名字是|名称是)([\u4e00-\u9fffA-Za-z0-9_]{2,24})",
    ]
    for pattern in patterns:
        match = re.search(pattern, request)
        if match:
            return match.group(1).strip(" ，,。.;；：:")
    return None


def _extract_after_tokens(request: str, tokens: tuple[str, ...]) -> str | None:
    for token in tokens:
        if token in request:
            text = request.split(token, 1)[1].strip()
            return text.strip(" ，,。.;；") or None
    return None


def _slugify_name(name: str) -> str:
    ascii_text = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    if ascii_text:
        return ascii_text[:40]
    codepoints = "_".join(f"{ord(char):x}" for char in name[:6])
    return codepoints or "new_entity"


def _extract_event_id(request: str) -> str | None:
    match = re.search(r"\b(event_[a-zA-Z0-9_]+)\b", request)
    return match.group(1) if match else None


def _mock_infer_event_role(request: str) -> str | None:
    if any(token in request for token in ("回忆", "插叙", "过去")):
        return "flashback"
    if any(token in request for token in ("当前行动", "当前发生", "现在发生")):
        return "current_action"
    if any(token in request for token in ("揭示", "发现真相")):
        return "revelation"
    return None


def _domains_from_files(target_files: list[str]) -> list[MemoryChangeDomain]:
    return [domain for path in target_files if (domain := FILE_DOMAINS.get(path))]


def _normalize_allowed_target_files(target_files: list[str] | None) -> list[str]:
    if target_files is None:
        return sorted(ALLOWED_MEMORY_FILES)
    allowed = [path for path in target_files if path in ALLOWED_MEMORY_FILES]
    return sorted(dict.fromkeys(allowed)) or sorted(ALLOWED_MEMORY_FILES)
