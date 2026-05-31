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
  web_static/           # Vanilla Web 前端静态资源
    index.html          # 页面结构
    app.css             # 页面样式
    app.js              # API 调用和交互逻辑
  core/                 # 可复用业务逻辑，CLI/Web 共用
  prompts/              # Agent system prompt 模板
schemas/                # 由 Pydantic 导出的 JSON Schema
examples/               # 可验证示例项目
tests/                  # 单元、集成、Web、真实 API 标记测试
docs/                   # 用户、集成、开发文档
skills/                 # 给人类开发者和外部 Agent 使用的工作流技能
scripts/                # 本地质量门禁、smoke、排障、provider ping 等工具脚本
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
.env                 # [引导后] 本地私密 API 配置；git/Web 文件树/导出/日志都必须排除
config/
  agents.yaml        # Agent 默认 API 和各 Agent 覆盖参数；只保存 env 名
  embeddings.yaml    # embedding provider 配置；只保存 env 名
memory/
  inspiration.md     # init 创建空文件；inspire 后写入弱总纲
  inspiration.json   # [可选] inspire 结构化输出；并非所有流程都会生成
  style_guide.md     # init 创建空文件；作者可手动维护文风规则
  canon/
    characters.json  # init 创建空列表；canon apply 或作者确认后更新
    locations.json   # init 创建空列表；canon apply 或作者确认后更新
    items.json       # init 创建空列表；canon apply 或作者确认后更新
    world.json       # init 创建空列表；canon apply 或作者确认后更新
    hidden_truths.json      # init 创建空列表；作者内部信息，不进读者可见摘要
    foreshadowing.json      # init 创建空列表；伏笔和 payoff 关系
  state/
    current_state.json      # init 创建空状态；state proposal apply 后更新
    timeline.json           # init 创建空时间线；使用 narrative/story 双轨
  chapters/
    001/
      plan.json             # [运行时] plan-chapter/session outline approval 后生成
      plan.md               # [运行时] 面向作者阅读的章节计划
      draft.md              # [运行时] write-chapter/session run 后生成
      polished.md           # [运行时] polish/revision 后生成，export 默认使用
      audit.json            # [运行时] audit-chapter/session run 后生成
      state_update_proposal.json     # [运行时] propose-state-update/session accept 前生成
      state_update_apply_log.json    # [运行时] apply-state-update/session accept 后生成
      revision_log.json     # [运行时] revise/edit 保存版本后生成
      metadata.json         # [运行时] accept 后记录章节状态
      context_report*.json  # [运行时] 开启检索上下文后生成
  sessions/                 # [运行时] session start 后生成会话目录
  archive/                  # [运行时] session archive 后生成不可原地篡改归档
runs/
  run_*.json                # [运行时] generate/session 工作流日志
  provider_calls.jsonl      # [运行时] provider 调用元数据，不含 API Key
  provider_usage.json       # [运行时] token 汇总缓存
  model_io/                 # [运行时] 完整模型 I/O，本地调试用，不提交
  agent_output_violations/  # [运行时] 输出契约违规日志
exports/
  novel.md                  # [运行时] export markdown 后生成
  novel.docx                # [运行时] export docx 后生成
  export_manifest.json      # [运行时] export 后记录源章节 hash 和导出记录
```

标注 `[运行时]` 的文件/目录不是空项目必须立刻存在的内容，而是在相应 workflow 第一次执行后创建。开发文档把它们列出，是为了说明稳定位置和文件保护规则。

开发时要区分三类文件：

- 作者可编辑记忆：`memory/inspiration.md`、`memory/style_guide.md`、canon/state/timeline JSON。
- Agent 产物：plan/draft/polished/audit/state proposal/revision log/context report。
- 调试与统计：`runs/` 下的 run log、provider log、完整模型 I/O、输出守卫日志。

记忆分层：

- 长期设定记忆：`memory/canon/*.json`，包括角色、地点、物品、世界规则、隐藏真相和伏笔。Canon 修改必须走 proposal/apply 或明确的 memory repair。
- 动态状态记忆：`memory/state/current_state.json` 和 `memory/state/timeline.json`。章节通过 state update proposal 变更，timeline 使用 narrative/story 双轨结构。
- 创作过程记忆：`memory/chapters/{NNN}/`、`memory/sessions/{session_id}/` 和 `memory/archive/`。未归档内容可版本化修订；归档内容默认不可原地改。
- 检索索引：`memory/search_index.json`、`memory/search_index.sqlite`、`memory/search_index_manifest.json`。FTS 可自动刷新；真实 embedding 向量只在显式 vector 检索或手动 `--with-embeddings` 刷新时调用外部 API，不用 `local_hash` 冒充真实语义检索。
- 调试记忆：`runs/model_io/`、`runs/provider_calls.jsonl`、`runs/agent_output_violations/`。这些文件可用于定位问题，但可能包含小说正文和隐藏设定，不应提交。

## 4. Workflow Skills 和工具脚本

仓库级技能放在 `skills/`，用于让人类开发者或外部大模型 Agent 按固定流程工作：

- `writeryang-maintainer`：代码修改、测试、文档同步和 GitHub 同步规则。
- `writeryang-workflow-debug`：session / audit / state / provider 失败的证据收集顺序。
- `writeryang-real-api-smoke`：真实 provider smoke 的安全流程。
- `writeryang-web-ui-qa`：Web UI 变更后的浏览器验证流程。
- `writeryang-release`：发版前检查和发布流程。

Agent 级技能也放在 `skills/`，每个 Agent 单独保存、按需加载：

- `writeryang-agent-orchestrator`：用户协商、session 状态机和 handoff 边界。
- `writeryang-agent-inspiration`：灵感输入、弱方向输出和 inspiration artifact 边界。
- `writeryang-agent-canon`：canon proposal、stable ID、hidden truth 和 apply 边界。
- `writeryang-agent-plot`：ChapterPlan 输入、输出、引用校验和 plan artifact 边界。
- `writeryang-agent-writer`：draft 输入、Markdown/front matter 输出和内部任务输出契约。
- `writeryang-agent-polish`：polished 输出、edit mode 和事实保持边界。
- `writeryang-agent-audit`：AuditReport、deterministic checks 和 severity policy。
- `writeryang-agent-state-update`：state proposal、timeline update 和 acceptance gate。
- `writeryang-agent-revision`：版本化修订、revision log 和 session 修订语义。

不要把多个 Agent 的详细规则混到一个 skill 中；定位到具体 Agent 后再加载对应 skill。创意类能力不能 skill 化或脚本化：不要把剧情设计、正文表达、人物塑造写成固定模板。Agent skill 只记录安全边界、产物契约、排查顺序和测试入口。

渐进式披露原则：

1. 先加载通用维护或排障 skill，例如 `writeryang-maintainer` 或 `writeryang-workflow-debug`。
2. 通过日志和 artifact 定位到具体 Agent 后，只加载对应 `writeryang-agent-*` skill。
3. 不把所有 Agent skill 一次性塞进上下文，避免 token 浪费和职责污染。
4. Skill 只约束确定性流程、文件边界、输出契约和调试顺序；创意内容仍交给 prompt、用户意图和模型能力动态生成。
5. 如果开发者只改 Web UI、脚本或发布流程，不加载创意 Agent skill。

确定性脚本放在 `scripts/`，只组合 CLI/API，不复制 core 业务逻辑：

- `install_writeryang.py`：一键创建独立 conda/venv 环境，并用 editable 模式安装工具，确保源码更新后重启 Web UI 即可生效。
- `check_local.py`：本地复现 CI 质量门禁。
- `smoke_session.py`：用 CLI 跑完整 mock/config Session smoke。
- `debug_bundle.py`：生成脱敏排障包。
- `provider_ping.py`：检查 agent/embedding provider 配置和可选真实调用。
- `webui_smoke.py`：用 Playwright 跑最小 Web UI 流程。
- `capture_webui_guide_screenshots.py`：用 mock 临时项目重新生成 Web UI 小白指南截图。
- `project_health.py`：聚合 validate/status/usage/audit/session/export 状态。

## 5. 推荐工作流和底层命令

作者入口推荐使用 session/orchestrator：

```text
inspire -> canon suggest/apply -> session start -> approve-outline -> session run -> user review -> session accept/archive -> export
```

用户自然语言输入不能假定规范。作者可能随手输入、遗漏上下文、使用口语、缩写或错别字。任何高风险路由，例如“这次修改应回到 Plot、Writer/Polish 还是 Revision”“是否修改 timeline/state/canon”“是否接受/归档”，都不能只靠硬编码关键词判断。可以保留关键词启发式作为兼容或 fallback，但 fallback 不得执行 apply、archive、accepted、state/timeline/canon 写入等高风险动作。主路径应使用 Orchestrator 的结构化决策、schema 校验、明确错误提示和保守 fallback，并为这些路径添加测试。

`novel ask` 的非 dry-run 主路径应先调用 Orchestrator 输出 `AskIntentDecision`，再根据结构化 task 执行：创建 Session、生成 memory repair proposal、导出或查看状态。自然语言中的“确认/应用 repair”只有在模型结构化决策明确为 `memory_repair_apply` 且带有 `repair_id` 时才允许执行；provider 不可用或 fallback 场景必须提示用户使用显式命令 `novel memory-repair apply <repair_id>`。

`session run` 的自动修复分两层：正文实现问题先通过 Revision Agent 生成 `polished.vN.md`，再提升为当前 `polished.md` 并重跑 audit；连续失败或问题明显来自章节计划时，回退 Plot Agent 重写本章 `plan.json` 后重新生成正文。每次自动打回必须写入 `memory/sessions/{session_id}/rewrite_events.json`，并把打回前的 `polished.md` 快照写入 `memory/sessions/{session_id}/rejections/`。超过轮数时 session 状态应停在 `needs_revision`，不要继续显示 `generating`。用户或 Web UI 调用 `session revise-content` 后，用户意见必须先经 Orchestrator 输出 `RevisionRouteDecision`：剧情级修改走 Plot replan，写作实现级修改走 Writer/Polish rewrite，只有低风险局部表达修改走 Revision patch；随后都必须执行“生成/提升当前稿 -> 重审 -> 重建 state proposal”语义，不能只生成孤立版本文件。

Audit 打回后的人工控制有三个入口：`session revise-audit` 用用户纠正意见重新审核被打回原文；`session retry-rewrite` 基于最新 audit 再次发起打回；`session undo-rewrite` 恢复 rewrite event 的 rejected snapshot 并重跑 audit。三者都只能作用于未归档 session，且必须更新 `rewrite_events.json`、`audit_history` 和 session 状态。

orchestrator 同时承担项目管家职责。自然语言 memory 修复请求必须先由 Orchestrator / Memory Manager 输出结构化 `MemoryRepairDecision`，再生成 `MemoryRepairProposal`，保存到 `memory/repairs/{repair_id}/proposal.json`，由用户确认后再 apply。Memory repair 不得用关键词硬猜目标文件和 JSON Pointer；如果缺少 ID 或定位不安全，proposal 应为空操作并提示用户补充。apply 必须限制白名单文件、使用 JSON Pointer 操作、备份目标文件、atomic write、validate，失败时写 `apply_log.json` 并尽量回滚。

Audit 自动打回也必须走结构化分流：`route_audit_repair()` 根据 `AuditReport` 中的 `source_layer`、`evidence.source`、issue 类型和上下文输出 `AuditRepairRouteDecision`。只有明确指向 plan 层的问题才回退 Plot；正文实现问题走 Writer/Revision；信息不足时返回 manual review，不允许仅因为描述里出现“真相/伏笔/大纲”等词就自动重写大纲。

Web UI 面向普通作者时，Session 面板必须保留完整协商链路：创建大纲、修改大纲、批准大纲、开始写作、自动打回重写记录、Audit 复审/重试打回/撤回、按 Audit/用户意见修订、认可和归档。只读项目检查走 `/api/validate`，复用 `validate_project()`，不应在前端实现校验规则。后台状态、时间线和记忆管理变更必须写 `memory/management_events.jsonl`，Web UI 和 CLI 都要显示最近事件摘要。

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
novel session revise-audit <session_id> <event_id> --path <project> --instruction "这段是回忆"
novel session retry-rewrite <session_id> <event_id> --path <project>
novel session undo-rewrite <session_id> <event_id> --path <project>
novel ask "第2章 event_x 其实是回忆，不是当前行动" --path <project>
novel memory-repair apply <repair_id> --path <project>
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

## 6. 如何新增 CLI 命令

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

## 7. 如何新增 Web API

Web API 在 `src/novel/web_api.py`。新增接口时：

1. 在 `handle_api_request()` 增加路径分发。
2. 请求体读取使用 `_json_body()`。
3. 项目根目录解析使用 `_root_from_query()` 或 `_root_from_body()`。
4. 成功返回 `_success(data)`；失败返回 `_failure(...)`。
5. 写操作用 `_locked_write()`，并复用 core service。
6. 不要把真实 API Key、Authorization、env value 返回前端。
7. 前端只调用 API，不复制业务逻辑。

如果 API 保存文件，必须复用 core 的文件安全语义：默认不覆盖、版本化保存、必要备份、项目锁。

## 7.1 异常和错误处理约定

错误处理分层：

- core service 抛出领域异常，例如 `PlanningError`、`DraftingError`、`AuditError`、`StateUpdateError`、`SearchError`、`MemoryRepairError`。异常消息必须能指导用户下一步，但不能包含 API Key、Authorization 或真实 env 值。
- CLI 捕获领域异常后走 `_failure()`，文本模式输出简短错误，`--json` 输出稳定 `error.code`、`message` 和 `exit_code`。
- Web API 捕获异常后返回 `{ok:false,error:{code,message,details,request_id}}`。路径错误、锁冲突、JSON 错误、权限错误应映射到稳定 code。
- provider 错误使用 `ProviderError` / `EmbeddingError` 子类分类：缺 env、认证失败、rate limit、timeout、network、HTTP、response parse。日志写 error type，不写密钥。
- 写入失败时应保持原文件不被破坏；重要文件覆盖前先备份，state/timeline/canon apply 失败应尽量回滚。
- Agent 输出不合约属于 `AgentOutputContractError`，内部任务会 repair retry 一次；仍失败时不写正式 artifact，只写 violation log。

## 8. 如何新增 Core Service

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

## 9. 如何新增 Agent

新增 Agent 时至少要改：

- `src/novel/prompts/{agent}_system.txt`：system prompt。
- `core/{agent_service}.py`：options/result、prompt builder、provider 调用、schema 校验、文件写入。
- `core/provider_config.py`、`core/setup_guide.py` 和默认 `config/agents.yaml` 生成逻辑：让 agent 先继承顶层 `default` API，再按 Agent 差异覆盖；`mock` 只作为显式测试入口。
- `core/schemas.py`：如果 Agent 输出结构化 JSON，新增 Pydantic model。
- `tests/`：mock provider 成功、输出不合规、文件安全、CLI/API 集成。

内部 Agent 调用必须使用 `generate_with_output_guard()`，并传入：

- `AgentInvocationContext(agent_name=..., interaction_mode="internal_task")`
- `AgentOutputContract(output_kind="json" | "markdown", target_name=...)`

允许向用户提问的只有用户交互层，例如 orchestrator/session 协商阶段。内部 Agent 被调度后应执行任务，不应反问上游。

## 10. Provider 和模型调用

模型抽象在 `core/providers.py`：

- `ModelRequest`：system prompt、user prompt、可选 context、schema 名称、request_id。
- `ModelResponse`：content、raw_response、token_usage、reasoning_content。
- `ModelProvider`：抽象接口。
- `MockProvider`：测试 provider。
- `OpenAICompatibleProvider`：OpenAI Chat Completions 兼容 provider，包含 DeepSeek / ZAI 适配。
- `LoggingModelProvider`：包裹真实和 mock provider，写 `runs/model_io/`。

Agent provider 创建走 `core/provider_config.py::create_agent_provider()`，它会合并项目 `.env` 和当前进程环境。不要在业务 service 里直接读取 API Key。项目初始引导逻辑集中在 `core/setup_guide.py`，CLI/Web 只负责采集输入和展示结果。

调试文件：

- `runs/provider_calls.jsonl`：轻量调用元数据。
- `runs/provider_usage.json`：累计 token 用量。
- `runs/model_io/{request_id}.json`：完整 prompt、payload、response。
- `runs/agent_output_violations/{request_id}.json`：输出契约违规。

这些日志包含创作内容和隐藏设定，只用于本地 debug，不应提交。

## 11. Prompt 组装规则

Prompt 模板只放 system prompt。user prompt 由对应 service 的 `build_*_user_prompt()` 组装，通常包括：

- project 基本信息。
- inspiration / style guide。
- canon summary 或完整 canon 文件。
- current_state / timeline。
- 当前章节 plan / draft / polished / audit。
- 用户 instruction / input 文件内容。
- 可选 `ContextBundle.render_for_prompt()`。默认检索路径是 ChapterPlan 实体扩展 + 关键词/SQLite FTS；向量召回必须显式启用 `--use-vector-context`，搜索层会在真实 embedding 向量缺失或过期时先刷新索引。

详见 `docs/AGENT_PROMPT_ASSEMBLY.md`。

## 12. 如何定位 BUG

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
pytest tests/test_<area>.py -q
pytest -m "not real_api and not web_e2e" -q
ruff check .
mypy src
```

## 13. 重构准则

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

## 14. 文档维护

修改代码时同步检查：

- 新模块或新函数：更新 `docs/CODEBASE_REFERENCE.md`。
- 新 Agent 或 prompt：更新 `docs/AGENT_PROMPT_ASSEMBLY.md`。
- 新 CLI/Web API：更新 `README.md`、`docs/INTEGRATION.md` 和相关测试。
- 新 schema：运行 `novel schema export --output schemas`，并更新数据文档。
