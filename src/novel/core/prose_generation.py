from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from novel.core.agent_output import AgentInvocationContext, AgentOutputContract
from novel.core.contracts.prose import ProseArtifactKind, ProseArtifactPayload
from novel.core.json_extract import extract_json_object
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.structured_generation import generate_json_with_repair

PROSE_ARTIFACT_SCHEMA_NAME = "ProseArtifactPayload"


def generate_prose_artifact(
    provider: ModelProvider,
    request: ModelRequest,
    *,
    root: Path,
    invocation: AgentInvocationContext,
    artifact_kind: ProseArtifactKind,
    chapter_number: int | None,
    required_source_refs: tuple[str, ...],
    stream: bool = False,
) -> ProseArtifactPayload:
    request = replace(request, json_schema_name=PROSE_ARTIFACT_SCHEMA_NAME)
    contract = AgentOutputContract(
        output_kind="json",
        target_name=PROSE_ARTIFACT_SCHEMA_NAME,
        json_schema_name=PROSE_ARTIFACT_SCHEMA_NAME,
    )

    def parse(content: str) -> ProseArtifactPayload:
        payload = ProseArtifactPayload.model_validate(json.loads(extract_json_object(content)))
        if payload.artifact_kind != artifact_kind:
            raise ValueError(
                f"artifact_kind must be {artifact_kind.value}, got {payload.artifact_kind.value}"
            )
        if payload.chapter_number != chapter_number:
            raise ValueError(
                f"chapter_number must be {chapter_number!r}, got {payload.chapter_number!r}"
            )
        missing = sorted(set(required_source_refs) - set(payload.source_artifact_refs))
        if missing:
            raise ValueError("source_artifact_refs missing required refs: " + ", ".join(missing))
        return payload

    return generate_json_with_repair(
        provider,
        request,
        root=root,
        invocation=invocation,
        repair_invocation=invocation,
        contract=contract,
        parse=parse,
        repair_prompt=lambda invalid, error: _repair_prompt(
            invalid=invalid,
            error=error,
            artifact_kind=artifact_kind,
            chapter_number=chapter_number,
            required_source_refs=required_source_refs,
        ),
        stream=stream,
    )


def mock_prose_artifact_json(
    *,
    artifact_kind: ProseArtifactKind,
    body_markdown: str,
    source_artifact_refs: tuple[str, ...],
    chapter_number: int | None = None,
    change_summary: str,
) -> str:
    return ProseArtifactPayload(
        artifact_kind=artifact_kind,
        chapter_number=chapter_number,
        body_markdown=body_markdown,
        source_artifact_refs=list(source_artifact_refs),
        assumptions=[],
        warnings=[],
        change_summary=change_summary,
    ).model_dump_json()


def _repair_prompt(
    *,
    invalid: str,
    error: str,
    artifact_kind: ProseArtifactKind,
    chapter_number: int | None,
    required_source_refs: tuple[str, ...],
) -> str:
    return (
        "只输出符合 ProseArtifactPayload 的 JSON 对象。"
        f"artifact_kind 必须是 {artifact_kind.value!r}，chapter_number 必须是 {chapter_number!r}，"
        f"source_artifact_refs 必须至少包含 {list(required_source_refs)!r}。"
        "body_markdown 只放正文，不放 YAML front matter；assumptions、warnings 必须是字符串数组；"
        "change_summary 必须简要说明本次生成或改写。\n"
        f"校验错误：{error[:2000]}\n"
        f"无效输出摘要：{invalid[:6000]}"
    )
