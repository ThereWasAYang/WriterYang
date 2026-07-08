from __future__ import annotations

from .deps import (
    json,
    Path,
    re,
    atomic_write_model_json,
    load_json,
    _unescape_pointer,
    ALLOWED_MEMORY_FILES,
    DOMAIN_FILES,
    FILE_COLLECTION_KEYS,
    FILE_DOMAINS,
    SCANNED_IMPACT_SUFFIXES,
    STATE_COLLECTION_KEYS,
    MemoryChangeBatch,
    MemoryChangeBatchPlan,
    MemoryChangeDomain,
    MemoryChangeClarificationDecision,
    MemoryChangeClarificationSession,
    MemoryChangeConversationTurn,
    MemoryChangeFollowupAction,
    MemoryChangeImpact,
    MemoryChangeKind,
    MemoryChangeStage,
    MemoryRepairDecision,
    MemoryRepairRiskLevel,
    MemoryRepairOperation,
    utc_now,
)

from .models import (
    MemoryRepairError,
    MemoryRepairSuggestResult,
    _PreparedMemoryRepairDecision,
)

from .validation import (
    _clarification_path,
    _new_clarification_id,
)

from .preflight import (
    _dedupe_preserve_order,
)


def _validate_memory_change_batch_plan(plan: MemoryChangeBatchPlan) -> None:
    if plan.change_kind != "setting_change":
        raise MemoryRepairError("MemoryChangeBatchPlan.change_kind must be setting_change")
    for batch in plan.batches:
        _target_files_for_batch(batch)


def _target_files_for_batch(batch: MemoryChangeBatch) -> list[str]:
    target_files = [path for path in batch.target_files if path in ALLOWED_MEMORY_FILES]
    invalid_files = sorted({path for path in batch.target_files if path not in ALLOWED_MEMORY_FILES})
    if invalid_files:
        raise MemoryRepairError(
            f"MemoryChangeBatch {batch.batch_id} targets non-allowed file(s): " + ", ".join(invalid_files)
        )
    for domain in batch.domains:
        rel_path = DOMAIN_FILES.get(domain)
        if rel_path:
            target_files.append(rel_path)
    target_files = sorted(dict.fromkeys(target_files))
    if not target_files:
        raise MemoryRepairError(f"MemoryChangeBatch {batch.batch_id} has no allowed target_files")
    return target_files


def _batch_memory_repair_request(original_request: str, batch: MemoryChangeBatch) -> str:
    return (
        "原始设定变更请求：\n"
        f"{original_request}\n\n"
        f"当前批次：{batch.batch_id}\n"
        f"批次原因：{batch.reason}\n"
        "本批次只生成下列领域/文件相关 operations；不要处理其他批次内容。\n"
        f"domains: {', '.join(batch.domains) or 'none'}\n"
        f"target_files: {', '.join(_target_files_for_batch(batch))}\n\n"
        "本批次具体指令：\n"
        f"{batch.instruction}\n"
    )


def _merge_batched_memory_repair_decisions(
    plan: MemoryChangeBatchPlan,
    prepared_batches: list[_PreparedMemoryRepairDecision],
    *,
    stage: MemoryChangeStage,
) -> MemoryRepairDecision:
    operations: list[MemoryRepairOperation] = []
    target_files: list[str] = []
    domains: list[MemoryChangeDomain] = [domain for batch in plan.batches for domain in batch.domains]
    notes: list[str] = []
    assumptions: list[str] = list(plan.assumptions)
    followups: list[MemoryChangeFollowupAction] = []
    confidences = [plan.confidence]
    notes.extend(plan.notes)
    notes.append(f"已按 {len(plan.batches)} 个批次生成设定变更建议，并合并为单个 proposal。")
    for batch, prepared in zip(plan.batches, prepared_batches):
        decision = prepared.decision
        batch_prefix = f"批次 {batch.batch_id}"
        operations.extend(prepared.operations)
        target_files.extend(prepared.target_files)
        domains.extend(decision.domains)
        domains.extend(_domains_from_files(prepared.target_files))
        assumptions.extend(decision.assumptions)
        followups.extend(decision.followup_actions)
        confidences.append(decision.confidence)
        if prepared.operations:
            notes.append(f"{batch_prefix} 生成 {len(prepared.operations)} 条 operations。")
        else:
            notes.append(f"{batch_prefix} 未生成可安全自动应用的 operations。")
        notes.extend(prepared.notes)
    confidence_values = [value for value in confidences if value > 0]
    confidence = min(confidence_values) if confidence_values else 0.0
    return MemoryRepairDecision(
        change_kind="setting_change",
        target_files=sorted(set(target_files)),
        operations=operations,
        domains=_dedupe_domains(domains),
        stage=stage or plan.stage or "unknown",
        followup_actions=followups,
        confidence=confidence,
        assumptions=_dedupe_preserve_order(assumptions),
        needs_user_confirmation=True,
        notes=_dedupe_preserve_order(notes),
        source=plan.source,
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


def _new_clarification_session(
    root: Path,
    request: str,
    *,
    decision: MemoryChangeClarificationDecision,
    stage: MemoryChangeStage,
    session_id: str | None,
    chapter_number: int | None,
    audit_issue_ids: list[str],
) -> MemoryChangeClarificationSession:
    now = utc_now()
    clarification = MemoryChangeClarificationSession(
        clarification_id=_new_clarification_id(),
        original_request=request,
        stage=stage,
        session_id=session_id,
        chapter_number=chapter_number,
        audit_issue_ids=audit_issue_ids,
        status="needs_clarification",
        questions=decision.questions,
        conversation_turns=[
            MemoryChangeConversationTurn(role="user", content=request, created_at=now),
            MemoryChangeConversationTurn(role="agent", content="\n".join(decision.questions), created_at=now),
        ],
        created_at=now,
        updated_at=now,
    )
    _write_clarification_session(root, clarification)
    return clarification


def _write_clarification_session(root: Path, clarification: MemoryChangeClarificationSession) -> None:
    atomic_write_model_json(_clarification_path(root, clarification.clarification_id), clarification)


def _no_op_setting_change_proposal(
    root: Path,
    request: str,
    *,
    stage: MemoryChangeStage,
    session_id: str | None,
    chapter_number: int | None,
    audit_issue_ids: list[str],
    notes: list[str],
) -> MemoryRepairSuggestResult:
    from .service import suggest_memory_repair

    decision = MemoryRepairDecision(
        change_kind="setting_change",
        target_files=[],
        operations=[],
        domains=[],
        stage=stage,
        confidence=0.0,
        assumptions=[],
        needs_user_confirmation=True,
        notes=notes,
        source="fallback",
    )
    return suggest_memory_repair(
        root,
        request,
        provider_name="mock",
        decision=decision,
        change_kind="setting_change",
        stage=stage,
        session_id=session_id,
        chapter_number=chapter_number,
        audit_issue_ids=audit_issue_ids,
    )


def _combined_setting_change_request(original_request: str, turns: list[MemoryChangeConversationTurn]) -> str:
    answer_lines = [
        f"{index}. {turn.content}"
        for index, turn in enumerate(turns, start=1)
        if turn.role == "user" and turn.content != original_request
    ]
    if not answer_lines:
        return original_request
    return (
        "原始设定变更请求：\n"
        f"{original_request}\n\n"
        "用户补充信息：\n"
        + "\n".join(answer_lines)
    )


def _entity_id_has_references(root: Path, entity_id: str, *, exclude_file: str) -> bool:
    for path in _impact_scan_paths(root):
        rel_path = _safe_rel(root, path)
        if rel_path == exclude_file:
            continue
        try:
            if entity_id in path.read_text(encoding="utf-8"):
                return True
        except Exception:
            continue
    return False


def _dedupe_domains(domains: list[MemoryChangeDomain]) -> list[MemoryChangeDomain]:
    return sorted(set(domains))


def _domains_from_files(target_files: list[str]) -> list[MemoryChangeDomain]:
    return [domain for path in target_files if (domain := FILE_DOMAINS.get(path))]


def _analyze_memory_change_impact(
    root: Path,
    operations: list[MemoryRepairOperation],
    *,
    domains: list[MemoryChangeDomain],
    session_id: str | None,
    chapter_number: int | None,
) -> MemoryChangeImpact:
    entity_ids = _affected_entity_ids(root, operations)
    affected_files: set[str] = {operation.file for operation in operations}
    affected_chapters: set[int] = set()
    affected_sessions: set[str] = set()
    reference_count = 0

    if chapter_number and chapter_number > 0:
        affected_chapters.add(chapter_number)
    if session_id:
        affected_sessions.add(session_id)

    for chapter in _chapters_from_timeline_operations(root, operations):
        affected_chapters.add(chapter)

    if entity_ids:
        for path in _impact_scan_paths(root):
            rel_path = _safe_rel(root, path)
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if not any(entity_id in text for entity_id in entity_ids):
                continue
            reference_count += 1
            affected_files.add(rel_path)
            chapter = _chapter_number_from_path(path)
            if chapter:
                affected_chapters.add(chapter)
            session = _session_id_from_path(path)
            if session:
                affected_sessions.add(session)

    for session in _sessions_referencing_chapters(root, affected_chapters):
        affected_sessions.add(session)

    stale_chapters = sorted(chapter for chapter in affected_chapters if _chapter_is_accepted(root, chapter))
    risk_level: MemoryRepairRiskLevel = "low"
    if any(operation.op == "remove" for operation in operations) or stale_chapters:
        risk_level = "high"
    elif affected_chapters or len(affected_files) > len({operation.file for operation in operations}):
        risk_level = "medium"
    summary = _impact_summary(entity_ids, affected_chapters, affected_sessions, stale_chapters)
    return MemoryChangeImpact(
        domains=domains,
        entity_ids=sorted(entity_ids),
        affected_files=sorted(affected_files),
        affected_chapters=sorted(affected_chapters),
        affected_sessions=sorted(affected_sessions),
        stale_chapters=stale_chapters,
        risk_level=risk_level,
        reference_count=reference_count,
        summary=summary,
    )


def _memory_change_followups(
    root: Path,
    impact: MemoryChangeImpact,
    *,
    stage: MemoryChangeStage,
    session_id: str | None,
    chapter_number: int | None,
    change_kind: MemoryChangeKind,
) -> list[MemoryChangeFollowupAction]:
    if change_kind != "setting_change":
        return []
    actions: list[MemoryChangeFollowupAction] = []
    session_data = _safe_session_data(root, session_id) if session_id else None
    session_chapters = _session_chapters(session_data)
    affected_chapters = sorted(set(impact.affected_chapters) | ({chapter_number} if chapter_number else set()))
    if session_data:
        status = str(session_data.get("status") or "")
        outline_status = str(session_data.get("outline_status") or "")
        content_status = str(session_data.get("content_status") or "")
        if status in {"accepted", "archived"} or content_status in {"accepted", "archived"}:
            actions.append(
                MemoryChangeFollowupAction(
                    action="start_revision_session",
                    session_id=session_id,
                    chapter_numbers=session_chapters or affected_chapters,
                    auto=False,
                    reason="当前 session 已认可或归档；设定变更不会静默改写既有章节。",
                )
            )
        elif content_status == "not_started":
            actions.append(
                MemoryChangeFollowupAction(
                    action="revise_outline",
                    session_id=session_id,
                    chapter_numbers=session_chapters or affected_chapters,
                    auto=True,
                    reason="设定变更发生在大纲阶段，需要基于最新 memory 重生成 outline proposal。",
                )
            )
            if outline_status == "approved":
                actions.append(
                    MemoryChangeFollowupAction(
                        action="reapprove_outline",
                        session_id=session_id,
                        chapter_numbers=session_chapters or affected_chapters,
                        auto=False,
                        reason="原大纲已批准，重生成后需要用户重新批准。",
                    )
                )
        elif content_status in {"needs_user_review", "needs_revision"} or stage == "content_review":
            actions.extend(
                [
                    MemoryChangeFollowupAction(
                        action="revise_content",
                        session_id=session_id,
                        chapter_numbers=session_chapters or affected_chapters,
                        auto=True,
                        reason="设定变更影响审查中的正文，需要走现有修订链路。",
                    ),
                    MemoryChangeFollowupAction(
                        action="reaudit_chapters",
                        session_id=session_id,
                        chapter_numbers=session_chapters or affected_chapters,
                        auto=True,
                        reason="正文同步设定变更后必须重新 Audit。",
                    ),
                    MemoryChangeFollowupAction(
                        action="rebuild_state_proposal",
                        session_id=session_id,
                        chapter_numbers=session_chapters or affected_chapters,
                        auto=True,
                        reason="正文重审通过后必须重建 state/timeline proposal。",
                    ),
                ]
            )
        else:
            actions.append(
                MemoryChangeFollowupAction(
                    action="manual_review",
                    session_id=session_id,
                    chapter_numbers=session_chapters or affected_chapters,
                    auto=False,
                    reason="当前 session 状态不适合自动同步设定变更。",
                )
            )
    for stale_chapter in impact.stale_chapters:
        actions.append(
            MemoryChangeFollowupAction(
                action="start_revision_session",
                chapter_numbers=[stale_chapter],
                auto=False,
                reason="已 accepted 的章节只标记为受影响，需要用户显式发起修订 session。",
            )
        )
    if not actions:
        actions.append(
            MemoryChangeFollowupAction(
                action="none",
                chapter_numbers=affected_chapters,
                auto=False,
                reason="未发现需要自动同步的当前 session；后续创作会读取最新设定。",
            )
        )
    return actions


def _affected_entity_ids(root: Path, operations: list[MemoryRepairOperation]) -> set[str]:
    ids: set[str] = set()
    for operation in operations:
        if isinstance(operation.value, dict):
            for key in ("id", "entity_id", "hidden_truth_id"):
                value = operation.value.get(key)
                if isinstance(value, str) and value:
                    ids.add(value)
        if isinstance(operation.value, list):
            ids.update(item for item in operation.value if isinstance(item, str) and _looks_like_entity_id(item))
        ids.update(_entity_ids_from_operation_path(root, operation))
    return ids


def _entity_ids_from_operation_path(root: Path, operation: MemoryRepairOperation) -> set[str]:
    path = root / operation.file
    if not path.exists():
        return set()
    try:
        data = load_json(path)
    except Exception:
        return set()
    parts = [_unescape_pointer(part) for part in operation.path.strip("/").split("/") if part]
    if not parts:
        return set()
    ids: set[str] = set()
    collection_key = FILE_COLLECTION_KEYS.get(operation.file)
    if collection_key and parts[0] == collection_key and len(parts) >= 2:
        entity = _list_entity_at(data, collection_key, parts[1])
        ids.update(_ids_from_entity(entity))
    elif operation.file == "memory/state/current_state.json" and parts[0] in STATE_COLLECTION_KEYS and len(parts) >= 2:
        entity = _list_entity_at(data, parts[0], parts[1])
        ids.update(_ids_from_entity(entity))
    elif operation.file == "memory/state/timeline.json" and parts[0] == "events" and len(parts) >= 2:
        entity = _list_entity_at(data, "events", parts[1])
        ids.update(_ids_from_entity(entity))
    return ids


def _list_entity_at(data: object, collection_key: str, index_text: str) -> object | None:
    if not isinstance(data, dict):
        return None
    collection = data.get(collection_key)
    if not isinstance(collection, list) or not index_text.isdigit():
        return None
    index = int(index_text)
    if index < 0 or index >= len(collection):
        return None
    return collection[index]


def _ids_from_entity(entity: object | None) -> set[str]:
    if not isinstance(entity, dict):
        return set()
    ids: set[str] = set()
    for key in ("id", "entity_id", "hidden_truth_id"):
        value = entity.get(key)
        if isinstance(value, str) and value:
            ids.add(value)
    for key in ("related_entity_ids", "participant_ids", "known_by_character_ids", "state_change_ids"):
        value = entity.get(key)
        if isinstance(value, list):
            ids.update(item for item in value if isinstance(item, str) and _looks_like_entity_id(item))
    return ids


def _chapters_from_timeline_operations(root: Path, operations: list[MemoryRepairOperation]) -> set[int]:
    chapters: set[int] = set()
    timeline_path = root / "memory/state/timeline.json"
    if not timeline_path.exists():
        return chapters
    try:
        timeline = load_json(timeline_path)
    except Exception:
        return chapters
    for operation in operations:
        if operation.file != "memory/state/timeline.json":
            continue
        parts = [_unescape_pointer(part) for part in operation.path.strip("/").split("/") if part]
        if len(parts) < 2 or parts[0] != "events":
            continue
        event = _list_entity_at(timeline, "events", parts[1])
        if isinstance(event, dict):
            narrative = event.get("narrative_position")
            chapter = _coerce_positive_int(narrative.get("chapter")) if isinstance(narrative, dict) else None
            if chapter:
                chapters.add(chapter)
    return chapters


def _impact_scan_paths(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for base in (root / "memory" / "canon", root / "memory" / "state", root / "memory" / "chapters", root / "memory" / "sessions"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in SCANNED_IMPACT_SUFFIXES:
                candidates.append(path)
    return candidates


def _chapter_number_from_path(path: Path) -> int | None:
    for part in path.parts:
        if re.fullmatch(r"[0-9]{3}", part):
            return int(part)
    return None


def _session_id_from_path(path: Path) -> str | None:
    for part in path.parts:
        if re.fullmatch(r"session_[0-9]{8}_[0-9]{6}_[0-9]{6}", part):
            return part
    return None


def _sessions_referencing_chapters(root: Path, chapters: set[int]) -> set[str]:
    if not chapters:
        return set()
    sessions_dir = root / "memory" / "sessions"
    if not sessions_dir.exists():
        return set()
    sessions: set[str] = set()
    for session_json in sessions_dir.glob("session_*/session.json"):
        try:
            data = load_json(session_json)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        chapter_range = {
            number for item in data.get("chapter_range", []) if (number := _coerce_positive_int(item))
        }
        if chapter_range & chapters:
            sessions.add(session_json.parent.name)
    return sessions


def _chapter_is_accepted(root: Path, chapter_number: int) -> bool:
    metadata_path = root / "memory" / "chapters" / f"{chapter_number:03d}" / "metadata.json"
    if not metadata_path.exists():
        return False
    try:
        data = load_json(metadata_path)
    except Exception:
        return False
    return isinstance(data, dict) and data.get("status") == "accepted"


def _safe_session_data(root: Path, session_id: str | None) -> dict[str, object] | None:
    if not session_id:
        return None
    path = root / "memory" / "sessions" / session_id / "session.json"
    if not path.exists():
        return None
    try:
        data = load_json(path)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _session_chapters(session_data: dict[str, object] | None) -> list[int]:
    if not session_data:
        return []
    chapters: list[int] = []
    chapter_range = session_data.get("chapter_range", [])
    if not isinstance(chapter_range, list):
        return []
    for item in chapter_range:
        number = _coerce_positive_int(item)
        if number:
            chapters.append(number)
    return sorted(set(chapters))


def _impact_summary(
    entity_ids: set[str],
    affected_chapters: set[int],
    affected_sessions: set[str],
    stale_chapters: list[int],
) -> str:
    parts = []
    if entity_ids:
        parts.append(f"涉及实体 {', '.join(sorted(entity_ids))}")
    if affected_chapters:
        parts.append("影响章节 " + ", ".join(str(number) for number in sorted(affected_chapters)))
    if affected_sessions:
        parts.append("影响 session " + ", ".join(sorted(affected_sessions)))
    if stale_chapters:
        parts.append("其中已认可章节 " + ", ".join(str(number) for number in stale_chapters))
    return "；".join(parts) if parts else "未发现章节或 session 引用；后续创作会使用最新 memory。"


def _coerce_positive_int(value: object) -> int | None:
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _looks_like_entity_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]*", value))


def _safe_rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _proposal_notes(
    target_files: list[str],
    operations: list[MemoryRepairOperation],
    decision_notes: list[str],
    *,
    change_kind: MemoryChangeKind,
    audit_issue_ids: list[str],
) -> list[str]:
    notes = ["项目管家只生成 proposal，不会静默修改正式 memory 文件。"]
    if change_kind == "setting_change":
        notes.append("设定变更会先进行影响分析；已 accepted/archived 章节不会被静默改写。")
    if audit_issue_ids:
        notes.append("该 proposal 来源于 Audit issue：" + ", ".join(audit_issue_ids))
    notes.extend(decision_notes)
    if not operations:
        notes.append("没有足够信息生成可安全自动应用的 patch；请在请求中提供具体 event/entity id。")
    if "memory/state/timeline.json" in target_files:
        notes.append("timeline 修复应区分 narrative_position 与 story_position。")
    return notes


def _normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    normalized.append(text)
            elif item is not None:
                normalized.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
        return normalized
    return [json.dumps(value, ensure_ascii=False, sort_keys=True)]

__all__ = [
    "_validate_memory_change_batch_plan",
    "_target_files_for_batch",
    "_batch_memory_repair_request",
    "_merge_batched_memory_repair_decisions",
    "_fallback_clarification_decision",
    "_new_clarification_session",
    "_write_clarification_session",
    "_no_op_setting_change_proposal",
    "_combined_setting_change_request",
    "_entity_id_has_references",
    "_dedupe_domains",
    "_domains_from_files",
    "_analyze_memory_change_impact",
    "_memory_change_followups",
    "_affected_entity_ids",
    "_entity_ids_from_operation_path",
    "_list_entity_at",
    "_ids_from_entity",
    "_chapters_from_timeline_operations",
    "_impact_scan_paths",
    "_chapter_number_from_path",
    "_session_id_from_path",
    "_sessions_referencing_chapters",
    "_chapter_is_accepted",
    "_safe_session_data",
    "_session_chapters",
    "_impact_summary",
    "_coerce_positive_int",
    "_looks_like_entity_id",
    "_safe_rel",
    "_proposal_notes",
    "_normalize_string_list",
]
