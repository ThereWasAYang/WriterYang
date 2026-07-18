from __future__ import annotations

import re

from novel.core.schemas import AuditIssue, AuditReport, SessionRewriteIssue

CANON_REFERENCE_SUGGESTED_FIX = "检查该 canon 关联关系，必要时补齐缺失 ID，或移除已经失效的引用。"

_SUMMARY_FIXES = {
    "Mock audit found no blocking consistency issues.": "Mock audit 未发现阻断性一致性问题。",
    "Invalid mock report.": "Mock 审核报告状态不合法。",
}

_SUGGESTED_FIXES = {
    "Review the referenced canon relationship and update missing IDs if needed.": CANON_REFERENCE_SUGGESTED_FIX,
    "Change status to needs_revision.": "将审核状态改为 needs_revision。",
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
    return text


def localize_audit_description(text: str) -> str:
    return text


def localize_audit_suggested_fix(text: str | None) -> str | None:
    if text is None:
        return None
    return _SUGGESTED_FIXES.get(text, text)
