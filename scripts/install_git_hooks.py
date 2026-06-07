#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install WriterYang repository Git hooks.")
    parser.add_argument("--cwd", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--dry-run", action="store_true", help="Print the git config command without running it.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args(argv)

    root = Path(args.cwd).expanduser().resolve()
    hooks_dir = root / ".githooks"
    pre_push = hooks_dir / "pre-push"
    command = ["git", "config", "core.hooksPath", ".githooks"]
    payload: dict[str, object] = {
        "ok": True,
        "dry_run": args.dry_run,
        "cwd": str(root),
        "hooks_path": ".githooks",
        "pre_push": str(pre_push),
        "command": command,
    }

    if not pre_push.exists():
        payload.update({"ok": False, "error": f"{pre_push} does not exist"})
        _print(payload, json_output=args.json)
        return 1

    if not args.dry_run:
        completed = subprocess.run(command, cwd=root, text=True, capture_output=True)
        payload.update(
            {
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        if completed.returncode != 0:
            payload["ok"] = False
            _print(payload, json_output=args.json)
            return completed.returncode

    _print(payload, json_output=args.json)
    return 0


def _print(payload: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not payload.get("ok"):
        print(f"Git hook install failed: {payload.get('error')}")
        return
    if payload.get("dry_run"):
        print("Would install WriterYang Git hooks:")
    else:
        print("Installed WriterYang Git hooks:")
    print(f"- cwd: {payload['cwd']}")
    print(f"- command: {shlex.join(payload['command'])}")  # type: ignore[arg-type]
    print("- pre-push: runs python scripts/check_local.py before push")
    print("- skip: WRITERYANG_SKIP_PRE_PUSH=1 git push")


if __name__ == "__main__":
    raise SystemExit(main())
