from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re
import time
from pathlib import Path
from typing import Iterable, Literal

from novel.core.io import atomic_write_json
from novel.core.json_extract import strip_code_fence
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.security import redact_secret_text


InteractionMode = Literal["internal_task", "user_facing"]
OutputKind = Literal["json", "markdown", "conversation"]


class AgentOutputContractError(RuntimeError):
    """Raised when an agent response does not match the invocation contract."""

    def __init__(self, message: str, *, reason_codes: Iterable[str]) -> None:
        self.reason_codes = tuple(dict.fromkeys(reason_codes))
        super().__init__(message)


@dataclass(frozen=True)
class AgentInvocationContext:
    agent_name: str
    caller: str = "cli"
    interaction_mode: InteractionMode = "internal_task"
    task: str | None = None
    chapter_number: int | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class AgentOutputContract:
    output_kind: OutputKind
    target_name: str
    json_schema_name: str | None = None
    allow_user_questions: bool = False
    disallow_workspace_language: bool = True
    allow_json_payload: bool = False
    disallow_outline_or_analysis: bool = True


def generate_with_output_guard(
    provider: ModelProvider,
    model_request: ModelRequest,
    *,
    root: Path,
    invocation: AgentInvocationContext,
    contract: AgentOutputContract,
    stream: bool = False,
) -> str:
    request = _request_with_id(model_request)
    output = _call_provider(provider, request, stream=stream)
    first_error: AgentOutputContractError | None = None
    try:
        validate_agent_output(output, invocation=invocation, contract=contract)
        return output
    except AgentOutputContractError as exc:
        first_error = exc
        write_agent_output_violation_log(
            root,
            invocation=invocation,
            contract=contract,
            model_request=request,
            output=output,
            error=exc,
        )

    assert first_error is not None
    repair_request = _request_with_id(
        replace(
            model_request,
            user_prompt=build_output_contract_repair_prompt(
                original_prompt=model_request.user_prompt,
                invalid_output=output,
                error=first_error,
                invocation=invocation,
                contract=contract,
            ),
        )
    )
    repaired_output = _call_provider(provider, repair_request, stream=stream)
    try:
        validate_agent_output(repaired_output, invocation=invocation, contract=contract)
        return repaired_output
    except AgentOutputContractError as second_error:
        write_agent_output_violation_log(
            root,
            invocation=invocation,
            contract=contract,
            model_request=repair_request,
            output=repaired_output,
            error=second_error,
        )
        raise AgentOutputContractError(
            "agent output violated contract after repair retry: "
            + ", ".join(second_error.reason_codes),
            reason_codes=second_error.reason_codes,
        ) from second_error


def validate_agent_output(
    content: str,
    *,
    invocation: AgentInvocationContext,
    contract: AgentOutputContract,
) -> None:
    reasons: list[str] = []
    stripped = content.strip()
    if not stripped:
        reasons.append("empty_output")
    user_facing = invocation.interaction_mode == "user_facing" or contract.allow_user_questions
    if not user_facing and _looks_like_clarification_request(stripped):
        reasons.append("clarification_request")
    if not user_facing and _looks_like_model_meta_response(stripped):
        reasons.append("model_meta_response")
    if contract.output_kind == "json" and not _looks_like_json_payload(stripped):
        reasons.append("non_json_output")
    if contract.output_kind == "markdown":
        if _looks_like_json_payload(stripped) and not contract.allow_json_payload:
            reasons.append("unexpected_json_output")
        if contract.disallow_outline_or_analysis and _looks_like_outline_or_analysis(stripped):
            reasons.append("unexpected_outline_or_analysis")
        if contract.disallow_workspace_language and _contains_workspace_language(stripped):
            reasons.append("workspace_language")
    if reasons:
        raise AgentOutputContractError(
            f"{invocation.agent_name} output does not satisfy {contract.target_name}: "
            + ", ".join(dict.fromkeys(reasons)),
            reason_codes=reasons,
        )


def build_output_contract_repair_prompt(
    *,
    original_prompt: str,
    invalid_output: str,
    error: AgentOutputContractError,
    invocation: AgentInvocationContext,
    contract: AgentOutputContract,
) -> str:
    mode_rule = (
        "这是内部 Agent 任务，不要向用户或上游 Agent 提问。"
        "如果信息不足，请基于已给上下文做保守假设，并在允许的 warnings/notes 中记录假设。"
        if invocation.interaction_mode == "internal_task"
        else "这是用户交互任务；如果需要澄清，可以直接向用户提出简洁问题。"
    )
    output_rule = (
        f"请只输出合法 {contract.json_schema_name or contract.target_name} JSON，不要 Markdown、解释或包装语。"
        if contract.output_kind == "json"
        else f"请只输出 {contract.target_name}，不要 JSON、解释、大纲、分析或包装语。"
    )
    return (
        f"{original_prompt}\n\n"
        "上一次输出违反了 Agent 输出契约。\n"
        f"{mode_rule}\n"
        f"{output_rule}\n"
        f"违反原因：{', '.join(error.reason_codes)}。\n\n"
        f"上一次输出摘要：\n{_redact_sensitive(invalid_output)[:4000]}\n"
    )


def write_agent_output_violation_log(
    root: Path,
    *,
    invocation: AgentInvocationContext,
    contract: AgentOutputContract,
    model_request: ModelRequest,
    output: str,
    error: AgentOutputContractError,
) -> Path:
    request_id = model_request.request_id or _new_request_id()
    path = root.resolve() / "runs" / "agent_output_violations" / f"{request_id}.json"
    log = {
        "schema_version": "1.0",
        "request_id": request_id,
        "agent_name": invocation.agent_name,
        "caller": invocation.caller,
        "interaction_mode": invocation.interaction_mode,
        "task": invocation.task,
        "chapter_number": invocation.chapter_number,
        "session_id": invocation.session_id,
        "target_name": contract.target_name,
        "output_kind": contract.output_kind,
        "json_schema_name": contract.json_schema_name,
        "reason_codes": list(error.reason_codes),
        "message": str(error),
        "output_excerpt": _redact_sensitive(output)[:2000],
        "output_length": len(output),
        "created_at": _utc_now(),
    }
    atomic_write_json(path, log)
    return path


def _call_provider(provider: ModelProvider, request: ModelRequest, *, stream: bool) -> str:
    if stream:
        return "".join(provider.stream(request))
    return provider.generate(request).content


def _request_with_id(request: ModelRequest) -> ModelRequest:
    return request if request.request_id else replace(request, request_id=_new_request_id())


def _new_request_id() -> str:
    return "agent_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + f"_{time.time_ns() % 1000000:06d}"


def _looks_like_json_payload(text: str) -> bool:
    cleaned = strip_code_fence(text)
    if not cleaned:
        return False
    if cleaned[0] in "{[":
        return True
    return bool(re.search(r"```(?:json)?\s*[\[{]", text, re.IGNORECASE))


def _looks_like_clarification_request(text: str) -> bool:
    normalized = " ".join(text.strip().split())
    lower = normalized.lower()
    starters = (
        "请提供",
        "请补充",
        "请确认",
        "请告诉我",
        "需要你提供",
        "我需要知道",
        "我还需要",
        "在继续之前",
        "能否补充",
        "是否希望",
        "你希望",
        "你想要",
        "could you provide",
        "please provide",
        "please clarify",
        "before i continue",
        "i need to know",
    )
    if lower.startswith(starters):
        return True
    question_marks = normalized.count("?") + normalized.count("？")
    if len(normalized) <= 500 and question_marks:
        phrases = (
            "请问",
            "是否",
            "能否",
            "可否",
            "要不要",
            "需要我",
            "你希望",
            "你想",
            "do you want",
            "would you like",
            "should i",
            "may i",
        )
        if any(phrase in lower for phrase in phrases):
            return True
    return False


def _looks_like_model_meta_response(text: str) -> bool:
    normalized = " ".join(text.strip().split())
    lower = normalized.lower()
    patterns = (
        "作为ai",
        "作为 ai",
        "作为一个ai",
        "作为一个 ai",
        "我是一个ai",
        "我是一个 ai",
        "我不能",
        "我无法",
        "无法继续",
        "不能继续",
        "as an ai",
        "i cannot",
        "i can't",
        "i am unable",
    )
    return len(normalized) <= 1200 and any(pattern in lower for pattern in patterns)


def _looks_like_outline_or_analysis(text: str) -> bool:
    normalized = text.strip()
    if len(normalized) > 2000:
        return False
    patterns = (
        "以下是",
        "下面是",
        "润色如下",
        "正文如下",
        "修改说明",
        "分析如下",
        "大纲",
        "本章大纲",
        "写作思路",
        "## 大纲",
        "## 分析",
        "here is",
        "the outline",
        "analysis:",
    )
    lower = normalized.lower()
    return any(pattern in lower for pattern in patterns)


def _contains_workspace_language(text: str) -> bool:
    patterns = (
        "根据设定",
        "本章目标",
        "隐藏真相",
        "作者内部",
        "ChapterPlan",
        "AuditReport",
        "StateUpdateProposal",
        "Canon",
        "current_state",
        "timeline.json",
        "plan.json",
        "draft.md",
        "polished.md",
    )
    return any(pattern in text for pattern in patterns)


def _redact_sensitive(text: str) -> str:
    return redact_secret_text(text)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
