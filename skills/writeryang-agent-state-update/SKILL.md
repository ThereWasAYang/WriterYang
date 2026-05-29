---
name: writeryang-agent-state-update
description: Use when debugging or changing WriterYang State Update Agent proposals, current_state/timeline application, conflict detection, or acceptance gates.
---

# WriterYang State Update Agent

Use this skill only for the State Update Agent.

## Inputs

- `memory/chapters/{NNN}/plan.json`
- `memory/chapters/{NNN}/polished.md`
- `memory/chapters/{NNN}/audit.json`
- All canon files under `memory/canon/`.
- `memory/state/current_state.json`
- `memory/state/timeline.json`
- Optional state update instruction.

## Output artifacts

- `memory/chapters/{NNN}/state_update_proposal.json`
- `memory/chapters/{NNN}/state_update_apply_log.json`
- Updated `memory/state/current_state.json`
- Updated `memory/state/timeline.json`

## Hard boundaries

- Default behavior is proposal-first; applying state changes is a separate explicit step.
- Do not modify canon or chapter prose.
- Only extract events and state changes supported by the chapter text and plan.
- Every state change needs a reason and source.
- Timeline event scene numbers must come from the ChapterPlan scene list.
- Reject duplicate event IDs, invalid entity references, and item holder/location conflicts.

## Debug and tests

- Inspect proposal, apply log, backups, and validation output.
- Use project health and validation before accepting a chapter.
- Relevant tests: `tests/test_state_update.py`, `tests/test_session.py`, `tests/test_validation.py`.
