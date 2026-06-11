#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Iterable, Sequence

from novel.core.timeutil import utc_now_iso, utc_timestamp


IMPORTANT_PATTERNS = (
    "project.yaml",
    "config/agents.yaml",
    "config/embeddings.yaml",
    "memory/sessions/*/session.json",
    "memory/sessions/*/outline_proposal.md",
    "memory/sessions/*/approved_outline.md",
    "memory/chapters/*/audit.json",
    "memory/chapters/*/state_update_proposal.json",
    "memory/chapters/*/state_update_apply_log.json",
    "memory/chapters/*/revision_log.json",
    "memory/chapters/*/metadata.json",
    "runs/provider_calls.jsonl",
    "runs/provider_usage.json",
    "runs/model_io/index.jsonl",
    "exports/export_manifest.json",
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect a redacted WriterYang debug bundle.")
    parser.add_argument("--project", required=True, help="Novel workspace path.")
    parser.add_argument("--output", default=None, help="Output directory. Defaults to /tmp/writeryang-debug-bundle-*.")
    parser.add_argument("--max-model-io", type=int, default=10, help="Maximum full model_io files to include.")
    parser.add_argument("--zip", action="store_true", help="Also create a .zip archive.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args(argv)

    root = Path(args.project).expanduser().resolve()
    output = Path(args.output).expanduser().resolve() if args.output else _default_output_dir()
    output.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for path in _important_files(root, args.max_model_io):
        rel = _relative(root, path)
        target = output / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_redact(path.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
        copied.append(rel)

    command_outputs = output / "command_outputs"
    command_outputs.mkdir(exist_ok=True)
    commands = {
        "validate.json": ["validate", "--project", str(root)],
        "status.json": ["status", "--project", str(root)],
        "usage.json": ["usage", "--project", str(root)],
    }
    for name, command in commands.items():
        result = _run_cli_json(command)
        (command_outputs / name).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        copied.append(f"command_outputs/{name}")

    manifest = {
        "created_at": utc_now_iso(),
        "project": str(root),
        "output": str(output),
        "files": copied,
        "redacted": True,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    zip_path = None
    if args.zip:
        zip_path = shutil.make_archive(str(output), "zip", output)
        manifest["zip_path"] = zip_path

    _print({"ok": True, **manifest}, args.json)
    return 0


def _important_files(root: Path, max_model_io: int) -> Iterable[Path]:
    seen: set[Path] = set()
    for pattern in IMPORTANT_PATTERNS:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path not in seen:
                seen.add(path)
                yield path
    model_io_dir = root / "runs" / "model_io"
    if model_io_dir.exists():
        files = sorted(
            (path for path in model_io_dir.glob("*.json") if path.name != "index.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for path in files[:max_model_io]:
            if path not in seen:
                seen.add(path)
                yield path


def _run_cli_json(args: list[str]) -> dict[str, object]:
    command = [sys.executable, "-m", "novel", *args, "--json", "--quiet"]
    completed = subprocess.run(command, text=True, capture_output=True)
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"ok": False, "raw_stdout": completed.stdout}
    if not isinstance(payload, dict):
        payload = {"ok": False, "raw_stdout": completed.stdout}
    payload["returncode"] = completed.returncode
    payload["stderr"] = _redact(completed.stderr[-4000:])
    return payload


def _redact(text: str) -> str:
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[redacted-api-key]", text)
    redacted = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;\"']+", r"\1[redacted]", redacted)
    redacted = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;\"']+", r"\1[redacted]", redacted)
    for key, value in os.environ.items():
        if value and ("KEY" in key or "TOKEN" in key or "SECRET" in key):
            redacted = redacted.replace(value, "[redacted]")
    return redacted


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _default_output_dir() -> Path:
    stamp = utc_timestamp("%Y%m%d_%H%M%S")
    return Path("/tmp") / f"writeryang-debug-bundle-{stamp}"


def _print(payload: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Debug bundle: {payload['output']}")
        if payload.get("zip_path"):
            print(f"Zip: {payload['zip_path']}")
if __name__ == "__main__":
    raise SystemExit(main())
