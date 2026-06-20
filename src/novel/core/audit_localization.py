from __future__ import annotations

from collections.abc import Callable
import re

from novel.core.schemas import AuditIssue, AuditReport, SessionRewriteIssue


CANON_REFERENCE_SUGGESTED_FIX = "检查该 canon 关联关系，必要时补齐缺失 ID，或移除已经失效的引用。"


_DESCRIPTION_PATTERNS: tuple[tuple[str, Callable[[re.Match[str]], str]], ...] = (
    (
        r"hidden truth (?P<truth>\S+) related_entity_ids references missing entity: (?P<entity>\S+)",
        lambda m: f"隐藏真相 {m['truth']} 的 related_entity_ids 引用了不存在的实体：{m['entity']}",
    ),
    (
        r"hidden truth (?P<truth>\S+) foreshadowing_ids references missing thread: (?P<thread>\S+)",
        lambda m: f"隐藏真相 {m['truth']} 的 foreshadowing_ids 引用了不存在的伏笔线：{m['thread']}",
    ),
    (
        r"foreshadowing thread (?P<thread>\S+) hidden_truth_id references missing hidden truth: (?P<truth>\S+)",
        lambda m: f"伏笔线 {m['thread']} 的 hidden_truth_id 引用了不存在的隐藏真相：{m['truth']}",
    ),
    (
        r"foreshadowing thread (?P<thread>\S+) related_entity_ids references missing entity: (?P<entity>\S+)",
        lambda m: f"伏笔线 {m['thread']} 的 related_entity_ids 引用了不存在的实体：{m['entity']}",
    ),
    (
        r"Chapter appears to reveal hidden truth (?P<truth>\S+) before its planned reveal\.",
        lambda m: f"本章疑似在计划揭示前暴露了隐藏真相 {m['truth']}。",
    ),
    (
        r"Chapter appears to reveal foreshadowing hidden text for (?P<thread>\S+)\.",
        lambda m: f"本章疑似提前暴露了伏笔线 {m['thread']} 的隐藏信息。",
    ),
    (
        r"Character (?P<character>\S+) knows hidden truth (?P<truth>\S+) before planned reveal\.",
        lambda m: f"角色 {m['character']} 在计划揭示前已经知道隐藏真相 {m['truth']}。",
    ),
    (
        r"Character (?P<character>\S+) knowledge references an unknown id-like value\.",
        lambda m: f"角色 {m['character']} 的 knowledge 引用了未知的疑似 ID 值。",
    ),
    (
        r"Item (?P<item>\S+) has both holder_id and location_id\.",
        lambda m: f"物品 {m['item']} 同时设置了 holder_id 和 location_id。",
    ),
    (
        r"Item (?P<item>\S+) appears in possessions of multiple characters\.",
        lambda m: f"物品 {m['item']} 同时出现在多个角色的 possessions 中。",
    ),
    (
        r"Item (?P<item>\S+) holder_id conflicts with character possessions\.",
        lambda m: f"物品 {m['item']} 的 holder_id 与角色 possessions 不一致。",
    ),
    (
        r"Item (?P<item>\S+) is mentioned, but current_state places it outside planned scenes\.",
        lambda m: f"正文提到了物品 {m['item']}，但 current_state 显示它不在本章计划场景中。",
    ),
    (
        r"State change (?P<change>\S+) old_value no longer matches current_state\.",
        lambda m: f"状态变更 {m['change']} 的 old_value 已与 current_state 不一致。",
    ),
    (
        r"Timeline events are not ordered by narrative_position chapter, scene, and sequence\.",
        lambda m: "Timeline 事件未按 narrative_position 的 chapter、scene、sequence 排序。",
    ),
    (
        r"Timeline event (?P<event>\S+) scene is outside ChapterPlan scene range\.",
        lambda m: f"Timeline 事件 {m['event']} 的 scene 超出 ChapterPlan 场景范围。",
    ),
    (
        r"Timeline event (?P<event>\S+) references missing (?P<ref_type>cause|effect) event (?P<ref>\S+)\.",
        lambda m: f"Timeline 事件 {m['event']} 的 {m['ref_type']} 引用了不存在的事件：{m['ref']}",
    ),
    (
        r"Timeline event (?P<event>\S+) has a reversed story-world (?P<ref_type>cause|effect) relationship with (?P<ref>\S+)\.",
        lambda m: f"Timeline 事件 {m['event']} 与 {m['ref']} 的故事世界 {m['ref_type']} 关系顺序反了。",
    ),
    (
        r"plan\.json chapter_number does not match chapter directory\.",
        lambda m: "plan.json 的 chapter_number 与章节目录不一致。",
    ),
    (
        r"(?P<name>draft\.md|polished\.md) front matter chapter_number does not match chapter directory\.",
        lambda m: f"{m['name']} 的 front matter chapter_number 与章节目录不一致。",
    ),
    (
        r"state_update_proposal\.json chapter_number does not match chapter directory\.",
        lambda m: "state_update_proposal.json 的 chapter_number 与章节目录不一致。",
    ),
    (
        r"state_update_apply_log\.json chapter_number does not match chapter directory\.",
        lambda m: "state_update_apply_log.json 的 chapter_number 与章节目录不一致。",
    ),
    (
        r"Accepted chapter must have a readable audit report\.",
        lambda m: "已认可章节必须有可读取的 audit 报告。",
    ),
    (
        r"Accepted chapter must not have medium, high, or critical audit issues\.",
        lambda m: "已认可章节不能保留 medium、high 或 critical 级别的 audit 问题。",
    ),
    (
        r"Accepted chapter audit has non-blocking issues\.",
        lambda m: "已认可章节的 audit 仍有非阻断问题。",
    ),
    (
        r"Accepted chapter must have an applied state update log\.",
        lambda m: "已认可章节必须有已应用的 state update log。",
    ),
    (
        r"audit\.json chapter_number does not match chapter directory\.",
        lambda m: "audit.json 的 chapter_number 与章节目录不一致。",
    ),
    (
        r"audit\.json audited_file must be draft\.md or polished\.md\.",
        lambda m: "audit.json 的 audited_file 必须是 draft.md 或 polished.md。",
    ),
    (
        r"audit\.json references a missing audited_file\.",
        lambda m: "audit.json 引用了不存在的 audited_file。",
    ),
    (
        r"Reader-facing chapter text contains workspace or agent-process language\.",
        lambda m: "面向读者的章节正文包含工作区或 Agent 流程用语。",
    ),
    (
        r"Character (?P<character>\S+) is mentioned but not listed in ChapterPlan participants\.",
        lambda m: f"角色 {m['character']} 在正文中出现，但未列入 ChapterPlan 的 participants。",
    ),
    (
        r"Reader-visible summary for (?P<entity>\S+) contains hidden truth (?P<truth>\S+)\.",
        lambda m: f"实体 {m['entity']} 的 reader_visible_summary 包含隐藏真相 {m['truth']}。",
    ),
    (
        r"Foreshadowing thread (?P<thread>\S+) reader-visible description leaks hidden_truth text\.",
        lambda m: f"伏笔线 {m['thread']} 的读者可见描述泄露了 hidden_truth 文本。",
    ),
    (
        r"Provider returned chapter_number (?P<actual>\S+), expected (?P<expected>\S+)\.",
        lambda m: f"Provider 返回的 chapter_number 是 {m['actual']}，但本次请求的是第 {m['expected']} 章。",
    ),
    (
        r"Provider returned audited_file (?P<actual>\S+), expected (?P<expected>\S+)\.",
        lambda m: f"Provider 返回的 audited_file 是 {m['actual']}，但本次请求的是 {m['expected']}。",
    ),
    (
        r"plan\.json chapter_number (?P<actual>\S+) does not match requested chapter (?P<expected>\S+)\.",
        lambda m: f"plan.json 的 chapter_number 是 {m['actual']}，与本次请求的第 {m['expected']} 章不一致。",
    ),
    (
        r"(?P<name>draft\.md|polished\.md) front matter chapter_number (?P<actual>.+) does not match requested chapter (?P<expected>\S+)\.",
        lambda m: f"{m['name']} front matter 中的 chapter_number 是 {m['actual']}，与本次请求的第 {m['expected']} 章不一致。",
    ),
    (
        r"(?P<name>draft\.md|polished\.md) does not contain obvious keywords from plan title/goal/summary\.",
        lambda m: f"{m['name']} 未包含 plan 标题、目标或摘要中的明显关键词。",
    ),
    (
        r"current_state\.json cannot be validated as EntityState: (?P<error>.+)",
        lambda m: f"current_state.json 无法通过 EntityState schema 校验：{m['error']}",
    ),
    (
        r"timeline\.json cannot be validated as TimelineFile: (?P<error>.+)",
        lambda m: f"timeline.json 无法通过 TimelineFile schema 校验：{m['error']}",
    ),
)


_SUGGESTED_FIXES = {
    "Review the referenced canon relationship and update missing IDs if needed.": CANON_REFERENCE_SUGGESTED_FIX,
    "Fix canon validation errors before accepting this chapter.": "先修复 canon 校验错误，再认可本章。",
    "Regenerate the audit with the requested chapter number.": "用本次请求的章节编号重新生成 audit。",
    "Use the requested audited_file value in audit.json.": "在 audit.json 中使用本次请求的 audited_file 值。",
    "Update plan.json or audit the matching chapter directory.": "修正 plan.json，或改为审核与该 plan 匹配的章节目录。",
    "Regenerate or correct the chapter front matter before export.": "导出前重新生成章节文件，或修正章节 front matter。",
    "Review whether the chapter drifted away from plan.json; revise or update the plan if intentional.": (
        "检查章节是否偏离 plan.json；如非有意偏离，请修订正文；如有意调整，请更新计划。"
    ),
    "Fix memory/state/current_state.json before accepting this chapter.": "认可本章前先修复 memory/state/current_state.json。",
    "Fix memory/state/timeline.json before accepting this chapter.": "认可本章前先修复 memory/state/timeline.json。",
    "Remove or disguise this hidden truth from reader-facing text, or explicitly move its planned reveal to this chapter.": (
        "从读者可见正文中移除或弱化该隐藏真相，或明确把计划揭示章节改到本章。"
    ),
    "Keep only reader-visible clue text before the planned payoff chapter.": "在计划回收章节前，只保留读者可见的线索文本。",
    "Remove this knowledge from current_state until reveal, or update hidden_truth planned_reveal.": (
        "在揭示前从 current_state 移除该知识，或调整 hidden_truth 的 planned_reveal。"
    ),
    "Use stable canon/timeline ids for structured knowledge or rewrite as natural language.": (
        "结构化 knowledge 请使用稳定的 canon/timeline ID，或改写为自然语言。"
    ),
    "Set either holder_id or location_id, not both.": "只保留 holder_id 或 location_id 其中一个。",
    "Keep the item in exactly one character possession list.": "确保该物品只出现在一个角色的 possessions 中。",
    "Synchronize item_state.holder_id with character_state.possessions.": (
        "同步 item_state.holder_id 与 character_state.possessions。"
    ),
    "Move the item through state_update, adjust the scene location, or revise the text.": (
        "通过 state_update 移动物品，调整场景地点，或修订正文。"
    ),
    "Regenerate the state update proposal from the current state before applying it.": (
        "应用前基于当前 state 重新生成 state update proposal。"
    ),
    "Sort timeline events by narrative_position or correct the event narrative position.": (
        "按 narrative_position 排序 timeline 事件，或修正事件的 narrative_position。"
    ),
    "Correct event.narrative_position.scene or update the chapter plan scene list.": (
        "修正 event.narrative_position.scene，或更新章节计划的场景列表。"
    ),
    "Move the plan to the matching chapter directory or regenerate it.": "把 plan 移到匹配的章节目录，或重新生成计划。",
    "Regenerate draft.md or correct its front matter.": "重新生成 draft.md，或修正其 front matter。",
    "Regenerate polished.md or correct its front matter.": "重新生成 polished.md，或修正其 front matter。",
    "Regenerate the state update proposal for this chapter.": "为本章重新生成 state update proposal。",
    "Rollback and re-apply the correct state update proposal.": "回滚后重新应用正确的 state update proposal。",
    "Regenerate audit.json and rerun accept-chapter.": "重新生成 audit.json，然后重新运行 accept-chapter。",
    "Resolve blocking audit issues and rerun accept-chapter after audit passes.": (
        "解决阻断性 audit 问题，待 audit 通过后重新运行 accept-chapter。"
    ),
    "Review low/medium audit issues when convenient, or rerun audit after revision.": (
        "检查低/中级别 audit 问题，或修订后重新运行 audit。"
    ),
    "Run propose-state-update/apply-state-update before accepting the chapter.": (
        "认可章节前先运行 propose-state-update/apply-state-update。"
    ),
    "Regenerate the audit for this chapter.": "重新生成本章 audit。",
    "Regenerate the audit with a supported audited_file.": "使用受支持的 audited_file 重新生成 audit。",
    "Regenerate the missing file or rerun audit against an existing file.": (
        "重新生成缺失文件，或改为审核一个已存在的文件。"
    ),
    "Remove agent/process wording from draft or polished prose.": "从 draft 或 polished 正文中移除 Agent/流程用语。",
    "Add the character to the relevant plan scene or remove the accidental mention.": (
        "把该角色加入相关计划场景，或移除误写的出场。"
    ),
    "Move hidden information into private_author_notes or hidden_truths.json only.": (
        "把隐藏信息仅保存在 private_author_notes 或 hidden_truths.json 中。"
    ),
    "Keep reader_visible clue text separate from hidden_truth.": "将 reader_visible 线索文本与 hidden_truth 分开保存。",
    "Create the referenced timeline event or remove the stale reference.": "创建被引用的 timeline 事件，或移除失效引用。",
    "Correct story_position.order or causes/effects so story-world causes happen before effects.": (
        "修正 story_position.order 或 causes/effects，确保故事世界中的原因早于结果发生。"
    ),
    "Change status to needs_revision.": "将审核状态改为 needs_revision。",
}


_SUMMARY_FIXES = {
    "Mock audit found no blocking consistency issues.": "Mock audit 未发现阻断性一致性问题。",
    "Invalid mock report.": "Mock 审核报告状态不合法。",
}


def localize_audit_report_for_author(report: AuditReport) -> AuditReport:
    return report.model_copy(
        update={
            "summary": localize_audit_summary(report.summary),
            "issues": [localize_audit_issue_for_author(issue) for issue in report.issues],
        }
    )


def localize_audit_issue_for_author(issue: AuditIssue) -> AuditIssue:
    return issue.model_copy(
        update={
            "description": localize_audit_description(issue.description),
            "suggested_fix": localize_audit_suggested_fix(issue.suggested_fix),
        }
    )


def localize_session_rewrite_issue_for_author(issue: SessionRewriteIssue) -> SessionRewriteIssue:
    return issue.model_copy(
        update={
            "description": localize_audit_description(issue.description),
            "suggested_fix": localize_audit_suggested_fix(issue.suggested_fix),
        }
    )


def localize_audit_summary(text: str) -> str:
    match = re.fullmatch(r"Deterministic pre-checks found (?P<count>\d+) issue\(s\)\. (?P<rest>.*)", text)
    if match:
        rest = localize_audit_summary(match["rest"])
        return f"程序预检发现 {match['count']} 个问题。{rest}"
    return _SUMMARY_FIXES.get(text, text)


def localize_canon_validation_message(text: str) -> str:
    localized = localize_audit_description(text)
    if localized != text:
        return localized
    return f"Canon 校验提示：{text}"


def localize_audit_description(text: str) -> str:
    for pattern, replacement in _DESCRIPTION_PATTERNS:
        match = re.fullmatch(pattern, text)
        if match:
            return replacement(match)
    return text


def localize_audit_suggested_fix(text: str | None) -> str | None:
    if text is None:
        return None
    return _SUGGESTED_FIXES.get(text, text)
