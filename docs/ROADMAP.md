# WriterYang 路线图

> 更新日期：2026-07-18；本文件只描述未来方向，不作为当前能力证明。

## 当前稳定基线

- schema v3 文件式 workspace，CLI/Web 共用 typed Command Bus；
- strict structured inter-agent 输出与可编辑 Markdown 正文；
- Creation/Revision 状态机、Session-local projection、artifact lineage 和事务化 acceptance；
- passed Audit + freshness gate 的 Markdown/DOCX 正式导出；
- 增量 SQLite FTS、候选集向量评分和 10/100/500 章基准工具；
- 有界真流式 Provider、统一 EventWriter、retention 与 health；
- loopback-only Web、typed command endpoint/OpenAPI、Python 3.11–3.13 与完整质量门禁。

## 近期：作者体验

1. 继续减少工作台默认暴露的技术概念，并用真实作者任务验证一个阶段一个主动作。
2. 改进 Revision block selection、正文 diff 与 acceptance 前影响预览。
3. 为 stale lineage、失败 node 和 transaction recovery 提供更直接的恢复建议。
4. 在不引入前端框架迁移的前提下持续改善键盘、屏幕阅读器和窄屏体验。

## 中期：长篇容量与可解释性

1. 持续记录 Search、ContextBundle、Prompt token 和 artifact snapshot 的容量历史，只有基准证明需要时才评估 ANN。
2. 增加不同题材、叙事顺序和中文文风的脱敏真实 Provider 回归样本。
3. 聚合 Audit evidence、自动修复资格、Provider retry 和 run failure 趋势，但继续保持本地、metadata-first。
4. 研究较早 accepted 章节修改后的 dependent-chapter rebase；在安全设计完成前继续拒绝该操作。

## 发布规则

任何新公开 mutation 必须登记 CommandSpec、使用 strict request/response schema、通过统一锁/确认/预算，并由 CLI/Web 共享 core handler。任何 Agent Task 必须声明 Prompt、上下文 authority、输出 schema 和写权限。任何状态变化必须有合法/非法 transition 测试；任何正式导出变化必须保留 Audit、lineage 与 hash 门禁。

发布需通过 constraints 工具链上的 Ruff、Mypy、coverage、dependency audit、secret scan、离线测试、Web E2E、macOS smoke、build、twine 和 fresh wheel resource/schema smoke。

## 明确非目标

当前不计划微服务、外部消息队列、外部向量数据库、分布式监控、云账户系统或公网 Web。项目 `.env` 明文保存 API Key 是所有者接受的本地便利性决策；未来只有产品范围转向多用户/远程部署时才重新评估 CredentialStore。
