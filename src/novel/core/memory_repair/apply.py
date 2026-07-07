# mypy: ignore-errors
# ruff: noqa: F403,F405
from __future__ import annotations

from .deps import *
from .models import *
from .validation import *
from .preflight import *
from .impact import *

def apply_memory_repair(root: Path, proposal_path: Path) -> MemoryRepairApplyResult:
    root = root.resolve()
    proposal = load_json_model(_resolve_proposal_path(root, proposal_path), MemoryRepairProposal)
    backups: list[str] = []
    touched_files: list[str] = []
    apply_log_path = _repair_dir(root, proposal.repair_id) / "apply_log.json"
    try:
        if not proposal.operations:
            raise MemoryRepairError("memory repair proposal has no operations to apply")
        operations, _gender_notes = _normalize_setting_change_gender_operations(
            root,
            proposal.operations,
            change_kind=proposal.change_kind,
        )
        preflight_errors = _preflight_memory_repair_operations(root, operations, change_kind=proposal.change_kind)
        if preflight_errors:
            log_app_warning(
                root,
                "memory_repair_preflight_rejected",
                workflow="apply_preflight",
                repair_id=proposal.repair_id,
                change_kind=proposal.change_kind,
                target_files=proposal.target_files,
                operation_count=len(proposal.operations),
                preflight_errors=preflight_errors,
            )
            raise MemoryRepairError(
                "memory repair proposal failed target schema preflight or semantic preflight: "
                + _format_preflight_errors(preflight_errors)
            )
        grouped = _group_operations(operations)
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
            applied_at=utc_now(),
            status="applied",
            target_files=touched_files,
            backups=backups,
        )
        atomic_write_model_json(apply_log_path, apply_log)
        record_management_event(
            root,
            "memory_repair_applied",
            f"项目管家已应用记忆修复：{proposal.repair_id}",
            source="memory_repair",
            target_files=touched_files,
            status="success",
            details={
                "repair_id": proposal.repair_id,
                "change_kind": proposal.change_kind,
                "affected_chapters": proposal.impact.affected_chapters if proposal.impact else [],
                "affected_sessions": proposal.impact.affected_sessions if proposal.impact else [],
                "stale_chapters": proposal.impact.stale_chapters if proposal.impact else [],
            },
        )
        return MemoryRepairApplyResult(proposal=proposal, apply_log=apply_log, apply_log_path=apply_log_path)
    except Exception as exc:
        _restore_backups(root, touched_files, backups)
        log_app_warning(
            root,
            "memory_repair_apply_rolled_back" if backups else "memory_repair_apply_failed",
            workflow="apply",
            repair_id=proposal.repair_id,
            change_kind=proposal.change_kind,
            target_files=touched_files or proposal.target_files,
            backups=backups,
            error_type=exc.__class__.__name__,
            error=str(exc),
        )
        apply_log = MemoryRepairApplyLog(
            repair_id=proposal.repair_id,
            applied_at=utc_now(),
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
            source="memory_repair",
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
        f"- Change kind: {proposal.change_kind}",
        f"- Stage: {proposal.stage}",
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
    if proposal.impact:
        lines.extend(["", "## Impact", ""])
        lines.append(f"- Summary: {proposal.impact.summary}")
        lines.append(f"- Domains: {', '.join(proposal.impact.domains) or 'none'}")
        lines.append(f"- Entity IDs: {', '.join(proposal.impact.entity_ids) or 'none'}")
        lines.append(
            "- Affected chapters: "
            + (", ".join(str(number) for number in proposal.impact.affected_chapters) or "none")
        )
        lines.append(f"- Affected sessions: {', '.join(proposal.impact.affected_sessions) or 'none'}")
        lines.append(
            "- Stale accepted chapters: "
            + (", ".join(str(number) for number in proposal.impact.stale_chapters) or "none")
        )
    if proposal.followup_actions:
        lines.extend(["", "## Follow-up Actions", ""])
        for action in proposal.followup_actions:
            chapters = ",".join(str(number) for number in action.chapter_numbers) or "-"
            lines.append(f"- `{action.action}` session={action.session_id or '-'} chapters={chapters}: {action.reason}")
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


def _drop_unsafe_remove_operations(
    root: Path,
    operations: list[MemoryRepairOperation],
    notes: list[str],
) -> list[MemoryRepairOperation]:
    safe_operations: list[MemoryRepairOperation] = []
    for operation in operations:
        if operation.op != "remove":
            safe_operations.append(operation)
            continue
        entity_ids = _entity_ids_from_operation_path(root, operation)
        referenced = sorted(
            entity_id
            for entity_id in entity_ids
            if _entity_id_has_references(root, entity_id, exclude_file=operation.file)
        )
        if referenced:
            notes.append(
                "已拒绝删除仍被其他 memory/章节/session 文件引用的实体："
                + ", ".join(referenced)
            )
            continue
        safe_operations.append(operation)
    return safe_operations

__all__ = [name for name in globals() if not name.startswith("__")]
