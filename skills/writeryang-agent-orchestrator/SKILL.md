---
name: writeryang-agent-orchestrator
description: Use when debugging or changing WriterYang orchestrator/session routing, user-facing task negotiation, handoff decisions, run logs, or session state transitions.
---

# WriterYang Orchestrator Agent

Use this skill only for the Orchestrator / Session layer.

## Inputs

- User request or Web UI instruction.
- `project.yaml`, project status, and latest validation summary.
- `memory/sessions/{session_id}/session.json` when continuing a session.
- Existing chapter artifacts, audit summaries, and run logs needed to choose the next workflow step.

## Output artifacts

- `memory/sessions/{session_id}/session.json`
- `memory/sessions/{session_id}/outline_proposal.json`
- `memory/sessions/{session_id}/outline_proposal.md`
- `memory/sessions/{session_id}/approved_outline.json`
- `memory/sessions/{session_id}/approved_outline.md`
- `runs/run_*.json` and handoff trace entries.

## Hard boundaries

- User-facing orchestration may ask the user for missing scope, intent, chapter range, or approval.
- Internal handoffs must call the target Agent service and must not ask that Agent to negotiate with the user.
- Do not generate prose, canon, audit findings, or state changes directly in the orchestrator.
- Do not run writing before outline approval.
- Do not modify archived content in place; create a new revision session when the user explicitly wants to change archived work.
- Keep handoff loops bounded by max steps, retries, and agent call limits.

## Debug and tests

- Start with `memory/sessions/{session_id}/session.json`, then inspect run logs and target Agent artifacts.
- Use `python scripts/debug_bundle.py --project <project> --output /tmp/writeryang-debug --json` for handoff debugging.
- Relevant tests: `tests/test_orchestrator.py`, `tests/test_session.py`, `tests/test_web.py`.
