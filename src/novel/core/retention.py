from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from novel.core.io import append_jsonl
from novel.core.timeutil import utc_now_iso

WORKFLOW_RUN_MAX_COUNT_ENV = "WRITERYANG_RUN_MAX_COUNT"
WORKFLOW_RUN_MAX_AGE_DAYS_ENV = "WRITERYANG_RUN_MAX_AGE_DAYS"


@dataclass(frozen=True)
class RetentionResult:
    removed_paths: tuple[str, ...]
    reclaimed_bytes: int


def prune_workflow_runs(root: Path) -> RetentionResult:
    root = root.expanduser().resolve()
    runs_dir = root / "runs"
    if not runs_dir.exists():
        return RetentionResult((), 0)
    max_count = _positive_env_int(WORKFLOW_RUN_MAX_COUNT_ENV, 500)
    max_age_days = _positive_env_int(WORKFLOW_RUN_MAX_AGE_DAYS_ENV, 90)
    cutoff = time.time() - max_age_days * 86400
    candidates: list[tuple[float, Path]] = []
    for path in runs_dir.glob("run_*"):
        if not path.is_dir() or not _is_terminal_run(path):
            continue
        candidates.append((path.stat().st_mtime, path))
    candidates.sort(reverse=True)
    remove: list[Path] = []
    for index, (mtime, path) in enumerate(candidates):
        if index >= max_count or mtime < cutoff:
            remove.append(path)
    reclaimed = 0
    removed: list[str] = []
    for path in remove:
        reclaimed += _directory_size(path)
        removed.append(path.relative_to(root).as_posix())
        shutil.rmtree(path)
    if removed:
        append_jsonl(
            runs_dir / "retention.jsonl",
            {
                "timestamp": utc_now_iso(),
                "event_type": "workflow_run_retention",
                "status": "completed",
                "removed_paths": removed,
                "reclaimed_bytes": reclaimed,
                "max_count": max_count,
                "max_age_days": max_age_days,
            },
        )
    return RetentionResult(tuple(removed), reclaimed)


def observability_health(root: Path) -> dict[str, object]:
    root = root.expanduser().resolve()
    runs_dir = root / "runs"
    run_paths = sorted((path for path in runs_dir.glob("run_*") if path.is_dir()), reverse=True)
    statuses: dict[str, int] = {}
    recent_failures: list[dict[str, object]] = []
    for path in run_paths[:100]:
        run_file = path / "run.json"
        try:
            data = json.loads(run_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        status = str(data.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        if status == "failed" and len(recent_failures) < 10:
            recent_failures.append(
                {
                    "workflow_run_id": data.get("workflow_run_id"),
                    "updated_at": data.get("updated_at"),
                    "root_request_id": data.get("root_request_id"),
                }
            )
    total = sum(statuses.values())
    completed = statuses.get("completed", 0)
    return {
        "run_count_sampled": total,
        "success_rate": (completed / total) if total else None,
        "status_counts": statuses,
        "recent_failures": recent_failures,
        "runs_disk_bytes": _directory_size(runs_dir),
        "retention": {
            "max_count": _positive_env_int(WORKFLOW_RUN_MAX_COUNT_ENV, 500),
            "max_age_days": _positive_env_int(WORKFLOW_RUN_MAX_AGE_DAYS_ENV, 90),
        },
    }


def _is_terminal_run(path: Path) -> bool:
    try:
        data = json.loads((path / "run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("status") in {"completed", "failed", "cancelled"}


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
