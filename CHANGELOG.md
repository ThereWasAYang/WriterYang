# 更新日志

## 0.1.1 - 未发布

- 以破坏性 schema v3 重建 Agent workflow 基础：Strict Contracts、Task Registry、immutable Artifact lineage、Creation Session state/timeline projection、transaction journal、全 Session 原子 acceptance 和 artifact-aware production export。
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
- `runs/model_io/` 默认保留最近 500 份完整日志，并限制总体积约 200MB；`WRITERYANG_MODEL_IO_MAX_FILES`、`WRITERYANG_MODEL_IO_MAX_BYTES` 可调整上限，`WRITERYANG_MODEL_IO_MODE=metadata` 可省略 prompt、正文和 raw response。
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
- `revise-chapter` 增加受控 revision loop：多轮修订必须显式 `--confirm-loop`，并写入 loop run log。
- 搜索索引增强：增加中文 n-gram 分词、字段权重、章节过滤、结果高亮、SQLite FTS5 和本地 hash embedding 向量表。
- 增加真实 embedding provider 抽象和适配：`local_hash`、阿里 DashScope `text-embedding-v4`、智谱 `embedding-3`，并为 `index rebuild` / `search --use-vector` 接入可配置 embedding。
- 明确中文长篇小说默认工作流，新增新手快速开始、memory 手动编辑说明和模型配置最佳实践文档。
- `.gitignore` 纳入版本控制，默认忽略 `.env*`、缓存、构建产物、`runs/`、本地 agent 协作文档和私密项目规划文档。
- CI 扩展为 pytest、build、secret scan、ruff lint、阻断式 mypy type check、Web E2E 和 CLI 入口检查。
- `cli.py` 顶层命令分发重构为同文件 handler 表，并收口 mypy 类型错误到 0。
- Web UI 增加项目搜索、schema 迁移入口和用量统计页；搜索默认使用 FTS，只有显式启用语义检索才调用 embedding。
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
