---
name: writeryang-workflow-debug
description: Use when diagnosing WriterYang session, generate-chapter, audit, revision, state update, export, provider, or Web UI workflow failures. Provides a fixed evidence-gathering order and debug bundle workflow.
---

# WriterYang Workflow Debug

Use this skill when a WriterYang project or smoke run fails.

## Evidence order

Inspect in this order:

1. `memory/sessions/*/session.json`
2. `memory/chapters/{NNN}/audit.json`
3. `memory/chapters/{NNN}/revision_log.json`
4. `memory/chapters/{NNN}/state_update_proposal.json`
5. `memory/chapters/{NNN}/state_update_apply_log.json`
6. `runs/run_*.json`
7. `runs/provider_calls.jsonl`
8. `runs/model_io/index.jsonl` and the relevant `runs/model_io/{request_id}.json`
9. `runs/provider_usage.json`

## Commands

Start with read-only checks:

```bash
novel validate --path <project>
novel status --path <project>
novel usage --path <project>
python scripts/project_health.py --project <project>
```

Create a redacted bundle for handoff:

```bash
python scripts/debug_bundle.py --project <project> --output /tmp/writeryang-debug --zip --json
```

## Classification

- Schema/reference error: fix data schema, validation, or file wiring.
- Deterministic audit issue: fix state/timeline/canon/chapter linkage.
- Model audit issue: inspect prompt, context, model output, and revision behavior.
- Provider error: run provider ping and inspect sanitized provider logs.
- Web UI error: reproduce with Web UI QA skill or `scripts/webui_smoke.py`.

Do not bypass medium/high/critical audit blockers by marking content accepted.
