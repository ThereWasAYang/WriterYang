# 更新日志

## 0.1.1 - 未发布

- 闭环 Agent 重构复审遗留项：Audit 强绑定正文 SHA-256，多章修订按 projection 交错推进，Revision Session 支持放弃并恢复，Ask 确认直接派发已展示 proposal，事务终态清理正文副本，并固定 immutable lineage 与确定性 ChapterMemory 语义。
- 完成 Agent 系统最终审计遗留项闭环：Creation Session 删除 `status/outline_status/content_status`，真实执行统一使用 `SessionPhase + ChapterNodeState`，支持失败节点持久化和定向恢复。
- Revision route 在解析和执行两层强制 `chapter_numbers` scope；非只读 Ask 增加 confidence gate，模型路由失败不再用关键词选择写 executor。
- Artifact Store 强制 Task 写权限与显式 deterministic/user authority；Context Policy 强制 Task 读 authority；Workflow Runtime 拒绝 workflow definition 外 Task。
- Artifact lineage 扩展为包含 state/timeline snapshot、Plan、Candidate、Audit、State Proposal、Chapter Memory 和 Acceptance 的完整 DAG；Acceptance 与 Export 绑定 transaction journal ID。
- Audit issue 使用固定 category，Prompt 与自动修复 policy 统一；删除 Web 语义检索旧布尔兼容输入和 completion 中残留的 migrate 命令。
- 以破坏性 schema v3 重建 Agent workflow 基础：Strict Contracts、Task Registry、immutable Artifact lineage、Creation Session state/timeline projection、transaction journal、全 Session 原子 acceptance 和 artifact-aware production export。
- 公开 CLI/Web/Ask 领域 workflow 统一进入 Typed Command Bus；Ask 改为 proposal-first，Creation/Revision 使用 workflow-wide budget 并跨 human gate 续写同一 trace。
- Typed Command Bus 覆盖全部公开写入口，包括 Inspiration、Canon、Chapter Memory、Index、Style Guide、编辑器 immutable candidate、Agent/Embedding 配置、项目/Web 端口与 JSON Schema 导出；Web adapter 不再重复加锁或直接调用 mutation service。
- Setup typed command 对 API Key 使用 secret-safe contract；明文只写项目 `.env`，不会进入响应、配置 YAML 或 workflow trace。
- 删除旧模板 Profile 默认值识别与自动剥离逻辑；当前 `inherit_default` Profile 的显式容量 patch 按现行 schema 原样保留，不再执行历史配置清理。
- Search 改为 authority/lifecycle allowlist，Prompt 使用 untrusted workspace delimiter；hidden truth 只能由已批准 `RevealAuthorization` 精确放行，Audit 自动修复要求强证据与可执行 source layer。
- Run/Node/Decision trace 统一记录 request、parent request、session、surface、Task/Profile、prompt/policy hash、budget、retry 与 artifact lineage；Provider call 和 Model I/O 使用相同关联字段。
- Project lock 增加 lock id、PID、process start time、host、workflow/command 和 heartbeat；stale lock 回收会写本地审计事件，不再仅按锁创建时间判断。
- 删除 flat `AgentRunLog`、端到端低层 generation pipeline、`chapter_versions.py`、编号正文版本和 revision loop 兼容轨；Creation 全文修订与 Web 编辑器改为 immutable candidate artifact。
- 新增独立 `revision-session` Segment Patch Workflow：稳定 Markdown block selection、structured `SegmentPatch`、范围外逐字节不变校验、重新 Audit/State Proposal/Chapter Memory，以及 transaction acceptance；删除 Creation Session 的 `scope_type`、`segment_range`、`--segments` 和旧 segment 执行分支。
- 新增独立 `preview package`：可打包 working draft/polished candidate，固定写入 `exports/previews/` 并标记 `production_eligible=false`，不读取或更新正式 `export_manifest.json`。
- 推广初期平台口径收敛为 macOS / Linux；Windows 适配暂缓，相关入口脚本保留为后续验收基础但不作为当前支持平台。
- README 瘦身为项目入口索引，新增 `docs/CLI_COMMANDS.md` 承接详细 CLI 命令参考。
- Web API 从单文件拆为 `src/novel/web_api/` 包，按 router、common、generation、config、memory、session、revision_session、inspection 分域组织。
- Web 前端从单一 `app.js` 拆为多个无构建普通脚本，按状态/API、创作工作台、工作区、配置、渲染和启动绑定分域加载。
- `core/memory_repair.py` 拆为 `core/memory_repair/` 包，保留原 public API，内部按 service、generation、apply、impact、preflight、validation 和 models 分层。
- 拆分后的 `web_api` 与 `memory_repair` 模块恢复显式 import、静态 `__all__`、ruff 和 mypy 覆盖，避免拆分过渡期的星号导入和整包类型豁免长期化。
- `memory_repair` 的 timeline backstory 自动修复只接受严格整数章节值；`bool`、非整数 `float` 和带 `+` 前缀的字符串不再被折算成章节号，而是交由 schema/preflight 校验处理。
- 安装器支持 Python 3.11-3.13，推荐 3.12；conda 环境使用 `python>=3.11,<3.14`。
- `runs/model_io/` 默认只保存 metadata、内容 SHA-256、token 和 trace 关联；`WRITERYANG_MODEL_IO_MODE=full` 才显式保存 prompt、正文和 raw response。默认保留最近 500 份、总体积约 200MB。
- `runs/provider_usage.json` 改为根据新增 provider log 增量刷新；日志截断或替换时自动全量重算。
- Web server 默认限制 POST 请求体为 32MB，可用 `WRITERYANG_WEB_MAX_BODY_BYTES` 调整，并校验 `/api/*` 请求的本机 Host / Origin；GET API 读面也会拒绝非本机来源。
- `novel.web_api` 包级出口移除无消费方的 `revise_content` / `revise_outline` 转口；内部 Web API 模块和 CLI 仍使用 core session 修订函数。
- 工作区直接升级为 schema v3；删除 migration 命令、旧 schema 兼容读取和过渡字段。
- 增加结构化章节状态文件 `memory/chapters/{chapter}/metadata.json`。
- `accept-chapter` 现在会写入章节 metadata，并继续保持 `polished.md` front matter 兼容。
- `apply-state-update` 现在会写入 `state_update_apply_log.json`。
- state/timeline 写入失败时会尝试从备份回滚。
- state update 增加更细的冲突检测，包括 `old_value` 不匹配、timeline 引用不存在的 state change、重复 possession holder 等。
- 增加 `schemas/*.schema.json` 和 `novel schema export`，供外部工具使用 JSON Schema 校验项目文件。
- 扩展 validation 的跨文件检查：chapter metadata、draft/polished front matter、audit audited_file、timeline state_change_ids、timeline causes/effects、location active_events、死亡角色后续出场、物品持有人/位置差异等。
- 默认 `config/agents.yaml` 改为顶层 `default` API + 标准 Agent `inherit_default: true` 业务 patch；离线测试通过显式 `--provider mock` 覆盖。
- 增加 `deepseek` 和 `zai` provider 适配，厂商私有 `thinking.type` 只对对应 provider 生效，并解析响应中的 `reasoning_content`。
- Provider 调用增加错误分类、retry/backoff、timeout 处理、streaming 输出、`max_tokens` 配置和安全调用日志。
- Prompt 模板从代码中抽出到 `src/novel/prompts/`，增加关键约束测试。
- Canon apply 增加 proposal 内部和跨类型 ID 冲突检查。
- Audit precheck 增加 plan 关键词和 hidden truth 直出检测。
- 已认可正文修订统一使用 `revision-session`；Creation 内部 Revision Task 每次只生成一个 immutable candidate 并立即回到 Audit gate。
- 搜索索引增强：增加中文 n-gram 分词、字段权重、章节过滤、结果高亮、SQLite FTS5 和本地 hash embedding 向量表。
- 增加真实 embedding provider 抽象和适配：`local_hash`、阿里 DashScope `text-embedding-v4`、智谱 `embedding-3`，并为 `index rebuild` / `search --use-vector` 接入可配置 embedding。
- 明确中文长篇小说默认工作流，新增新手快速开始、memory 手动编辑说明和模型配置最佳实践文档。
- `.gitignore` 纳入版本控制，默认忽略 `.env*`、缓存、构建产物、`runs/`、本地 agent 协作文档和私密项目规划文档。
- CI 扩展为 pytest、build、secret scan、ruff lint、阻断式 mypy type check、Web E2E 和 CLI 入口检查。
- `cli.py` 顶层命令分发重构为同文件 handler 表，并收口 mypy 类型错误到 0。
- Web UI 增加项目搜索、schema 和用量统计页；搜索默认使用 FTS，只有显式启用语义检索才调用 embedding。
- `core/usage.py` 增加按 Agent 的 provider 调用和 token 汇总。
- `core/consistency.py` 增加直接单元测试，覆盖 hidden truth、角色知识链、物品状态、双轨 timeline 和 accepted 闭环。
- 新增 GitHub Release workflow，tag `v*` 时构建 sdist/wheel 并上传到 GitHub Release。
- 新增贡献指南、issue template 和 PR template。
- Orchestrator 增加项目管家能力，可生成和应用 `MemoryRepairProposal`，用于修正 timeline/state/canon 等项目记忆错误。
- 新增 `memory/management_events.jsonl`，状态更新、时间线更新、记忆修复和章节认可等后台管理动作会显式记录并展示。
- Session 自动打回支持 Audit 复审、基于新审核重试打回、撤回打回并恢复被打回原文快照。

## 0.1.0

- 初始 CLI / core / minimal Web UI 版本。
- 支持项目初始化、校验、灵感、canon、章节计划、写作、润色、审核、修订、状态更新时间线、搜索、导出和外部 agent JSON contract。
