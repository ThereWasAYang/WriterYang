from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TypeVar

from novel.core.agent_output import AgentInvocationContext, AgentOutputContract, generate_with_output_guard
from novel.core.providers import ModelProvider, ModelRequest


T = TypeVar("T")
REPAIR_INVALID_OUTPUT_LIMIT = 12000
REPAIR_ERROR_LIMIT = 4000


class JsonRepairExhaustedError(RuntimeError):
    """Raised when structured JSON output is still invalid after one repair retry."""

    def __init__(self, target_name: str, first_error: Exception, second_error: Exception) -> None:
        self.target_name = target_name
        self.first_error = first_error
        self.second_error = second_error
        super().__init__(f"provider returned invalid {target_name} after repair retry: {second_error}")


def generate_json_with_repair(
    provider: ModelProvider,
    request: ModelRequest,
    *,
    root: Path,
    invocation: AgentInvocationContext,
    repair_invocation: AgentInvocationContext,
    contract: AgentOutputContract,
    parse: Callable[[str], T],
    repair_prompt: Callable[[str, str], str],
) -> T:
    content = generate_with_output_guard(
        provider,
        request,
        root=root,
        invocation=invocation,
        contract=contract,
    )
    try:
        return parse(content)
    except Exception as first_error:
        repair_content = generate_with_output_guard(
            provider,
            replace(
                request,
                repair_count=request.repair_count + 1,
                user_prompt=_repair_user_prompt(
                    original_prompt=request.user_prompt,
                    repair_instruction=repair_prompt(content, str(first_error)),
                ),
            ),
            root=root,
            invocation=repair_invocation,
            contract=contract,
        )
        try:
            return parse(repair_content)
        except Exception as second_error:
            raise JsonRepairExhaustedError(contract.target_name, first_error, second_error) from second_error


def _repair_user_prompt(*, original_prompt: str, repair_instruction: str) -> str:
    return (
        f"{original_prompt}\n\n"
        "上一次结构化输出没有通过解析或校验。请基于上方完整原始任务重新输出修复后的 JSON。\n"
        "以下是修复上下文；不要忽略原始任务中的素材、正文或设定。\n\n"
        f"{repair_instruction}"
    )
