# 项目目标

构建一个支持 multi-agent workflow 的 AI 辅助小说写作工具。

# 架构原则

- Agent memory 保持为可编辑的 Markdown/JSON 文件。
- 所有 inter-agent 输出都必须使用 structured schema。
- 永远不要把 API key 存入项目文件。
- Web UI 和 CLI 必须共享同一套 core logic。
- 每次章节生成都必须更新 timeline 和 state 文件。
- 每个生成章节在 export 前都必须通过 consistency audit。
- 把用户自然语言输入视为嘈杂、随意且可能包含 typo。高风险 workflow routing 不要把 hard-coded keyword matching 作为主要决策机制；应使用结构化 Orchestrator/model 决策、schema、validation 和保守 fallback。

# Commands

- install:
- test:
- lint:
- run web:
- run cli:

# 编码规则

- 使用 typed schema。
- 为 state transition 添加测试。
- 给用户呈现的计划都要用中文写（专业术语、简写、特定名词除外）。
- 项目所有文档都要用中文撰写，特有名词、专业术语、缩写、代码标识、schema 字段名、命令、路径和协议名除外。
- Provider adapter 必须与 agent logic 分离。
- 避免用脆弱的 keyword-only classifier 判断用户意图。如果为了兼容保留 keyword heuristic，必须把它记录为 fallback，并用测试覆盖 robust path。
- 除非用户明确另有要求，完成实现和验证后要 commit 并 push 已验证的改动到远端仓库。
