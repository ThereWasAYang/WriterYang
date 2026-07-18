# 工作流说明

本文说明 schema v3 的当前创作与接受路径。CLI、Web API 和内部 Agent Task 必须复用同一 core service。

## 1. 工作区

```bash
novel init "雨夜旧车站" --path ./rain-station
novel validate --path ./rain-station
```

新工作区只生成 schema v3。旧版本项目会返回 `unsupported_project_schema`；没有 `migrate` 命令，也没有新旧字段双读逻辑。

## 2. 推荐 Creation Session

```bash
novel session start "连续写前两章" --path ./rain-station --chapters 1,2 --provider config
novel session revise-outline <session_id> --path ./rain-station --instruction "加强第二章钩子" --provider config
novel session approve-outline <session_id> --path ./rain-station
novel session run <session_id> --path ./rain-station --provider config
novel session revise-content <session_id> --path ./rain-station --instruction "收紧第一章节奏" --provider config
novel session accept <session_id> --path ./rain-station
novel session archive <session_id> --path ./rain-station
```

`session run` 按章节升序执行 Writer、可选 Polish、Audit、自动修复和 State Update。中高严重度问题不能越过审阅门。

`session.json` 只保存一个 `phase` 和每章 `chapter_runs`。合法路径由 transition table 限定；失败时进入 `failed_recoverable` 并保存 `failure_node`。再次运行只继续未完成生成 node；修订类失败必须使用与失败节点匹配的修订命令，不会误回生成路径。

`session revise-content` 的实际循环只处理 `RevisionRouteDecision.chapter_numbers` 授权的章节。解析阶段拒绝 Session 范围外章节，执行完成后再次校验范围外正文 hash。

## 3. 多章 state/timeline projection

Session 开始运行时记录 canonical state/timeline base hash，并在 `memory/sessions/<session_id>/projection/` 创建本地投影。第 N 章读取第 N-1 章 proposal 应用后的投影，而不是继续读取未更新的 canonical 文件。

每章完成后：

1. 冻结 state/timeline snapshot、plan、candidate、Audit 和 State Proposal 为不可变 artifact。
2. 为每个 artifact 写 lineage sidecar，并在 `lifecycle.json` 汇总完整 DAG。
3. deterministic 校验 `old_value`、timeline ID 和应用后状态。
4. 写入 projection checkpoint。

接受前 canonical state/timeline 不变。如果 canonical base 在 Session 运行期间被其他操作修改，接受会失败，不自动合并。

## 4. Artifact 与 freshness

每个正文 candidate、Audit、State Proposal、Chapter Memory 和 Acceptance 都有唯一 `artifact_id`、project-relative path 与 SHA-256。`lifecycle.json` 是章节 active lineage 的入口。

人工编辑是允许的，但会让依赖该文件的下游 artifact stale。接受和正式导出都会重新计算 hash，并返回具体的 stale edge。

## 5. 事务化接受

`session accept` 不再逐章调用独立 apply。core 会先完成全 Session preflight 和 pending Chapter Memory，再创建 `transactions/tx_<id>/journal.json`：

```text
PREPARED -> APPLYING -> COMMITTED
                  \-> ROLLING_BACK -> ROLLED_BACK
```

同一个 transaction 包含：

- canonical `current_state.json` 与 `timeline.json`；
- 每章 `accepted.md`、`acceptance.json`、`chapter_memory.json`、`metadata.json`、`lifecycle.json`；
- Session 最终状态。

任何目标写入失败时，已写入章节和 canonical memory 全部回滚。模型调用不会发生在 commit 阶段。

## 6. 正式导出

```bash
novel export markdown --path ./rain-station --force
novel export docx --path ./rain-station --force
```

生产导出只读取 `accepted.md`，并校验 AcceptanceCommit、candidate、passed Audit、State Proposal 和 artifact hashes。`accepted.md` 被人工修改后导出会失败。

生产路径没有绕过 Acceptance、Audit 或 finalization policy 的参数。中途停止的 working candidate 不能进入生产导出。

需要分享中间稿时使用 `novel preview package --source polished`。Preview 固定写入 `exports/previews/<preview_id>/`，正文和 manifest 都明确标记为非正式内容；它不会创建或修改 `exports/export_manifest.json`。

## 7. 低层 Task 与 Command Bus 边界

`plan`、`write`、`polish`、`audit` 与 `state-update` 只作为 workflow runtime 内部 Task 存在。公开 CLI 与 Web 不再暴露对应单步 mutation，也不暴露端到端低层 generation command。开发测试通过 `tests/internal_task_cli.py` 验证必要的单 Task 契约，不构成产品 API。

Session、Revision、Memory/Setting Change、Preview 与 Production Export 都先构造 strict `CommandEnvelope`，由 `core/command_bus.py` 获取锁、执行 handler 并返回统一 `CommandResult`。`accept`、`apply` 与 Production Export 必须显式确认；Web 按 `next_allowed_commands` 控制 Session 动作按钮。

`novel ask` 先生成 `CommandProposal`，不会把模型分类结果直接当作执行授权。只读低风险 command 可以自动执行；其余 proposal 需显式确认。非只读 intent 的 confidence 低于执行阈值时只能澄清。模型解析失败的 fallback 不得用关键词选择写 executor；Revision 路由失败固定转人工复核。一个 `WorkflowBudget` 从 intent router 开始累计模型调用、Provider attempt、token、章节范围和自动修订轮次，并随 Creation/Revision Session 持久化；human gate 后继续运行不能重置用量。

每个公开 command 都在 `runs/{workflow_run_id}/` 写 `WorkflowRun` 和 command node，内部 Provider 调用成为其 model child node。Session/Revision 保存原 `workflow_run_id`，因此 outline approval、review、accept 等后续 command 会继续向同一 run 追加节点。旧的 Orchestrator handoff graph 和 keyword classifier 已删除；`orchestrator.py` 只负责生成结构化 decision/proposal。

Run 同时登记 `request_ids`、`session_ids`、`node_ids` 和 `decision_ids`。Ask Intent、Command Proposal、Revision Route 与 Audit Repair Route 写入 `decisions/`；Provider call 和 Model I/O 携带相同 workflow/node/session/parent request。Model I/O 默认仅保存 metadata 与内容 hash，显式 full capture 才保存正文和 prompt。

Command Bus 获取的项目锁包含 process identity、host、workflow/command 和 heartbeat。长 Session 自动续租；只有进程消失、process start time 不匹配或 heartbeat 超时才回收，回收事件写入 `runs/lock_events.jsonl`。

所有模型上下文都经过 Context Authority Policy。检索只读取显式 allowlist：canonical world/state/timeline、当前 Session 已批准 plan，以及具备有效 AcceptanceCommit lineage 的章节；archive、backup、rejected candidate、working candidate 和来源不明的 Markdown 不会进入索引。检索结果携带 `artifact_ref`、authority、lifecycle、session/commit lineage 与 source hash，Task 只能消费其 policy 允许的来源。

隐藏真相不能由正文中的自然语言指令自行解锁。Writer、Polish 和 Revision 仅在已批准 `ChapterPlan.reveal_authorizations` 精确授权 truth、章节和揭示方式时获得对应内容。工作区文本统一放入 `UNTRUSTED_WORKSPACE_DATA` 边界，提示词明确禁止把其中内容当作系统指令；Audit 每次只接收一个最终候选正文，自动修复还要求 strong evidence、可执行 source layer、hard-blocker 标记和足够置信度，否则进入人工复核。

## 8. Accepted 章节局部修订

局部修订已从 Creation Session 拆为独立 Revision Session：

```bash
novel revision-session blocks 1 --path ./rain-station
novel revision-session start 1 --blocks 2-4 --instruction "压缩节奏" --path ./rain-station
novel revision-session run <revision_session_id> --path ./rain-station --provider config
novel revision-session accept <revision_session_id> --path ./rain-station
```

系统对 accepted candidate 建立稳定 Markdown block selection，Revision Task 只返回 `SegmentPatch`。deterministic applier 校验 source/selection/prefix/suffix hash，保证授权范围外逐字节不变，再对合成后的完整 candidate 重新运行 Audit、revision-mode State Proposal 和 pending Chapter Memory。接受通过同一 transaction journal 替换 canonical state/timeline 与章节 acceptance；提交前旧 accepted chapter 保持不变。

当前只允许修订 canonical state 中最新 accepted chapter，避免静默破坏后续章节依赖。更早章节需要未来的 dependent-chapter rebase workflow。Creation Session 的 `scope_type`、`segment_range`、`--chapter`/`--segments` 与 `_run_segment_session` 已删除。
