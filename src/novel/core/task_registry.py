from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from novel.core.contracts import ArtifactKind, ProfileId, TaskId
from novel.core.prompts import PROMPT_VERSIONS, load_prompt_template


@dataclass(frozen=True)
class TaskDefinition:
    task_id: TaskId
    profile: ProfileId
    prompt_template: str
    output_kind: ArtifactKind | None
    readable_authorities: tuple[str, ...]
    writable_artifacts: tuple[ArtifactKind, ...]
    risk: str
    context_policy_id: str
    prompt_policy_id: str


@dataclass(frozen=True)
class PromptRegistryEntry:
    task_id: TaskId
    template_name: str
    template_version: str
    template_hash: str
    context_policy_id: str
    prompt_policy_id: str
    policy_hash: str


def _task(
    task_id: TaskId,
    profile: ProfileId,
    prompt_template: str,
    output_kind: ArtifactKind | None,
    readable_authorities: tuple[str, ...],
    writable_artifacts: tuple[ArtifactKind, ...],
    risk: str,
    context_policy_id: str,
    prompt_policy_id: str,
) -> TaskDefinition:
    return TaskDefinition(
        task_id,
        profile,
        prompt_template,
        output_kind,
        readable_authorities,
        writable_artifacts,
        risk,
        context_policy_id,
        prompt_policy_id,
    )


TASK_REGISTRY: dict[TaskId, TaskDefinition] = {
    TaskId.INSPIRATION: _task(
        TaskId.INSPIRATION,
        ProfileId.LOREMASTER,
        "inspiration_system",
        None,
        ("user_input", "canonical", "accepted_chapter", "chapter_memory"),
        (),
        "low",
        "inspiration_safe",
        "creative_markdown",
    ),
    TaskId.STYLE_GUIDE: _task(
        TaskId.STYLE_GUIDE,
        ProfileId.LOREMASTER,
        "style_guide_system",
        None,
        ("user_input", "inspiration"),
        (),
        "low",
        "style_input_only",
        "structured_style",
    ),
    TaskId.CANON: _task(
        TaskId.CANON,
        ProfileId.LOREMASTER,
        "canon_system",
        None,
        ("user_input", "canonical", "accepted_chapter", "chapter_memory"),
        (),
        "high",
        "canon_authoring",
        "proposal_only",
    ),
    TaskId.PLAN: _task(
        TaskId.PLAN,
        ProfileId.ARCHITECT,
        "planning_system",
        ArtifactKind.PLAN,
        ("canonical", "accepted_chapter", "chapter_memory"),
        (ArtifactKind.PLAN,),
        "medium",
        "plot_authority",
        "reveal_authorization",
    ),
    TaskId.WRITE: _task(
        TaskId.WRITE,
        ProfileId.SCRIBE,
        "writer_system",
        ArtifactKind.CANDIDATE,
        ("approved_plan", "canonical", "accepted_chapter", "chapter_memory"),
        (ArtifactKind.CANDIDATE,),
        "medium",
        "writer_reveal_guard",
        "drafting_direct_output",
    ),
    TaskId.POLISH: _task(
        TaskId.POLISH,
        ProfileId.SCRIBE,
        "polish_system",
        ArtifactKind.CANDIDATE,
        ("approved_plan", "candidate", "canonical"),
        (ArtifactKind.CANDIDATE,),
        "medium",
        "polish_reveal_guard",
        "preserve_facts",
    ),
    TaskId.REVISION: _task(
        TaskId.REVISION,
        ProfileId.SCRIBE,
        "segment_revision_system",
        ArtifactKind.SEGMENT_PATCH,
        ("authorized_candidate", "segment_selection", "user_instruction"),
        (ArtifactKind.SEGMENT_PATCH,),
        "high",
        "revision_reveal_guard",
        "authorized_patch",
    ),
    TaskId.AUDIT: _task(
        TaskId.AUDIT,
        ProfileId.ARCHITECT,
        "audit_system",
        ArtifactKind.AUDIT,
        ("candidate", "approved_plan", "canonical", "accepted_chapter", "chapter_memory"),
        (ArtifactKind.AUDIT,),
        "medium",
        "audit_single_candidate",
        "evidence_classification",
    ),
    TaskId.STATE_UPDATE: _task(
        TaskId.STATE_UPDATE,
        ProfileId.CLERK,
        "state_update_system",
        ArtifactKind.STATE_PROPOSAL,
        ("candidate", "passed_audit", "canonical"),
        (ArtifactKind.STATE_PROPOSAL,),
        "high",
        "state_commit_ready",
        "proposal_only",
    ),
    TaskId.CHAPTER_MEMORY: _task(
        TaskId.CHAPTER_MEMORY,
        ProfileId.CLERK,
        "chapter_memory_system",
        ArtifactKind.CHAPTER_MEMORY,
        ("candidate", "passed_audit", "state_proposal"),
        (ArtifactKind.CHAPTER_MEMORY,),
        "medium",
        "commit_ready_only",
        "visibility_preserving",
    ),
    TaskId.INTENT_ROUTER: _task(
        TaskId.INTENT_ROUTER,
        ProfileId.CLERK,
        "intent_router_ask_intent_system",
        None,
        ("user_input", "workflow_summary"),
        (),
        "high",
        "router_no_workspace",
        "command_proposal",
    ),
    TaskId.MEMORY_REPAIR: _task(
        TaskId.MEMORY_REPAIR,
        ProfileId.CLERK,
        "memory_repair_system",
        None,
        ("user_input", "canonical"),
        (),
        "high",
        "memory_repair_allowlist",
        "proposal_only",
    ),
}


def task_definition(task_id: TaskId | str) -> TaskDefinition:
    return TASK_REGISTRY[TaskId(task_id)]


AGENT_TASK_IDS: dict[str, TaskId] = {
    "plot": TaskId.PLAN,
    "planning": TaskId.PLAN,
    "writer": TaskId.WRITE,
    "write": TaskId.WRITE,
    "polish": TaskId.POLISH,
    "revision": TaskId.REVISION,
    "audit": TaskId.AUDIT,
    "state_update": TaskId.STATE_UPDATE,
    "chapter_memory": TaskId.CHAPTER_MEMORY,
    "intent_router": TaskId.INTENT_ROUTER,
    "memory_repair": TaskId.MEMORY_REPAIR,
    "inspiration": TaskId.INSPIRATION,
    "style_guide": TaskId.STYLE_GUIDE,
    "canon": TaskId.CANON,
}


def task_definition_for_agent(agent_name: str) -> TaskDefinition | None:
    task_id = AGENT_TASK_IDS.get(agent_name)
    return TASK_REGISTRY.get(task_id) if task_id is not None else None


def prompt_registry_entry(task_id: TaskId | str) -> PromptRegistryEntry:
    definition = task_definition(task_id)
    template_name = definition.prompt_template
    if template_name in PROMPT_VERSIONS:
        template_text = load_prompt_template(template_name)
        template_version = PROMPT_VERSIONS[template_name]
    else:
        template_text = template_name
        template_version = "builtin"
    template_hash = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
    policy_payload = {
        "task_id": definition.task_id.value,
        "profile": definition.profile.value,
        "readable_authorities": definition.readable_authorities,
        "writable_artifacts": [item.value for item in definition.writable_artifacts],
        "risk": definition.risk,
        "context_policy_id": definition.context_policy_id,
        "prompt_policy_id": definition.prompt_policy_id,
    }
    policy_hash = hashlib.sha256(
        json.dumps(policy_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return PromptRegistryEntry(
        task_id=definition.task_id,
        template_name=template_name,
        template_version=template_version,
        template_hash=template_hash,
        context_policy_id=definition.context_policy_id,
        prompt_policy_id=definition.prompt_policy_id,
        policy_hash=policy_hash,
    )


def render_task_registry_markdown() -> str:
    rows = [
        "| Task | Profile | Prompt | Output | Context Policy | Prompt Policy | Risk |",
        "|---|---|---|---|---|---|---|",
    ]
    for task_id in TaskId:
        item = TASK_REGISTRY[task_id]
        output = item.output_kind.value if item.output_kind else "decision/text"
        rows.append(
            f"| `{item.task_id.value}` | `{item.profile.value}` | `{item.prompt_template}` | "
            f"`{output}` | `{item.context_policy_id}` | `{item.prompt_policy_id}` | `{item.risk}` |"
        )
    return "\n".join(rows) + "\n"


_PROFILE_PURPOSES: dict[ProfileId, str] = {
    ProfileId.SCRIBE: "正文写作、润色和 scoped revision patch",
    ProfileId.ARCHITECT: "章节计划和一致性 Audit",
    ProfileId.LOREMASTER: "灵感、文风与 Canon proposal",
    ProfileId.CLERK: "State Update、Chapter Memory、路由与 Memory Repair",
}


def render_profile_registry_markdown() -> str:
    rows = [
        "| Profile | Runtime Tasks | 默认用途 |",
        "| --- | --- | --- |",
    ]
    for profile in ProfileId:
        tasks = "、".join(f"`{task_id.value}`" for task_id in TaskId if TASK_REGISTRY[task_id].profile == profile)
        rows.append(f"| `{profile.value}` | {tasks} | {_PROFILE_PURPOSES[profile]} |")
    return "\n".join(rows) + "\n"
