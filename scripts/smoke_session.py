#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence

from novel.core.timeutil import utc_now_iso

import yaml


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic WriterYang session smoke flow via CLI.")
    parser.add_argument("--project", default=None, help="Workspace path. Defaults to a temporary directory.")
    parser.add_argument("--chapters", default="1", help="Chapter range, for example 1 or 1-2.")
    parser.add_argument("--provider", default="mock", help="Provider passed to generation commands.")
    parser.add_argument("--model", default=None, help="Optional model override passed to generation commands.")
    parser.add_argument("--title", default="WriterYang Smoke Novel", help="Temporary project title.")
    parser.add_argument("--intent", default="写一个雨夜车站开篇，建立悬疑感，不揭示隐藏真相。")
    parser.add_argument("--inspiration", default="雨夜旧车站传来停播多年的广播声。")
    parser.add_argument("--report", default=None, help="Optional JSON report path.")
    parser.add_argument("--keep", action="store_true", help="Keep temporary workspace when --project is omitted.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands without running them.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args(argv)

    temp_dir: Path | None = None
    if args.project:
        root = Path(args.project).expanduser().resolve()
    elif args.dry_run:
        root = Path(tempfile.gettempdir()).resolve() / "writeryang-smoke-dry-run" / "novel"
    else:
        temp_dir = Path(tempfile.mkdtemp(prefix="writeryang-smoke-"))
        root = temp_dir / "novel"

    proposal_path = root / "runs" / "smoke_canon_proposal.json"
    planned = _planned_commands(args, root, proposal_path)
    if args.dry_run:
        payload = {"ok": True, "dry_run": True, "project": str(root), "commands": planned}
        _write_report(args.report, payload)
        _print(payload, args.json)
        return 0

    report: dict[str, object] = {
        "started_at": utc_now_iso(),
        "project": str(root),
        "provider": args.provider,
        "chapters": args.chapters,
        "steps": [],
    }
    ok = True
    try:
        _run_step(report, "init", planned[0])
        _patch_default_api_config(root, provider=args.provider, model=args.model)
        _run_step(report, "inspire", planned[1])
        _run_step(report, "canon_suggest", planned[2])
        _run_step(report, "canon_apply", planned[3])
        start_payload = _run_step(report, "session_start", planned[4])
        session_id = str(start_payload["session_id"])
        _run_step(report, "session_approve", _json_cli(["session", "approve-outline", session_id, "--project", str(root)]))
        _run_step(
            report,
            "session_run",
            _json_cli(["session", "run", session_id, "--project", str(root), *_provider_args(args, include_model=False)]),
        )
        _run_step(
            report,
            "session_accept",
            _json_cli(["session", "accept", session_id, "--project", str(root), *_provider_args(args, include_model=False)]),
        )
        _run_step(report, "session_archive", _json_cli(["session", "archive", session_id, "--project", str(root)]))
        _run_step(report, "export_markdown", _json_cli(["export", "markdown", "--project", str(root), "--force"]))
        _run_step(report, "validate", _json_cli(["validate", "--project", str(root)]))
        report["session_id"] = session_id
    except RuntimeError:
        ok = False
    finally:
        report["ended_at"] = utc_now_iso()
        report["ok"] = ok
        _write_report(args.report, report)
        if temp_dir and not args.keep:
            shutil.rmtree(temp_dir, ignore_errors=True)

    _print(report, args.json)
    return 0 if ok else 1


def _planned_commands(args: argparse.Namespace, root: Path, proposal_path: Path) -> list[list[str]]:
    return [
        _json_cli(["init", args.title, "--project", str(root)]),
        _json_cli(["inspire", args.inspiration, "--project", str(root), *_provider_args(args), "--overwrite"]),
        _json_cli(
            ["canon", "suggest", "--project", str(root), *_provider_args(args), "--output", str(proposal_path)]
        ),
        _json_cli(["canon", "apply", str(proposal_path), "--project", str(root)]),
        _json_cli(
            [
                "session",
                "start",
                args.intent,
                "--project",
                str(root),
                "--chapters",
                args.chapters,
                *_provider_args(args, include_model=False),
            ]
        ),
    ]


def _provider_args(args: argparse.Namespace, *, include_model: bool = True) -> list[str]:
    values = ["--provider", args.provider]
    if include_model and args.model:
        values.extend(["--model", args.model])
    return values


def _patch_default_api_config(root: Path, *, provider: str, model: str | None) -> None:
    config_path = root / "config" / "agents.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    default = data.setdefault("default", {})
    if isinstance(default, dict):
        if provider == "config" and os.environ.get("WRITERYANG_REAL_API_KEY"):
            default["provider"] = os.environ.get("WRITERYANG_REAL_PROVIDER") or "openai_compatible"
            default["base_url_env"] = "WRITERYANG_REAL_BASE_URL"
            default["api_key_env"] = "WRITERYANG_REAL_API_KEY"
        if model:
            default["model"] = model
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _json_cli(args: list[str]) -> list[str]:
    return [sys.executable, "-m", "novel", *args, "--json", "--quiet"]


def _run_step(report: dict[str, object], name: str, command: list[str]) -> dict[str, object]:
    completed = subprocess.run(command, text=True, capture_output=True)
    payload = _parse_json(completed.stdout)
    step = {
        "name": name,
        "command": command,
        "returncode": completed.returncode,
        "ok": completed.returncode == 0 and bool(payload.get("ok", True)),
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "payload": payload,
    }
    steps = report.setdefault("steps", [])
    if not isinstance(steps, list):
        steps = []
        report["steps"] = steps
    steps.append(step)
    if not step["ok"]:
        raise RuntimeError(f"smoke step failed: {name}")
    return payload


def _parse_json(text: str) -> dict[str, object]:
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "raw_stdout": text}
    return payload if isinstance(payload, dict) else {"ok": False, "raw_stdout": text}


def _write_report(path: str | None, payload: dict[str, object]) -> None:
    if not path:
        return
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _print(payload: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Smoke {'passed' if payload.get('ok') else 'failed'}: {payload.get('project')}")
if __name__ == "__main__":
    raise SystemExit(main())
