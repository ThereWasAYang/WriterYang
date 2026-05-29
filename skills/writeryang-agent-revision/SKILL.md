---
name: writeryang-agent-revision
description: Use when debugging or changing WriterYang Revision Agent versioned chapter revisions, audit-driven fixes, revision logs, or session revise-content behavior.
---

# WriterYang Revision Agent

Use this skill only for the Revision Agent.

## Inputs

- `memory/chapters/{NNN}/draft.md` or `polished.md`
- `memory/chapters/{NNN}/plan.json`
- Optional `memory/chapters/{NNN}/audit.json`
- `memory/style_guide.md`
- Canon, state, and timeline files.
- User instruction or audit issue summary.

## Output artifacts

- Versioned files such as `draft.v2.md` or `polished.v2.md`
- `memory/chapters/{NNN}/revision_log.json`
- In session flow, a promoted current `polished.md` after successful revision handling.

## Hard boundaries

- Do not overwrite the original draft or polished file silently.
- Do not modify archived content in place.
- Do not update canon, state, timeline, audit, export, or accepted metadata directly.
- When revising from audit, address medium, high, and critical issues before low issues.
- Preserve core chapter facts unless the user explicitly asks for a structural change.

## Debug and tests

- Inspect versioned output, `revision_log.json`, promotion behavior, and post-revision audit.
- If a session remains `needs_revision`, inspect audit history and latest blocking issue summaries.
- Relevant tests: `tests/test_revision.py`, `tests/test_session.py`, `tests/test_auditing.py`.
