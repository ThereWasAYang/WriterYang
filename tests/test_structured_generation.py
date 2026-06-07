from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel.core.agent_output import AgentInvocationContext, AgentOutputContract
from novel.core.json_extract import extract_json_object
from novel.core.providers import MockProvider, ModelRequest
from novel.core.structured_generation import JsonRepairExhaustedError, generate_json_with_repair


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
    assert "repair" in provider.requests[1].user_prompt


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
    return AgentInvocationContext(agent_name="test", caller="test", interaction_mode="internal_task", task=task)


def _contract() -> AgentOutputContract:
    return AgentOutputContract(output_kind="json", target_name="TestSchema", json_schema_name="TestSchema")
