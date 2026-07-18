# WriterYang 文档目录

> 此目录列出当前权威文档。`docs/history/` 仅用于追溯，不代表当前设计。

## 新用户

- [README](../README.md)：产品概览、安装入口和 FAQ。
- [快速开始](QUICKSTART.md)：用 mock 在 10 分钟内跑通流程。
- [Web UI 用户指南](WEB_UI_USER_GUIDE.md)：作者主流程。
- [CLI 命令](CLI_COMMANDS.md)：命令速查。

## 配置、运行与排错

- [配置参考](CONFIGURATION.md)：配置优先级、字段、环境变量和 API Key 决策。
- [模型配置最佳实践](MODEL_CONFIG_BEST_PRACTICES.md)：profile/task 继承策略。
- [部署与运行](DEPLOYMENT.md)：支持平台、本地 Web、备份和升级。
- [安全策略](SECURITY.md)：威胁模型、明文 `.env` 接受风险和公开绑定边界。
- [调试与重构](DEBUGGING_AND_REFACTORING.md)：常见故障和诊断路径。

## 产品、架构与数据

- [产品需求](PRODUCT_SPEC.md)：目标、范围、硬性需求和非目标。
- [架构说明](ARCHITECTURE.md)：模块边界、数据流与设计决策。
- [工作流](WORKFLOW.md)：Creation/Revision、projection、acceptance 和 export。
- [数据结构](DATA_SCHEMA.md)：workspace 与 JSON Schema。
- [性能基线](PERFORMANCE.md)：10/100/500 章 Search 刷新、查询和内存门禁。
- [集成/API](INTEGRATION.md)：JSON CLI、Web API、OpenAPI 和错误模型。
- [代码库参考](CODEBASE_REFERENCE.md)：源码级定位地图。
- [Agent Prompt 组装](AGENT_PROMPT_ASSEMBLY.md)：Prompt policy 与模板。

## 开发、测试与发布

- [开发指南](DEVELOPER_GUIDE.md)：开发环境、边界和测试策略。
- [贡献指南](../CONTRIBUTING.md)：提交要求。
- [发布说明](RELEASE.md)：发布门禁和 artifact 检查。
- [路线图](ROADMAP.md)：未来计划与非目标。
- [变更记录](../CHANGELOG.md)：用户可感知变化。

## 历史

- [历史材料目录](history/README.md)：带日期的评审、计划和完成性审计。
