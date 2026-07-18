#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from novel.core.timeutil import utc_now_iso


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize WriterYang project health.")
    parser.add_argument("--project", required=True, help="Novel workspace path.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args(argv)

    root = Path(args.project).expanduser().resolve()
    payload = {
        "ok": True,
        "generated_at": utc_now_iso(),
        "project": str(root),
        "validate": _cli_json(["validate", "--project", str(root)]),
        "status": _cli_json(["status", "--project", str(root)]),
        "usage": _cli_json(["usage", "--project", str(root)]),
        "chapters": _chapter_health(root),
        "sessions": _session_health(root),
        "exports": _export_health(root),
    }
    validation = payload["validate"]
    if isinstance(validation, dict) and validation.get("ok") is False:
        payload["ok"] = False
    _print(payload, args.json)
    return 0 if payload["ok"] else 1


def _cli_json(args: list[str]) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "novel", *args, "--json", "--quiet"],
        text=True,
        capture_output=True,
    )
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"ok": False, "raw_stdout": completed.stdout}
    if not isinstance(payload, dict):
        payload = {"ok": False, "raw_stdout": completed.stdout}
    payload["returncode"] = completed.returncode
    payload["stderr"] = completed.stderr[-4000:]
    return payload


def _chapter_health(root: Path) -> list[dict[str, object]]:
    chapters_dir = root / "memory" / "chapters"
    rows: list[dict[str, object]] = []
    if not chapters_dir.exists():
        return rows
    for chapter_dir in sorted(path for path in chapters_dir.iterdir() if path.is_dir()):
        audit = _read_json(chapter_dir / "audit.json")
        metadata = _read_json(chapter_dir / "metadata.json")
        apply_log = _read_json(chapter_dir / "state_update_apply_log.json")
        raw_issues = audit.get("issues", []) if isinstance(audit, dict) else []
        issues = raw_issues if isinstance(raw_issues, list) else []
        blocking = [
            issue for issue in issues
            if isinstance(issue, dict) and issue.get("severity") in {"medium", "high", "critical"}
        ]
        rows.append(
            {
                "chapter": chapter_dir.name,
                "has_plan": (chapter_dir / "plan.json").exists(),
                "has_draft": (chapter_dir / "draft.md").exists(),
                "has_polished": (chapter_dir / "polished.md").exists(),
                "audit_status": audit.get("overall_status") if isinstance(audit, dict) else None,
                "blocking_issue_count": len(blocking),
                "accepted": bool(metadata.get("accepted")) if isinstance(metadata, dict) else False,
                "state_update_applied": apply_log.get("status") == "applied" if isinstance(apply_log, dict) else False,
            }
        )
    return rows


def _session_health(root: Path) -> list[dict[str, object]]:
    sessions_dir = root / "memory" / "sessions"
    rows: list[dict[str, object]] = []
    if not sessions_dir.exists():
        return rows
    for session_path in sorted(sessions_dir.glob("session_*/session.json")):
        data = _read_json(session_path)
        rows.append(
            {
                "session_id": data.get("session_id"),
                "status": data.get("status"),
                "outline_status": data.get("outline_status"),
                "content_status": data.get("content_status"),
                "chapter_range": data.get("chapter_range"),
                "path": str(session_path.relative_to(root)),
            }
        )
    return rows


def _export_health(root: Path) -> dict[str, object]:
    manifest = _read_json(root / "exports" / "export_manifest.json")
    return manifest if isinstance(manifest, dict) else {}


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_error": "invalid_json"}
    return data if isinstance(data, dict) else {"_error": "not_object"}


def _print(payload: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    status = "passed" if payload["ok"] else "failed"
    print(f"Project health {status}: {payload['project']}")
    validation = payload["validate"]
    if isinstance(validation, dict):
        validation_payload = validation.get("validation")
        if isinstance(validation_payload, dict):
            print(
                f"Validation: {validation_payload.get('error_count', 0)} error(s), "
                f"{validation_payload.get('warning_count', 0)} warning(s)"
            )
    print("Chapters:")
    chapters = payload.get("chapters")
    if not isinstance(chapters, list):
        return
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        print(
            f"- {chapter['chapter']}: audit={chapter['audit_status']} "
            f"blocking={chapter['blocking_issue_count']} accepted={chapter['accepted']}"
        )
if __name__ == "__main__":
    raise SystemExit(main())
