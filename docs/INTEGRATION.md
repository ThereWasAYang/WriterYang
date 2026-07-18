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
- 真实项目应配置 `config/agents.yaml` 顶层 `default` API，并默认传 `--provider config`；`--provider mock` 只用于外部工具的离线测试。
- 所有公开写操作都通过同一 strict typed Command Bus；成功 JSON 会携带 `command_type`、`command_id`、`request_id`、`workflow_run_id`、`changed_paths` 和累计 `budget_usage`，外部 Agent 不应绕过 CLI/Web 直接调用 mutation service。

## 稳定命令

项目创建和查看：

```bash
novel init "雨夜旧车站" --project ./rain-station --json --quiet
novel validate --project ./rain-station --json --quiet
novel schema export --project ./rain-station --output ./schemas --json --quiet
novel status --project ./rain-station --json --quiet
novel usage --project ./rain-station --json --quiet
novel doctor --project ./rain-station --json --quiet
novel show characters --project ./rain-station --json --quiet
novel show timeline --project ./rain-station --json --quiet
novel show state --project ./rain-station --json --quiet
novel show canon --project ./rain-station --json --quiet
novel web --project ./rain-station --host 127.0.0.1 --port 8765
```

生成流程：

```bash
novel inspire "雨夜旧车站里传来广播声" --project ./rain-station --provider config --json --quiet --overwrite
novel canon suggest --project ./rain-station --provider config --json --quiet --output canon-proposal.json
novel canon apply canon-proposal.json --project ./rain-station --json --quiet
novel canon validate --project ./rain-station --json --quiet
novel canon show --project ./rain-station --json --quiet
novel session start "写第1章" --chapters 1 --project ./rain-station --provider config --json --quiet
```

状态、导出和编排：

```bash
novel chapter-memory show 1 --project ./rain-station --json --quiet
novel chapter-memory generate 1 --project ./rain-station --provider config --force --json --quiet
novel chapter-memory rebuild --project ./rain-station --provider config --missing-only --json --quiet
novel export markdown --project ./rain-station --json --quiet
novel export docx --project ./rain-station --json --quiet
novel ask "请为第1章生成章节计划" --path ./rain-station --provider config --json --quiet
novel ask "请为第1章生成章节计划" --path ./rain-station --provider config --json --quiet
novel ask --path ./rain-station --confirm <workflow_run_id> --json --quiet
novel ask "第2章 event_x 其实是回忆，不是当前行动" --path ./rain-station --json --quiet
novel ask --path ./rain-station --confirm <workflow_run_id> --json --quiet
novel memory-repair apply repair_20260530_010101_000001 --project ./rain-station --json --quiet
novel session start "写第1章" --chapters 1 --project ./rain-station --provider config --json --quiet
novel session show <session_id> --project ./rain-station --json --quiet
novel session revise-outline <session_id> --project ./rain-station --provider config --instruction "降低伏笔暴露强度" --json --quiet
novel session approve-outline <session_id> --project ./rain-station --json --quiet
novel session run <session_id> --project ./rain-station --provider config --json --quiet
novel session revise-content <session_id> --project ./rain-station --provider config --from-audit --json --quiet
novel session revise-audit <session_id> <event_id> --project ./rain-station --provider config --instruction "这是回忆段落" --json --quiet
novel session retry-rewrite <session_id> <event_id> --project ./rain-station --provider config --json --quiet
novel session undo-rewrite <session_id> <event_id> --project ./rain-station --provider config --json --quiet
novel session accept <session_id> --project ./rain-station --provider config --json --quiet
novel session archive <session_id> --project ./rain-station --json --quiet
```

搜索索引和记忆检索：

```bash
novel index status --project ./rain-station --json --quiet
novel index refresh --project ./rain-station --json --quiet
novel index rebuild --project ./rain-station --json --quiet
novel index refresh --project ./rain-station --with-embeddings --embedding-provider dashscope --json --quiet
novel search "车站广播" --project ./rain-station --type all --limit 10 --json --quiet
novel search "未解决线索" --project ./rain-station --type chapter_memory --limit 10 --json --quiet
novel search "角色是否知道旧案真相" --project ./rain-station --use-vector --embedding-provider dashscope --json --quiet
```

Web API 也提供同等修复入口：`POST /api/chapter-memory/generate` 生成或强制重建单章记忆，`POST /api/chapter-memory/rebuild` 默认批量处理缺失或 stale 的章节记忆。

## 稳定 Web API 契约

业务 mutation 的 canonical typed 入口是 `POST /api/command`：

```json
{
  "path": "/absolute/path/to/project",
  "command": {"type": "project.status"},
  "confirmed": false
}
```

`command` 必须匹配 `PublicCommand` discriminator union，未知字段会被拒绝。每个 command 的输入 schema、读写/锁/确认策略和错误目录由 `core/command_registry.py` 的 `CommandSpec` 唯一登记。`GET /api/openapi.json` 返回 OpenAPI 3.1，`GET /api/commands` 返回逐 command schema/policy catalog。

页面专用读取/projection endpoint 可以继续服务本地 UI，但不得实现业务状态转换；所有 mutation 最终进入同一 Command Bus。Web 失败 envelope 包含稳定 `code`、安全 `message`、`details`、`request_id`、`http_status` 和 `retryable`。未知异常使用 HTTP 500 `internal_error`，stack 只写本地日志。

以下 Web API 可供本地自动化和外部 Agent 使用：

- `GET /api/projects?root=<dir>`：列出 `root` 自身或其一级子目录中的 WriterYang 项目。
- `GET /api/session?path=<project>&session_id=<id>`：读取 session、progress、audit summary、rewrite events 和后台管理事件。
- `POST /api/session/start`、`approve-outline`、`run`、`revise-content`、`accept`、`archive`：通过同一 Command Bus 驱动完整创作生命周期。
- `POST /api/setup/open-web`：只返回建议打开的本地 Web URL 和 `opened=false`，不会替调用方打开浏览器。

直接 `plan/write/polish/audit/generate-chapter` Web 端点已删除。接口不会返回真实 API Key；长时间运行的创作任务使用 Session API，以便获得 progress、取消和自动修订状态。

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

`ask` 的默认成功结果为 `status=proposed`，`proposal.command` 是 typed public command，且包含 `risk`、`estimated_model_calls`、`requires_confirmation` 与 `budget`。确认时使用不带 `request` 的 `novel ask --confirm <workflow_run_id>`；CLI 直接加载该 run 的 `proposal.json`，执行结果位于 `execution`。确认模式拒绝额外 request，既不会静默忽略新文本，也不会重新路由；proposal 与 execution 共用同一个 `workflow_run_id` 和累计 `budget_usage`。Creation/Revision Session 后续 command 会从 session 恢复该 ID 与预算，在 `runs/{workflow_run_id}/` 下继续追加 trace。

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
- `memory_repair_error`
- `orchestrator_error`
- `planning_error`
- `polishing_error`
- `project_read_error`
- `revision_error`
- `search_error`
- `session_error`
- `setup_guide_error`
- `state_update_error`
- `usage_error`
- `validation_failed`
- `web_error`
- `workflow_error`
- `workspace_exists`
- `doctor_failed`
- `secret_detected`
- `invalid_env_example`
- `unsafe_config_secret`
- `project_locked`
- `atomic_write_failed`
- `backup_failed`
- `error`

`novel doctor --json` 会在 `error_codes` 字段返回同一份错误码说明。

## 常见退出码

- `0`：成功。
- `1`：命令失败、校验失败、文件缺失、输入非法或 provider 错误。
- `2`：`argparse` 参数解析错误。

写入类命令遇到同一项目已有 `.writeryang.lock` 时会返回 `project_locked`。锁记录 PID、process start time、host、workflow/command 和 heartbeat；长任务持续刷新 heartbeat，只有进程消失、process identity 不匹配或 heartbeat 超时才会回收。自动回收会追加 `runs/lock_events.jsonl`。只读命令不受锁影响。

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
novel ask "请审核第1章一致性" --path ./rain-station --provider config --json --quiet
```

## 常见生成文件

外部 Agent 可以读取这些生成文件做状态同步，但不要把它们当作手写源文件直接覆盖：

- `memory/search_index.json` / `memory/search_index.sqlite`：搜索索引，可重建。
- `memory/chapters/{chapter}/state_update_proposal.json`：章节状态更新建议；生成 proposal 时不直接修改 `current_state.json` 或 `timeline.json`。
- `memory/chapters/{chapter}/revision_log.json`：章节修订记录。
- `exports/export_manifest.json`：导出清单。
- `memory/chapters/{chapter}/metadata.json`：章节 metadata，`accepted` 状态用于判断是否可进入默认导出。

## 轻量 Tool Manifest

`docs/openclaw_tool_manifest.json` 提供了一个非 MCP 的轻量工具描述，列出推荐调用方式。

当前项目还没有实现完整 MCP server 或云端 API。
