# 性能基线

本页记录可重复执行的容量基线，不是跨硬件 SLA。Search 运行时使用 SQLite FTS5；增量刷新只更新 source manifest 发生变化的文档，向量评分只加载词法候选。

## 2026-07-18 基线

环境：macOS 26.5.2、arm64、Python 3.12.13；每个规模查询 20 次。临时 workspace 通过真实 Mock Creation Session 生成并审核 10/100/500 章，因此 chapter plan 具有正式 lifecycle authority。

| 章节 | 索引文档 | 初次刷新 | 单源增量刷新 | 查询 p50 | 查询 p95 | Python 峰值内存 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 14 | 75.255 ms | 21.742 ms | 2.573 ms | 3.338 ms | 0.19 MiB |
| 100 | 104 | 721.493 ms | 128.794 ms | 15.072 ms | 16.591 ms | 1.48 MiB |
| 500 | 504 | 4014.535 ms | 632.111 ms | 27.324 ms | 28.252 ms | 7.13 MiB |

CI 使用宽松于本机基线的回归阈值：每个规模 query p95 不超过 250 ms、单源增量刷新不超过 5000 ms、`tracemalloc` 峰值不超过 64 MiB。阈值用于发现数量级退化，不应用于比较不同开发机的细微差异。

复现命令：

```bash
python scripts/benchmark_search.py --sizes 10,100,500 --query-runs 20
```

脚本输出 JSON；可用 `--output` 保存报告，并通过 `--max-query-p95-ms`、`--max-incremental-refresh-ms`、`--max-peak-memory-mb` 启用阻断门禁。
