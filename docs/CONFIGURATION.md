# 配置参考

> 适用版本：0.1.x；最近核验：2026-07-18

## 1. 配置优先级

模型调用按 `task -> profile -> default` 解析 `config/agents.yaml`。Provider 读取配置中声明的 `api_key_env`、`base_url_env`，再从当前进程环境变量和项目根目录 `.env` 合并取值；进程环境变量优先于 `.env`。YAML 只保存环境变量名和非密钥参数。

推荐用 Web UI 初始引导配置 `default`，只有确实需要不同模型或容量时才增加 profile/task patch。完整字段和继承示例见 `MODEL_CONFIG_BEST_PRACTICES.md`。

## 2. API Key 与 `.env`

本项目面向可信单用户本地运行，按产品决策允许在 `<project>/.env` 明文保存 API Key，以便维护和查询。Setup 会保留既有键、写前创建本地备份，并尽量把文件权限设为 `0600`。

必须遵守：

- `.env*` 与 `*.bak_*` 保持在 `.gitignore`；
- 不把 key 写入 YAML、JSON、Markdown、命令参数、日志、issue 或测试 fixture；
- 不把包含 `.env` 的项目目录同步到不可信云盘或备份介质；
- 共享项目之前检查并删除 `.env` 与备份；
- 对密钥敏感度较高的场景只使用进程环境变量，不在 `.env` 落盘。

## 3. `config/agents.yaml`

关键字段：

| 字段 | 含义 |
| --- | --- |
| `provider` | `openai`、`openai_compatible`、`deepseek`、`zai` 或测试用 `mock` |
| `model` | Provider 模型名 |
| `api_key_env` | API Key 对应的环境变量名 |
| `base_url_env` | OpenAI-compatible Base URL 环境变量名 |
| `max_context_tokens` | 上下文窗口预算 |
| `max_tokens` | 单次最大输出 token |
| `timeout_seconds` | HTTP 超时 |
| `max_retries` | Provider 重试次数 |
| `json_response_format` | `auto`、`json_object` 或 Provider 支持的 JSON Schema 模式 |

`temperature`、`thinking.type`、`reasoning` 属于 task 业务参数。配置经 Pydantic strict schema 校验，未知字段会被拒绝。

## 4. `config/embeddings.yaml`

关键词 FTS 不依赖外部 API。语义检索需要配置 active provider、model、API 环境变量名、可选 `dimensions`、`batch_size`、timeout 和 retry。`local_hash` 仅用于测试，不代表生产语义质量。

索引刷新：

```bash
novel index status --path ./my-novel
novel index refresh --path ./my-novel
novel index refresh --path ./my-novel --with-embeddings
```

## 5. 运行环境变量

| 变量 | 默认值 | 用途 |
| --- | ---: | --- |
| `WRITERYANG_MODEL_IO_MODE` | `metadata` | `metadata` 或显式隐私敏感的 `full` Model I/O 记录 |
| `WRITERYANG_MODEL_IO_MAX_FILES` | 受代码默认控制 | Model I/O 文件数量上限 |
| `WRITERYANG_MODEL_IO_MAX_BYTES` | 受代码默认控制 | Model I/O 总体积上限 |
| `WRITERYANG_RUN_MAX_COUNT` | `500` | terminal workflow run 最大保留数量 |
| `WRITERYANG_RUN_MAX_AGE_DAYS` | `90` | terminal workflow run 最大保留天数 |
| `WRITERYANG_WEB_MAX_BODY_BYTES` | `33554432` | Web request body 上限 |
| `WRITERYANG_WEB_ACCESS_LOG` | 未启用 | 设为真值时输出本地访问日志 |

Provider API 环境变量名由 YAML 决定，不要求固定为 `OPENAI_API_KEY`。

## 6. Web 配置

Web 普通模式只接受 `127.0.0.1`、`localhost` 或 `::1`。`0.0.0.0`、局域网地址和域名会在启动前被拒绝：

```bash
novel web --path ./my-novel --host 127.0.0.1 --port 8765
```

## 7. 配置诊断

```bash
novel doctor --project ./my-novel --json --quiet
novel validate --path ./my-novel
```

`doctor` 只报告所需环境变量是否存在，不返回其明文值，同时展示最近 run 状态、磁盘占用和 retention 配置。
