---
name: writeryang-agent-polish
description: Use when debugging or changing WriterYang Polish Agent behavior, edit modes, polished front matter, or polish prompt constraints.
---

# WriterYang Polish Agent

Use this skill only for the Polish Agent.

## Inputs

- `memory/chapters/{NNN}/draft.md`
- `memory/chapters/{NNN}/plan.json`
- `project.yaml`
- `memory/inspiration.md`
- `memory/style_guide.md`
- All canon files under `memory/canon/`.
- `memory/state/current_state.json`
- `memory/state/timeline.json`
- Optional instruction, style note, keep-length flag, and edit mode.

## Output artifacts

- `memory/chapters/{NNN}/polished.md`
- Provider and model I/O logs under `runs/`.

## Hard boundaries

- Output only the polished chapter body wrapped in required YAML front matter.
- Preserve core plot facts, known information, item/location state, and ending hook.
- Do not add major setting facts or update canon, state, timeline, plan, draft, audit, or exports.
- Do not reveal hidden truth content unless already allowed by plan or user instruction.
- Respect edit mode: light, normal, or deep.

## Debug and tests

- Inspect `polished.md`, front matter, and model I/O logs.
- Check that output does not include modification notes, JSON, or provider raw response.
- Relevant tests: `tests/test_polishing.py`, `tests/test_session.py`, `tests/test_agent_output.py`.
