from __future__ import annotations

import json
from pathlib import Path

from novel.core.usage import refresh_provider_usage_summary_for_log, summarize_provider_usage


def test_provider_usage_refresh_consumes_appended_lines_incrementally(tmp_path: Path) -> None:
    root = tmp_path
    log_path = root / "runs" / "provider_calls.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(_jsonl({"request_id": "req_1", "provider": "openai", "model": "gpt", "status": "success", "total_tokens": 7}), encoding="utf-8")

    output_path = refresh_provider_usage_summary_for_log(log_path)
    first = json.loads(output_path.read_text(encoding="utf-8"))
    with log_path.open("a", encoding="utf-8") as file:
        file.write("not json\n")
        file.write(_jsonl({"request_id": "req_2", "provider": "openai", "model": "gpt", "status": "failed"}))

    refresh_provider_usage_summary_for_log(log_path)
    second = json.loads(output_path.read_text(encoding="utf-8"))
    cached = summarize_provider_usage(root).as_dict()

    assert first["total"]["call_count"] == 1
    assert second["total"]["call_count"] == 2
    assert second["total"]["total_tokens"] == 7
    assert second["malformed_line_count"] == 1
    assert second["_aggregation"]["log_size_bytes"] == log_path.stat().st_size
    assert cached["total"]["call_count"] == 2


def test_provider_usage_refresh_rebuilds_when_log_is_truncated(tmp_path: Path) -> None:
    log_path = tmp_path / "runs" / "provider_calls.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        _jsonl({"request_id": "req_1", "provider": "openai", "model": "gpt", "status": "success", "total_tokens": 7})
        + _jsonl({"request_id": "req_2", "provider": "zai", "model": "glm", "status": "success", "total_tokens": 11}),
        encoding="utf-8",
    )
    output_path = refresh_provider_usage_summary_for_log(log_path)

    log_path.write_text(_jsonl({"request_id": "req_3", "provider": "deepseek", "model": "deep", "status": "failed"}), encoding="utf-8")
    refresh_provider_usage_summary_for_log(log_path)
    summary = json.loads(output_path.read_text(encoding="utf-8"))

    assert summary["total"]["call_count"] == 1
    assert summary["total"]["total_tokens"] == 0
    assert "deepseek" in summary["by_provider"]
    assert "openai" not in summary["by_provider"]


def _jsonl(data: dict[str, object]) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"
