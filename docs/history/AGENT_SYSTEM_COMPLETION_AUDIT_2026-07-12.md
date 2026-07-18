# Agent 系统优化完成性审计与遗留项闭环报告

> 历史材料：审计范围截至 2026-07-12，后续实现已变化，不应据此判断当前完成度。现行设计与质量状态以权威文档和当前 CI 为准。

> 审计日期：2026-07-12
>
> 闭环日期：2026-07-12
>
> 审计基准：`AGENT_SYSTEM_DESIGN_REVIEW_2026-07-11.md`、`AGENT_SYSTEM_OPTIMIZATION_AND_IMPLEMENTATION_PLAN_2026-07-11.md`
>
> 审计范围：优化计划阶段 0–9、15 项完成定义、原审视报告 P0/P1/P2 问题、代码、测试、JSON Schema 与项目文档。

## 一、最终结论

首次核验时，15 项完成定义中有 10 项已达成、4 项部分达成、1 项未达成。随后已对全部遗留项实施破坏性闭环，不保留旧 Session 字段、历史命令别名、keyword-only 高风险路由或双轨兼容代码。

闭环后的最终结论为：

- 15 项完成定义全部达成；
- 原审视报告列出的 P0/P1/P2 优化目标均已有代码约束、typed schema、运行时门禁或测试证据；
- Creation 与 Segment Revision 继续保持两套独立 workflow，不以兼容名义重新合并；
- CLI、Web 与 Ask 共享 Command Bus、领域 service、状态机、预算、权限与 trace；
- 所有正式导出内容均可追溯到同一 transaction、Acceptance Commit 和 immutable artifact DAG。

## 二、首次审计发现

首次审计确认以下遗留问题尚未闭环：

1. Creation Session 虽声明 `SessionPhase`，真实执行仍使用 `status + outline_status + content_status`。
2. `RevisionRouteDecision.chapter_numbers` 未约束实际修订循环。
3. 高风险 inter-agent decision 可能静默接受未知字段。
4. Ask 低置信度不阻断执行，失败 fallback 仍可能用关键词选择写 executor。
5. Workflow Definition 与 Task Registry 主要是声明性元数据，运行时未强制 Task 权限。
6. Plan、Candidate、Audit、State Proposal、Acceptance 与 state/timeline snapshot 的血缘不是完整 DAG。
7. Acceptance Commit 未绑定 transaction journal ID。
8. Revision、Audit、State Update Prompt 与 runtime policy 存在冲突。
9. run trace 缺少足够的 artifact 关联信息。
10. Creation transition、越权 scope、权限、恢复路径与文档矩阵存在缺口。

## 三、闭环实现

### 3.1 Creation Session 状态机

- `CreationSession` 只保留单一 `phase` 和按章节持久化的 `chapter_runs`。
- 删除 `status`、`outline_status`、`content_status` 及对应旧类型，不提供 deprecated alias 或兼容读取。
- outline、approval、run、review、revision、commit、recovery、archive 与 cancel 全部通过 `validate_session_transition()`。
- 每章持久化 `plan/write/polish/audit/state_update/chapter_memory` node 状态。
- 生成或修订异常进入 `FAILED_RECOVERABLE`，记录 `failure_node` 与脱敏错误；恢复命令按失败节点收窄。
- 运行恢复会复用已完成 node 与 projection checkpoint，不重复执行已完成工作。

### 3.2 修订范围和控制决策

- `RevisionRouteDecision.chapter_numbers` 必须非空且为 Session 章节集合的子集。
- `revise_content()` 只循环获授权章节，并在结束时重新校验范围外 `polished.md` hash。
- route 解析会拒绝越权章节和未知控制字段。
- 高风险 control-plane contract 使用 `extra="forbid"`；provider-facing 数据先在边界完成明确归一化，再进入 strict validation。
- Audit Repair Route 收敛为 `plot_replan / revision_rewrite / manual_review`，不再保留无独立 executor 的 `writer_rewrite` 旁路。

### 3.3 Ask、confidence 与 fallback

- 非只读命令低于 `MIN_EXECUTABLE_INTENT_CONFIDENCE` 时不生成可执行 command，只返回澄清请求。
- Ask fallback 只允许明确的只读 status/show 和显式 repair ID 指引。
- Revision route repair 失败后固定进入 `manual_review`，不再用剧情关键词选择 Plot、Writer 或 Revision。
- `mock` 是显式测试 provider，其确定性结构化输出不作为生产 fallback。

### 3.4 Task Registry 与 Workflow Runtime

- Artifact Store 在每次 Agent artifact 写入时调用 `require_task_write_permission()`。
- 未注册 producer 必须显式声明 `deterministic` 或 `user` authority；不存在隐式写权限。
- Context Policy 同时检查自身 allowlist 和 Task Registry 的 `readable_authorities`。
- Creation/Revision Workflow Definition 会约束 workflow 中可执行的 Task；越权 Task 在 runtime 失败。
- Creation 高层顺序由正式 Session transition table 驱动，Segment Revision 由独立 Revision state machine 驱动；静态 definition、Task 授权和持久化状态共同组成 executor 约束。

### 3.5 Artifact DAG、transaction 与 export

- 每个 immutable artifact 均写入同路径 `.lineage.json` sidecar。
- Lineage 记录 output、inputs、Task、workflow run、Prompt hash 和 policy hash。
- Creation capture 会冻结 state/timeline snapshot，并形成：

  `snapshot → plan → candidate → audit → state proposal → chapter memory → acceptance`

- `ChapterLifecycle.lineages` 持久化当前完整 DAG；freshness 检查同时验证 artifact hash、sidecar 和 lifecycle 声明一致。
- `AcceptanceCommit.transaction_id` 与 transaction journal 使用同一 ID；多章 Acceptance 共享一个 transaction。
- Export manifest 为每章记录 `transaction_id`、Acceptance ID、artifact IDs 与提交后 state/timeline hash。

### 3.6 Prompt 与 Audit Policy

- `AuditIssue.category` 固定为 `consistency_violation / plan_deviation / clarity_risk / craft_suggestion / informational`。
- 只有有证据的 consistency/plan 问题可进入中高严重度；clarity、craft 与 informational 为 advisory。
- Revision Prompt 明确轮数、scope 和人工 gate 由 Workflow Runtime 授权，不再要求每次模型调用自行停等。
- State Update Prompt 将 `location_id` 改为 evidence-first 可选字段。
- 自动修复继续要求具体 evidence、strong evidence、hard-blocker、blocking reason 和足够 confidence；否则进入人工复核。

### 3.7 测试与 schema

- 新增正式 Session transition table 合法/非法转换测试。
- 新增 chapter node 完成条件、Task 写权限、非 Agent authority、越权章节和未知控制字段测试。
- 更新 CLI/Web/Session fixture，只使用 `phase + chapter_runs`。
- 更新 Acceptance/Export fixture，要求 transaction ID 和完整 lineage。
- 重新导出全部 checked-in JSON Schema，不保留旧字段。

## 四、15 项完成定义最终核验

| 编号 | 完成定义 | 最终结论 | 证据摘要 |
| --- | --- | --- | --- |
| 1 | 无旧 schema migration 或双读写兼容 | 已达成 | loader 仅接受 schema v3；completion 和文档不再暴露 migrate |
| 2 | 无 `.vN`、`include_unaccepted`、生产 `skip_audit`、legacy graph | 已达成 | 代码、CLI 和测试均无旁路 |
| 3 | CLI/Web/Ask 写操作经过 Typed Command Bus | 已达成 | Adapter 只构造 command 和格式化结果 |
| 4 | Creation 与 Revision 是独立 typed workflow | 已达成 | Creation `SessionPhase`；Revision `RevisionSession` |
| 5 | 多章生成使用 persisted projection | 已达成 | 每章 checkpoint，后章读取前章 projected state/timeline |
| 6 | Session 接受具备 journal、rollback、crash recovery | 已达成 | 多章单 transaction，失败整体回滚，支持 incomplete recovery |
| 7 | Segment 范围可证明且 review hash 等于 accepted hash | 已达成 | block selection/hash、范围外字节校验、stale gate |
| 8 | Audit、Proposal、Acceptance、Export 完整 lineage | 已达成 | sidecar + lifecycle DAG + transaction/export binding |
| 9 | Production Export 拒绝 stale/未审计/未提交章节 | 已达成 | Markdown/DOCX 共用 eligibility |
| 10 | Search 默认只用当前权威 fresh artifact | 已达成 | allowlist、freshness、RevealAuthorization |
| 11 | Agent 权限由 Task Registry/runtime 强制 | 已达成 | Context read authority + Artifact write permission + workflow Task authorization |
| 12 | Workflow budget 覆盖 Agent 调用、retry、repair | 已达成 | Provider attempt、structured repair、auto revision 共用 ledger |
| 13 | run trace 可还原决策和 artifact 链 | 已达成 | node/decision trace + command outputs + lineage sidecar + workflow run ID |
| 14 | transition、transaction failure、关键跨入口行为有测试 | 已达成 | 状态机、scope、权限、回滚、CLI/Web parity 与 E2E 门禁 |
| 15 | 离线测试、Web E2E、lint、strict type check、build、secret scan 通过 | 已达成 | 见第五节最终验证记录 |

## 五、最终验证记录

闭环提交前的最终结果：

- 离线/非 Web E2E 测试：`698 passed, 26 deselected, 7 subtests passed`；
- Web E2E：`5 passed, 719 deselected`；
- JSON Schema、开发文档与旧兼容残留专项测试：`15 passed`；
- `ruff check .`：通过；
- `mypy src scripts`：121 个 source files 通过；
- secret scan：通过；
- 应用内 Browser：页面标题与内容正确，无错误 overlay；创作工作台只有一个 `vector_context` 下拉框，旧 `use_vector_context` 控件不存在；高级选项可展开；控制台无 warning/error；
- 隔离 `python -m build`：成功生成 sdist 与 wheel；
- `twine check dist/*`：全部通过。

## 六、审计判断边界

“全部达成”指优化计划定义的架构和质量目标已经形成可执行约束，并不表示产品未来不再演进。以下属于后续增强而非本轮遗留：更早 accepted 章节的 dependent-chapter rebase、跨机器分布式调度、生产级 telemetry backend，以及更多真实 Provider 的长期回归样本。
