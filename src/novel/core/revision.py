from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from novel.core.agent_output import AgentInvocationContext
from novel.core.artifact_store import ArtifactStore
from novel.core.canon import format_canon_summary, load_canon_files
from novel.core.context_budget import render_state_prompt_text, render_timeline_prompt_text
from novel.core.context_policy import render_untrusted_workspace_data
from novel.core.contracts import ArtifactKind, TaskId
from novel.core.contracts.prose import ProseArtifactKind
from novel.core.drafting import _chapter_number_text
from novel.core.io import (
    atomic_write_model_json,
    backup_if_exists,
    load_json_model,
    load_yaml_model,
)
from novel.core.polishing import DraftDocument, read_markdown_with_front_matter
from novel.core.prompts import load_prompt_template, prompt_template_version
from novel.core.prose_generation import generate_prose_artifact, mock_prose_artifact_json
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.schemas import (
    AuditReport,
    ChapterPlan,
    ContextBundle,
    EntityState,
    ProjectConfig,
    RevisionLog,
    RevisionRecord,
    TimelineFile,
    VectorContextMode,
)
from novel.core.search import retrieve_context_bundle, write_context_report
from novel.core.style_guide import DEFAULT_STYLE_GUIDANCE
from novel.core.timeutil import new_request_id, utc_now, utc_now_iso
from novel.core.world_state import resolve_world_state_paths

RevisionTarget = Literal["draft", "polished"]


class RevisionError(RuntimeError):
    """Raised when chapter revision cannot proceed safely."""


@dataclass(frozen=True)
class ChapterRevisionOptions:
    root: Path
    chapter_number: int
    instruction: str | None = None
    from_audit: bool = False
    target: RevisionTarget = "polished"
    force: bool = False
    use_search_context: bool = False
    use_vector_context: bool | VectorContextMode = "auto"
    world_state_dir: Path | None = None


@dataclass(frozen=True)
class ChapterRevisionResult:
    output_path: Path
    revision_log_path: Path
    record: RevisionRecord
    revised_markdown: str
    warnings: tuple[str, ...] = ()
    context_report_path: Path | None = None


@dataclass(frozen=True)
class RevisionContext:
    project: ProjectConfig
    plan: ChapterPlan
    source_document: DraftDocument
    source_file: str
    audit: AuditReport | None
    style_guide: str
    canon_summary: str
    state: EntityState
    timeline: TimelineFile
    search_context: str = ""
    context_bundle: ContextBundle | None = None


def revise_chapter(
    options: ChapterRevisionOptions,
    provider: ModelProvider,
    *,
    provider_name: str | None = None,
) -> ChapterRevisionResult:
    root = options.root.resolve()
    if options.chapter_number < 1:
        raise RevisionError("chapter_number must be a positive integer")
    if options.target not in {"draft", "polished"}:
        raise RevisionError("--target must be draft or polished")
    if not options.from_audit and not _clean_optional(options.instruction):
        raise RevisionError("provide --instruction, --input, or --from-audit")

    context, warnings = load_revision_context(root, options)
    if context.plan.chapter_number != options.chapter_number:
        raise RevisionError(
            f"plan.json chapter_number {context.plan.chapter_number} does not match requested "
            f"chapter {options.chapter_number}"
        )
    source_chapter = context.source_document.metadata.get("chapter_number")
    if source_chapter != options.chapter_number:
        raise RevisionError(
            f"{context.source_file} chapter_number {source_chapter} does not match requested "
            f"chapter {options.chapter_number}"
        )

    payload = generate_prose_artifact(
        provider,
        ModelRequest(
            system_prompt=build_revision_system_prompt(),
            user_prompt=build_revision_user_prompt(context, options),
            context=context.canon_summary,
            prompt_version=prompt_template_version("revision_system"),
        ),
        root=root,
        invocation=AgentInvocationContext(
            agent_name="revision",
            interaction_mode="internal_task",
            task="revise_chapter",
            chapter_number=options.chapter_number,
        ),
        artifact_kind=ProseArtifactKind.CHAPTER_REVISION,
        chapter_number=options.chapter_number,
        required_source_refs=(
            f"memory/chapters/{options.chapter_number:03d}/{context.source_file}",
        ),
    )
    body = payload.body_markdown
    warnings.extend(payload.warnings)
    warnings.extend(f"Agent assumption: {item}" for item in payload.assumptions)
    if not body:
        raise RevisionError("revision provider returned empty content")

    chapter_dir = root / "memory" / "chapters" / f"{options.chapter_number:03d}"
    revision_id = _new_revision_id()
    title = str(context.source_document.metadata.get("title") or context.plan.title)
    revised_markdown = render_revised_markdown(
        chapter_number=options.chapter_number,
        title=title,
        target=options.target,
        source_file=context.source_file,
        revision_id=revision_id,
        body=body,
        created_at=utc_now_iso(),
    )
    output_ref = ArtifactStore(root).create(
        chapter_number=options.chapter_number,
        kind=ArtifactKind.CANDIDATE,
        content=revised_markdown.encode("utf-8"),
        suffix=".md",
        producer_task_id=TaskId.REVISION,
    )
    output_path = root / output_ref.path

    record = RevisionRecord(
        id=revision_id,
        chapter_number=options.chapter_number,
        target=options.target,
        source_file=context.source_file,
        output_file=output_ref.path,
        instruction=_clean_optional(options.instruction),
        from_audit=options.from_audit,
        audit_file="audit.json" if context.audit else None,
        audit_issue_ids=[issue.id for issue in context.audit.issues] if context.audit else [],
        created_at=utc_now(),
        provider=provider_name,
    )
    log_path = chapter_dir / "revision_log.json"
    _append_revision_log(log_path, options.chapter_number, record)
    context_report_path = (
        write_context_report(root, context.context_bundle, force=options.force) if context.context_bundle else None
    )
    return ChapterRevisionResult(
        output_path=output_path,
        revision_log_path=log_path,
        record=record,
        revised_markdown=revised_markdown,
        warnings=tuple(warnings),
        context_report_path=context_report_path,
    )


def load_revision_context(
    root: Path,
    options: ChapterRevisionOptions,
) -> tuple[RevisionContext, list[str]]:
    chapter_dir = root / "memory" / "chapters" / f"{options.chapter_number:03d}"
    source_file = f"{options.target}.md"
    source_path = chapter_dir / source_file
    plan_path = chapter_dir / "plan.json"
    audit_path = chapter_dir / "audit.json"
    if not plan_path.exists():
        raise RevisionError(f"{plan_path} is missing; run novel plan-chapter first")
    if not source_path.exists():
        raise RevisionError(f"{source_path} is missing; run the earlier chapter step first")
    if options.from_audit and not audit_path.exists():
        raise RevisionError(f"{audit_path} is missing; run novel audit-chapter first")

    warnings: list[str] = []
    style_guide = _read_style_guide(root, warnings)
    audit = load_json_model(audit_path, AuditReport) if audit_path.exists() else None
    canon = load_canon_files(root)
    plan = load_json_model(plan_path, ChapterPlan)
    context_bundle = (
        retrieve_context_bundle(
            root,
            chapter_number=options.chapter_number,
            task="revision",
            instruction=options.instruction,
            plan=plan,
            use_vector=options.use_vector_context,
        )
        if options.use_search_context
        else None
    )
    state_path, timeline_path = resolve_world_state_paths(root, options.world_state_dir)
    return (
        RevisionContext(
            project=load_yaml_model(root / "project.yaml", ProjectConfig),
            plan=plan,
            source_document=read_markdown_with_front_matter(source_path),
            source_file=source_file,
            audit=audit,
            style_guide=style_guide,
            canon_summary=format_canon_summary(canon),
            state=load_json_model(state_path, EntityState),
            timeline=load_json_model(timeline_path, TimelineFile),
            search_context=context_bundle.render_for_prompt() if context_bundle else "",
            context_bundle=context_bundle,
        ),
        warnings,
    )


def load_revision_provider(
    root: Path,
    provider_name: str,
    *,
    target: RevisionTarget = "polished",
    chapter_number: int = 1,
    agent_config_path: Path | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    return create_agent_provider(
        agent_config_path or default_agent_config_path(root),
        "revision",
        overrides=ProviderOverrides(provider_name=provider_name, model_name=model_name),
        mock_response=default_mock_revised_payload_json(target, chapter_number),
    )


def read_revision_instruction(instruction: str | None, input_path: Path | None) -> str | None:
    if instruction and input_path:
        raise RevisionError("provide either --instruction or --input, not both")
    if input_path:
        if not input_path.exists():
            raise RevisionError(f"revision instruction input file is missing: {input_path}")
        return input_path.read_text(encoding="utf-8").strip() or None
    return instruction.strip() if instruction and instruction.strip() else None


def build_revision_system_prompt() -> str:
    return load_prompt_template("revision_system")


def build_revision_user_prompt(context: RevisionContext, options: ChapterRevisionOptions) -> str:
    audit_text = "无"
    blocking_issue_text = "无"
    if context.audit:
        audit_text = context.audit.model_dump_json(indent=2)
        blocking_issue_text = _blocking_audit_issue_text(context.audit)
    mode = "基于 audit issues 修复" if options.from_audit else "基于用户 instruction 修订"
    state_text = render_state_prompt_text(
        context.state,
        project=context.project,
        chapter_number=context.plan.chapter_number,
        plan=context.plan,
    )
    timeline_text = render_timeline_prompt_text(
        context.timeline,
        project=context.project,
        chapter_number=context.plan.chapter_number,
        task="revision",
        plan=context.plan,
    )
    project_text = json.dumps(
        {"title": context.project.title, "language": context.project.language},
        ensure_ascii=False,
    )
    return (
        f"{render_untrusted_workspace_data('project', project_text)}\n"
        f"修订模式：{mode}\n"
        f"目标文件类型：{options.target}\n"
        f"源文件：{context.source_file}\n"
        f"用户修订要求：{options.instruction or '无'}\n\n"
        "请只输出 ProseArtifactPayload JSON。artifact_kind 必须是 chapter_revision，"
        f"chapter_number 必须是 {context.plan.chapter_number}，source_artifact_refs 必须包含 "
        f"memory/chapters/{context.plan.chapter_number:03d}/{context.source_file}。"
        "body_markdown 只放修订正文，不要包含 YAML front matter。"
        "保留章节核心剧情与结尾钩子，除非用户明确要求改变。\n\n"
        f"{render_untrusted_workspace_data('search_context', context.search_context)}\n"
        f"{render_untrusted_workspace_data('blocking_audit_issues', blocking_issue_text)}\n"
        f"{render_untrusted_workspace_data('approved_chapter_plan', context.plan.model_dump_json(indent=2))}\n"
        f"{render_untrusted_workspace_data('source_metadata', json.dumps(context.source_document.metadata, ensure_ascii=False, indent=2, default=str))}\n"
        f"{render_untrusted_workspace_data('source_body', context.source_document.body)}\n"
        f"{render_untrusted_workspace_data('audit_report', audit_text)}\n"
        f"{render_untrusted_workspace_data('style_guide', context.style_guide)}\n"
        f"{render_untrusted_workspace_data('canon_summary', context.canon_summary)}\n"
        f"{render_untrusted_workspace_data('current_state', state_text)}\n"
        f"{render_untrusted_workspace_data('timeline', timeline_text)}\n"
    )


def _blocking_audit_issue_text(audit: AuditReport) -> str:
    lines = []
    for issue in audit.issues:
        if issue.severity not in {"medium", "high", "critical"}:
            continue
        evidence = "; ".join(f"{item.source}: {item.quote}" for item in issue.evidence) or "无"
        lines.append(
            f"- {issue.id} [{issue.severity}/{issue.type}] {issue.description}\n"
            f"  evidence: {evidence}\n"
            f"  suggested_fix: {issue.suggested_fix or '无'}"
        )
    return "\n".join(lines) if lines else "无"


def render_revised_markdown(
    *,
    chapter_number: int,
    title: str,
    target: RevisionTarget,
    source_file: str,
    revision_id: str,
    body: str,
    created_at: str,
) -> str:
    status = "draft_revision" if target == "draft" else "polished_revision"
    return (
        "---\n"
        f"chapter_number: {chapter_number}\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        f"status: {status}\n"
        "created_by: revision_agent\n"
        f"based_on: {source_file}\n"
        f"revision_id: {revision_id}\n"
        f"created_at: {created_at}\n"
        "---\n\n"
        f"# 第{_chapter_number_text(chapter_number)}章 {title}\n\n"
        f"{body.strip()}\n"
    )


def default_mock_revised_body(target: RevisionTarget = "polished") -> str:
    label = "润色稿" if target == "polished" else "初稿"
    return (
        f"雨夜旧车站里的雨声比先前更低，像一层细密的幕布压在站台上。这个修订后的{label}保留了原本的事件，"
        "却让林澈的迟疑更清楚地浮出水面。\n\n"
        "他重新望向站台尽头，广播里的杂音已经消失，只剩檐下不断坠落的水珠。那张湿透的破损车票仍在"
        "笔记本里发凉，像一个尚未被说出口的问题。\n\n"
        "林澈没有得到答案。他只是意识到，自己已经无法把这座车站当成一处废墟。"
    )


def default_mock_revised_payload_json(
    target: RevisionTarget = "polished",
    chapter_number: int = 1,
) -> str:
    return mock_prose_artifact_json(
        artifact_kind=ProseArtifactKind.CHAPTER_REVISION,
        chapter_number=chapter_number,
        body_markdown=default_mock_revised_body(target),
        source_artifact_refs=(f"memory/chapters/{chapter_number:03d}/{target}.md",),
        change_summary="按当前修订范围生成不可变候选稿。",
    )


def _append_revision_log(path: Path, chapter_number: int, record: RevisionRecord) -> None:
    if path.exists():
        log = load_json_model(path, RevisionLog)
        if log.chapter_number != chapter_number:
            raise RevisionError(
                f"{path} chapter_number {log.chapter_number} does not match requested chapter {chapter_number}"
            )
    else:
        log = RevisionLog(chapter_number=chapter_number, revisions=[])
    updated = log.model_copy(update={"revisions": [*log.revisions, record]})
    backup_if_exists(path, reason="revision_log")
    atomic_write_model_json(path, updated)


def _read_style_guide(root: Path, warnings: list[str]) -> str:
    path = root / "memory" / "style_guide.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    warnings.append("memory/style_guide.md is missing; using default style guidance")
    return DEFAULT_STYLE_GUIDANCE


def _clean_revised_body(content: str) -> str:
    body = content.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    for wrapper in ("以下是修订后的文本：", "修订如下：", "以下是修订后的正文："):
        if body.startswith(wrapper):
            body = body[len(wrapper) :].strip()
    return body


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _new_revision_id() -> str:
    return new_request_id("revision")
