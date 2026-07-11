from __future__ import annotations

from dataclasses import dataclass

from novel.core.contracts import ArtifactKind, ProfileId, TaskId


@dataclass(frozen=True)
class TaskDefinition:
    task_id: TaskId
    profile: ProfileId
    prompt_template: str
    output_kind: ArtifactKind | None
    readable_authorities: tuple[str, ...]
    writable_artifacts: tuple[ArtifactKind, ...]
    risk: str


TASK_REGISTRY: dict[TaskId, TaskDefinition] = {
    TaskId.INSPIRATION: TaskDefinition(TaskId.INSPIRATION, ProfileId.LOREMASTER, "inspiration_system", None, ("user_input",), (), "low"),
    TaskId.STYLE_GUIDE: TaskDefinition(TaskId.STYLE_GUIDE, ProfileId.LOREMASTER, "style_guide_system", None, ("user_input", "inspiration"), (), "low"),
    TaskId.CANON: TaskDefinition(TaskId.CANON, ProfileId.LOREMASTER, "canon_system", None, ("user_input", "canon"), (), "high"),
    TaskId.PLAN: TaskDefinition(TaskId.PLAN, ProfileId.ARCHITECT, "planning_system", ArtifactKind.PLAN, ("canon", "state", "timeline", "accepted_chapters"), (ArtifactKind.PLAN,), "medium"),
    TaskId.WRITE: TaskDefinition(TaskId.WRITE, ProfileId.SCRIBE, "writer_system", ArtifactKind.CANDIDATE, ("approved_plan", "canon", "state", "timeline"), (ArtifactKind.CANDIDATE,), "medium"),
    TaskId.POLISH: TaskDefinition(TaskId.POLISH, ProfileId.SCRIBE, "polish_system", ArtifactKind.CANDIDATE, ("candidate", "style_guide"), (ArtifactKind.CANDIDATE,), "medium"),
    TaskId.REVISION: TaskDefinition(TaskId.REVISION, ProfileId.SCRIBE, "segment_revision_system", ArtifactKind.SEGMENT_PATCH, ("authorized_candidate", "segment_selection", "user_instruction"), (ArtifactKind.SEGMENT_PATCH,), "high"),
    TaskId.AUDIT: TaskDefinition(TaskId.AUDIT, ProfileId.ARCHITECT, "audit_system", ArtifactKind.AUDIT, ("candidate", "approved_plan", "canon", "state", "timeline"), (ArtifactKind.AUDIT,), "medium"),
    TaskId.STATE_UPDATE: TaskDefinition(TaskId.STATE_UPDATE, ProfileId.CLERK, "state_update_system", ArtifactKind.STATE_PROPOSAL, ("candidate", "passed_audit", "state", "timeline"), (ArtifactKind.STATE_PROPOSAL,), "high"),
    TaskId.CHAPTER_MEMORY: TaskDefinition(TaskId.CHAPTER_MEMORY, ProfileId.CLERK, "chapter_memory_system", ArtifactKind.CHAPTER_MEMORY, ("candidate", "passed_audit", "state_proposal"), (ArtifactKind.CHAPTER_MEMORY,), "medium"),
    TaskId.INTENT_ROUTER: TaskDefinition(TaskId.INTENT_ROUTER, ProfileId.CLERK, "intent_router_ask_intent_system", None, ("user_input", "workflow_summary"), (), "high"),
    TaskId.MEMORY_REPAIR: TaskDefinition(TaskId.MEMORY_REPAIR, ProfileId.CLERK, "memory_repair_system", None, ("user_input", "canon", "state", "timeline"), (), "high"),
    TaskId.SETUP: TaskDefinition(TaskId.SETUP, ProfileId.CLERK, "setup", None, ("user_input",), (), "low"),
}


def task_definition(task_id: TaskId | str) -> TaskDefinition:
    return TASK_REGISTRY[TaskId(task_id)]


def render_task_registry_markdown() -> str:
    rows = [
        "| Task | Profile | Prompt | Output | Risk |",
        "|---|---|---|---|---|",
    ]
    for task_id in TaskId:
        item = TASK_REGISTRY[task_id]
        output = item.output_kind.value if item.output_kind else "decision/text"
        rows.append(
            f"| `{item.task_id.value}` | `{item.profile.value}` | `{item.prompt_template}` | `{output}` | `{item.risk}` |"
        )
    return "\n".join(rows) + "\n"
