---
name: writeryang-agent-canon
description: Use when debugging or changing WriterYang Canon Agent proposals, canon apply/merge behavior, stable IDs, hidden truth handling, or canon validation.
---

# WriterYang Canon Agent

Use this skill only for the Canon Agent and Canon Manager.

## Inputs

- `memory/inspiration.md`
- Optional `memory/inspiration.json`
- `project.yaml`
- `memory/style_guide.md`
- Existing files in `memory/canon/`.

## Output artifacts

- Canon proposal JSON with `characters`, `locations`, `items`, `world_rules`, `hidden_truths`, `foreshadowing_threads`, and `notes`.
- Applied canon files:
  - `memory/canon/characters.json`
  - `memory/canon/locations.json`
  - `memory/canon/items.json`
  - `memory/canon/world.json`
  - `memory/canon/hidden_truths.json`
  - `memory/canon/foreshadowing.json`

## Hard boundaries

- Default behavior is proposal-first; only apply when explicitly requested.
- Use stable IDs and fail clearly on duplicate IDs.
- Do not put hidden truth content into reader-visible summaries.
- Do not update state, timeline, chapter plans, drafts, or audits.
- Do not overwrite canon files silently; use atomic writes and backups when replacing important files.

## Debug and tests

- Validate only canon when debugging apply failures before running full-project validation.
- Inspect proposal parsing, merge result, and `runs/model_io/{request_id}.json`.
- Relevant tests: `tests/test_canon.py`, `tests/test_validation.py`, `tests/test_security.py`.
