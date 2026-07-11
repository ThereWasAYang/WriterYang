from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel.core.contracts import CommandProposal, DecisionRisk, SessionStartCommand, WorkflowBudget
from novel.core.schemas import ProjectConfig
from novel.core.task_registry import TASK_REGISTRY, render_task_registry_markdown
from novel.core.contracts import TaskId


def test_control_contracts_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CommandProposal.model_validate(
            {
                "command": SessionStartCommand(user_intent="写第一章", chapter_range=[1]),
                "reason": "用户请求创作",
                "confidence": 0.9,
                "risk": DecisionRisk.MEDIUM,
                "estimated_model_calls": 2,
                "requires_confirmation": True,
                "budget": {
                    "max_chapters": 1,
                    "max_model_calls": 3,
                    "max_provider_attempts": 3,
                    "max_auto_revision_rounds": 1,
                },
                "unexpected": "must fail",
            }
        )


def test_v2_domain_schema_is_rejected_without_migration() -> None:
    with pytest.raises(ValidationError, match="unsupported_project_schema"):
        ProjectConfig.model_validate(
            {
                "schema_version": 2,
                "project_id": "novel_test",
                "title": "旧项目",
                "language": "zh-CN",
                "genre": ["悬疑"],
                "created_at": "2026-07-11T00:00:00Z",
                "updated_at": "2026-07-11T00:00:00Z",
            }
        )


def test_workflow_budget_rejects_impossible_provider_attempt_limit() -> None:
    with pytest.raises(ValidationError, match="max_provider_attempts"):
        WorkflowBudget(
            max_chapters=1,
            max_model_calls=3,
            max_provider_attempts=2,
            max_auto_revision_rounds=1,
        )


def test_task_registry_covers_every_task_and_renders_mapping() -> None:
    assert set(TASK_REGISTRY) == set(TaskId)
    table = render_task_registry_markdown()
    assert "`write` | `scribe`" in table
    assert "`audit` | `architect`" in table
    assert "`state_update` | `clerk`" in table
