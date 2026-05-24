# 外部 Agent 集成说明

本文档描述 WriterYang 面向 openclaw 或其他外部 agent 的稳定 CLI 调用约定。

## 基本约定

使用 `--project` 指定小说项目目录，使用 `--json` 获取机器可读输出。自动化场景建议同时传入 `--quiet`，避免混入人类可读文本。

```bash
novel status --project ./rain-station --json --quiet
```

子进程调用建议：

- 退出码 `0` 表示成功。
- 非零退出码表示失败。
- 传入 `--json` 时，只解析 stdout 的 JSON。
- 不要抓取人类可读文本。
- 不要把真实 API Key 作为 CLI 参数传入；请使用 `config/agents.yaml` 中声明的环境变量名。

## 稳定命令

项目创建和查看：

```bash
novel init "雨夜旧车站" --project ./rain-station --json --quiet
novel validate --project ./rain-station --json --quiet
novel status --project ./rain-station --json --quiet
novel doctor --project ./rain-station --json --quiet
```

生成流程：

```bash
novel inspire "雨夜旧车站里传来广播声" --project ./rain-station --provider mock --json --quiet --overwrite
novel canon suggest --project ./rain-station --provider mock --json --quiet --output canon-proposal.json
novel canon apply canon-proposal.json --project ./rain-station --json --quiet
novel canon show --project ./rain-station --json --quiet
novel plan-chapter 1 --project ./rain-station --provider mock --json --quiet
novel write-chapter 1 --project ./rain-station --provider mock --json --quiet
novel polish-chapter 1 --project ./rain-station --provider mock --json --quiet
novel audit-chapter 1 --project ./rain-station --provider mock --json --quiet
novel generate-chapter 1 --project ./rain-station --provider mock --json --quiet
```

状态、导出和编排：

```bash
novel propose-state-update 1 --project ./rain-station --provider mock --json --quiet
novel apply-state-update 1 --project ./rain-station --json --quiet
novel accept-chapter 1 --project ./rain-station --json --quiet
novel export markdown --project ./rain-station --include-unaccepted --json --quiet
novel export docx --project ./rain-station --include-unaccepted --json --quiet
novel ask "请为第1章生成章节计划" --project ./rain-station --provider mock --json --quiet
```

## JSON 输出格式

成功命令返回 `ok: true`：

```json
{
  "ok": true,
  "command": "status",
  "status": {
    "title": "雨夜旧车站",
    "latest_chapter": 0,
    "inspiration_exists": true,
    "character_count": 1,
    "location_count": 1,
    "item_count": 1,
    "timeline_event_count": 0,
    "latest_run_log": null,
    "latest_run_summary": null
  }
}
```

失败命令返回 `ok: false`：

```json
{
  "ok": false,
  "error": {
    "type": "planning_error",
    "code": "planning_error",
    "message": "memory/inspiration.md is missing; run novel inspire first",
    "exit_code": 1
  }
}
```

## 稳定错误码

`error.code` 与 `error.type` 保持一致，当前稳定值包括：

- `audit_error`
- `canon_error`
- `drafting_error`
- `export_error`
- `inspiration_error`
- `migration_error`
- `orchestrator_error`
- `planning_error`
- `polishing_error`
- `project_read_error`
- `revision_error`
- `search_error`
- `state_update_error`
- `validation_failed`
- `workflow_error`
- `workspace_exists`
- `doctor_failed`
- `error`

`novel doctor --json` 会在 `error_codes` 字段返回同一份错误码说明。

## 常见退出码

- `0`：成功。
- `1`：命令失败、校验失败、文件缺失、输入非法或 provider 错误。
- `2`：`argparse` 参数解析错误。

大多数命令级错误在 `--json` 模式下会以结构化 JSON 写到 stdout。参数解析错误发生在命令分发前，仍由 `argparse` 输出。

## Shell Completion

WriterYang 可以输出基础 shell completion 脚本：

```bash
novel completion bash > ~/.local/share/bash-completion/completions/novel
novel completion zsh > ~/.zfunc/_novel
novel completion fish > ~/.config/fish/completions/novel.fish
```

## Doctor

`novel doctor` 检查本地 Python 版本、关键依赖、项目结构、schema validation，以及 `config/agents.yaml` / `config/embeddings.yaml` 声明的环境变量是否存在。它只输出环境变量名和是否设置，不输出真实值。

## openclaw 调用示例

Python 子进程示例：

```python
import json
import subprocess

cmd = [
    "novel",
    "ask",
    "请为第1章生成章节计划",
    "--project",
    "./rain-station",
    "--provider",
    "mock",
    "--json",
    "--quiet",
]
completed = subprocess.run(cmd, text=True, capture_output=True)
payload = json.loads(completed.stdout)
if completed.returncode != 0 or not payload.get("ok"):
    raise RuntimeError(payload["error"]["message"])
print(payload)
```

Shell 示例：

```bash
novel status --project ./rain-station --json --quiet
novel ask "请审核第1章一致性" --project ./rain-station --provider mock --json --quiet
```

## 轻量 Tool Manifest

`docs/openclaw_tool_manifest.json` 提供了一个非 MCP 的轻量工具描述，列出推荐调用方式。

当前项目还没有实现完整 MCP server 或云端 API。
