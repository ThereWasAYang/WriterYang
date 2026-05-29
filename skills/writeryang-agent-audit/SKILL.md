---
name: writeryang-agent-audit
description: Use when debugging or changing WriterYang Audit Agent reports, deterministic consistency checks, severity policy, or audit prompt assembly.
---

# WriterYang Audit Agent

Use this skill only for the Audit Agent and deterministic consistency checks.

## Inputs

- `memory/chapters/{NNN}/plan.json`
- `memory/chapters/{NNN}/draft.md` or `polished.md`
- `project.yaml`
- `memory/inspiration.md`
- `memory/style_guide.md`
- All canon files under `memory/canon/`.
- `memory/state/current_state.json`
- `memory/state/timeline.json`
- Deterministic consistency findings and optional focus/instruction.

## Output artifacts

- `memory/chapters/{NNN}/audit.json`
- Deterministic issues merged into audit report issues.
- Provider and model I/O logs under `runs/`.

## Hard boundaries

- Output must satisfy the `AuditReport` schema.
- Do not modify prose, canon, state, timeline, plan, revision files, or exports.
- Evidence must reference concrete source files or text.
- `passed` reports must not contain medium, high, or critical issues.
- Medium or higher severity must be reserved for concrete, evidence-backed continuity, state, timeline, knowledge, or reveal problems.

## Debug and tests

- Start with deterministic findings, then inspect model audit output and normalized report.
- Verify severity normalization when the model reports vague risk without hard evidence.
- Relevant tests: `tests/test_auditing.py`, `tests/test_validation.py`, `tests/test_consistency.py`.
