from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
from typing import Any

from pydantic import ValidationError

from novel.core.agent_output import (
    AgentInvocationContext,
    AgentOutputContract,
    AgentOutputContractError,
    generate_with_output_guard,
)
from novel.core.io import atomic_write_json, atomic_write_model_json, atomic_write_text, backup_file, load_json, load_json_model
from novel.core.management import record_management_event
from novel.core.prompts import load_prompt_template
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.schemas import (
    CharactersFile,
    EntityState,
    ForeshadowingFile,
    HiddenTruthsFile,
    ItemsFile,
    LocationsFile,
    MemoryRepairDecision,
    MemoryRepairApplyLog,
    MemoryRepairOperation,
    MemoryRepairProposal,
    TimelineFile,
    WorldFile,
)
from novel.core.validation import validate_project


class MemoryRepairError(RuntimeError):
    """Raised when a memory repair proposal cannot be created or applied safely."""


@dataclass(frozen=True)
class MemoryRepairSuggestResult:
    proposal: MemoryRepairProposal
    proposal_path: Path
    markdown_path: Path


@dataclass(frozen=True)
class MemoryRepairApplyResult:
    proposal: MemoryRepairProposal
    apply_log: MemoryRepairApplyLog
    apply_log_path: Path


ALLOWED_MEMORY_FILES: dict[str, type] = {
    "memory/state/timeline.json": TimelineFile,
    "memory/state/current_state.json": EntityState,
    "memory/canon/characters.json": CharactersFile,
    "memory/canon/locations.json": LocationsFile,
    "memory/canon/items.json": ItemsFile,
    "memory/canon/world.json": WorldFile,
    "memory/canon/hidden_truths.json": HiddenTruthsFile,
    "memory/canon/foreshadowing.json": ForeshadowingFile,
}


def suggest_memory_repair(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
    decision: MemoryRepairDecision | None = None,
) -> MemoryRepairSuggestResult:
    root = root.resolve()
    request = user_request.strip()
    if not request:
        raise MemoryRepairError("memory repair request must not be empty")
    repair_id = _new_repair_id()
    repair_decision = decision or generate_memory_repair_decision(
        root,
        request,
        provider_name=provider_name,
        provider=provider,
    )
    target_files, operations, notes = _sanitize_repair_decision(repair_decision)
    report = validate_project(root)
    proposal = MemoryRepairProposal(
        repair_id=repair_id,
        user_request=request,
        target_files=target_files,
        operations=operations,
        risk_level="low" if operations else "medium",
        confidence=repair_decision.confidence,
        assumptions=repair_decision.assumptions,
        needs_user_confirmation=True,
        validation_before={
            "ok": report.ok,
            "error_count": len(report.errors),
            "warning_count": len(report.warnings),
        },
        notes=_proposal_notes(target_files, operations, notes),
        created_at=_utc_now(),
    )
    repair_dir = _repair_dir(root, repair_id)
    proposal_path = repair_dir / "proposal.json"
    markdown_path = repair_dir / "proposal.md"
    atomic_write_model_json(proposal_path, proposal)
    atomic_write_json(markdown_path.with_suffix(".diff.json"), _preview_operations(root, proposal))
    atomic_write_text(markdown_path, render_memory_repair_markdown(proposal))
    record_management_event(
        root,
        "memory_repair_proposed",
        f"项目管家生成记忆修复建议：{repair_id}",
        source="orchestrator",
        target_files=target_files,
        status="info",
        details={"repair_id": repair_id, "operation_count": len(operations)},
    )
    return MemoryRepairSuggestResult(proposal=proposal, proposal_path=proposal_path, markdown_path=markdown_path)


def generate_memory_repair_decision(
    root: Path,
    user_request: str,
    *,
    provider_name: str = "config",
    provider: ModelProvider | None = None,
) -> MemoryRepairDecision:
    request = user_request.strip()
    if provider is None and provider_name.lower() == "mock":
        return _mock_memory_repair_decision(root, request)
    repair_provider = provider or create_agent_provider(
        default_agent_config_path(root),
        "orchestrator",
        overrides=ProviderOverrides(provider_name=provider_name),
    )
    user_prompt = _memory_repair_user_prompt(root, request)
    try:
        content = generate_with_output_guard(
            repair_provider,
            ModelRequest(
                system_prompt=load_prompt_template("memory_repair_system"),
                user_prompt=user_prompt,
                json_schema_name="MemoryRepairDecision",
            ),
            root=root,
            invocation=AgentInvocationContext(
                agent_name="orchestrator",
                caller="memory_repair",
                interaction_mode="internal_task",
                task="memory_repair_decision",
            ),
            contract=AgentOutputContract(
                output_kind="json",
                target_name="MemoryRepairDecision",
                json_schema_name="MemoryRepairDecision",
                allow_user_questions=False,
            ),
        )
    except AgentOutputContractError:
        return _empty_memory_repair_decision("provider output violated MemoryRepairDecision contract")
    try:
        return parse_memory_repair_decision(content)
    except MemoryRepairError as first_error:
        try:
            repair_content = generate_with_output_guard(
                repair_provider,
                ModelRequest(
                    system_prompt=load_prompt_template("memory_repair_system"),
                    user_prompt=_repair_decision_repair_prompt(
                        original_prompt=user_prompt,
                        invalid_output=content,
                        error=str(first_error),
                    ),
                    json_schema_name="MemoryRepairDecision",
                ),
                root=root,
                invocation=AgentInvocationContext(
                    agent_name="orchestrator",
                    caller="memory_repair",
                    interaction_mode="internal_task",
                    task="memory_repair_decision_repair",
                ),
                contract=AgentOutputContract(
                    output_kind="json",
                    target_name="MemoryRepairDecision",
                    json_schema_name="MemoryRepairDecision",
                    allow_user_questions=False,
                ),
            )
        except AgentOutputContractError:
            return _empty_memory_repair_decision("provider repair output violated MemoryRepairDecision contract")
        try:
            return parse_memory_repair_decision(repair_content)
        except MemoryRepairError as second_error:
            return _empty_memory_repair_decision(f"provider returned invalid MemoryRepairDecision: {second_error}")


def parse_memory_repair_decision(content: str) -> MemoryRepairDecision:
    raw = _extract_json_object(content)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryRepairDecision JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MemoryRepairError("provider returned MemoryRepairDecision as a non-object JSON value")
    data = dict(data)
    data["needs_user_confirmation"] = True
    data["source"] = data.get("source") or "model"
    try:
        return MemoryRepairDecision.model_validate(data)
    except ValidationError as exc:
        raise MemoryRepairError(f"provider returned invalid MemoryRepairDecision: {exc}") from exc


def apply_memory_repair(root: Path, proposal_path: Path) -> MemoryRepairApplyResult:
    root = root.resolve()
    proposal = load_json_model(_resolve_proposal_path(root, proposal_path), MemoryRepairProposal)
    backups: list[str] = []
    touched_files: list[str] = []
    apply_log_path = _repair_dir(root, proposal.repair_id) / "apply_log.json"
    try:
        if not proposal.operations:
            raise MemoryRepairError("memory repair proposal has no operations to apply")
        grouped = _group_operations(proposal.operations)
        for rel_path, operations in grouped.items():
            _ensure_allowed_file(rel_path)
            path = root / rel_path
            data = load_json(path)
            updated = _apply_operations_to_data(data, operations)
            _validate_file_model(rel_path, updated)
            backup = backup_file(path, reason="memory_repair")
            backups.append(str(backup.relative_to(root)))
            touched_files.append(rel_path)
            atomic_write_json(path, updated)
        report = validate_project(root)
        if not report.ok:
            raise MemoryRepairError(
                "memory repair produced validation errors: "
                + "; ".join(message.message for message in report.errors)
            )
        apply_log = MemoryRepairApplyLog(
            repair_id=proposal.repair_id,
            applied_at=_utc_now(),
            status="applied",
            target_files=touched_files,
            backups=backups,
        )
        atomic_write_model_json(apply_log_path, apply_log)
        record_management_event(
            root,
            "memory_repair_applied",
            f"项目管家已应用记忆修复：{proposal.repair_id}",
            source="orchestrator",
            target_files=touched_files,
            status="success",
            details={"repair_id": proposal.repair_id},
        )
        return MemoryRepairApplyResult(proposal=proposal, apply_log=apply_log, apply_log_path=apply_log_path)
    except Exception as exc:
        _restore_backups(root, touched_files, backups)
        apply_log = MemoryRepairApplyLog(
            repair_id=proposal.repair_id,
            applied_at=_utc_now(),
            status="rolled_back" if backups else "failed",
            target_files=touched_files,
            backups=backups,
            errors=[f"{exc.__class__.__name__}: {exc}"],
        )
        atomic_write_model_json(apply_log_path, apply_log)
        record_management_event(
            root,
            "memory_repair_failed",
            f"项目管家记忆修复失败：{proposal.repair_id}",
            source="orchestrator",
            target_files=touched_files or proposal.target_files,
            status="error",
            details={"repair_id": proposal.repair_id, "error": str(exc)},
        )
        raise MemoryRepairError(str(exc)) from exc


def render_memory_repair_markdown(proposal: MemoryRepairProposal) -> str:
    lines = [
        f"# Memory Repair {proposal.repair_id}",
        "",
        f"- Created by: {proposal.created_by}",
        f"- Risk: {proposal.risk_level}",
        f"- Confidence: {proposal.confidence:.2f}",
        f"- Needs confirmation: {proposal.needs_user_confirmation}",
        f"- Request: {proposal.user_request}",
        "",
        "## Target Files",
        "",
    ]
    lines.extend(f"- {path}" for path in proposal.target_files)
    lines.extend(["", "## Operations", ""])
    if proposal.operations:
        for operation in proposal.operations:
            lines.append(f"- `{operation.op}` `{operation.file}` `{operation.path}`: {operation.reason}")
    else:
        lines.append("- 暂无可安全自动应用的操作；请补充具体 ID 或手动编辑 proposal。")
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in proposal.notes)
    return "\n".join(lines) + "\n"


def _sanitize_repair_decision(decision: MemoryRepairDecision) -> tuple[list[str], list[MemoryRepairOperation], list[str]]:
    notes = list(decision.notes)
    target_files = sorted({path for path in decision.target_files if path in ALLOWED_MEMORY_FILES})
    dropped_targets = sorted({path for path in decision.target_files if path not in ALLOWED_MEMORY_FILES})
    if dropped_targets:
        notes.append("已忽略非白名单目标文件：" + ", ".join(dropped_targets))
    operations: list[MemoryRepairOperation] = []
    for operation in decision.operations:
        if operation.file not in ALLOWED_MEMORY_FILES:
            notes.append(f"已忽略非白名单操作目标：{operation.file} {operation.path}")
            continue
        operations.append(operation)
        if operation.file not in target_files:
            target_files.append(operation.file)
    return sorted(target_files), operations, notes


def _memory_repair_user_prompt(root: Path, request: str) -> str:
    return (
        "请生成 MemoryRepairDecision JSON。\n"
        "允许 target_files：\n"
        + "\n".join(f"- {path}" for path in sorted(ALLOWED_MEMORY_FILES))
        + "\n\n"
        "当前可见 ID 摘要：\n"
        f"{_memory_id_summary(root)}\n\n"
        f"用户请求：\n{request}\n"
    )


def _memory_id_summary(root: Path) -> str:
    lines: list[str] = []
    for rel_path in sorted(ALLOWED_MEMORY_FILES):
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


def _repair_decision_repair_prompt(*, original_prompt: str, invalid_output: str, error: str) -> str:
    return (
        f"{original_prompt}\n\n"
        "上一次输出不能被解析为 MemoryRepairDecision。\n"
        f"错误：{error}\n\n"
        "请重新只输出 JSON object。不要 Markdown 或解释。"
        "如果无法安全定位 JSON Pointer，operations 必须为空，并在 notes 说明需要用户补充什么。\n"
        f"上一次输出：\n{invalid_output[:3000]}\n"
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


def _mock_memory_repair_decision(root: Path, request: str) -> MemoryRepairDecision:
    target_files = _mock_infer_target_files(request)
    operations = _mock_infer_operations(root, request, target_files)
    return MemoryRepairDecision(
        target_files=target_files,
        operations=operations,
        confidence=0.8 if operations else 0.2,
        assumptions=["mock provider fixture only; not used as real business inference"],
        needs_user_confirmation=True,
        notes=["mock provider generated deterministic repair proposal for tests."],
        source="mock",
    )


def _mock_infer_target_files(request: str) -> list[str]:
    text = request.lower()
    targets: list[str] = []
    if any(token in text for token in ("timeline", "时间线", "事件", "回忆", "插叙", "倒序")):
        targets.append("memory/state/timeline.json")
    if any(token in text for token in ("state", "状态", "位置", "持有人", "知道", "知识")):
        targets.append("memory/state/current_state.json")
    if any(token in text for token in ("canon", "设定", "角色", "地点", "物品", "世界观")):
        targets.extend(["memory/canon/characters.json", "memory/canon/locations.json", "memory/canon/items.json"])
    return sorted(set(targets or ["memory/state/timeline.json"]))


def _mock_infer_operations(root: Path, request: str, target_files: list[str]) -> list[MemoryRepairOperation]:
    operations: list[MemoryRepairOperation] = []
    if "memory/state/timeline.json" not in target_files:
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


def _proposal_notes(target_files: list[str], operations: list[MemoryRepairOperation], decision_notes: list[str]) -> list[str]:
    notes = ["项目管家只生成 proposal，不会静默修改正式 memory 文件。"]
    notes.extend(decision_notes)
    if not operations:
        notes.append("没有足够信息生成可安全自动应用的 patch；请在请求中提供具体 event/entity id。")
    if "memory/state/timeline.json" in target_files:
        notes.append("timeline 修复应区分 narrative_position 与 story_position。")
    return notes


def _extract_json_object(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise MemoryRepairError("provider response did not contain a JSON object")
    return stripped[start : end + 1]


def _preview_operations(root: Path, proposal: MemoryRepairProposal) -> dict[str, object]:
    preview: dict[str, object] = {}
    for rel_path, operations in _group_operations(proposal.operations).items():
        try:
            preview[rel_path] = _apply_operations_to_data(load_json(root / rel_path), operations)
        except Exception as exc:
            preview[rel_path] = {"error": str(exc)}
    return preview


def _group_operations(operations: list[MemoryRepairOperation]) -> dict[str, list[MemoryRepairOperation]]:
    grouped: dict[str, list[MemoryRepairOperation]] = {}
    for operation in operations:
        _ensure_allowed_file(operation.file)
        grouped.setdefault(operation.file, []).append(operation)
    return grouped


def _apply_operations_to_data(data: object, operations: list[MemoryRepairOperation]) -> object:
    updated = json.loads(json.dumps(data, ensure_ascii=False))
    for operation in operations:
        _apply_operation(updated, operation)
    return updated


def _apply_operation(data: object, operation: MemoryRepairOperation) -> None:
    parent, key = _resolve_pointer_parent(data, operation.path)
    if operation.op == "replace":
        if isinstance(parent, list):
            parent[int(key)] = operation.value
        elif isinstance(parent, dict):
            if key not in parent:
                raise MemoryRepairError(f"replace path does not exist: {operation.path}")
            parent[key] = operation.value
        return
    if operation.op == "add":
        if isinstance(parent, list):
            if key == "-":
                parent.append(operation.value)
            else:
                parent.insert(int(key), operation.value)
        elif isinstance(parent, dict):
            parent[key] = operation.value
        return
    if operation.op == "remove":
        if isinstance(parent, list):
            parent.pop(int(key))
        elif isinstance(parent, dict):
            if key not in parent:
                raise MemoryRepairError(f"remove path does not exist: {operation.path}")
            del parent[key]


def _resolve_pointer_parent(data: object, pointer: str) -> tuple[Any, str]:
    if not pointer.startswith("/"):
        raise MemoryRepairError(f"invalid JSON pointer: {pointer}")
    parts = [_unescape_pointer(part) for part in pointer.strip("/").split("/")]
    if not parts:
        raise MemoryRepairError("operation path cannot target the document root")
    target: Any = data
    for part in parts[:-1]:
        if isinstance(target, list):
            target = target[int(part)]
        elif isinstance(target, dict):
            target = target[part]
        else:
            raise MemoryRepairError(f"invalid JSON pointer path: {pointer}")
    return target, parts[-1]


def _unescape_pointer(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _ensure_allowed_file(rel_path: str) -> None:
    if rel_path not in ALLOWED_MEMORY_FILES:
        raise MemoryRepairError(f"memory repair target is not allowed: {rel_path}")


def _validate_file_model(rel_path: str, data: object) -> None:
    model = ALLOWED_MEMORY_FILES[rel_path]
    try:
        model.model_validate(data)
    except ValidationError as exc:
        raise MemoryRepairError(f"schema validation failed for {rel_path}: {exc}") from exc


def _restore_backups(root: Path, touched_files: list[str], backups: list[str]) -> None:
    for rel_path, backup_rel in zip(touched_files, backups, strict=False):
        backup_path = root / backup_rel
        if backup_path.exists():
            shutil.copy2(backup_path, root / rel_path)


def _resolve_proposal_path(root: Path, proposal_path: Path) -> Path:
    path = proposal_path if proposal_path.is_absolute() else root / proposal_path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise MemoryRepairError("repair proposal must be inside the project workspace") from exc
    if path.name != "proposal.json" or "memory/repairs" not in str(path.relative_to(root)):
        raise MemoryRepairError("repair proposal must be memory/repairs/{repair_id}/proposal.json")
    return path


def _repair_dir(root: Path, repair_id: str) -> Path:
    return root / "memory" / "repairs" / repair_id


def _new_repair_id() -> str:
    return "repair_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
