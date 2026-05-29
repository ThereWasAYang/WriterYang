---
name: writeryang-real-api-smoke
description: Use when running WriterYang real API smoke tests with DeepSeek, ZAI, OpenAI-compatible, DashScope, or Zhipu providers. Requires explicit permission before reading local env files or sending novel content to external APIs.
---

# WriterYang Real API Smoke

Use this skill only after the user explicitly allows reading local credentials and sending smoke-test content to the configured provider.

## Safety rules

- Never print API keys or env values.
- Load `.env.real` only when explicitly allowed.
- Use a temporary project unless the user names a project.
- Keep full model I/O logs local; do not commit generated novels or `runs/`.
- If the provider returns malformed JSON, inspect repair retry behavior before changing prompts.

## Preflight

```bash
python scripts/provider_ping.py --project <project> --env-file /path/to/.env.real --provider config --allow-network --json
```

For embedding providers:

```bash
python scripts/provider_ping.py --project <project> --env-file /path/to/.env.real --embedding-provider config --allow-network --json
```

## Session smoke

Run the author-facing flow:

```bash
python scripts/smoke_session.py --provider config --chapters 1-2 --keep --report /tmp/writeryang-real-smoke.json --json
```

Acceptance criteria:

- Session reaches accepted and archived.
- `novel validate` has no errors.
- Markdown export succeeds with accepted chapters.
- Provider usage shows zero failed calls.
- No secret scanner findings.

If it fails, switch to `writeryang-workflow-debug`.
