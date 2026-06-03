#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Sequence


@dataclass(frozen=True)
class Check:
    name: str
    command: list[str]
    blocking: bool = True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local WriterYang quality gate.")
    parser.add_argument("--cwd", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument(
        "--only",
        action="append",
        choices=("pytest", "ruff", "mypy", "secret-scan", "build", "twine"),
        help="Run only the selected check. Can be repeated.",
    )
    parser.add_argument("--skip-build", action="store_true", help="Skip build and twine checks.")
    parser.add_argument("--keep-going", action="store_true", help="Continue after a failed check.")
    parser.add_argument(
        "--strict-mypy",
        action="store_true",
        help="Compatibility flag; mypy is always a blocking check.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands without running them.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args(argv)

    cwd = Path(args.cwd).resolve()
    checks = _selected_checks(args)
    if args.dry_run:
        payload = {"ok": True, "dry_run": True, "checks": [_check_payload(check) for check in checks]}
        _print_result(payload, json_output=args.json)
        return 0

    results: list[dict[str, object]] = []
    ok = True
    for check in checks:
        completed = subprocess.run(check.command, cwd=cwd, text=True, capture_output=True)
        result = {
            **_check_payload(check),
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
        results.append(result)
        if completed.returncode != 0 and check.blocking:
            ok = False
            if not args.keep_going:
                break

    payload = {"ok": ok, "dry_run": False, "checks": results}
    _print_result(payload, json_output=args.json)
    return 0 if ok else 1


def _selected_checks(args: argparse.Namespace) -> list[Check]:
    checks = [
        Check("pytest", [sys.executable, "-m", "pytest", "-m", "not real_api and not web_e2e", "-q"]),
        Check("ruff", [sys.executable, "-m", "ruff", "check", "."]),
        Check("mypy", [sys.executable, "-m", "mypy", "src"]),
        Check("secret-scan", _secret_scan_command()),
        Check("build", [sys.executable, "-m", "build"]),
        Check("twine", _twine_check_command()),
    ]
    if args.skip_build:
        checks = [check for check in checks if check.name not in {"build", "twine"}]
    if args.only:
        selected = set(args.only)
        checks = [check for check in checks if check.name in selected]
    return checks


def _secret_scan_command() -> list[str]:
    code = (
        "from pathlib import Path; "
        "from novel.core.security import scan_security; "
        "r=scan_security(Path('.')); "
        "assert r.ok, [(f.code, str(f.path), f.line) for f in r.findings]"
    )
    return [sys.executable, "-c", code]


def _twine_check_command() -> list[str]:
    code = (
        "import glob, subprocess, sys; "
        "files=glob.glob('dist/*'); "
        "sys.exit(subprocess.call([sys.executable, '-m', 'twine', 'check', *files]) if files else 1)"
    )
    return [sys.executable, "-c", code]


def _check_payload(check: Check) -> dict[str, object]:
    return {
        "name": check.name,
        "command": check.command,
        "display": shlex.join(check.command),
        "blocking": check.blocking,
    }


def _print_result(payload: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    status = "passed" if payload["ok"] else "failed"
    print(f"Local checks {status}")
    for item in payload["checks"]:  # type: ignore[index]
        code = item.get("returncode")
        suffix = "" if code is None else f" -> {code}"
        mode = "blocking" if item.get("blocking") else "informational"
        print(f"- {item['name']} ({mode}): {item['display']}{suffix}")


if __name__ == "__main__":
    raise SystemExit(main())
