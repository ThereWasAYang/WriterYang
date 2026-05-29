---
name: writeryang-agent-plot
description: Use when debugging or changing WriterYang Plot Agent chapter planning, ChapterPlan schema output, plan markdown rendering, or search context integration.
---

# WriterYang Plot Agent

Use this skill only for the Plot / Chapter Planning Agent.

## Inputs

- Approved session outline or direct chapter instruction.
- `project.yaml`
- `memory/inspiration.md`
- `memory/style_guide.md`
- All canon files under `memory/canon/`.
- `memory/state/current_state.json`
- `memory/state/timeline.json`
- Optional `ContextBundle` when `--use-search-context` is enabled.

## Output artifacts

- `memory/chapters/{NNN}/plan.json`
- `memory/chapters/{NNN}/plan.md`
- Optional `memory/chapters/{NNN}/context_report*.json`

## Hard boundaries

- Output must satisfy the `ChapterPlan` schema.
- Do not write prose.
- Do not modify canon, state, timeline, drafts, polished text, or audit files.
- Do not reference missing character, location, item, state, or timeline IDs in final `plan.json`.
- Do not reveal hidden truth content in reader-visible plan text unless the user explicitly requested the reveal.
- Do not prescribe creative formulas, story templates, or fixed plot patterns.

## Debug and tests

- Inspect `plan.json`, `plan.md`, validation results, and context reports.
- For model output shape failures, inspect `runs/model_io/{request_id}.json` and repair retry logs.
- Relevant tests: `tests/test_planning.py`, `tests/test_search.py`, `tests/test_agent_output.py`.
