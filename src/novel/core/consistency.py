from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Iterable, Literal, TypeVar

from pydantic import BaseModel
import yaml

from novel.core.io import load_json, load_json_model
from novel.core.schemas import (
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
            description=self.description,
            evidence=[{"source": str(self.source), "quote": self.quote}],
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
            return "Deterministic consistency checks: no blocking issues found.\n"
        lines = ["Deterministic consistency checks:"]
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
    findings.extend(_check_reader_visible_hidden_truth_leaks(root))
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
                            f"Chapter appears to reveal hidden truth {truth.id} before its planned reveal."
                        ),
                        source=source,
                        quote=fragment[:160],
                        suggested_fix=(
                            "Remove or disguise this hidden truth from reader-facing text, "
                            "or explicitly move its planned reveal to this chapter."
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
                        description=f"Chapter appears to reveal foreshadowing hidden text for {thread.id}.",
                        source=source,
                        quote=hidden_text[:160],
                        suggested_fix="Keep only reader-visible clue text before the planned payoff chapter.",
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
                            f"Character {character.entity_id} knows hidden truth {matched.id} before planned reveal."
                        ),
                        source=source,
                        quote=value[:160],
                        suggested_fix=(
                            "Remove this knowledge from current_state until reveal, or update hidden_truth planned_reveal."
                        ),
                    )
                )
            elif _looks_like_id(value) and value not in truth_by_id:
                findings.append(
                    ConsistencyFinding(
                        id=f"cons_knowledge_unknown_{character.entity_id}_{index}",
                        severity="low",
                        type="state_conflict",
                        description=f"Character {character.entity_id} knowledge references an unknown id-like value.",
                        source=source,
                        quote=value[:160],
                        suggested_fix="Use stable canon/timeline ids for structured knowledge or rewrite as natural language.",
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
                    description=f"Item {item.entity_id} has both holder_id and location_id.",
                    source=source,
                    quote=f"holder_id={item.holder_id}; location_id={item.location_id}",
                    suggested_fix="Set either holder_id or location_id, not both.",
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
                        description=f"Item {item_id} appears in possessions of multiple characters.",
                        source=source,
                        quote=f"{previous}, {character.entity_id}",
                        suggested_fix="Keep the item in exactly one character possession list.",
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
                        description=f"Item {item_id} holder_id conflicts with character possessions.",
                        source=source,
                        quote=f"holder_id={holder}; possession_owner={character.entity_id}",
                        suggested_fix="Synchronize item_state.holder_id with character_state.possessions.",
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
                    description=f"Item {item.id} is mentioned, but current_state places it outside planned scenes.",
                    source=_chapter_file(snapshot),
                    quote=f"{item.name}: location_id={state.location_id}",
                    suggested_fix="Move the item through state_update, adjust the scene location, or revise the text.",
                )
            )
    return findings


def _check_state_update_old_values(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    if not snapshot.state or not snapshot.proposal or snapshot.apply_log:
        return []
    state_values = _state_value_lookup(snapshot.state)
    findings: list[ConsistencyFinding] = []
    source = _chapter_dir(snapshot) / "state_update_proposal.json"
    for change in snapshot.proposal.state_changes:
        if change.old_value is None:
            continue
        actual = state_values.get((change.entity_id, change.field))
        if actual != change.old_value:
            findings.append(
                ConsistencyFinding(
                    id=f"cons_state_change_old_value_{change.id}",
                    severity="high",
                    type="state_conflict",
                    description=f"State change {change.id} old_value no longer matches current_state.",
                    source=source,
                    quote=f"expected={change.old_value!r}; actual={actual!r}",
                    suggested_fix="Regenerate the state update proposal from the current state before applying it.",
                )
            )
    return findings


def _check_timeline_order(snapshot: ConsistencySnapshot) -> list[ConsistencyFinding]:
    if not snapshot.timeline:
        return []
    source = snapshot.root / "memory" / "state" / "timeline.json"
    findings: list[ConsistencyFinding] = []
    events = snapshot.timeline.events
    event_by_id = {event.id: event for event in events}
    previous_key: tuple[int, int, int] | None = None
    for event in events:
        key = _event_narrative_key(event)
        if previous_key and key < previous_key:
            severity: Severity = (
                "medium"
                if snapshot.chapter_number is None or event.narrative_position.chapter == snapshot.chapter_number
                else "low"
            )
            findings.append(
                ConsistencyFinding(
                    id=f"cons_timeline_order_{event.id}",
                    severity=severity,
                    type="timeline_conflict",
                    description="Timeline events are not ordered by narrative_position chapter, scene, and sequence.",
                    source=source,
                    quote=(
                        f"{event.id}: chapter={event.narrative_position.chapter}, "
                        f"scene={event.narrative_position.scene}, sequence={event.narrative_position.sequence}"
                    ),
                    suggested_fix="Sort timeline events by narrative_position or correct the event narrative position.",
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
        if snapshot.chapter_number and event.narrative_position.chapter == snapshot.chapter_number and snapshot.plan:
            scene_count = len(snapshot.plan.scenes)
            scene = event.narrative_position.scene
            if scene and scene > scene_count:
                findings.append(
                    ConsistencyFinding(
                        id=f"cons_timeline_scene_{event.id}",
                        severity="medium",
                        type="timeline_conflict",
                        description=f"Timeline event {event.id} scene is outside ChapterPlan scene range.",
                        source=source,
                        quote=f"narrative_position.scene={scene}; plan_scene_count={scene_count}",
                        suggested_fix="Correct event.narrative_position.scene or update the chapter plan scene list.",
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
                description="plan.json chapter_number does not match chapter directory.",
                source=chapter_dir / "plan.json",
                quote=f"chapter_number={snapshot.plan.chapter_number}",
                suggested_fix="Move the plan to the matching chapter directory or regenerate it.",
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
                    description=f"{name} front matter chapter_number does not match chapter directory.",
                    source=chapter_dir / name,
                    quote=f"chapter_number={front_chapter}",
                    suggested_fix=f"Regenerate {name} or correct its front matter.",
                )
            )
    if snapshot.proposal and snapshot.proposal.chapter_number != chapter_number:
        findings.append(
            ConsistencyFinding(
                id=f"cons_loop_state_proposal_{chapter_number:03d}",
                severity="high",
                type="state_conflict",
                description="state_update_proposal.json chapter_number does not match chapter directory.",
                source=chapter_dir / "state_update_proposal.json",
                quote=f"chapter_number={snapshot.proposal.chapter_number}",
                suggested_fix="Regenerate the state update proposal for this chapter.",
            )
        )
    if snapshot.apply_log and snapshot.apply_log.chapter_number != chapter_number:
        findings.append(
            ConsistencyFinding(
                id=f"cons_loop_state_apply_{chapter_number:03d}",
                severity="high",
                type="state_conflict",
                description="state_update_apply_log.json chapter_number does not match chapter directory.",
                source=chapter_dir / "state_update_apply_log.json",
                quote=f"chapter_number={snapshot.apply_log.chapter_number}",
                suggested_fix="Rollback and re-apply the correct state update proposal.",
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
                description="Accepted chapter must have a readable audit report.",
                source=chapter_dir / "metadata.json",
                quote=f"audit_path={metadata.audit_path or 'audit.json'}",
                suggested_fix="Regenerate audit.json and rerun accept-chapter.",
            )
        )
    else:
        blocking_issues = [
            issue
            for issue in audit_data.get("issues", [])
            if isinstance(issue, dict) and issue.get("severity") in {"medium", "high", "critical"}
        ]
        if audit_data.get("overall_status") == "blocked" or blocking_issues:
            findings.append(
                ConsistencyFinding(
                    id=f"cons_accepted_audit_{metadata.chapter_number:03d}",
                    severity="critical",
                    type="continuity_issue",
                    description="Accepted chapter must not have medium, high, or critical audit issues.",
                    source=chapter_dir / "metadata.json",
                    quote=f"audit_path={metadata.audit_path or 'audit.json'}",
                    suggested_fix="Resolve blocking audit issues and rerun accept-chapter after audit passes.",
                )
            )
        elif audit_data.get("overall_status") != "passed":
            findings.append(
                ConsistencyFinding(
                    id=f"cons_accepted_audit_{metadata.chapter_number:03d}",
                    severity="medium",
                    type="continuity_issue",
                    description="Accepted chapter audit has non-blocking issues.",
                    source=chapter_dir / "metadata.json",
                    quote=f"overall_status={audit_data.get('overall_status')}",
                    suggested_fix="Review low/medium audit issues when convenient, or rerun audit after revision.",
                )
            )
    apply_path = snapshot.root / metadata.state_update_apply_log_path if metadata.state_update_apply_log_path else chapter_dir / "state_update_apply_log.json"
    apply_log = _load_optional_model(apply_path, StateUpdateApplyLog)
    if not apply_log or apply_log.status != "applied":
        findings.append(
            ConsistencyFinding(
                id=f"cons_accepted_state_apply_{metadata.chapter_number:03d}",
                severity="critical",
                type="state_conflict",
                description="Accepted chapter must have an applied state update log.",
                source=chapter_dir / "metadata.json",
                quote=f"state_update_apply_log_path={metadata.state_update_apply_log_path or 'state_update_apply_log.json'}",
                suggested_fix="Run propose-state-update/apply-state-update before accepting the chapter.",
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
                description="audit.json chapter_number does not match chapter directory.",
                source=chapter_dir / "audit.json",
                quote=f"chapter_number={audit_data.get('chapter_number')}",
                suggested_fix="Regenerate the audit for this chapter.",
            )
        )
    audited_file = audit_data.get("audited_file")
    if audited_file not in {None, "draft.md", "polished.md"}:
        findings.append(
            ConsistencyFinding(
                id=f"cons_loop_audit_file_{chapter_number:03d}",
                severity="high",
                type="continuity_issue",
                description="audit.json audited_file must be draft.md or polished.md.",
                source=chapter_dir / "audit.json",
                quote=str(audited_file),
                suggested_fix="Regenerate the audit with a supported audited_file.",
            )
        )
    elif audited_file and not (chapter_dir / str(audited_file)).exists():
        findings.append(
            ConsistencyFinding(
                id=f"cons_loop_audit_missing_file_{chapter_number:03d}",
                severity="high",
                type="continuity_issue",
                description="audit.json references a missing audited_file.",
                source=chapter_dir / "audit.json",
                quote=str(audited_file),
                suggested_fix="Regenerate the missing file or rerun audit against an existing file.",
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
            description="Reader-facing chapter text contains workspace or agent-process language.",
            source=_chapter_file(snapshot),
            quote=", ".join(hits),
            suggested_fix="Remove agent/process wording from draft or polished prose.",
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
                description=f"Character {character.id} is mentioned but not listed in ChapterPlan participants.",
                source=_chapter_file(snapshot),
                quote=character.name,
                suggested_fix="Add the character to the relevant plan scene or remove the accidental mention.",
            )
        )
    return findings


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
                            description=f"Reader-visible summary for {entity_id} contains hidden truth {truth.id}.",
                            source=path,
                            quote=fragment[:160],
                            suggested_fix="Move hidden information into private_author_notes or hidden_truths.json only.",
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
                        description=f"Foreshadowing thread {thread.id} reader-visible description leaks hidden_truth text.",
                        source=root / "memory" / "canon" / "foreshadowing.json",
                        quote=thread.hidden_truth[:160],
                        suggested_fix="Keep reader_visible clue text separate from hidden_truth.",
                    )
                )
    return findings


def _timeline_missing_reference(source: Path, event: TimelineEvent, ref_id: str, ref_type: str) -> ConsistencyFinding:
    return ConsistencyFinding(
        id=f"cons_timeline_missing_{event.id}_{ref_type}_{ref_id}",
        severity="medium",
        type="timeline_conflict",
        description=f"Timeline event {event.id} references missing {ref_type} event {ref_id}.",
        source=source,
        quote=f"{ref_type}={ref_id}",
        suggested_fix="Create the referenced timeline event or remove the stale reference.",
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
        description=f"Timeline event {event.id} has a reversed story-world {ref_type} relationship with {referenced.id}.",
        source=source,
        quote=(
            f"{event.id}=thread {event.story_position.thread_id}/order {event.story_position.order}; "
            f"{referenced.id}=thread {referenced.story_position.thread_id}/order {referenced.story_position.order}"
        ),
        suggested_fix="Correct story_position.order or causes/effects so story-world causes happen before effects.",
    )


def _event_narrative_key(event: TimelineEvent) -> tuple[int, int, int]:
    narrative = event.narrative_position
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
