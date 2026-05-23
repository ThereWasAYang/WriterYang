# 更新日志

## 0.1.1 - 未发布

- 增加 `novel migrate`，为项目工作区补齐 `schema_version`，覆盖 `project.yaml`、`config/agents.yaml` 和所有核心 JSON 文件。
- 增加结构化章节状态文件 `memory/chapters/{chapter}/metadata.json`。
- `accept-chapter` 现在会写入章节 metadata，并继续保持 `polished.md` front matter 兼容。
- `apply-state-update` 现在会写入 `state_update_apply_log.json`。
- state/timeline 写入失败时会尝试从备份回滚。
- state update 增加更细的冲突检测，包括 `old_value` 不匹配、timeline 引用不存在的 state change、重复 possession holder 等。
- 增加 `schemas/*.schema.json` 和 `novel schema export`，供外部工具使用 JSON Schema 校验项目文件。
- 扩展 validation 的跨文件检查：chapter metadata、draft/polished front matter、audit audited_file、timeline state_change_ids、timeline causes/effects、location active_events、死亡角色后续出场、物品持有人/位置差异等。
- 示例项目的 `config/agents.yaml` 改为真实 DeepSeek 配置模板，并新增 `config/agents.mock.yaml` 供无 API Key 测试使用。
- 增加 `deepseek` 和 `zai` provider 适配，厂商私有 `thinking.type` 只对对应 provider 生效，并解析响应中的 `reasoning_content`。

## 0.1.0

- 初始 CLI / core / minimal Web UI 版本。
- 支持项目初始化、校验、灵感、canon、章节计划、写作、润色、审核、修订、状态更新时间线、搜索、导出和外部 agent JSON contract。
