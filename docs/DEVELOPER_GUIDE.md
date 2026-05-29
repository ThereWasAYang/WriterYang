# WriterYang 开发者指南

本文是代码开发入口。目标读者是第一次接触本项目的人类开发者或大模型 Agent。读完后应能判断代码放在哪里、数据如何流动、如何新增功能、如何定位 BUG、如何安全重构。

## 1. 项目定位

WriterYang 是一个本地优先、文件驱动的 AI 辅助中文长篇小说写作工具。它不追求 AI 独自完成长篇小说，而是围绕“作者与工具协作”设计：

- 作者用自然语言表达本次创作意图。
- Orchestrator / Session 层先和作者协商创作范围和大纲。
- 作者批准大纲后，系统调度 Plot / Writer / Polish / Audit / StateUpdate 等内部 Agent。
- 内部 Agent 必须执行任务，不应反问上游 Agent；必要时做保守假设或返回结构化错误。
- 作者认可最终内容后，章节才会 accepted / archived；归档内容默认不可原地篡改。

项目状态保存在可编辑的 Markdown / JSON / YAML 文件中。CLI 和 Web UI 都必须复用 `src/novel/core/` 的业务逻辑。

## 2. 代码分层

```text
src/novel/
  cli.py                # CLI 参数解析、文本/JSON 输出、命令锁
  web_api.py            # 本地 Web API，包装 core service
  web_server.py         # 静态页面和 API 的本地 HTTP server
  web_static/index.html # Vanilla HTML/JS 前端
  core/                 # 可复用业务逻辑，CLI/Web 共用
  prompts/              # Agent system prompt 模板
schemas/                # 由 Pydantic 导出的 JSON Schema
examples/               # 可验证示例项目
tests/                  # 单元、集成、Web、真实 API 标记测试
docs/                   # 用户、集成、开发文档
```

核心原则：

- CLI 只负责参数解析、输出格式、调用 core。
- Web API 只负责 HTTP 请求/响应、脱敏、项目锁、调用 core。
- 业务规则放在 core service。
- schema 写在 `core/schemas.py`。
- prompt 纯文本模板放在 `src/novel/prompts/`，组装逻辑放在对应 core service。
- 所有写文件操作必须走 `core/io.py` 的 atomic write 和必要备份。
- 修改项目的 CLI/Web 入口必须使用 `ProjectLock`，避免并发写同一 workspace。
- API Key 只能放环境变量，项目文件只保存环境变量名。

## 3. Workspace 文件结构

`novel init` 创建的小说项目大致如下：

```text
project.yaml
config/
  agents.yaml
  embeddings.yaml
memory/
  inspiration.md
  inspiration.json
  style_guide.md
  canon/
    characters.json
    locations.json
    items.json
    world.json
    hidden_truths.json
    foreshadowing.json
  state/
    current_state.json
    timeline.json
  chapters/
    001/
      plan.json
      plan.md
      draft.md
      polished.md
      audit.json
      state_update_proposal.json
      state_update_apply_log.json
      revision_log.json
      metadata.json
      context_report*.json
  sessions/
  archive/
runs/
  run_*.json
  provider_calls.jsonl
  provider_usage.json
  model_io/
  agent_output_violations/
exports/
  novel.md
  novel.docx
  export_manifest.json
```

开发时要区分三类文件：

- 作者可编辑记忆：`memory/inspiration.md`、`memory/style_guide.md`、canon/state/timeline JSON。
- Agent 产物：plan/draft/polished/audit/state proposal/revision log/context report。
- 调试与统计：`runs/` 下的 run log、provider log、完整模型 I/O、输出守卫日志。

## 4. 推荐工作流和底层命令

作者入口推荐使用 session/orchestrator：

```text
inspire -> canon suggest/apply -> session start -> approve-outline -> session run -> user review -> session accept/archive -> export
```

`session run` 的自动修复分两层：正文实现问题先通过 Revision Agent 生成 `polished.vN.md`，再提升为当前 `polished.md` 并重跑 audit；连续失败或问题明显来自章节计划时，回退 Plot Agent 重写本章 `plan.json` 后重新生成正文。超过轮数时 session 状态应停在 `needs_revision`，不要继续显示 `generating`。用户或 Web UI 调用 `session revise-content` 后，也必须执行同一套“版本稿 -> 提升当前稿 -> 重审 -> 重建 state proposal”语义，不能只生成孤立版本文件。

底层命令仍可用于调试：

```text
plan-chapter -> write-chapter -> polish-chapter -> audit-chapter -> propose/apply state update -> accept-chapter
```

常用命令清单：

```bash
novel validate --path <project>
novel doctor --project <project>
novel usage --path <project>
novel session start "写第1章" --path <project> --chapters 1
novel plan-chapter 1 --path <project>
novel write-chapter 1 --path <project>
novel polish-chapter 1 --path <project>
novel audit-chapter 1 --path <project>
novel propose-state-update 1 --path <project>
novel apply-state-update 1 --path <project>
novel export markdown --path <project>
```

开发新能力时应先判断它属于：

- 作者协作层：改 `session.py` / `orchestrator.py` / Web session API。
- 单步 Agent 能力：改对应 core service，例如 `planning.py`、`drafting.py`。
- 项目读写/展示：改 `inspection.py`、`validation.py`、`web_api.py` 或 CLI 输出。
- 基础设施：改 provider、schema、search、io、locking、security。

## 5. 如何新增 CLI 命令

推荐流程：

1. 在 `core/` 新增或扩展 service，定义 options/result dataclass。
2. service 中完成读取、校验、provider 调用、文件写入和返回结果。
3. 在 `cli.py::build_parser()` 增加子命令和参数。
4. 在 `cli.py::main()` 增加分支，调用 core service。
5. 如果命令写项目文件，用 `_command_lock()` 包住。
6. 支持已有集成参数：`--path` / `--project`、`--json`、`--quiet`。
7. 写 CLI 测试，覆盖成功、缺失文件、默认不覆盖、JSON 输出。

CLI 输出约定：

- 人类输出走 `_success()` / `_failure()`。
- 机器输出必须是合法 JSON，不混入说明文字。
- 错误 JSON 必须包含稳定 `error.code`。

## 6. 如何新增 Web API

Web API 在 `src/novel/web_api.py`。新增接口时：

1. 在 `handle_api_request()` 增加路径分发。
2. 请求体读取使用 `_json_body()`。
3. 项目根目录解析使用 `_root_from_query()` 或 `_root_from_body()`。
4. 成功返回 `_success(data)`；失败返回 `_failure(...)`。
5. 写操作用 `_locked_write()`，并复用 core service。
6. 不要把真实 API Key、Authorization、env value 返回前端。
7. 前端只调用 API，不复制业务逻辑。

如果 API 保存文件，必须复用 core 的文件安全语义：默认不覆盖、版本化保存、必要备份、项目锁。

## 7. 如何新增 Core Service

一个 core service 的常见形态：

```python
@dataclass(frozen=True)
class XxxOptions:
    root: Path
    ...

@dataclass(frozen=True)
class XxxResult:
    output_path: Path
    ...

def run_xxx(options: XxxOptions, provider: ModelProvider | None = None) -> XxxResult:
    root = options.root.resolve()
    # 1. 校验参数和输入文件
    # 2. 加载 schema model
    # 3. 构造 prompt / 调用 provider
    # 4. 校验输出
    # 5. atomic write / backup
    # 6. 返回 result
```

要求：

- 读取 JSON/YAML 用 `load_json_model()` / `load_yaml_model()`。
- 写 JSON model 用 `atomic_write_model_json()`。
- 写文本用 `atomic_write_text()`。
- 覆盖已有文件前调用 `backup_if_exists()` 或更具体的备份逻辑。
- 输入和输出 schema 必须写入 `schemas.py` 并补测试。
- Provider 调用必须可注入，测试使用 `MockProvider`。

## 8. 如何新增 Agent

新增 Agent 时至少要改：

- `src/novel/prompts/{agent}_system.txt`：system prompt。
- `core/{agent_service}.py`：options/result、prompt builder、provider 调用、schema 校验、文件写入。
- `core/provider_config.py` 和默认 `config/agents.yaml` 生成逻辑：让 agent 能读取独立配置。
- `core/schemas.py`：如果 Agent 输出结构化 JSON，新增 Pydantic model。
- `tests/`：mock provider 成功、输出不合规、文件安全、CLI/API 集成。

内部 Agent 调用必须使用 `generate_with_output_guard()`，并传入：

- `AgentInvocationContext(agent_name=..., interaction_mode="internal_task")`
- `AgentOutputContract(output_kind="json" | "markdown", target_name=...)`

允许向用户提问的只有用户交互层，例如 orchestrator/session 协商阶段。内部 Agent 被调度后应执行任务，不应反问上游。

## 9. Provider 和模型调用

模型抽象在 `core/providers.py`：

- `ModelRequest`：system prompt、user prompt、可选 context、schema 名称、request_id。
- `ModelResponse`：content、raw_response、token_usage、reasoning_content。
- `ModelProvider`：抽象接口。
- `MockProvider`：测试 provider。
- `OpenAICompatibleProvider`：OpenAI Chat Completions 兼容 provider，包含 DeepSeek / ZAI 适配。
- `LoggingModelProvider`：包裹真实和 mock provider，写 `runs/model_io/`。

Agent provider 创建走 `core/provider_config.py::create_agent_provider()`。不要在业务 service 里直接读取 API Key。

调试文件：

- `runs/provider_calls.jsonl`：轻量调用元数据。
- `runs/provider_usage.json`：累计 token 用量。
- `runs/model_io/{request_id}.json`：完整 prompt、payload、response。
- `runs/agent_output_violations/{request_id}.json`：输出契约违规。

这些日志包含创作内容和隐藏设定，只用于本地 debug，不应提交。

## 10. Prompt 组装规则

Prompt 模板只放 system prompt。user prompt 由对应 service 的 `build_*_user_prompt()` 组装，通常包括：

- project 基本信息。
- inspiration / style guide。
- canon summary 或完整 canon 文件。
- current_state / timeline。
- 当前章节 plan / draft / polished / audit。
- 用户 instruction / input 文件内容。
- 可选 `ContextBundle.render_for_prompt()`。

详见 `docs/AGENT_PROMPT_ASSEMBLY.md`。

## 11. 如何定位 BUG

推荐顺序：

1. 先看 CLI/Web 返回的错误码和路径。
2. 运行 `novel validate --path <project>`。
3. 运行 `novel doctor --project <project> --json`。
4. 如果是模型问题，看 `runs/model_io/index.jsonl` 和对应 `runs/model_io/{request_id}.json`。
5. 如果输出被守卫拦截，看 `runs/agent_output_violations/`。
6. 如果是 provider 调用失败，看 `runs/provider_calls.jsonl`。
7. 如果是检索上下文问题，看 `memory/chapters/{NNN}/context_report*.json`。
8. 如果是 state/timeline 问题，看 `state_update_proposal.json`、`state_update_apply_log.json` 和备份文件。
9. 写最小复现测试，再修业务逻辑。

常用命令：

```bash
conda run -n py312 pytest tests/test_<area>.py -q
conda run -n py312 pytest -m "not real_api and not web_e2e" -q
conda run -n py312 ruff check .
conda run -n py312 mypy src
```

## 12. 重构准则

重构前先确认：

- CLI 和 Web 是否仍共用 core service。
- 是否改变了 workspace 文件格式；若改变，需要 migration 和 JSON Schema。
- 是否改变 Agent 输出；若改变，需要 schema、prompt、repair、validation 和 tests。
- 是否改变写文件路径；若改变，需要默认不覆盖、atomic write、backup、lock。
- 是否改变 provider payload；若改变，需要 mock 测试和不泄漏 API Key 测试。

禁止事项：

- 不要在项目文件中写真实 API Key。
- 不要绕过 `core/io.py` 直接写重要文件。
- 不要让前端保存逻辑绕过 core service。
- 不要让内部 Agent 输出问题落盘成正式 artifact。
- 不要原地修改 archived session 内容。

## 13. 文档维护

修改代码时同步检查：

- 新模块或新函数：更新 `docs/CODEBASE_REFERENCE.md`。
- 新 Agent 或 prompt：更新 `docs/AGENT_PROMPT_ASSEMBLY.md`。
- 新 CLI/Web API：更新 `README.md`、`docs/INTEGRATION.md` 和相关测试。
- 新 schema：运行 `novel schema export --output schemas`，并更新数据文档。
