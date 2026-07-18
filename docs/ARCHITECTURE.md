# 架构说明

WriterYang 是文件式 AI 辅助小说写作工具。当前工作区 schema 为 v3；系统不读取旧 schema，也不提供 migration 或兼容别名。

## 分层

- `src/novel/cli.py`、`src/novel/cli_parsers/` 与 `src/novel/web_api/`：分域参数注册、请求校验和结果序列化。
- `src/novel/core/command_registry.py`：公开 command 输入模型、结果 envelope、读写/锁/确认策略和错误目录的单一事实源。
- `src/novel/core/command_bus.py`：只执行 typed dispatch、预算、项目锁和统一 `DomainError`；不导入具体领域实现。
- `src/novel/core/command_handlers/`：按 project、generation、session/revision、publishing/memory 分域注册 handler，并在领域边界翻译错误。
- `src/novel/core/command_workflow_state.py`：跨 human gate 的 Creation/Revision workflow budget 恢复与持久化。
- `src/novel/core/budget.py`：跨 intent router、Command、Provider 与 Session/Revision 的 workflow-wide 预算记账。
- `src/novel/core/workflow_runtime.py`：静态 Creation/Revision workflow、node executor，以及跨 human gate 连续的 run trace。
- `src/novel/core/`：CLI 与 Web 共享的领域服务。
- `src/novel/core/contracts/`：严格的控制面和 inter-agent typed schema，全部 `extra="forbid"`。
- `src/novel/core/prose_generation.py`：Writer、Polish、Revision 与 Inspiration 的 structured prose 校验和 repair 边界。
- `src/novel/core/providers.py`：Provider adapter；与 Agent 任务逻辑分离。
- `src/novel/core/task_registry.py`：Task、Profile、Prompt、读写权限和风险等级的单一事实源。
- `src/novel/core/context_policy.py`：Search authority/lifecycle/visibility、Reveal Authorization 与 untrusted workspace delimiter。
- `src/novel/core/audit_policy.py`：Audit issue 的自动修复资格分类和保守人工回退。
- `src/novel/core/search.py`：SQLite 作为运行时唯一查询索引，FTS/向量/来源记录事务内增量更新；JSON/manifest 仅为诊断快照。
- `src/novel/core/event_writer.py` 与 `retention.py`：本地结构化事件的并发安全写入、轮转、留存和 health 汇总。
- `src/novel/core/session_progress.py`：独立于 Session 编排器的进度 repository 与取消状态持久化。

业务状态转换不能放在前端或 adapter 中。模型只生成内容或结构化 proposal；文件权限、hash 校验、projection 和提交由 deterministic core 管理。

## Artifact lineage

Session 生成完成后，当前 `plan.json`、`polished.md`、`audit.json` 和 `state_update_proposal.json` 会冻结为不可变 artifact：

```text
memory/chapters/001/
  lifecycle.json
  plans/plan_art_<id>.json
  candidates/candidate_art_<id>.md
  audits/audit_art_<id>.json
  state_proposals/state_proposal_art_<id>.json
  patches/segment_patch_art_<id>.json
  chapter_memories/chapter_memory_art_<id>.json
  acceptances/acceptance_art_<id>.json
  accepted.md
  acceptance.json
  chapter_memory.json
```

`ArtifactRef` 保存 project-relative path、artifact kind、唯一 ID、创建时间和 SHA-256。每个 artifact 同目录写入 `.lineage.json`，记录输入 refs、Task、workflow run、Prompt hash 与 policy hash；`lifecycle.json` 汇总当前完整 DAG。Creation 还冻结 state/timeline snapshot，因此可还原 `snapshot → plan → candidate → audit → state proposal → chapter memory → acceptance`。读取、接受和导出都会重新计算 hash 并核对 sidecar；人工修改任何上游文件会使下游 lineage stale。

Artifact Store 不接受匿名 Agent 写入。Agent 必须提供已注册 `TaskId`，并通过 Task Registry 的 `writable_artifacts` 校验；模型外写入必须显式声明 `deterministic` 或 `user` authority。Context Policy 同时校验 Task Registry 的 `readable_authorities`。

## Creation 状态机

Creation Session 只持久化一个 `phase` 和每章 `chapter_runs`。合法 phase 为 outline 起草/审批、待运行、运行、内容审阅、修订、待提交、提交、恢复、已提交、归档、可恢复失败、终止失败和取消。所有变化必须通过 transition table；CLI、Web 与 Agent 都不能直接拼接状态字符串。

每章 node 独立记录 Plan、Write、Polish、Audit、State Update 与 Chapter Memory 的 `pending/running/completed/failed/stale`。中断会保存 `failure_node`；恢复只重跑失败或未完成 node，并复用已有 projection checkpoint。Creation 不再包含 `status`、`outline_status` 或 `content_status`。

## 多章 Projection

Creation Session 初始化时复制 canonical `current_state.json` 和 `timeline.json` 到 `memory/sessions/<session_id>/projection/`。第 N 章的 Writer、Polish、Audit 和 State Update 读取第 N-1 章更新后的 projection。每章 proposal 通过 deterministic applier 后写 checkpoint；canonical state/timeline 在用户接受前不变。接受前再次核对 canonical base hash；发生并发修改时拒绝提交。

## 事务化接受

`session accept` 在模型调用全部结束后执行：

1. 校验所有章节的 artifact freshness、passed Audit 和 projection checkpoint。
2. 在 PREPARED 前生成 pending deterministic Chapter Memory 和 immutable acceptance artifact。
3. 将 canonical state/timeline、每章 `accepted.md`、`acceptance.json`、Chapter Memory、metadata、lifecycle 和 Session 状态写入一个 transaction journal。
4. 原子替换文件并校验 hash；任一步失败时从 journal backup 回滚全部目标。

事务 journal 位于 `transactions/tx_<id>/journal.json`。同一多章提交的每个 `AcceptanceCommit.transaction_id` 都等于该 journal ID，Export manifest 继续保存这个绑定。正常结果只有完整 COMMITTED 或完整 ROLLED_BACK，不允许 durable partial acceptance。

## 正式导出

Markdown 与 DOCX 共用同一 eligibility 逻辑。正式导出只读取 `accepted.md`，并要求 acceptance、candidate、Audit、State Proposal 和 Chapter Memory 的引用 fresh，Audit 为 `passed`，正文 hash 与 acceptance 完全一致；生产导出不存在绕过生命周期检查的参数。

Preview Package 是独立服务，只读取指定 working draft/polished，输出到 `exports/previews/` 并固定 `production_eligible=false`。它与 Production Export 不共享 manifest 写入路径，不能成为发布授权来源。

## Segment Revision Workflow

`core/markdown_blocks.py` 把 accepted Markdown 解析为带精确字符区间的 heading、paragraph、quote、list、thematic break 和 fenced code block。`core/revision_workflow.py` 使用独立 `RevisionSession` 管理 selection、structured patch、candidate、Audit、State Proposal、projection 与 acceptance；不复用 Creation Session 的 outline/status 字段。

Revision Agent 只能生成 `SegmentPatch`。范围授权和 patch 合成由 deterministic core 完成，所有下游 binding 指向同一个 immutable candidate。revision-mode State Proposal 用完整事件集合替换目标章节 timeline，并只对当前 canonical state 描述净变化。最终提交复用 transaction journal，失败时恢复旧 acceptance。

## Context Authority 与 Prompt Policy

Search 使用显式 collector，只索引 current canonical、approved plan、accepted chapter 与 fresh ChapterMemory。每条文档带 authority、lifecycle、visibility、source hash 和可用 lineage；archive、rejection、backup、stale artifact 与其他 workflow candidate 默认不召回。

Context Policy 按 Task 二次过滤结果。Writer/Polish/Revision 只有在用户已批准的 ChapterPlan 含当前章节、精确 truth ID 的 `RevealAuthorization` 时才能接收 hidden truth。所有 workspace-derived 内容均以 untrusted delimiter 注入 prompt，不能把数据中的文字提升为权限或 route 指令。Audit 只接收一个 candidate；自动修复必须通过强证据/hard-blocker/confidence policy。

## Observability、隐私与锁

每次用户操作生成 `request_id`；Ask 确认、Session continuation 和跨 human gate 操作通过 `parent_request_id` 与同一 `workflow_run_id` 串联。`run.json` 索引 request、session、node 和 decision；model node、Provider call、Model I/O 使用同一 workflow/node/session metadata。Ask Intent、Command Proposal、Revision Route 和 Audit Repair Route 以 `WorkflowDecision` 单独持久化，因此无需读取完整 prompt 也能还原真正影响执行的结构化决定。

Model I/O 默认是 metadata + SHA-256，不保存正文、prompt、hidden truth、reasoning 或 raw response；只有显式设置 `WRITERYANG_MODEL_IO_MODE=full` 才进行 full capture。Project lock 绑定 lock id、PID、process start time、host、workflow/command 和 heartbeat；长任务持续续租，不再仅凭锁创建时间回收。stale lock 自动回收会写审计事件。

JSONL 统一通过 `EventWriter` 使用进程内锁和可用时的文件锁追加，单行 JSON 经过 `O_APPEND`、可配置 fsync、按大小轮转。terminal workflow run 默认按 500 个/90 天留存；`novel doctor` 与 `/api/health` 汇总 sampled success rate、近期失败和 runs 磁盘占用。未知 Web 异常返回 HTTP 500、稳定 `internal_error` 与 request ID，stack 只留在本地诊断日志。

Provider 非流式响应按块读取并有 16 MiB 默认上限；SSE 逐行解析并实时 yield，支持生成器取消后关闭连接。日志记录响应字节数、首 token 时间、总时长和 attempt count，不记录正文或 Authorization。重试优先遵守数值 `Retry-After`，否则使用指数退避和有界 jitter。

## Web 与安全边界

0.1.x 只支持可信本机用户。`web_security.py` 在 socket 创建前拒绝 `0.0.0.0`、局域网地址和域名，只允许 IPv4/IPv6 loopback。Host 必须是本机地址；Origin 是附加检查而非认证。不存在 public mode、账户或远程部署承诺。

Web UI 的专用 projection endpoint 服务页面读取；所有业务 mutation 最终构造 `PublicCommand`。`POST /api/command` 是 strict canonical command endpoint，`GET /api/openapi.json` 和 `/api/commands` 由 `CommandSpec` registry 生成契约目录。错误 envelope 包含 `code`、`http_status`、`retryable`、`request_id` 和安全 details。

API Key 可按项目所有者决策写入项目 `.env`；这是可信本地便利性策略，而不是无泄漏保证。`.env`/备份必须被 Git、Web 文件树、导出和日志排除，敏感场景只使用进程环境变量。威胁模型见 `SECURITY.md`。

旧 flat `AgentRunLog`、端到端 `generate_chapter()` pipeline、编号正文版本和 `chapter_versions.py` 已删除。Creation 内部全文修订与 Web 编辑器都输出 immutable candidate artifact；accepted 章节仍只允许独立 Segment Revision Workflow。

## 当前实施边界

系统保持模块化单体、本地文件 workspace、单进程 Web 与 SQLite Search，不引入微服务、外部队列、外部向量数据库或分布式观测平台。核心完整性能力包括 strict contracts、structured prose、真实状态机、Artifact DAG、Session projection、事务化 acceptance、Production/Preview 分离、Segment Revision、CommandSpec/Command Bus、Task 权限、Ask confidence gate、Workflow Budget、Context/Prompt/Audit Policy、增量索引和统一有界 trace。

`schemas.py` 保持持久化 model 的公开 facade，agent/command/prose 等边界 contract 已按域进入 `core/contracts/`；Search 仍以单模块部署，但 repository/index/query/embedding 通过独立函数和 SQLite 表契约隔离。新增 command 不修改 dispatcher 分支，只新增 contract、CommandSpec 和对应领域 handler。拆分以依赖方向、owner 和可独立测试为准，不以微服务化或单纯 LOC 指标为目标。
