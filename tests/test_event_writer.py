from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from novel.core.event_writer import EventWriter
from novel.core.retention import observability_health, prune_workflow_runs


def test_event_writer_preserves_concurrent_jsonl_line_integrity(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    writer = EventWriter(path, max_bytes=1024 * 1024)

    def append_worker(worker: int) -> None:
        for sequence in range(50):
            writer.append({"worker": worker, "sequence": sequence})

    threads = [threading.Thread(target=append_worker, args=(worker,)) for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    payloads = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(payloads) == 400
    assert len({(item["worker"], item["sequence"]) for item in payloads}) == 400
    assert all(item["event_type"] == "events" and item["timestamp"] for item in payloads)


def test_event_writer_rotates_bounded_files(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    writer = EventWriter(path, max_bytes=180, backup_count=2, fsync=False)

    for sequence in range(12):
        writer.append({"sequence": sequence, "message": "x" * 30})

    files = [candidate for candidate in (path, tmp_path / "events.jsonl.1", tmp_path / "events.jsonl.2") if candidate.exists()]
    assert len(files) == 3
    assert all(candidate.stat().st_size <= 180 for candidate in files)
    assert all(json.loads(line) for candidate in files for line in candidate.read_text(encoding="utf-8").splitlines())


def test_workflow_run_retention_removes_only_terminal_runs(monkeypatch, tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    for index, status in enumerate(("completed", "failed", "running"), start=1):
        run = runs / f"run_{index:032x}"
        run.mkdir()
        (run / "run.json").write_text(json.dumps({"status": status, "workflow_run_id": run.name}), encoding="utf-8")
        old = time.time() - 10 * 86400
        os.utime(run, (old, old))
    monkeypatch.setenv("WRITERYANG_RUN_MAX_COUNT", "1")
    monkeypatch.setenv("WRITERYANG_RUN_MAX_AGE_DAYS", "1")

    result = prune_workflow_runs(tmp_path)
    health = observability_health(tmp_path)

    assert set(result.removed_paths) == {"runs/run_00000000000000000000000000000001", "runs/run_00000000000000000000000000000002"}
    assert (runs / "run_00000000000000000000000000000003").is_dir()
    assert (runs / "retention.jsonl").is_file()
    assert health["status_counts"] == {"running": 1}
    assert health["runs_disk_bytes"] > 0
