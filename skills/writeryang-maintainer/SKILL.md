---
name: writeryang-maintainer
description: Use when modifying WriterYang code, docs, tests, packaging, CLI, Web UI, providers, prompts, or schemas. Enforces project-specific development, validation, documentation, and GitHub sync rules.
---

# WriterYang Maintainer

Use this skill before changing WriterYang.

## Required reading

Read the smallest relevant set first:

- `AGENTS.md`
- `docs/DEVELOPER_GUIDE.md`
- `docs/CODEBASE_REFERENCE.md`
- `docs/AGENT_PROMPT_ASSEMBLY.md` for Agent/prompt/provider changes
- `docs/DEBUGGING_AND_REFACTORING.md` for bugfixes

## Development rules

- Put business logic in `src/novel/core/`; CLI and Web API stay thin.
- Do not duplicate `plan/write/polish/audit/state_update` logic in scripts or frontend.
- Use atomic write, backups, and project locks for project mutations.
- Never store or print API keys, Authorization headers, or env values.
- Keep archived/accepted content immutable unless creating a new revision/session.
- After code changes, update developer docs when behavior or entrypoints change.

## Verification

Prefer:

```bash
python scripts/check_local.py --skip-build
```

Before release/package changes:

```bash
python scripts/check_local.py
```

If a project workflow is affected, also run:

```bash
python scripts/smoke_session.py --provider mock --json
```

Commit and push after tests pass when the user has requested default GitHub sync.
