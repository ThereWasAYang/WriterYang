---
name: writeryang-agent-writer
description: Use when debugging or changing WriterYang Writer Agent drafting, draft front matter, internal-task output contracts, or chapter writing prompt assembly.
---

# WriterYang Writer Agent

Use this skill only for the Writer Agent.

## Inputs

- `memory/chapters/{NNN}/plan.json`
- `project.yaml`
- `memory/inspiration.md`
- `memory/style_guide.md`
- All canon files under `memory/canon/`.
- `memory/state/current_state.json`
- `memory/state/timeline.json`
- Optional user instruction, target words, style note, and `ContextBundle`.

## Output artifacts

- `memory/chapters/{NNN}/draft.md`
- Optional `memory/chapters/{NNN}/context_report*.json`
- Provider and model I/O logs under `runs/`.

## Hard boundaries

- Output only Markdown chapter prose with required YAML front matter.
- Do not output JSON, outline, analysis, explanation, or workspace language.
- Do not modify canon, state, timeline, plan, polished text, audit, or exports.
- Do not let characters know facts unavailable from current state, timeline, plan, or allowed context.
- Do not reveal hidden truth content unless the approved plan or user instruction explicitly requires it.
- Do not prescribe creative formulas, story templates, or fixed prose patterns.

## Debug and tests

- Inspect `draft.md`, output contract violation logs, and `runs/model_io/{request_id}.json`.
- If the model asks a question during drafting, treat it as an internal-task contract failure and inspect repair retry behavior.
- Relevant tests: `tests/test_drafting.py`, `tests/test_agent_output.py`, `tests/test_session.py`.
