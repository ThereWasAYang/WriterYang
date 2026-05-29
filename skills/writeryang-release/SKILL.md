---
name: writeryang-release
description: Use when preparing a WriterYang release, version bump, changelog, package build, GitHub Release, or release candidate validation.
---

# WriterYang Release

Use this skill for release preparation.

## Checklist

1. Confirm version in `pyproject.toml`.
2. Update `CHANGELOG.md` and `docs/RELEASE.md` if release behavior changed.
3. Run the full local quality gate:

   ```bash
   python scripts/check_local.py
   ```

4. Run a mock session smoke:

   ```bash
   python scripts/smoke_session.py --provider mock --json
   ```

5. Run secret scan and confirm no API keys are tracked.
6. Build artifacts and check them with twine.
7. Tag only after tests pass.

## Rules

- Do not configure PyPI credentials or tokens in the repo.
- Do not include local `.env*`, smoke novels, screenshots, or `runs/model_io/` in commits.
- If real API smoke is requested, use `writeryang-real-api-smoke` first and keep reports local unless redacted.
