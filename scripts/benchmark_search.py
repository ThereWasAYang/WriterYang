#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import TypedDict

from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.io import atomic_write_json
from novel.core.search import refresh_search_index, search_project
from novel.core.session import (
    SessionActionOptions,
    SessionRunOptions,
    SessionStartOptions,
    approve_outline,
    run_session,
    start_session,
)
from novel.core.workspace import InitOptions, init_workspace


class BenchmarkResult(TypedDict):
    chapter_count: int
    document_count: int
    initial_refresh_ms: float
    incremental_refresh_ms: float
    incremental_refreshed_documents: int
    query_p50_ms: float
    query_p95_ms: float
    peak_memory_bytes: int


def main() -> int:
    parser = argparse.ArgumentParser(description="测量 10/100/500 章 Search 刷新和查询基线。")
    parser.add_argument("--sizes", default="10,100,500", help="逗号分隔的章节规模。")
    parser.add_argument("--query-runs", type=int, default=20, help="每个规模的查询重复次数。")
    parser.add_argument("--output", type=Path, help="可选 JSON 报告路径。")
    parser.add_argument("--max-query-p95-ms", type=float, help="可选的每个规模查询 p95 上限。")
    parser.add_argument("--max-incremental-refresh-ms", type=float, help="可选的单源增量刷新上限。")
    parser.add_argument("--max-peak-memory-mb", type=float, help="可选的 tracemalloc 峰值上限。")
    args = parser.parse_args()
    sizes = [int(item) for item in args.sizes.split(",") if item.strip()]
    if not sizes or any(size < 1 for size in sizes) or args.query_runs < 1:
        parser.error("sizes 和 query-runs 必须是正整数")

    with tempfile.TemporaryDirectory(prefix="writeryang-search-benchmark-") as directory:
        results = [_benchmark_size(Path(directory) / f"chapters-{size}", size, args.query_runs) for size in sizes]
    payload = {"schema_version": 1, "results": results}
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        atomic_write_json(args.output, payload)
    violations = _threshold_violations(
        results,
        max_query_p95_ms=args.max_query_p95_ms,
        max_incremental_refresh_ms=args.max_incremental_refresh_ms,
        max_peak_memory_mb=args.max_peak_memory_mb,
    )
    if violations:
        for violation in violations:
            print(f"PERF GATE FAILED: {violation}")
        return 1
    return 0


def _benchmark_size(root: Path, chapter_count: int, query_runs: int) -> BenchmarkResult:
    init_workspace(InitOptions(title=f"Search benchmark {chapter_count}", root=root))
    proposal_path = root / "memory" / "canon" / "benchmark_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    apply_canon_proposal(root, proposal_path)
    session_result = start_session(
        SessionStartOptions(
            root=root,
            user_intent=f"生成 {chapter_count} 章 Search 性能基准大纲",
            chapter_range=tuple(range(1, chapter_count + 1)),
            provider_name="mock",
        )
    )
    approve_outline(SessionActionOptions(root=root, session_id=session_result.session.session_id))
    run_session(
        SessionRunOptions(
            root=root,
            session_id=session_result.session.session_id,
            provider_name="mock",
            use_search_context=False,
        )
    )

    tracemalloc.start()
    started = time.perf_counter()
    initial = refresh_search_index(root)
    refresh_ms = (time.perf_counter() - started) * 1000
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    samples = []
    for _ in range(query_runs):
        started = time.perf_counter()
        matches = search_project(root, "旧车站", limit=5)
        samples.append((time.perf_counter() - started) * 1000)
        if not matches:
            raise RuntimeError("benchmark query did not find approved chapter plans")

    characters_path = root / "memory" / "canon" / "characters.json"
    changed = json.loads(characters_path.read_text(encoding="utf-8"))
    changed["characters"][0]["tags"].append("incremental_change")
    characters_path.write_text(json.dumps(changed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    started = time.perf_counter()
    incremental = refresh_search_index(root)
    incremental_ms = (time.perf_counter() - started) * 1000
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, max(0, round(0.95 * len(ordered) + 0.5) - 1))
    return {
        "chapter_count": chapter_count,
        "document_count": initial.document_count,
        "initial_refresh_ms": round(refresh_ms, 3),
        "incremental_refresh_ms": round(incremental_ms, 3),
        "incremental_refreshed_documents": incremental.refreshed_count,
        "query_p50_ms": round(statistics.median(samples), 3),
        "query_p95_ms": round(ordered[p95_index], 3),
        "peak_memory_bytes": peak_bytes,
    }


def _threshold_violations(
    results: list[BenchmarkResult],
    *,
    max_query_p95_ms: float | None,
    max_incremental_refresh_ms: float | None,
    max_peak_memory_mb: float | None,
) -> list[str]:
    violations: list[str] = []
    for result in results:
        chapters = int(result["chapter_count"])
        if max_query_p95_ms is not None and float(result["query_p95_ms"]) > max_query_p95_ms:
            violations.append(f"{chapters} 章 query p95={result['query_p95_ms']}ms > {max_query_p95_ms}ms")
        if (
            max_incremental_refresh_ms is not None
            and float(result["incremental_refresh_ms"]) > max_incremental_refresh_ms
        ):
            violations.append(
                f"{chapters} 章 incremental refresh={result['incremental_refresh_ms']}ms"
                f" > {max_incremental_refresh_ms}ms"
            )
        peak_mb = int(result["peak_memory_bytes"]) / (1024 * 1024)
        if max_peak_memory_mb is not None and peak_mb > max_peak_memory_mb:
            violations.append(f"{chapters} 章 peak memory={peak_mb:.2f}MiB > {max_peak_memory_mb}MiB")
    return violations


if __name__ == "__main__":
    raise SystemExit(main())
