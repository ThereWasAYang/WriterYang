from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from novel.core.schemas import AuditIssue, AuditReport


class AuditRepairEligibility(StrEnum):
    AUTO_FIX = "auto_fix"
    MANUAL_REVIEW = "manual_review"
    ADVISORY = "advisory"


@dataclass(frozen=True)
class AuditIssueClassification:
    issue_id: str
    eligibility: AuditRepairEligibility
    reason: str


_EXECUTABLE_SOURCE_LAYERS = {"plan", "draft", "polished", "style"}


def classify_audit_issue(issue: AuditIssue) -> AuditIssueClassification:
    if issue.severity == "low":
        return AuditIssueClassification(
            issue.id,
            AuditRepairEligibility.ADVISORY,
            "low severity issue remains an author advisory",
        )
    if issue.source_layer not in _EXECUTABLE_SOURCE_LAYERS:
        return AuditIssueClassification(
            issue.id,
            AuditRepairEligibility.MANUAL_REVIEW,
            "missing executable source_layer",
        )
    if not issue.evidence:
        return AuditIssueClassification(
            issue.id,
            AuditRepairEligibility.MANUAL_REVIEW,
            "missing concrete evidence",
        )
    if issue.evidence_strength != "strong":
        return AuditIssueClassification(
            issue.id,
            AuditRepairEligibility.MANUAL_REVIEW,
            "evidence_strength is not strong",
        )
    if issue.is_hard_blocker is not True or not issue.blocking_reason:
        return AuditIssueClassification(
            issue.id,
            AuditRepairEligibility.MANUAL_REVIEW,
            "issue is not explicitly classified as a hard blocker",
        )
    if issue.confidence is None or issue.confidence < 0.75:
        return AuditIssueClassification(
            issue.id,
            AuditRepairEligibility.MANUAL_REVIEW,
            "classification confidence is below 0.75",
        )
    return AuditIssueClassification(
        issue.id,
        AuditRepairEligibility.AUTO_FIX,
        "strong structured evidence authorizes automatic repair routing",
    )


def classify_audit_report(report: AuditReport) -> tuple[AuditIssueClassification, ...]:
    return tuple(classify_audit_issue(issue) for issue in report.issues)


def auto_fixable_issues(report: AuditReport) -> list[AuditIssue]:
    classifications = {item.issue_id: item for item in classify_audit_report(report)}
    return [
        issue for issue in report.issues if classifications[issue.id].eligibility is AuditRepairEligibility.AUTO_FIX
    ]


def manual_review_blockers(report: AuditReport) -> list[AuditIssueClassification]:
    return [item for item in classify_audit_report(report) if item.eligibility is AuditRepairEligibility.MANUAL_REVIEW]
