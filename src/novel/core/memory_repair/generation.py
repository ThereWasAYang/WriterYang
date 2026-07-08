from __future__ import annotations

from .deps import (
    json,
    Path,
    ValidationError,
    AgentInvocationContext,
    AgentOutputContract,
    AgentOutputContractError,
    generate_with_output_guard,
    log_app_warning,
    load_json,
    JsonExtractionError,
    extract_json_object,
    mock_memory_change_batch_plan,
    mock_memory_change_clarification_decision,
    mock_memory_repair_decision,
    _escape_pointer,
    _unescape_pointer,
    load_prompt_template,
    prompt_template_version,
    ProviderOverrides,
    create_agent_provider,
    default_agent_config_path,
    ModelProvider,
    ModelRequest,
    ALLOWED_MEMORY_FILES,
    COLLECTION_FIELD_HINTS,
    COLLECTION_PATH_FILES,
    COLLECTION_SCHEMA_HINTS,
    FILE_COLLECTION_KEYS,
    POINTER_PATH_FILES,
    SETTING_CHANGE_MAPPING_RULES,
    STATE_COLLECTION_KEYS,
    MemoryChangeBatchPlan,
    MemoryChangeClarificationDecision,
    MemoryChangeConversationTurn,
    MemoryChangeKind,
    MemoryChangeStage,
    MemoryRepairDecision,
    REPAIR_ERROR_LIMIT,
    REPAIR_INVALID_OUTPUT_LIMIT,
    JsonRepairExhaustedError,
    generate_json_with_repair,
)

from .models import (
    MemoryRepairError,
)

from .validation import (
    _format_preflight_errors,
)

from .impact import (
    _validate_memory_change_batch_plan,
    _fallback_clarification_decision,
    _normalize_string_list,
)


def generate_memory_change_clarification_decision(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    stage: MemoryChangeStage = "unknown",
    conversation_turns: list[MemoryChangeConversationTurn] | None = None,
) -> MemoryChangeClarificationDecision:
    request = user_request.strip()
    if provider is None and provider_name.lower() == "mock":
        return mock_memory_change_clarification_decision(request)
    repair_provider = provider or create_agent_provider(
        default_agent_config_path(root),
        "memory_repair",
        overrides=ProviderOverrides(provider_name=provider_name),
    )
    user_prompt = _memory_change_clarification_user_prompt(
        root,
        request,
        stage=stage,
        conversation_turns=conversation_turns or [],
    )
    model_request = ModelRequest(
        system_prompt=load_prompt_template("memory_change_clarification_system"),
        user_prompt=user_prompt,
        json_schema_name="MemoryChangeClarificationDecision",
        prompt_version=prompt_template_version("memory_change_clarification_system"),
    )
    contract = AgentOutputContract(
        output_kind="json",
        target_name="MemoryChangeClarificationDecision",
        json_schema_name="MemoryChangeClarificationDecision",
        allow_user_questions=False,
    )
    try:
        return generate_json_with_repair(
            repair_provider,
            model_request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="memory_repair",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_change_clarification",
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="memory_repair",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_change_clarification_repair",
            ),
            contract=contract,
            parse=parse_memory_change_clarification_decision,
            repair_prompt=lambda invalid_output, error: _structured_decision_repair_prompt(
                schema_name="MemoryChangeClarificationDecision",
                invalid_output=invalid_output,
                error=error,
            ),
        )
    except (AgentOutputContractError, JsonRepairExhaustedError) as exc:
        log_app_warning(
            root,
            "memory_repair_fallback",
            workflow="clarification",
            stage=stage,
            error_type=exc.__class__.__name__,
            error=str(exc),
        )
        return _fallback_clarification_decision(f"provider returned invalid clarification decision: {exc}")


def parse_memory_change_clarification_decision(content: str) -> MemoryChangeClarificationDecision:
    try:
        raw = extract_json_object(content)
    except JsonExtractionError as exc:
        raise MemoryRepairError("provider response did not contain a JSON object") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryChangeClarificationDecision JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MemoryRepairError("provider returned MemoryChangeClarificationDecision as a non-object JSON value")
    data = dict(data)
    data["source"] = data.get("source") or "model"
    data["questions"] = _normalize_string_list(data.get("questions"))
    data["assumptions"] = _normalize_string_list(data.get("assumptions"))
    data["notes"] = _normalize_string_list(data.get("notes"))
    try:
        return MemoryChangeClarificationDecision.model_validate(data)
    except ValidationError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryChangeClarificationDecision: {exc}") from exc


def generate_memory_change_batch_plan(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    stage: MemoryChangeStage = "unknown",
) -> MemoryChangeBatchPlan:
    request = user_request.strip()
    if provider is None and provider_name.lower() == "mock":
        return mock_memory_change_batch_plan(request, stage=stage)
    repair_provider = provider or create_agent_provider(
        default_agent_config_path(root),
        "memory_repair",
        overrides=ProviderOverrides(provider_name=provider_name),
    )
    user_prompt = _memory_change_batch_plan_user_prompt(root, request, stage=stage)
    model_request = ModelRequest(
        system_prompt=load_prompt_template("memory_change_batch_plan_system"),
        user_prompt=user_prompt,
        json_schema_name="MemoryChangeBatchPlan",
        prompt_version=prompt_template_version("memory_change_batch_plan_system"),
    )
    contract = AgentOutputContract(
        output_kind="json",
        target_name="MemoryChangeBatchPlan",
        json_schema_name="MemoryChangeBatchPlan",
        allow_user_questions=False,
    )
    try:
        return generate_json_with_repair(
            repair_provider,
            model_request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="memory_repair",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_change_batch_plan",
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="memory_repair",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_change_batch_plan_repair",
            ),
            contract=contract,
            parse=parse_memory_change_batch_plan,
            repair_prompt=lambda invalid_output, error: _structured_decision_repair_prompt(
                schema_name="MemoryChangeBatchPlan",
                invalid_output=invalid_output,
                error=error,
            ),
        )
    except JsonRepairExhaustedError as exc:
        raise MemoryRepairError(f"setting change batch planner returned invalid output: {exc}") from exc.second_error
    except Exception as exc:
        raise MemoryRepairError(f"setting change batch planner failed: {exc}") from exc


def parse_memory_change_batch_plan(content: str) -> MemoryChangeBatchPlan:
    try:
        raw = extract_json_object(content)
    except JsonExtractionError as exc:
        raise MemoryRepairError("provider response did not contain a JSON object") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryChangeBatchPlan JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MemoryRepairError("provider returned MemoryChangeBatchPlan as a non-object JSON value")
    if "operations" in data:
        raise MemoryRepairError("MemoryChangeBatchPlan must not include operations")
    batches = data.get("batches")
    if isinstance(batches, list):
        normalized_batches: list[object] = []
        for index, batch in enumerate(batches):
            if isinstance(batch, dict) and "operations" in batch:
                raise MemoryRepairError(f"MemoryChangeBatchPlan batch {index + 1} must not include operations")
            if not isinstance(batch, dict):
                normalized_batches.append(batch)
                continue
            normalized_batches.append(_normalize_memory_change_batch_data(batch, index=index))
        data = dict(data)
        data["batches"] = normalized_batches
    else:
        data = dict(data)
    data["assumptions"] = _normalize_string_list(data.get("assumptions"))
    data["notes"] = _normalize_string_list(data.get("notes"))
    data["source"] = data.get("source") or "model"
    try:
        plan = MemoryChangeBatchPlan.model_validate(data)
    except ValidationError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryChangeBatchPlan: {exc}") from exc
    _validate_memory_change_batch_plan(plan)
    return plan


def _normalize_memory_change_batch_data(batch: dict[str, object], *, index: int) -> dict[str, object]:
    normalized = dict(batch)
    normalized["target_files"] = _normalize_string_list(normalized.get("target_files"))
    normalized["domains"] = _normalize_string_list(normalized.get("domains"))
    instruction = normalized.get("instruction")
    if not isinstance(instruction, str):
        instruction_parts = _normalize_string_list(instruction)
        if instruction_parts:
            normalized["instruction"] = "\n".join(instruction_parts)
    if not isinstance(normalized.get("batch_id"), str) or not str(normalized.get("batch_id")).strip():
        normalized["batch_id"] = f"batch_{index + 1}"
    if not isinstance(normalized.get("reason"), str) or not str(normalized.get("reason")).strip():
        candidates = [
            *_normalize_string_list(normalized.get("notes")),
            *_normalize_string_list(normalized.get("assumptions")),
        ]
        instruction_text = normalized.get("instruction")
        if isinstance(instruction_text, str) and instruction_text.strip():
            candidates.append(instruction_text.strip())
        normalized["reason"] = candidates[0] if candidates else f"按第 {index + 1} 个批次生成设定变更。"
    return normalized


def generate_memory_repair_decision(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
    target_files: list[str] | None = None,
) -> MemoryRepairDecision:
    request = user_request.strip()
    if provider is None and provider_name.lower() == "mock":
        return mock_memory_repair_decision(
            root,
            request,
            change_kind=change_kind,
            stage=stage,
            target_files=target_files,
        )
    repair_provider = provider or create_agent_provider(
        default_agent_config_path(root),
        "memory_repair",
        overrides=ProviderOverrides(provider_name=provider_name),
    )
    user_prompt = _memory_repair_user_prompt(root, request, change_kind=change_kind, stage=stage, target_files=target_files)
    model_request = ModelRequest(
        system_prompt=load_prompt_template("memory_repair_system"),
        user_prompt=user_prompt,
        json_schema_name="MemoryRepairDecision",
        prompt_version=prompt_template_version("memory_repair_system"),
    )
    contract = AgentOutputContract(
        output_kind="json",
        target_name="MemoryRepairDecision",
        json_schema_name="MemoryRepairDecision",
        allow_user_questions=False,
    )
    try:
        return generate_json_with_repair(
            repair_provider,
            model_request,
            root=root,
            invocation=AgentInvocationContext(
                agent_name="memory_repair",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_repair_decision",
            ),
            repair_invocation=AgentInvocationContext(
                agent_name="memory_repair",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_repair_decision_repair",
            ),
            contract=contract,
            parse=parse_memory_repair_decision,
            repair_prompt=lambda invalid_output, error: _repair_decision_repair_prompt(
                invalid_output=invalid_output,
                error=error,
            ),
        )
    except AgentOutputContractError as exc:
        log_app_warning(
            root,
            "memory_repair_fallback",
            workflow="decision",
            stage=stage,
            change_kind=change_kind,
            error_type=exc.__class__.__name__,
            error=str(exc),
        )
        return _empty_memory_repair_decision("provider output violated MemoryRepairDecision contract")
    except JsonRepairExhaustedError as exc:
        log_app_warning(
            root,
            "memory_repair_fallback",
            workflow="decision",
            stage=stage,
            change_kind=change_kind,
            error_type=exc.__class__.__name__,
            error=str(exc.second_error),
        )
        return _empty_memory_repair_decision(f"provider returned invalid MemoryRepairDecision: {exc.second_error}")


def _repair_memory_repair_decision_target_schema(
    root: Path,
    user_request: str,
    *,
    invalid_decision: MemoryRepairDecision,
    preflight_errors: list[str],
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
    target_files: list[str] | None = None,
) -> MemoryRepairDecision:
    request = user_request.strip()
    repair_provider = provider or create_agent_provider(
        default_agent_config_path(root),
        "memory_repair",
        overrides=ProviderOverrides(provider_name=provider_name),
    )
    original_prompt = _memory_repair_user_prompt(root, request, change_kind=change_kind, stage=stage, target_files=target_files)
    try:
        content = generate_with_output_guard(
            repair_provider,
            ModelRequest(
                system_prompt=load_prompt_template("memory_repair_system"),
                user_prompt=_target_schema_repair_prompt(
                    original_prompt=original_prompt,
                    invalid_decision=invalid_decision,
                    preflight_errors=preflight_errors,
                ),
                json_schema_name="MemoryRepairDecision",
                prompt_version=prompt_template_version("memory_repair_system"),
            ),
            root=root,
            invocation=AgentInvocationContext(
                agent_name="memory_repair",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_repair_target_schema_repair",
            ),
            contract=AgentOutputContract(
                output_kind="json",
                target_name="MemoryRepairDecision",
                json_schema_name="MemoryRepairDecision",
                allow_user_questions=False,
            ),
        )
    except AgentOutputContractError as exc:
        log_app_warning(
            root,
            "memory_repair_target_schema_repair_failed",
            workflow="target_schema_repair",
            stage=stage,
            change_kind=change_kind,
            preflight_error_count=len(preflight_errors),
            error_type=exc.__class__.__name__,
            error=str(exc),
        )
        raise MemoryRepairError(
            "provider target-schema repair output violated MemoryRepairDecision contract: "
            + ", ".join(exc.reason_codes)
        ) from exc
    try:
        return parse_memory_repair_decision(content)
    except MemoryRepairError as exc:
        log_app_warning(
            root,
            "memory_repair_target_schema_repair_failed",
            workflow="target_schema_repair",
            stage=stage,
            change_kind=change_kind,
            preflight_error_count=len(preflight_errors),
            error_type=exc.__class__.__name__,
            error=str(exc),
        )
        raise MemoryRepairError(f"provider returned invalid target-schema repair decision: {exc}") from exc


def parse_memory_repair_decision(content: str) -> MemoryRepairDecision:
    try:
        raw = extract_json_object(content)
    except JsonExtractionError as exc:
        raise MemoryRepairError("provider response did not contain a JSON object") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryRepairDecision JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MemoryRepairError("provider returned MemoryRepairDecision as a non-object JSON value")
    data = dict(data)
    data = _normalize_memory_repair_decision_data(data)
    data["needs_user_confirmation"] = True
    data["source"] = data.get("source") or "model"
    try:
        return MemoryRepairDecision.model_validate(data)
    except ValidationError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryRepairDecision: {exc}") from exc


def _normalize_memory_repair_decision_data(data: dict[str, object]) -> dict[str, object]:
    data = dict(data)
    data["target_files"] = _normalize_string_list(data.get("target_files"))
    data["assumptions"] = _normalize_string_list(data.get("assumptions"))
    data["notes"] = _normalize_string_list(data.get("notes"))
    operations = data.get("operations")
    if not isinstance(operations, list):
        return data
    normalized_operations: list[object] = []
    for raw_operation in operations:
        if not isinstance(raw_operation, dict):
            normalized_operations.append(raw_operation)
            continue
        normalized_operations.append(_normalize_memory_repair_operation(raw_operation))
    data["operations"] = normalized_operations
    return data


def _normalize_memory_repair_operation(raw_operation: dict[str, object]) -> dict[str, object]:
    operation = dict(raw_operation)
    op = operation.get("op")
    path = operation.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        return operation
    inferred_file = _infer_file_from_pointer_path(path)
    if not isinstance(operation.get("file"), str) or not operation.get("file"):
        if inferred_file:
            operation["file"] = inferred_file
    if op == "add":
        normalized_path = _normalize_add_collection_path(path)
        can_default_reason = normalized_path != path or _is_append_collection_path(normalized_path)
        if normalized_path != path:
            operation["path"] = normalized_path
            if not isinstance(operation.get("file"), str) or not operation.get("file"):
                operation["file"] = _infer_file_from_pointer_path(normalized_path) or inferred_file
        if can_default_reason and (not isinstance(operation.get("reason"), str) or not operation.get("reason")):
            operation["reason"] = "用户要求新增设定；系统根据集合路径补齐操作原因。"
    return operation


def _infer_file_from_pointer_path(path: str) -> str | None:
    parts = [part for part in path.strip("/").split("/") if part]
    if not parts:
        return None
    return POINTER_PATH_FILES.get(_unescape_pointer(parts[0]))


def _normalize_add_collection_path(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) != 2:
        return path
    collection_key = _unescape_pointer(parts[0])
    item_selector = _unescape_pointer(parts[1])
    if collection_key not in COLLECTION_PATH_FILES or item_selector == "-" or item_selector.isdigit():
        return path
    return f"/{_escape_pointer(collection_key)}/-"


def _is_append_collection_path(path: str) -> bool:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) != 2:
        return False
    return _unescape_pointer(parts[0]) in COLLECTION_PATH_FILES and _unescape_pointer(parts[1]) == "-"

def _memory_repair_user_prompt(
    root: Path,
    request: str,
    *,
    change_kind: MemoryChangeKind | None = None,
    stage: MemoryChangeStage | None = None,
    target_files: list[str] | None = None,
) -> str:
    allowed_files = _normalize_allowed_target_files(target_files)
    task_note = ""
    if change_kind == "setting_change":
        task_note = (
            "本次任务是 setting_change：允许根据用户明确请求新增、修改或删除人物/背景设定。\n"
            "新增实体时必须生成稳定小写下划线 id，并填齐目标 schema 必填字段。\n"
            "修改必须定位到明确 ID、exact name 或 exact alias；不要做近似联想匹配。\n"
            "无精确匹配且用户没有明确要求替换/删除/合并时，按新增实体处理。\n"
            "删除被引用实体必须同时安全清理引用，否则 operations 留空。\n"
            "每个 operation 必须包含 file、path、reason；array 新增必须使用 /collection/-，不能使用 /characters/{id} 这类路径。\n"
            "不要要求用户提供文件结构、字段、visibility 或 JSON Pointer；这些由当前结构索引决定。\n"
            f"{SETTING_CHANGE_MAPPING_RULES}"
            f"创作阶段：{stage or 'unknown'}。\n\n"
        )
    return (
        "请生成 MemoryRepairDecision JSON。\n"
        f"{task_note}"
        "允许 target_files：\n"
        + "\n".join(f"- {path}" for path in allowed_files)
        + "\n\n"
        "当前文件结构与 JSON Pointer 路径索引：\n"
        f"{_memory_pointer_index(root, target_files=allowed_files)}\n\n"
        "当前可见 ID 摘要：\n"
        f"{_memory_id_summary(root, target_files=allowed_files)}\n\n"
        f"用户请求：\n{request}\n"
    )


def _memory_change_batch_plan_user_prompt(
    root: Path,
    request: str,
    *,
    stage: MemoryChangeStage,
) -> str:
    return (
        "请生成 MemoryChangeBatchPlan JSON。\n"
        "本次任务是 setting_change 的分批规划：只拆分批次，不生成 operations。\n"
        f"{SETTING_CHANGE_MAPPING_RULES}"
        f"创作阶段：{stage or 'unknown'}。\n\n"
        "允许 target_files：\n"
        + "\n".join(f"- {path}" for path in sorted(ALLOWED_MEMORY_FILES))
        + "\n\n"
        "当前文件结构与 JSON Pointer 路径索引：\n"
        f"{_memory_pointer_index(root)}\n\n"
        "当前可见 ID 摘要：\n"
        f"{_memory_id_summary(root)}\n\n"
        f"用户请求：\n{request}\n"
    )


def _memory_change_clarification_user_prompt(
    root: Path,
    request: str,
    *,
    stage: MemoryChangeStage,
    conversation_turns: list[MemoryChangeConversationTurn],
) -> str:
    transcript = "\n".join(
        f"- {turn.role}: {turn.content}"
        for turn in conversation_turns
    ) or "- user: " + request
    return (
        "请判断本次 setting_change 是否已经足以生成安全的 MemoryRepairProposal。\n"
        "只要用户的创作意图足够明确，就输出 ready；文件、字段、visibility 和 JSON Pointer 映射是系统责任，不是用户责任。\n"
        "如果只是新实体属于 characters/locations/items/world/hidden_truths/foreshadowing 哪类需要系统判断，不要为此追问用户。\n"
        "如果缺少具体新增/修改内容、用户要求替换/删除但目标不唯一，或存在会改变剧情含义的真实创作歧义，才输出 needs_clarification。\n"
        "不要要求用户提供现有文件完整结构、目标文件、字段名、visibility 或 JSON Pointer；现有文件结构和 JSON Pointer 路径索引已经在本 prompt 中提供。\n"
        "不要把新姓名近似联想到现有角色；只有 exact id、exact name 或 exact alias 匹配才视为已有实体。\n"
        f"{SETTING_CHANGE_MAPPING_RULES}"
        f"创作阶段：{stage or 'unknown'}。\n\n"
        "允许 target_files：\n"
        + "\n".join(f"- {path}" for path in sorted(ALLOWED_MEMORY_FILES))
        + "\n\n"
        "当前文件结构与 JSON Pointer 路径索引：\n"
        f"{_memory_pointer_index(root)}\n\n"
        "当前可见 ID 摘要：\n"
        f"{_memory_id_summary(root)}\n\n"
        "对话记录：\n"
        f"{transcript}\n\n"
        f"合并后的用户请求：\n{request}\n"
    )


def _normalize_allowed_target_files(target_files: list[str] | None) -> list[str]:
    if not target_files:
        return sorted(ALLOWED_MEMORY_FILES)
    allowed = [path for path in target_files if path in ALLOWED_MEMORY_FILES]
    return sorted(dict.fromkeys(allowed)) or sorted(ALLOWED_MEMORY_FILES)


def _memory_pointer_index(root: Path, *, target_files: list[str] | None = None) -> str:
    sections: list[str] = []
    for rel_path in _normalize_allowed_target_files(target_files):
        sections.append(_file_pointer_index(root, rel_path))
    return "\n".join(sections)


def _file_pointer_index(root: Path, rel_path: str) -> str:
    path = root / rel_path
    if not path.exists():
        return f"- {rel_path}: missing"
    try:
        data = load_json(path)
    except Exception as exc:
        return f"- {rel_path}: unreadable ({exc.__class__.__name__})"
    lines = [f"- {rel_path}"]
    if isinstance(data, dict):
        lines.append("  top-level keys: " + ", ".join(sorted(str(key) for key in data)))
    collection_key = FILE_COLLECTION_KEYS.get(rel_path)
    if collection_key and isinstance(data, dict):
        collection = data.get(collection_key)
        fields = COLLECTION_FIELD_HINTS.get(rel_path, [])
        lines.append(f"  collection: /{collection_key}")
        lines.append(f"  add new item path: /{collection_key}/-")
        if fields:
            lines.append("  common item fields: " + ", ".join(fields))
        schema_hint = COLLECTION_SCHEMA_HINTS.get(rel_path)
        if schema_hint:
            lines.extend(f"  {line}" for line in schema_hint.splitlines())
        if isinstance(collection, list) and collection:
            detailed_limit = 20
            for index, item in enumerate(collection[:detailed_limit]):
                if not isinstance(item, dict):
                    lines.append(f"  existing[{index}] path: /{collection_key}/{index}")
                    continue
                item_id = item.get("id") if isinstance(item.get("id"), str) else "-"
                name = item.get("name") or item.get("title") or "-"
                item_fields = sorted(str(key) for key in item)
                examples = [
                    f"/{collection_key}/{index}/{field}"
                    for field in item_fields
                    if field != "id"
                ][:8]
                lines.append(f"  existing[{index}]: id={item_id}; name/title={name}; path=/{collection_key}/{index}")
                lines.append("    fields: " + ", ".join(item_fields))
                if examples:
                    lines.append("    replace paths: " + ", ".join(examples))
            if len(collection) > detailed_limit:
                lines.append("  additional existing id/path index:")
                for index, item in enumerate(collection[detailed_limit:], start=detailed_limit):
                    if not isinstance(item, dict):
                        lines.append(f"  existing[{index}] path: /{collection_key}/{index}")
                        continue
                    item_id = item.get("id") if isinstance(item.get("id"), str) else "-"
                    name = item.get("name") or item.get("title") or "-"
                    lines.append(f"  existing[{index}]: id={item_id}; name/title={name}; path=/{collection_key}/{index}")
        else:
            lines.append(f"  existing items: none; use /{collection_key}/- for add")
        return "\n".join(lines)
    if rel_path == "memory/state/current_state.json" and isinstance(data, dict):
        lines.extend(_state_pointer_index(data))
    elif rel_path == "memory/state/timeline.json" and isinstance(data, dict):
        lines.extend(_timeline_pointer_index(data))
    return "\n".join(lines)


def _state_pointer_index(data: dict[str, object]) -> list[str]:
    lines = [
        "  story position paths: /story_position/latest_chapter, /story_position/current_arc",
        "  add state paths: /character_states/-, /item_states/-, /location_states/-",
    ]
    for key in sorted(STATE_COLLECTION_KEYS):
        collection = data.get(key)
        if not isinstance(collection, list):
            continue
        lines.append(f"  collection: /{key}")
        for index, item in enumerate(collection[:20]):
            if not isinstance(item, dict):
                continue
            entity_id = item.get("entity_id") or item.get("id") or "-"
            fields = sorted(str(field) for field in item)
            examples = [f"/{key}/{index}/{field}" for field in fields if field not in {"entity_id", "id"}][:8]
            lines.append(f"  existing[{index}]: entity_id={entity_id}; path=/{key}/{index}")
            if examples:
                lines.append("    replace paths: " + ", ".join(examples))
    return lines


def _timeline_pointer_index(data: dict[str, object]) -> list[str]:
    events = data.get("events")
    lines = [
        "  collection: /events",
        "  add event path: /events/-",
        "  common event fields: id, summary, reader_visible, narrative_position, story_position, event_role, causes, effects, state_change_ids",
        "  strict add value schema: id, summary, reader_visible, story_position are required; narrative_position is optional.",
        "  narrative_position, when present, must be an object: {chapter:int>=1, scene?:int>=1|null, sequence?:int>=1|null}.",
        "  未在正文揭示的开篇前/前史/背景事件必须省略 narrative_position，不要使用 chapter=0 或假章节。",
        "  story_position is required: {time_label: non-empty string, order?:number|null, thread_id?:string|null, certainty?:certain|inferred|uncertain|null}.",
        "  story-world labels such as 开篇前、前史、1540年代 must go into story_position.time_label.",
        "  top-level certainty is not allowed; certainty belongs inside story_position.",
    ]
    if not isinstance(events, list) or not events:
        lines.append("  existing events: none; use /events/- for add")
        return lines
    for index, item in enumerate(events[:40]):
        if not isinstance(item, dict):
            continue
        event_id = item.get("id") if isinstance(item.get("id"), str) else "-"
        summary = item.get("summary") if isinstance(item.get("summary"), str) else "-"
        fields = sorted(str(field) for field in item)
        examples = [f"/events/{index}/{field}" for field in fields if field != "id"][:8]
        lines.append(f"  existing[{index}]: id={event_id}; summary={summary}; path=/events/{index}")
        if examples:
            lines.append("    replace paths: " + ", ".join(examples))
    return lines


def _memory_id_summary(root: Path, *, target_files: list[str] | None = None) -> str:
    lines: list[str] = []
    for rel_path in _normalize_allowed_target_files(target_files):
        path = root / rel_path
        if not path.exists():
            lines.append(f"- {rel_path}: missing")
            continue
        try:
            data = load_json(path)
        except Exception as exc:
            lines.append(f"- {rel_path}: unreadable ({exc.__class__.__name__})")
            continue
        ids = _collect_ids(data)
        if ids:
            lines.append(f"- {rel_path}: " + ", ".join(ids[:40]))
        else:
            lines.append(f"- {rel_path}: no explicit ids found")
    return "\n".join(lines)


def _collect_ids(value: object) -> list[str]:
    found: list[str] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            item_id = node.get("id")
            if isinstance(item_id, str) and item_id not in found:
                found.append(item_id)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return found


def _repair_decision_repair_prompt(*, invalid_output: str, error: str) -> str:
    return (
        "上一次输出不能被解析为 MemoryRepairDecision。\n"
        f"错误：{error[:REPAIR_ERROR_LIMIT]}\n\n"
        "请重新只输出 JSON object。不要 Markdown 或解释。\n"
        "修复规则：\n"
        "- 每个 operation 必须包含 op、file、path、reason。\n"
        "- 如果是新增数组条目，path 必须使用 /collection/-，例如 /characters/-、/hidden_truths/-、/foreshadowing_threads/-。\n"
        "- 如果上次输出使用 /characters/{id}、/hidden_truths/{id}、/foreshadowing_threads/{id} 这类新增路径，请改成对应 /collection/-，并保留 value.id。\n"
        "- 如果缺少 file，但 path 能唯一映射到允许文件，请补齐 file。\n"
        "- 不要要求用户提供现有文件结构、目标文件、字段、visibility 或 JSON Pointer；原始 prompt 已提供这些结构上下文。\n"
        "- 只有创作意图本身缺失、替换/删除目标不唯一或删除风险无法安全处理时，operations 才能为空。\n"
        f"上一次输出：\n{invalid_output[:REPAIR_INVALID_OUTPUT_LIMIT]}\n"
    )


def _structured_decision_repair_prompt(
    *,
    schema_name: str,
    invalid_output: str,
    error: str,
) -> str:
    return (
        f"上一次输出不能被解析为 {schema_name}。\n"
        f"错误：{error[:REPAIR_ERROR_LIMIT]}\n\n"
        "请重新只输出 JSON object，不要 Markdown 或解释。\n"
        "不要新增 schema 未定义字段，不要向用户或上游 Agent 提问。\n"
        f"上一次输出：\n{invalid_output[:REPAIR_INVALID_OUTPUT_LIMIT]}\n"
    )


def _target_schema_repair_prompt(
    *,
    original_prompt: str,
    invalid_decision: MemoryRepairDecision,
    preflight_errors: list[str],
) -> str:
    invalid_json = json.dumps(invalid_decision.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return (
        f"{original_prompt}\n\n"
        "上一次输出已经可以解析为 MemoryRepairDecision，但把 operations 应用到目标 memory/canon 文件后没有通过目标文件 schema/semantic preflight。\n"
        "preflight 失败可能来自 file、path 或 value；本次修复必须同时修正非法 path 和非法 value，而不只是补齐 op/file/path/reason。\n"
        "请重新只输出修复后的 MemoryRepairDecision JSON object。不要 Markdown 或解释。\n"
        "修复规则：\n"
        "- 只修改下方 preflight 错误直接涉及的 operation；未被错误涉及的 operation 必须原样保留，包括 file、path、op、value、reason。\n"
        "- 保留用户创作意图；只有安全且存在的 file/path 才能保留，同时修正 value 的字段类型、嵌套对象和 enum。\n"
        "- 如果错误提示 replace path does not exist，说明 path 不存在；必须改到原始 prompt 中列出的 existing replace paths，或清空该 operation 并在 notes 写明原因。\n"
        "- add 到集合时，value 必须是对应集合元素的完整对象，且满足上方 strict add value schema。\n"
        "- visibility 只能是 reader_visible、hidden 或 partially_revealed；importance 只能是 low、medium、high 或 critical。\n"
        "- abilities、secrets、rules、special_properties 必须是对象数组，不要使用字符串数组。\n"
        "- planned_reveal 和 planned_payoff 必须是对象或 null，不要使用字符串。\n"
        "- introduced_in_chapter 必须是整数；如果用户说“开篇”，默认使用 1。\n"
        "- timeline 事件如果还没有在正文中揭示，必须省略 narrative_position；narrative_position.chapter 若给出必须 >= 1，开篇前/背景事件不是第 0 章，也不要使用假章节。\n"
        "- timeline 的故事世界时间必须写入 story_position.time_label；story_position.certainty 只能是 certain、inferred 或 uncertain；顶层不要输出 certainty。\n"
        "- Location 顶层没有 description 字段；地点公开描述写 reader_visible_summary，隐藏/作者私有说明写 private_author_notes，地点规则写 rules[]；不要使用 /locations/{i}/description。\n"
        "- Character.role 只能表示叙事角色；默认使用主角、主要人物、配角、次要人物。家族身份、门派身份、排行、职业/江湖身份必须移入 tags，并可保留在 summary/notes。\n"
        "- 明确性别必须写 Character.gender，值用 男、女或未知；明确男/女时写 男/女，不要只向 tags 追加 男性/女性。\n"
        "- 不要把谢家长女、谢家次子、张家幼女、唐门二房之女、江湖散人、武当俗家弟子这类身份短语写入 Character.role。\n"
        "- reader_visible_summary 只能写读者可见信息；如果错误提示 reader_visible_summary 包含隐藏真相，必须把隐藏内容移到 private_author_notes 或 hidden_truths.json，不要放在 reader_visible_summary。\n"
        "- 如果错误提示 add would duplicate existing ... at /collection/index 或 duplicate ... id，说明该实体已经存在；"
        "不要保留 add /collection/-，请改成对应已有 path 的 replace（字段级 replace 优先），"
        "或在无法确定时清空 operations 并在 notes 写明原因。\n"
        "- 如果仍无法安全修复，operations 置空并在 notes 中写明 target schema 缺失信息；不要向用户提问。\n\n"
        "目标 schema preflight 错误 / semantic preflight 错误：\n"
        f"{_format_preflight_errors(preflight_errors, max_chars=REPAIR_ERROR_LIMIT)}\n\n"
        f"上一次 MemoryRepairDecision：\n{invalid_json[:REPAIR_INVALID_OUTPUT_LIMIT]}\n"
    )


def _empty_memory_repair_decision(note: str) -> MemoryRepairDecision:
    return MemoryRepairDecision(
        target_files=[],
        operations=[],
        confidence=0.0,
        assumptions=[],
        needs_user_confirmation=True,
        notes=[note, "没有生成可安全自动应用的 patch；请提供具体 event/entity id 或手动编辑 proposal。"],
        source="fallback",
    )

__all__ = [
    "generate_memory_change_clarification_decision",
    "parse_memory_change_clarification_decision",
    "generate_memory_change_batch_plan",
    "parse_memory_change_batch_plan",
    "_normalize_memory_change_batch_data",
    "generate_memory_repair_decision",
    "_repair_memory_repair_decision_target_schema",
    "parse_memory_repair_decision",
    "_normalize_memory_repair_decision_data",
    "_normalize_memory_repair_operation",
    "_infer_file_from_pointer_path",
    "_normalize_add_collection_path",
    "_is_append_collection_path",
    "_memory_repair_user_prompt",
    "_memory_change_batch_plan_user_prompt",
    "_memory_change_clarification_user_prompt",
    "_normalize_allowed_target_files",
    "_memory_pointer_index",
    "_file_pointer_index",
    "_state_pointer_index",
    "_timeline_pointer_index",
    "_memory_id_summary",
    "_collect_ids",
    "_repair_decision_repair_prompt",
    "_structured_decision_repair_prompt",
    "_target_schema_repair_prompt",
    "_empty_memory_repair_decision",
]
