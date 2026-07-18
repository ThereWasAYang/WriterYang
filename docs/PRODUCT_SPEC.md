# WriterYang 产品需求说明

> 文档状态：现行规范
> 适用版本：0.1.x
> 最近核验：2026-07-18

## 1. 产品目标

WriterYang 是面向长篇小说作者的本地 AI 辅助写作工具。它不是普通聊天编辑器，而是以可编辑 Markdown、JSON、YAML 文件为长期记忆，借助受控 multi-agent workflow 完成灵感、设定、章节计划、正文、润色、一致性审核、修订、状态/时间线更新与导出。

产品优先保证故事事实可追溯、作者拥有最终控制权，以及中途失败不会静默污染 Canon。Web UI 是普通作者的推荐入口；CLI 用于高级使用、调试、自动化和外部集成。两者必须调用同一套 core logic。

## 2. 目标使用者与核心场景

- 小说作者：在 Web UI 中完成项目初始化、模型配置、创作、审阅、接受和导出。
- 高级作者或开发者：用 CLI 检查项目、自动化工作流、诊断失败和集成外部工具。
- 本地辅助 Agent：通过 strict command/schema 读取状态和提出变更，不直接绕过业务门禁。

核心流程是：初始化项目 → 配置 Provider → 发起 Creation Session → 审批大纲 → 生成/审核/修订正文 → 明确接受 → 更新 canonical state/timeline → 正式导出。

## 3. 必须满足的功能需求

1. 项目初始化、健康检查和 schema 校验。
2. 灵感、Canon、文风、章节计划、正文、润色与 Audit 的 multi-agent workflow。
3. Agent memory 以可人工编辑的 Markdown/JSON/YAML 持久化。
4. 所有 inter-agent 输出先进入 strict typed schema；小说正文再由确定性 renderer 写为 Markdown。
5. Creation/Revision 使用显式状态机，非法 transition 必须拒绝并有测试。
6. 每章生成必须更新 Session-local state/timeline projection；只有接受后才事务化写入 canonical 文件。
7. 每个正式导出章节必须具有 fresh acceptance lineage、通过 consistency audit，并与当前正文 hash 一致。
8. CLI、Web 和 Ask 入口共享 Command Bus、确认门、预算、项目锁和领域 service。
9. Provider adapter 与 Agent logic 分离；高风险自然语言路由使用结构化决策、校验和保守 fallback。
10. Search 提供本地 FTS，语义检索可选；前台查询不能隐式全项目扫描或全量重建。
11. 运行记录具有 request/workflow/node/session 关联、稳定错误码、轮转和留存策略。

## 4. 非功能需求

- 本地优先：0.1.x 只支持单机、可信单用户和 loopback Web 访问。
- 安全边界：普通模式禁止绑定非 loopback；Web 文件读取限制在 workspace allowlist；日志、响应、导出和 Git 不得包含 API Key。
- 密钥决策：为方便本地维护，允许用户选择在项目根目录 `.env` 明文保存 API Key。该文件和备份必须被 Git、Web 文件树、导出与日志排除，并尽量设置为 `0600`。用户负责保护项目目录和备份；高敏感环境应改用进程环境变量。详见 `SECURITY.md`。
- 兼容性：支持 CPython 3.11–3.13；未验证版本不在包元数据中承诺。
- 可恢复性：重要写入使用原子替换、hash、锁或 transaction journal；失败不得留下 durable partial acceptance。
- 可用性：当前阶段只显示可执行动作，主要流程不得被 sticky 元素遮挡，技术细节默认折叠。
- 可发布性：测试、coverage、lint、type check、dependency audit、build、wheel smoke 与 Web E2E 构成发布门禁。

## 5. 明确不在 0.1.x 范围

- 云同步、账户系统、多人协同或公网 Web 服务；
- 自治 Agent 自由辩论或绕过 Orchestrator 的任意执行；
- 外部向量数据库、微服务、消息队列和分布式监控平台；
- 未审核 working candidate 的正式发布旁路；
- 旧 schema 自动迁移与历史 API 兼容层；
- 完整出版发行、销售或版权管理平台。

## 6. 需求验收状态

当前版本已具备 Web/CLI 共核、结构化 prose、Session projection、事务化 acceptance、Audit/export freshness、增量 SQLite Search、真流式有界 Provider、统一本地事件与 typed command endpoint。正式能力以当前测试和 `ARCHITECTURE.md` 为准；未来计划只在 `ROADMAP.md` 维护，历史评审不作为当前需求来源。
