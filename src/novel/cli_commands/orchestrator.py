from __future__ import annotations

import argparse
from pathlib import Path

from novel.cli_shared import _failure, _success, _vector_context_mode_from_args
from novel.core.command_bus import (
    DomainError,
    command_result_payload,
    dispatch_command,
    new_command_envelope,
)
from novel.core.contracts import DecisionRisk, Surface, WorkflowBudget
from novel.core.orchestrator import OrchestratorError, propose_ask_command
from pydantic import ValidationError


def _cmd_ask(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    try:
        budget = WorkflowBudget(
            max_chapters=args.max_chapters,
            max_model_calls=args.max_agent_calls,
            max_provider_attempts=args.max_provider_attempts,
            max_auto_revision_rounds=args.max_auto_revision_rounds,
            max_input_tokens=args.max_input_tokens,
            max_output_tokens=args.max_output_tokens,
        )
        proposed = propose_ask_command(
            root,
            args.request,
            provider_name=args.provider,
            budget=budget,
            force=args.force,
            use_search_context=args.use_search_context,
            use_vector_context=_vector_context_mode_from_args(args),
        )
        proposal = proposed.proposal
        payload: dict[str, object] = {
            "command": "ask",
            "status": "proposed",
            "workflow_run_id": proposed.workflow_run_id,
            "proposal": proposal.model_dump(mode="json"),
            "ask_intent": proposed.intent.model_dump(mode="json"),
            "budget_usage": proposed.budget_usage.model_dump(mode="json"),
        }
        command = proposal.command
        should_execute = (
            command is not None and not args.dry_run and (args.confirm or proposal.risk is DecisionRisk.LOW)
        )
        if should_execute:
            result = dispatch_command(
                new_command_envelope(
                    surface=Surface.ASK,
                    project_root=root,
                    command=command,
                    confirmed=args.confirm,
                    workflow_run_id=proposed.workflow_run_id,
                    parent_request_id=proposed.request_id,
                    budget=budget,
                    initial_budget_usage=proposed.budget_usage,
                )
            )
            payload["status"] = "executed"
            payload["execution"] = command_result_payload(result)
            payload["budget_usage"] = result.budget_usage.model_dump(mode="json")
    except DomainError as exc:
        return _failure(args, exc.message, error_type=exc.code)
    except OrchestratorError as exc:
        return _failure(args, str(exc), error_type="orchestrator_error")
    except ValidationError as exc:
        return _failure(args, str(exc), error_type="invalid_command")

    lines = [
        f"Ask status: {payload['status']}",
        f"Risk: {proposal.risk.value}",
        f"Reason: {proposal.reason}",
        f"Estimated model calls: {proposal.estimated_model_calls}",
    ]
    if proposal.clarification_question:
        lines.append(f"Clarification: {proposal.clarification_question}")
    elif proposal.requires_confirmation and payload["status"] == "proposed":
        lines.append("确认范围与预算后，使用相同请求并添加 --confirm 执行。")
    return _success(args, payload, lines)
