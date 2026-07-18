from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeVar

import yaml
from pydantic import BaseModel

from novel.core.artifact_store import sha256_file
from novel.core.contracts import AcceptanceCommit
from novel.core.gender import CharacterGenderValue, infer_character_gender
from novel.core.io import load_json, load_json_model
from novel.core.schemas import (
    AuditEvidence,
    AuditIssue,
    ChapterMetadata,
    ChapterPlan,
    CharactersFile,
    EntityState,
    ForeshadowingFile,
    HiddenTruth,
    HiddenTruthsFile,
    ItemsFile,
    LocationsFile,
    StateUpdateApplyLog,
    StateUpdateProposal,
    TimelineEvent,
    TimelineFile,
    WorldFile,
)
from novel.core.state_change_values import compare_state_change_old_value

Severity = Literal["low", "medium", "high", "critical"]
ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class ConsistencyFinding:
    id: str
    severity: Severity
    type: str
    description: str
    source: Path
    quote: str
    suggested_fix: str

    def to_audit_issue(self) -> AuditIssue:
        return AuditIssue(
            id=self.id,
            severity=self.severity,
            type=self.type,
            category="consistency_violation",
            description=self.description,
            evidence=[AuditEvidence(source=str(self.source), quote=self.quote)],
            suggested_fix=self.suggested_fix,
        )


@dataclass(frozen=True)
class ConsistencyResult:
    findings: tuple[ConsistencyFinding, ...]
    passed_checks: tuple[str, ...]

    @property
    def highest_severity(self) -> Severity | None:
        rank: dict[Severity, int] = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        if not self.findings:
            return None
        return max((finding.severity for finding in self.findings), key=lambda item: rank[item])

    def render_for_prompt(self) -> str:
        if not self.findings:
            return "程序一致性检查：未发现阻断问题。\n"
        lines = ["程序一致性检查："]
        for finding in self.findings:
            lines.append(
                f"- {finding.id} [{finding.severity}/{finding.type}] "
                f"{finding.description} source={finding.source} evidence={finding.quote}"
            )
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ConsistencySnapshot:
    root: Path
    chapter_number: int | None = None
    plan: ChapterPlan | None = None
    draft_metadata: dict[str, object] = field(default_factory=dict)
    polished_metadata: dict[str, object] = field(default_factory=dict)
    audited_body: str | None = None
    audited_file: str | None = None
    include_existing_audit: bool = True
    characters: CharactersFile | None = None
    locations: LocationsFile | None = None
    items: ItemsFile | None = None
    world: WorldFile | None = None
    hidden_truths: HiddenTruthsFile | None = None
    foreshadowing: ForeshadowingFile | None = None
    state: EntityState | None = None
    timeline: TimelineFile | None = None
    proposal: StateUpdateProposal | None = None
    apply_log: StateUpdateApplyLog | None = None
    metadata: ChapterMetadata | None = None


def check_chapter_consistency(
    root: Path,
    chapter_number: int,
    *,
    audited_body: str | None = None,
    audited_file: str | None = None,
    include_existing_audit: bool = True,
) -> ConsistencyResult:
    root = root.resolve()
    chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
    snapshot = ConsistencySnapshot(
        root=root,
        chapter_number=chapter_number,
        plan=_load_optional_model(chapter_dir / "plan.json", ChapterPlan),
        draft_metadata=_read_markdown_metadata(chapter_dir / "draft.md"),
        polished_metadata=_read_markdown_metadata(chapter_dir / "polished.md"),
        audited_body=audited_body,
        audited_file=audited_file,
        include_existing_audit=include_existing_audit,
        characters=_load_optional_model(root / "memory" / "canon" / "characters.json", CharactersFile),
        locations=_load_optional_model(root / "memory" / "canon" / "locations.json", LocationsFile),
        items=_load_optional_model(root / "memory" / "canon" / "items.json", ItemsFile),
        world=_load_optional_model(root / "memory" / "canon" / "world.json", WorldFile),
        hidden_truths=_load_optional_model(root / "memory" / "canon" / "hidden_truths.json", HiddenTruthsFile),
        foreshadowing=_load_optional_model(root / "memory" / "canon" / "foreshadowing.json", ForeshadowingFile),
        state=_load_optional_model(root / "memory" / "state" / "current_state.json", EntityState),
        timeline=_load_optional_model(root / "memory" / "state" / "timeline.json", TimelineFile),
        proposal=_load_optional_model(chapter_dir / "state_update_proposal.json", StateUpdateProposal),
        apply_log=_load_optional_model(chapter_dir / "state_update_apply_log.json", StateUpdateApplyLog),
        metadata=_load_optional_model(chapter_dir / "metadata.json", ChapterMetadata),
    )
    return _check_snapshot(snapshot)


def check_project_consistency(root: Path) -> ConsistencyResult:
    root = root.resolve()
    findings: list[ConsistencyFinding] = []
    passed: list[str] = []
    findings.extend(check_canon_consistency(root).findings)
    project_snapshot = ConsistencySnapshot(
        root=root,
        characters=_load_optional_model(root / "memory" / "canon" / "characters.json", CharactersFile),
        locations=_load_optional_model(root / "memory" / "canon" / "locations.json", LocationsFile),
        items=_load_optional_model(root / "memory" / "canon" / "items.json", ItemsFile),
        hidden_truths=_load_optional_model(root / "memory" / "canon" / "hidden_truths.json", HiddenTruthsFile),
        foreshadowing=_load_optional_model(root / "memory" / "canon" / "foreshadowing.json", ForeshadowingFile),
        state=_load_optional_model(root / "memory" / "state" / "current_state.json", EntityState),
        timeline=_load_optional_model(root / "memory" / "state" / "timeline.json", TimelineFile),
    )
    project_result = _check_snapshot(project_snapshot)
    findings.extend(project_result.findings)
    passed.extend(project_result.passed_checks)
    chapters_dir = root / "memory" / "chapters"
    if chapters_dir.exists():
        for chapter_dir in sorted(path for path in chapters_dir.iterdir() if path.is_dir()):
            if not chapter_dir.name.isdigit():
                continue
            audited_path = chapter_dir / "polished.md"
            audited_body = _read_markdown_body(audited_path)
            result = check_chapter_consistency(
                root,
                int(chapter_dir.name),
                audited_body=audited_body,
                audited_file="polished.md" if audited_body is not None else None,
                include_existing_audit=True,
            )
            findings.extend(result.findings)
            passed.extend(result.passed_checks)
    if not findings:
        passed.append("project_consistency_checks_passed")
    return ConsistencyResult(findings=tuple(_dedupe_findings(findings)), passed_checks=tuple(_dedupe_strings(passed)))


def check_canon_consistency(root: Path) -> ConsistencyResult:
    root = root.resolve()
    findings = _check_reader_visible_hidden_truth_leaks(root)
    passed = [] if findings else ["canon_consistency_checks_passed"]
    return ConsistencyResult(findings=tuple(_dedupe_findings(findings)), passed_checks=tuple(passed))


def _check_snapshot(snapshot: ConsistencySnapshot) -> ConsistencyResult:
    findings: list[ConsistencyFinding] = []
    passed: list[str] = []
    findings.extend(_check_hidden_truth_body_exposure(snapshot))
    findings.extend(_check_character_knowledge(snapshot))
    findings.extend(_check_item_flow(snapshot))
    findings.extend(_check_timeline_order(snapshot))
    findings.extend(_check_chapter_loop(snapshot))
    findings.extend(_check_body_workspace_language(snapshot))
    findings.extend(_check_unplanned_character_mentions(snapshot))
    findings.extend(_check_character_gendered_references(snapshot))
    if not findings:
        passed.append("deterministic_consistency_checks_passed")
    else:
        passed.append("deterministic_consistency_checks_completed")
    return ConsistencyResult(findings=tuple(_dedupe_findings(findings)), passed_checks=tuple(passed))


def _check_hidden_truth_body_exposure(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    if not snapshot.hidden_truths or not snapshot.audited_body or not snapshot.chapter_number:
        return []
    findings: list[ConsistencyFinding] = []
    body = snapshot.audited_body
    compact_body = _compact_text(body)
    source = _chapter_file(snapshot)
    for truth in snapshot.hidden_truths.hidden_truths:
        if _truth_revealed_by_chapter(truth, snapshot.chapter_number):
            continue
        for fragment in _hidden_fragments(truth):
            if _fragment_in_text(fragment, body, compact_body):
                findings.append(
                    ConsistencyFinding(
                        id=f"cons_{snapshot.chapter_number:03d}_hidden_truth_{truth.id}",
                        severity="high",
                        type="premature_reveal",
                        description=(
                            f"本章疑似在计划揭示前暴露了隐藏真相 {truth.id}。"
                        ),
                        source=source,
                        quote=fragment[:160],
                        suggested_fix=(
                            "从读者可见正文中移除或弱化该隐藏真相，或明确把计划揭示章节改到本章。"
                        ),
                    )
                )
                break
    if snapshot.foreshadowing:
        for thread in snapshot.foreshadowing.foreshadowing_threads:
            payoff_chapter = thread.planned_payoff.chapter if thread.planned_payoff else None
            if payoff_chapter is not None and payoff_chapter <= snapshot.chapter_number:
                continue
            hidden_text = thread.hidden_truth or ""
            if hidden_text and _fragment_in_text(hidden_text, body, compact_body):
                findings.append(
                    ConsistencyFinding(
                        id=f"cons_{snapshot.chapter_number:03d}_foreshadow_hidden_{thread.id}",
                        severity="high",
                        type="premature_reveal",
                        description=f"本章疑似提前暴露了伏笔线 {thread.id} 的隐藏信息。",
                        source=source,
                        quote=hidden_text[:160],
                        suggested_fix="在计划回收章节前，只保留读者可见的线索文本。",
                    )
                )
    return findings


def _check_character_knowledge(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    if not snapshot.state or not snapshot.hidden_truths:
        return []
    chapter_number = snapshot.chapter_number or snapshot.state.story_position.latest_chapter
    truth_by_id = {truth.id: truth for truth in snapshot.hidden_truths.hidden_truths}
    findings: list[ConsistencyFinding] = []
    source = snapshot.root / "memory" / "state" / "current_state.json"
    for character in snapshot.state.character_states:
        for index, knowledge in enumerate(character.knowledge, start=1):
            value = str(knowledge)
            matched = _match_hidden_truth(value, truth_by_id.values())
            if matched and not _truth_revealed_by_chapter(matched, chapter_number):
                findings.append(
                    ConsistencyFinding(
                        id=f"cons_knowledge_{character.entity_id}_{matched.id}",
                        severity="high",
                        type="premature_reveal",
                        description=(
                            f"角色 {character.entity_id} 在计划揭示前已经知道隐藏真相 {matched.id}。"
                        ),
                        source=source,
                        quote=value[:160],
                        suggested_fix=(
                            "在揭示前从 current_state 移除该知识，或调整 hidden_truth 的 planned_reveal。"
                        ),
                    )
                )
            elif _looks_like_id(value) and value not in truth_by_id:
                findings.append(
                    ConsistencyFinding(
                        id=f"cons_knowledge_unknown_{character.entity_id}_{index}",
                        severity="low",
                        type="state_conflict",
                        description=f"角色 {character.entity_id} 的 knowledge 引用了未知的疑似 ID 值。",
                        source=source,
                        quote=value[:160],
                        suggested_fix="结构化 knowledge 请使用稳定的 canon/timeline ID，或改写为自然语言。",
                    )
                )
    return findings


def _check_item_flow(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    if not snapshot.state:
        return []
    source = snapshot.root / "memory" / "state" / "current_state.json"
    findings: list[ConsistencyFinding] = []
    item_holders = {item.entity_id: item.holder_id for item in snapshot.state.item_states}
    possession_owner: dict[str, str] = {}
    for item in snapshot.state.item_states:
        if item.holder_id and item.location_id:
            findings.append(
                ConsistencyFinding(
                    id=f"cons_item_holder_location_{item.entity_id}",
                    severity="high",
                    type="state_conflict",
                    description=f"物品 {item.entity_id} 同时设置了 holder_id 和 location_id。",
                    source=source,
                    quote=f"holder_id={item.holder_id}; location_id={item.location_id}",
                    suggested_fix="只保留 holder_id 或 location_id 其中一个。",
                )
            )
    for character in snapshot.state.character_states:
        for item_id in character.possessions:
            previous = possession_owner.get(item_id)
            if previous and previous != character.entity_id:
                findings.append(
                    ConsistencyFinding(
                        id=f"cons_item_multi_owner_{item_id}",
                        severity="high",
                        type="state_conflict",
                        description=f"物品 {item_id} 同时出现在多个角色的 possessions 中。",
                        source=source,
                        quote=f"{previous}, {character.entity_id}",
                        suggested_fix="确保该物品只出现在一个角色的 possessions 中。",
                    )
                )
            possession_owner[item_id] = character.entity_id
            holder = item_holders.get(item_id)
            if holder and holder != character.entity_id:
                findings.append(
                    ConsistencyFinding(
                        id=f"cons_item_holder_possession_{item_id}",
                        severity="high",
                        type="state_conflict",
                        description=f"物品 {item_id} 的 holder_id 与角色 possessions 不一致。",
                        source=source,
                        quote=f"holder_id={holder}; possession_owner={character.entity_id}",
                        suggested_fix="同步 item_state.holder_id 与 character_state.possessions。",
                    )
                )
    for item in snapshot.state.item_states:
        if item.holder_id and item.entity_id not in possession_owner:
            findings.append(
                ConsistencyFinding(
                    id=f"cons_item_holder_missing_possession_{item.entity_id}",
                    severity="low",
                    type="state_conflict",
                    description=f"物品 {item.entity_id} 设置了 holder_id，但持有角色的 possessions 未包含该物品。",
                    source=source,
                    quote=f"holder_id={item.holder_id}; possessions=missing",
                    suggested_fix="将该物品加入持有角色的 possessions，或移除 item_state.holder_id。",
                )
            )
    findings.extend(_check_item_mentions_against_plan(snapshot))
    findings.extend(_check_state_update_old_values(snapshot))
    return findings


def _check_item_mentions_against_plan(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    if not snapshot.items or not snapshot.state or not snapshot.plan or not snapshot.audited_body:
        return []
    scene_locations = {scene.location_id for scene in snapshot.plan.scenes}
    scene_participants = {participant for scene in snapshot.plan.scenes for participant in scene.participant_ids}
    item_states = {item.entity_id: item for item in snapshot.state.item_states}
    findings: list[ConsistencyFinding] = []
    for item in snapshot.items.items:
        state = item_states.get(item.id)
        if not state or item.name not in snapshot.audited_body:
            continue
        holder_ok = bool(state.holder_id and state.holder_id in scene_participants)
        location_ok = bool(state.location_id and state.location_id in scene_locations)
        if state.location_id and not location_ok and not holder_ok:
            findings.append(
                ConsistencyFinding(
                    id=f"cons_item_scene_location_{item.id}",
                    severity="medium",
                    type="continuity_issue",
                    description=f"正文提到了物品 {item.id}，但 current_state 显示它不在本章计划场景中。",
                    source=_chapter_file(snapshot),
                    quote=f"{item.name}: location_id={state.location_id}",
                    suggested_fix="通过 state_update 移动物品，调整场景地点，或修订正文。",
                )
            )
    return findings


def _check_state_update_old_values(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    if (
        not snapshot.state
        or not snapshot.proposal
        or snapshot.apply_log
        or _proposal_matches_accepted_commit(snapshot)
    ):
        return []
    character_ids = {item.id for item in snapshot.characters.characters} if snapshot.characters else set()
    item_ids = {item.id for item in snapshot.items.items} if snapshot.items else set()
    location_ids = {item.id for item in snapshot.locations.locations} if snapshot.locations else set()
    findings: list[ConsistencyFinding] = []
    source = _chapter_dir(snapshot) / "state_update_proposal.json"
    for change in snapshot.proposal.state_changes:
        comparison = compare_state_change_old_value(
            snapshot.state,
            change,
            character_ids=character_ids,
            item_ids=item_ids,
            location_ids=location_ids,
        )
        if not comparison.should_check:
            continue
        if not comparison.matches:
            findings.append(
                ConsistencyFinding(
                    id=f"cons_state_change_old_value_{change.id}",
                    severity="high",
                    type="state_conflict",
                    description=f"状态变更 {change.id} 的 old_value 已与 current_state 不一致。",
                    source=source,
                    quote=f"expected={change.old_value!r}; actual={comparison.actual!r}",
                    suggested_fix="应用前基于当前 state 重新生成 state update proposal。",
                )
            )
    return findings


def _proposal_matches_accepted_commit(snapshot: ConsistencySnapshot) -> bool:
    if snapshot.chapter_number is None:
        return False
    chapter_dir = _chapter_dir(snapshot)
    acceptance_path = chapter_dir / "acceptance.json"
    proposal_path = chapter_dir / "state_update_proposal.json"
    if not acceptance_path.is_file() or not proposal_path.is_file():
        return False
    try:
        acceptance = load_json_model(acceptance_path, AcceptanceCommit)
        return sha256_file(proposal_path) == acceptance.state_proposal.sha256
    except Exception:
        return False


def _check_timeline_order(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    if not snapshot.timeline:
        return []
    source = snapshot.root / "memory" / "state" / "timeline.json"
    findings: list[ConsistencyFinding] = []
    events = snapshot.timeline.events
    event_by_id = {event.id: event for event in events}
    previous_key: tuple[int, int, int] | None = None
    for event in events:
        narrative = event.narrative_position
        if narrative is None:
            continue
        key = _event_narrative_key(event)
        if previous_key and key < previous_key:
            severity: Severity = (
                "medium"
                if snapshot.chapter_number is None or narrative.chapter == snapshot.chapter_number
                else "low"
            )
            findings.append(
                ConsistencyFinding(
                    id=f"cons_timeline_order_{event.id}",
                    severity=severity,
                    type="timeline_conflict",
                    description="Timeline 事件未按 narrative_position 的 chapter、scene、sequence 排序。",
                    source=source,
                    quote=(
                        f"{event.id}: chapter={narrative.chapter}, "
                        f"scene={narrative.scene}, sequence={narrative.sequence}"
                    ),
                    suggested_fix="按 narrative_position 排序 timeline 事件，或修正事件的 narrative_position。",
                )
            )
            break
        previous_key = key
    for event in events:
        for cause_id in event.causes:
            if not _looks_like_id(cause_id):
                continue
            cause = event_by_id.get(cause_id)
            if not cause:
                findings.append(_timeline_missing_reference(source, event, cause_id, "cause"))
            elif _story_order_reversed(cause, event):
                findings.append(_timeline_reversed_reference(source, event, cause, "cause"))
        for effect_id in event.effects:
            if not _looks_like_id(effect_id):
                continue
            effect = event_by_id.get(effect_id)
            if not effect:
                findings.append(_timeline_missing_reference(source, event, effect_id, "effect"))
            elif _story_order_reversed(event, effect):
                findings.append(_timeline_reversed_reference(source, event, effect, "effect"))
        narrative = event.narrative_position
        if snapshot.chapter_number and narrative is not None and narrative.chapter == snapshot.chapter_number and snapshot.plan:
            scene_count = len(snapshot.plan.scenes)
            scene = narrative.scene
            if scene and scene > scene_count:
                findings.append(
                    ConsistencyFinding(
                        id=f"cons_timeline_scene_{event.id}",
                        severity="medium",
                        type="timeline_conflict",
                        description=f"Timeline 事件 {event.id} 的 scene 超出 ChapterPlan 场景范围。",
                        source=source,
                        quote=f"narrative_position.scene={scene}; plan_scene_count={scene_count}",
                        suggested_fix="修正 event.narrative_position.scene，或更新章节计划的场景列表。",
                    )
                )
    return findings


def _check_chapter_loop(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    findings: list[ConsistencyFinding] = []
    chapter_dir = _chapter_dir(snapshot)
    chapter_number = snapshot.chapter_number
    if not chapter_number:
        return findings
    if snapshot.plan and snapshot.plan.chapter_number != chapter_number:
        findings.append(
            ConsistencyFinding(
                id=f"cons_loop_plan_chapter_{chapter_number:03d}",
                severity="critical",
                type="continuity_issue",
                description="plan.json 的 chapter_number 与章节目录不一致。",
                source=chapter_dir / "plan.json",
                quote=f"chapter_number={snapshot.plan.chapter_number}",
                suggested_fix="把 plan 移到匹配的章节目录，或重新生成计划。",
            )
        )
    for name, metadata in (("draft.md", snapshot.draft_metadata), ("polished.md", snapshot.polished_metadata)):
        if not metadata:
            continue
        front_chapter = metadata.get("chapter_number")
        if front_chapter not in {None, chapter_number}:
            findings.append(
                ConsistencyFinding(
                    id=f"cons_loop_{name.replace('.', '_')}_chapter_{chapter_number:03d}",
                    severity="critical",
                    type="continuity_issue",
                    description=f"{name} 的 front matter chapter_number 与章节目录不一致。",
                    source=chapter_dir / name,
                    quote=f"chapter_number={front_chapter}",
                    suggested_fix=f"重新生成 {name}，或修正其 front matter。",
                )
            )
    if snapshot.proposal and snapshot.proposal.chapter_number != chapter_number:
        findings.append(
            ConsistencyFinding(
                id=f"cons_loop_state_proposal_{chapter_number:03d}",
                severity="high",
                type="state_conflict",
                description="state_update_proposal.json 的 chapter_number 与章节目录不一致。",
                source=chapter_dir / "state_update_proposal.json",
                quote=f"chapter_number={snapshot.proposal.chapter_number}",
                suggested_fix="为本章重新生成 state update proposal。",
            )
        )
    if snapshot.apply_log and snapshot.apply_log.chapter_number != chapter_number:
        findings.append(
            ConsistencyFinding(
                id=f"cons_loop_state_apply_{chapter_number:03d}",
                severity="high",
                type="state_conflict",
                description="state_update_apply_log.json 的 chapter_number 与章节目录不一致。",
                source=chapter_dir / "state_update_apply_log.json",
                quote=f"chapter_number={snapshot.apply_log.chapter_number}",
                suggested_fix="回滚后重新应用正确的 state update proposal。",
            )
        )
    if snapshot.metadata:
        findings.extend(_check_accepted_chapter_loop(snapshot))
    if snapshot.include_existing_audit:
        findings.extend(_check_existing_audit_loop(snapshot))
    return findings


def _check_accepted_chapter_loop(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    metadata = snapshot.metadata
    assert metadata is not None
    if metadata.status != "accepted":
        return []
    chapter_dir = _chapter_dir(snapshot)
    findings: list[ConsistencyFinding] = []
    audit_path = snapshot.root / metadata.audit_path if metadata.audit_path else chapter_dir / "audit.json"
    audit_data = _load_json_dict(audit_path)
    if not isinstance(audit_data, dict):
        findings.append(
            ConsistencyFinding(
                id=f"cons_accepted_audit_{metadata.chapter_number:03d}",
                severity="critical",
                type="continuity_issue",
                description="已认可章节必须有可读取的 audit 报告。",
                source=chapter_dir / "metadata.json",
                quote=f"audit_path={metadata.audit_path or 'audit.json'}",
                suggested_fix="重新生成 audit.json，然后重新运行 accept-chapter。",
            )
        )
    else:
        raw_issues = audit_data.get("issues", [])
        if not isinstance(raw_issues, list):
            raw_issues = []
        blocking_issues = [
            issue
            for issue in raw_issues
            if isinstance(issue, dict) and issue.get("severity") in {"medium", "high", "critical"}
        ]
        if audit_data.get("overall_status") == "blocked" or blocking_issues:
            findings.append(
                ConsistencyFinding(
                    id=f"cons_accepted_audit_{metadata.chapter_number:03d}",
                    severity="critical",
                    type="continuity_issue",
                    description="已认可章节不能保留 medium、high 或 critical 级别的 audit 问题。",
                    source=chapter_dir / "metadata.json",
                    quote=f"audit_path={metadata.audit_path or 'audit.json'}",
                    suggested_fix="解决阻断性 audit 问题，待 audit 通过后重新运行 accept-chapter。",
                )
            )
        elif audit_data.get("overall_status") != "passed":
            findings.append(
                ConsistencyFinding(
                    id=f"cons_accepted_audit_{metadata.chapter_number:03d}",
                    severity="medium",
                    type="continuity_issue",
                    description="已认可章节的 audit 仍有非阻断问题。",
                    source=chapter_dir / "metadata.json",
                    quote=f"overall_status={audit_data.get('overall_status')}",
                    suggested_fix="检查低/中级别 audit 问题，或修订后重新运行 audit。",
                )
            )
    acceptance_path = chapter_dir / "acceptance.json"
    accepted_path = chapter_dir / "accepted.md"
    acceptance = _load_optional_model(acceptance_path, AcceptanceCommit)
    acceptance_valid = bool(
        acceptance
        and accepted_path.is_file()
        and sha256_file(accepted_path) == acceptance.accepted_content_sha256
    )
    if not acceptance_valid:
        findings.append(
            ConsistencyFinding(
                id=f"cons_accepted_state_apply_{metadata.chapter_number:03d}",
                severity="critical",
                type="state_conflict",
                description="已认可章节必须有有效的 AcceptanceCommit 和 accepted.md hash。",
                source=chapter_dir / "metadata.json",
                quote="acceptance.json/accepted.md missing or stale",
                suggested_fix="通过 Session 或 RevisionSession transaction 重新接受章节。",
            )
        )
    return findings


def _check_existing_audit_loop(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    chapter_dir = _chapter_dir(snapshot)
    audit_data = _load_json_dict(chapter_dir / "audit.json")
    if not audit_data:
        return []
    chapter_number = snapshot.chapter_number or 0
    findings: list[ConsistencyFinding] = []
    if audit_data.get("chapter_number") != chapter_number:
        findings.append(
            ConsistencyFinding(
                id=f"cons_loop_audit_chapter_{chapter_number:03d}",
                severity="critical",
                type="continuity_issue",
                description="audit.json 的 chapter_number 与章节目录不一致。",
                source=chapter_dir / "audit.json",
                quote=f"chapter_number={audit_data.get('chapter_number')}",
                suggested_fix="重新生成本章 audit。",
            )
        )
    audited_file = audit_data.get("audited_file")
    if audited_file not in {None, "draft.md", "polished.md"}:
        findings.append(
            ConsistencyFinding(
                id=f"cons_loop_audit_file_{chapter_number:03d}",
                severity="high",
                type="continuity_issue",
                description="audit.json 的 audited_file 必须是 draft.md 或 polished.md。",
                source=chapter_dir / "audit.json",
                quote=str(audited_file),
                suggested_fix="使用受支持的 audited_file 重新生成 audit。",
            )
        )
    elif audited_file and not (chapter_dir / str(audited_file)).exists():
        findings.append(
            ConsistencyFinding(
                id=f"cons_loop_audit_missing_file_{chapter_number:03d}",
                severity="high",
                type="continuity_issue",
                description="audit.json 引用了不存在的 audited_file。",
                source=chapter_dir / "audit.json",
                quote=str(audited_file),
                suggested_fix="重新生成缺失文件，或改为审核一个已存在的文件。",
            )
        )
    return findings


def _check_body_workspace_language(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    if not snapshot.audited_body:
        return []
    markers = ("根据设定", "本章目标", "隐藏真相", "润色如下", "以下是润色", "ChapterPlan", "AuditReport")
    hits = [marker for marker in markers if marker in snapshot.audited_body]
    if not hits:
        return []
    return [
        ConsistencyFinding(
            id=f"cons_workspace_language_{snapshot.chapter_number or 0:03d}",
            severity="medium",
            type="style_mismatch",
            description="面向读者的章节正文包含工作区或 Agent 流程用语。",
            source=_chapter_file(snapshot),
            quote=", ".join(hits),
            suggested_fix="从 draft 或 polished 正文中移除 Agent/流程用语。",
        )
    ]


def _check_unplanned_character_mentions(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    if not snapshot.characters or not snapshot.plan or not snapshot.audited_body:
        return []
    planned = {participant for scene in snapshot.plan.scenes for participant in scene.participant_ids}
    findings: list[ConsistencyFinding] = []
    for character in snapshot.characters.characters:
        if character.id in planned or character.name not in snapshot.audited_body:
            continue
        findings.append(
            ConsistencyFinding(
                id=f"cons_unplanned_character_{snapshot.chapter_number or 0:03d}_{character.id}",
                severity="low",
                type="continuity_issue",
                description=f"角色 {character.id} 在正文中出现，但未列入 ChapterPlan 的 participants。",
                source=_chapter_file(snapshot),
                quote=character.name,
                suggested_fix="把该角色加入相关计划场景，或移除误写的出场。",
            )
        )
    return findings


def _check_character_gendered_references(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    if not snapshot.characters or not snapshot.audited_body:
        return []
    body = snapshot.audited_body
    character_names = [item.name for item in snapshot.characters.characters if item.name]
    findings: list[ConsistencyFinding] = []
    for character in snapshot.characters.characters:
        gender = infer_character_gender(character)
        if gender is None or character.name not in body:
            continue
        quote = _gender_conflict_quote(body, character.name, gender, character_names)
        if quote is None:
            continue
        findings.append(
            ConsistencyFinding(
                id=f"cons_gender_reference_{snapshot.chapter_number or 0:03d}_{character.id}",
                severity="medium",
                type="continuity_issue",
                description=f"角色 {character.id} 的正文性别指代疑似与 canon 不一致。",
                source=_chapter_file(snapshot),
                quote=quote[:160],
                suggested_fix="根据 canon 中的角色性别修订该角色附近的代词和性别化称谓。",
            )
        )
    return findings


def _gender_conflict_quote(
    body: str,
    character_name: str,
    gender: CharacterGenderValue,
    character_names: list[str],
) -> str | None:
    conflict_patterns = (
        (r"她(?:面前|背后|目光|指尖|心中|额前|膝上|的话|的)", r"两个女子")
        if gender == "男"
        else (r"他(?:面前|背后|目光|指尖|心中|额前|膝上|的话|的)", r"两个男子")
    )
    for match in re.finditer(re.escape(character_name), body):
        window = body[match.end() : match.end() + 120]
        group_match = re.search(conflict_patterns[1], window)
        if group_match:
            return body[match.start() : match.end() + group_match.end() + 30]
        pronoun_match = re.search(conflict_patterns[0], window)
        if not pronoun_match:
            continue
        before_pronoun = window[: pronoun_match.start()]
        if _contains_other_character_name(before_pronoun, character_name, character_names):
            continue
        return body[match.start() : match.end() + pronoun_match.end() + 30]
    return None


def _contains_other_character_name(text: str, character_name: str, character_names: list[str]) -> bool:
    return any(name != character_name and name in text for name in character_names)


def _check_reader_visible_hidden_truth_leaks(root: Path) -> list[ConsistencyFinding]:
    truths = _load_optional_model(root / "memory" / "canon" / "hidden_truths.json", HiddenTruthsFile)
    if not truths:
        return []
    findings: list[ConsistencyFinding] = []
    visible_sources: list[tuple[str, Path, str]] = []
    for path, model_type, attr in (
        (root / "memory" / "canon" / "characters.json", CharactersFile, "characters"),
        (root / "memory" / "canon" / "locations.json", LocationsFile, "locations"),
        (root / "memory" / "canon" / "items.json", ItemsFile, "items"),
    ):
        model = _load_optional_model(path, model_type)
        if not model:
            continue
        for item in getattr(model, attr):
            visible_sources.append((item.id, path, item.reader_visible_summary))
    for truth in truths.hidden_truths:
        for entity_id, path, text in visible_sources:
            compact_text = _compact_text(text)
            for fragment in _hidden_fragments(truth):
                if _fragment_in_text(fragment, text, compact_text):
                    findings.append(
                        ConsistencyFinding(
                            id=f"cons_reader_visible_hidden_{truth.id}_{entity_id}",
                            severity="critical",
                            type="premature_reveal",
                            description=f"实体 {entity_id} 的 reader_visible_summary 包含隐藏真相 {truth.id}。",
                            source=path,
                            quote=fragment[:160],
                            suggested_fix="把隐藏信息仅保存在 private_author_notes 或 hidden_truths.json 中。",
                        )
                    )
                    break
    foreshadowing = _load_optional_model(root / "memory" / "canon" / "foreshadowing.json", ForeshadowingFile)
    if foreshadowing:
        for thread in foreshadowing.foreshadowing_threads:
            if (
                thread.reader_visible
                and thread.hidden_truth
                and _fragment_in_text(thread.hidden_truth, thread.description, _compact_text(thread.description))
            ):
                findings.append(
                    ConsistencyFinding(
                            id=f"cons_reader_visible_foreshadow_{thread.id}",
                            severity="high",
                            type="premature_reveal",
                            description=f"伏笔线 {thread.id} 的读者可见描述泄露了 hidden_truth 文本。",
                            source=root / "memory" / "canon" / "foreshadowing.json",
                            quote=thread.hidden_truth[:160],
                            suggested_fix="将 reader_visible 线索文本与 hidden_truth 分开保存。",
                        )
                    )
    return findings


def _timeline_missing_reference(source: Path, event: TimelineEvent, ref_id: str, ref_type: str) -> ConsistencyFinding:
    return ConsistencyFinding(
        id=f"cons_timeline_missing_{event.id}_{ref_type}_{ref_id}",
        severity="medium",
        type="timeline_conflict",
        description=f"Timeline 事件 {event.id} 的 {ref_type} 引用了不存在的事件：{ref_id}",
        source=source,
        quote=f"{ref_type}={ref_id}",
        suggested_fix="创建被引用的 timeline 事件，或移除失效引用。",
    )


def _timeline_reversed_reference(
    source: Path,
    event: TimelineEvent,
    referenced: TimelineEvent,
    ref_type: str,
) -> ConsistencyFinding:
    return ConsistencyFinding(
        id=f"cons_timeline_reversed_{event.id}_{ref_type}_{referenced.id}",
        severity="high",
        type="timeline_conflict",
        description=f"Timeline 事件 {event.id} 与 {referenced.id} 的故事世界 {ref_type} 关系顺序反了。",
        source=source,
        quote=(
            f"{event.id}=thread {event.story_position.thread_id}/order {event.story_position.order}; "
            f"{referenced.id}=thread {referenced.story_position.thread_id}/order {referenced.story_position.order}"
        ),
        suggested_fix="修正 story_position.order 或 causes/effects，确保故事世界中的原因早于结果发生。",
    )


def _event_narrative_key(event: TimelineEvent) -> tuple[int, int, int]:
    narrative = event.narrative_position
    if narrative is None:
        raise ValueError(f"timeline event {event.id} has no narrative_position")
    return (narrative.chapter, narrative.scene or 0, narrative.sequence or 0)


def _story_order_reversed(before: TimelineEvent, after: TimelineEvent) -> bool:
    before_order = before.story_position.order
    after_order = after.story_position.order
    if before_order is None or after_order is None:
        return False
    if before.story_position.thread_id != after.story_position.thread_id:
        return False
    return before_order > after_order


def _chapter_dir(snapshot: ConsistencySnapshot) -> Path:
    return snapshot.root / "memory" / "chapters" / f"{snapshot.chapter_number or 0:03d}"


def _chapter_file(snapshot: ConsistencySnapshot) -> Path:
    return _chapter_dir(snapshot) / (snapshot.audited_file or "polished.md")


def _truth_revealed_by_chapter(truth: HiddenTruth, chapter_number: int) -> bool:
    return bool(truth.planned_reveal and truth.planned_reveal.chapter <= chapter_number)


def _match_hidden_truth(value: str, truths: Iterable[HiddenTruth]) -> HiddenTruth | None:
    compact_value = _compact_text(value)
    for truth in truths:
        if value == truth.id or truth.id in value:
            return truth
        if any(_fragment_in_text(fragment, value, compact_value) for fragment in _hidden_fragments(truth)):
            return truth
    return None


def _hidden_fragments(truth: HiddenTruth) -> list[str]:
    fragments = [truth.title.strip(), truth.description.strip()]
    fragments.extend(part.strip() for part in re.split(r"[。！？!?；;\n]", truth.description) if part.strip())
    return [fragment for fragment in fragments if len(_compact_text(fragment)) >= 6]


def _fragment_in_text(fragment: str, text: str, compact_text: str) -> bool:
    if fragment and fragment in text:
        return True
    compact_fragment = _compact_text(fragment)
    return bool(compact_fragment and len(compact_fragment) >= 6 and compact_fragment in compact_text)


def _compact_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value).lower()


def _state_value_lookup(state: EntityState) -> dict[tuple[str, str], object]:
    values: dict[tuple[str, str], object] = {}
    values[("story_position", "latest_chapter")] = state.story_position.latest_chapter
    values[("story_position", "in_story_time")] = state.story_position.in_story_time
    values[("story_position", "summary")] = state.story_position.summary
    for character in state.character_states:
        for field_name in ("location_id", "health", "mental_state", "knowledge", "goals", "possessions", "last_updated_chapter"):
            values[(character.entity_id, field_name)] = getattr(character, field_name)
    for item in state.item_states:
        for field_name in ("holder_id", "location_id", "condition", "known_properties", "last_updated_chapter"):
            values[(item.entity_id, field_name)] = getattr(item, field_name)
    for location in state.location_states:
        for field_name in ("accessibility", "condition", "active_events", "last_updated_chapter"):
            values[(location.entity_id, field_name)] = getattr(location, field_name)
    return values


def _read_markdown_metadata(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    data = yaml.safe_load(text[4:end])
    return data if isinstance(data, dict) else {}


def _read_markdown_body(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return text.strip()


def _load_json_dict(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = load_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_optional_model(path: Path, model_type: type[ModelT]) -> ModelT | None:
    if not path.exists():
        return None
    try:
        return load_json_model(path, model_type)
    except Exception:
        return None


def _looks_like_id(value: str) -> bool:
    return "_" in value and value == value.lower()


def _dedupe_findings(findings: list[ConsistencyFinding]) -> list[ConsistencyFinding]:
    seen: set[str] = set()
    result: list[ConsistencyFinding] = []
    for finding in findings:
        if finding.id in seen:
            continue
        seen.add(finding.id)
        result.append(finding)
    return result


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
