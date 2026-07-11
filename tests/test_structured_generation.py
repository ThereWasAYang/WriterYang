from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel.core.agent_output import AgentInvocationContext, AgentOutputContract
from novel.core.json_extract import extract_json_object
from novel.core.providers import MockProvider, ModelRequest
from novel.core.structured_generation import (
    REPAIR_ERROR_LIMIT,
    REPAIR_INVALID_OUTPUT_LIMIT,
    JsonRepairExhaustedError,
    generate_json_with_repair,
)


def test_generate_json_with_repair_retries_after_parse_error(tmp_path: Path) -> None:
    provider = MockProvider(fake_response=["{bad", json.dumps({"ok": True})])

    result = generate_json_with_repair(
        provider,
        ModelRequest(system_prompt="system", user_prompt="original", json_schema_name="TestSchema"),
        root=tmp_path,
        invocation=_invocation("primary"),
        repair_invocation=_invocation("repair"),
        contract=_contract(),
        parse=lambda content: json.loads(extract_json_object(content)),
        repair_prompt=lambda invalid_output, error: f"repair {error}: {invalid_output}",
    )

    assert result == {"ok": True}
    assert len(provider.requests) == 2
    assert provider.requests[1].user_prompt.startswith("original\n\n")
    assert provider.requests[1].user_prompt.count("original") == 1
    assert "repair" in provider.requests[1].user_prompt


def test_generate_json_with_repair_uses_single_original_prompt_and_shared_limits(tmp_path: Path) -> None:
    marker = "ORIGINAL_UNIQUE_MARKER"
    invalid_output = "{" + ("x" * (REPAIR_INVALID_OUTPUT_LIMIT + 10))
    error_text = "e" * (REPAIR_ERROR_LIMIT + 10)
    provider = MockProvider(fake_response=[invalid_output, json.dumps({"ok": True})])

    result = generate_json_with_repair(
        provider,
        ModelRequest(system_prompt="system", user_prompt=f"原始任务 {marker}", json_schema_name="TestSchema"),
        root=tmp_path,
        invocation=_invocation("primary"),
        repair_invocation=_invocation("repair"),
        contract=_contract(),
        parse=lambda content: (_ for _ in ()).throw(ValueError(error_text))
        if content == invalid_output
        else json.loads(extract_json_object(content)),
        repair_prompt=lambda bad_output, error: (
            f"校验错误摘要：\n{error[:REPAIR_ERROR_LIMIT]}\n\n"
            f"上一次输出：\n{bad_output[:REPAIR_INVALID_OUTPUT_LIMIT]}\n"
        ),
    )

    assert result == {"ok": True}
    repair_prompt = provider.requests[1].user_prompt
    assert repair_prompt.count(marker) == 1
    assert "e" * REPAIR_ERROR_LIMIT in repair_prompt
    assert "e" * (REPAIR_ERROR_LIMIT + 1) not in repair_prompt
    assert invalid_output[:REPAIR_INVALID_OUTPUT_LIMIT] in repair_prompt
    assert invalid_output[: REPAIR_INVALID_OUTPUT_LIMIT + 1] not in repair_prompt


def test_generate_json_with_repair_raises_after_failed_retry(tmp_path: Path) -> None:
    provider = MockProvider(fake_response=["{bad", "{still_bad"])

    with pytest.raises(JsonRepairExhaustedError):
        generate_json_with_repair(
            provider,
            ModelRequest(system_prompt="system", user_prompt="original", json_schema_name="TestSchema"),
            root=tmp_path,
            invocation=_invocation("primary"),
            repair_invocation=_invocation("repair"),
            contract=_contract(),
            parse=lambda content: json.loads(extract_json_object(content)),
            repair_prompt=lambda invalid_output, error: f"repair {error}: {invalid_output}",
        )


def _invocation(task: str) -> AgentInvocationContext:
    return AgentInvocationContext(agent_name="test", interaction_mode="internal_task", task=task, surface="test")


def _contract() -> AgentOutputContract:
    return AgentOutputContract(output_kind="json", target_name="TestSchema", json_schema_name="TestSchema")
