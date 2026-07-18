from __future__ import annotations

import pytest

from novel.core.agent_output import (
    AgentInvocationContext,
    AgentOutputContract,
    AgentOutputContractError,
    generate_with_output_guard,
    validate_agent_output,
    write_agent_output_violation_log,
)
from novel.core.providers import MockProvider, ModelRequest, ProviderOutputTruncatedError


def test_internal_task_rejects_clarification_question() -> None:
    with pytest.raises(AgentOutputContractError) as exc:
        validate_agent_output(
            "请提供角色设定后我再继续，可以吗？",
            invocation=AgentInvocationContext(
                agent_name="writer",
                interaction_mode="internal_task",
                task="write_chapter",
            ),
            contract=AgentOutputContract(output_kind="markdown", target_name="chapter draft"),
        )

    assert "clarification_request" in exc.value.reason_codes


def test_user_facing_allows_clarification_question() -> None:
    validate_agent_output(
        "这次要写第几章？",
        invocation=AgentInvocationContext(
            agent_name="intent_router",
            interaction_mode="user_facing",
            task="negotiate_session",
        ),
        contract=AgentOutputContract(output_kind="conversation", target_name="user conversation"),
    )


def test_internal_markdown_rejects_json_artifact() -> None:
    with pytest.raises(AgentOutputContractError) as exc:
        validate_agent_output(
            '{"content": "雨声压低了旧车站的轮廓。"}',
            invocation=AgentInvocationContext(
                agent_name="writer",
                interaction_mode="internal_task",
                task="write_chapter",
            ),
            contract=AgentOutputContract(output_kind="markdown", target_name="chapter draft"),
        )

    assert "unexpected_json_output" in exc.value.reason_codes


def test_internal_json_rejects_natural_language_question() -> None:
    with pytest.raises(AgentOutputContractError) as exc:
        validate_agent_output(
            "是否需要重点检查时间线？",
            invocation=AgentInvocationContext(
                agent_name="audit",
                interaction_mode="internal_task",
                task="audit_chapter",
            ),
            contract=AgentOutputContract(
                output_kind="json",
                target_name="AuditReport",
                json_schema_name="AuditReport",
            ),
        )

    assert "clarification_request" in exc.value.reason_codes
    assert "non_json_output" in exc.value.reason_codes


def test_internal_json_defers_string_field_semantics_to_typed_schema() -> None:
    validate_agent_output(
        '{"body_markdown":"角色说：我不能把 Canon 当答案，可以吗？"}',
        invocation=AgentInvocationContext(
            agent_name="writer",
            interaction_mode="internal_task",
            task="write_chapter",
        ),
        contract=AgentOutputContract(
            output_kind="json",
            target_name="ProseArtifactPayload",
            json_schema_name="ProseArtifactPayload",
        ),
    )


def test_violation_log_redacts_secret_like_values(tmp_path) -> None:
    error = AgentOutputContractError("bad output", reason_codes=("clarification_request",))

    api_key = "sk-" + "secret123456789"
    path = write_agent_output_violation_log(
        tmp_path,
        invocation=AgentInvocationContext(
            agent_name="writer",
            interaction_mode="internal_task",
            task="write_chapter",
        ),
        contract=AgentOutputContract(output_kind="markdown", target_name="chapter draft"),
        model_request=ModelRequest(system_prompt="s", user_prompt="u", request_id="req_001"),
        output=f"Authorization: Bearer {api_key}\n请补充角色设定。",
        error=error,
    )

    text = path.read_text(encoding="utf-8")
    assert api_key not in text
    assert "Authorization: Bearer [redacted]" in text


def test_generate_with_output_guard_raises_on_truncated_output_without_repair(tmp_path) -> None:
    provider = MockProvider(fake_response={"content": '{"ok":', "finish_reason": "length"})

    with pytest.raises(ProviderOutputTruncatedError) as exc:
        generate_with_output_guard(
            provider,
            ModelRequest(system_prompt="s", user_prompt="u"),
            root=tmp_path,
            invocation=AgentInvocationContext(agent_name="audit", interaction_mode="internal_task"),
            contract=AgentOutputContract(output_kind="json", target_name="AuditReport", json_schema_name="AuditReport"),
        )

    assert "finish_reason=length" in str(exc.value)
    assert len(provider.requests) == 1
