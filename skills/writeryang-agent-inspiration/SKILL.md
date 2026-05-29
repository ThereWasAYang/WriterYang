---
name: writeryang-agent-inspiration
description: Use when debugging or changing WriterYang Inspiration Agent inputs, weak outline output, inspiration artifacts, or inspire command behavior.
---

# WriterYang Inspiration Agent

Use this skill only for the Inspiration Agent.

## Inputs

- User-provided inspiration text or `--input` file content.
- `project.yaml` when available.
- Optional existing `memory/style_guide.md` for tone constraints.

## Output artifacts

- `memory/inspiration.md`
- Optional `memory/inspiration.json`
- Provider call logs under `runs/`.

## Hard boundaries

- Produce a weak creative direction, not binding plot facts.
- Do not write canon, state, timeline, chapter plans, drafts, audits, or exports.
- Do not overwrite existing inspiration files unless the caller explicitly allows it.
- Do not store API keys or provider credentials in output artifacts.
- Do not prescribe creative formulas, story templates, or fixed character-building steps.

## Debug and tests

- Inspect `memory/inspiration.md`, the corresponding `runs/model_io/{request_id}.json`, and provider call metadata.
- Check output guard violations under `runs/agent_output_violations/` when the model asks a question during an internal task.
- Relevant tests: `tests/test_inspiration.py`, `tests/test_prompts.py`, `tests/test_providers.py`.
