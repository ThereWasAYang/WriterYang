---
name: writeryang-web-ui-qa
description: Use after WriterYang Web UI, Web API, session panel, editor, provider config, state/timeline, or browser-facing workflow changes. Defines deterministic browser QA and screenshot capture.
---

# WriterYang Web UI QA

Use this skill after Web UI or Web API changes.

## Static checks

```bash
conda run -n py312 pytest tests/test_web.py -q
conda run -n py312 pytest -m "not real_api and not web_e2e" -q
```

Verify the HTML contains the expected controls and all mutating API calls still use core services through `web_api.py`.

## Browser smoke

If Playwright is available:

```bash
python scripts/webui_smoke.py --json
```

For manual inspection:

```bash
novel web --path <project>
```

Open the reported localhost URL and verify:

- project open/init
- project check
- inspiration and canon suggest/apply
- session start, revise outline, approve, run
- audit summary and revise-content controls
- chapter compare/editor/audit locate tabs
- provider config redaction
- state/timeline visualization

Capture screenshots for visual regressions. Do not use real provider credentials unless the user explicitly requests it.
