# 调试与重构手册

本文面向需要修 BUG、跑 smoke、调整配置或重构 WriterYang 的开发者和大模型 Agent。

## 1. 最短调试路径

遇到问题先不要直接改代码，按顺序收集证据：

```bash
novel status --path <project>
novel validate --path <project>
novel doctor --project <project> --json --quiet
novel usage --path <project>
```

如果是具体章节：

```bash
novel show state --path <project>
novel show timeline --path <project>
novel audit-chapter <n> --path <project> --provider mock --force
```

如果是真实 API 输出异常，查看：

```text
runs/model_io/index.jsonl
runs/model_io/{request_id}.json
runs/provider_calls.jsonl
runs/provider_usage.json
runs/agent_output_violations/{request_id}.json
```

## 2. 常见故障分类

### 2.1 schema validation 失败

现象：

- `novel validate` 非零退出。
- 错误指向 `project.yaml`、canon/state/timeline、plan、audit 或 run/export manifest。

处理：

1. 找到错误 path 和 message。
2. 对照 `schemas/*.schema.json` 或 `core/schemas.py`。
3. 如果是旧项目缺少 `schema_version`，运行 `novel migrate --path <project>`。
4. 如果是模型输出不合规，查看对应 `runs/model_io/{request_id}.json`。
5. 写一个最小 JSON fixture 测试，再修 parser/normalizer/schema。

相关代码：

- `core/schemas.py`
- `core/validation.py`
- `core/json_schema.py`
- `core/migration.py`

### 2.2 真实 API 输出不合规

现象：

- Canon / ChapterPlan / Audit / StateUpdate repair retry 后仍失败。
- Writer/Polish/Revision 输出问题、JSON、大纲、解释，被输出守卫拦截。

处理：

1. 查看 `runs/model_io/{request_id}.json`。
2. 查看 `runs/agent_output_violations/{request_id}.json`。
3. 判断问题属于：
   - prompt 约束不够清晰；
   - provider 返回格式特殊；
   - parser 低风险归一化不足；
   - schema 过严或真实业务确实不允许。
4. JSON Agent 优先修 prompt 和 parser，不要伪造缺失必填字段。
5. Markdown Agent 优先修 system prompt 和 output guard，避免把问句/解释落盘。

相关代码：

- `core/agent_output.py`
- `core/providers.py`
- `core/provider_config.py`
- 各 Agent service 的 `_generate_*_with_repair()` 或正文清洗函数。
- `src/novel/prompts/*.txt`

### 2.3 audit 阻断

现象：

- `audit.json` 的 `overall_status` 是 `needs_revision` 或 `blocked`。
- `session run` 自动修复多轮后停下。
- `accept-chapter` 或 `session accept` 阻止继续。

处理：

1. 看 `audit.json.issues` 的 severity/type/evidence/suggested_fix。
2. medium/high/critical 是硬伤，应自动修复或阻断。
3. low 是作者选择项，可展示给用户，不自动强改。
4. 判断 issue 来源：
   - deterministic precheck：通常是文件、引用、状态闭环问题，先修数据或 service。
   - model audit：通常是语义/风格/动机问题，可能要调 prompt 或走 revision。
5. 如果 session 自动修复后仍失败，检查 `revision_log.json` 中的 `polished.vN.md` 是否已提升为当前 `polished.md`，重审后的 `audit.json` 是否仍有 medium/high/critical，以及是否因为计划层问题触发了重新 planning。
6. 如果 Web UI 停在 `needs_revision`，优先走“按 Audit 修订内容”；修订后应看到新的版本稿、更新后的 `polished.md`、新的 audit 和新的 `state_update_proposal.json`。
7. 如果普通作者不确定下一步操作，先让他点击 Web UI 的“项目检查”，再看“下一步提示”；这两个入口都只读，不会修改项目文件。
8. 全局 timeline ordering 旧 warning 不应阻断某一章正文修复；真正会阻断的是当前章新增事件倒退、scene 超出 ChapterPlan 范围或引用冲突。
9. 对 accepted/export 相关问题，确认 state update apply log 和 metadata 是否一致。

相关代码：

- `core/auditing.py`
- `core/consistency.py`
- `core/session.py`
- `core/state_update.py`
- `core/validation.py`

### 2.4 state/timeline 冲突

现象：

- `propose-state-update` 被 audit 阻止。
- `apply-state-update` 报重复 event id、entity id 不存在、old_value 不匹配、物品 holder/location 冲突。
- accepted 章节 validate 出现闭环错误。

处理：

1. 查看 `state_update_proposal.json`。
2. 查看 `state_update_apply_log.json` 是否已 applied。
3. 查看 `memory/state/current_state.json` 和 `timeline.json` 的备份。
4. 确认 state_changes 引用的 entity_id 存在于 canon 或 state。
5. 确认 timeline event id 不重复；先检查 `narrative_position` 是否按正文呈现顺序递增，再检查同一 `story_position.thread_id` 且双方都有 `story_position.order` 的 causes/effects 是否倒置。
6. 如果 apply 写入失败，确认 rollback 是否恢复原 state/timeline。

相关代码：

- `core/state_update.py`
- `core/consistency.py`
- `core/validation.py`

### 2.5 provider 配置错误

现象：

- 缺少 API key env。
- base URL 错误。
- thinking 字段不被厂商接受。
- timeout / retry 过短。

处理：

1. 运行 `novel doctor --project <project> --json --quiet`。
2. 运行 `novel write-chapter 1 --path <project> --dry-run-provider` 查看将使用的 provider/model。
3. 确认 `config/agents.yaml` 只保存 env 名，不保存真实 key。
4. 根据 provider 检查默认 base URL：
   - `deepseek`: `https://api.deepseek.com`
   - `zai`: `https://open.bigmodel.cn/api/paas/v4`
   - `openai`: `https://api.openai.com/v1`
5. 看 `runs/provider_calls.jsonl` 的 `error_type`、`http_status`。

相关代码：

- `core/provider_config.py`
- `core/providers.py`
- `core/security.py`
- `docs/MODEL_CONFIG_BEST_PRACTICES.md`

### 2.6 Web API 错误

现象：

- 前端显示 `{ok:false,error:{code,...}}`。
- Web 能读但写操作失败。
- provider 配置页显示 env 缺失。

处理：

1. 查看 API 返回的 `request_id`、`code`、`details`。
2. 检查 Web API 是否使用 `_locked_write()`。
3. 检查路径是否通过白名单函数：`_safe_workspace_file()`、`_safe_config_file()`。
4. 确认 API 没有直接实现业务逻辑，而是调用 core service。
5. 运行 `tests/test_web.py`；如浏览器依赖可用，再运行 `pytest -m web_e2e`。

相关代码：

- `web_api.py`
- `web_server.py`
- `web_static/index.html`
- `tests/test_web.py`
- `tests/test_web_e2e.py`

## 3. 输出和日志位置

| 文件 | 用途 |
| --- | --- |
| `runs/run_*.json` | workflow/orchestrator run log，记录步骤、输入输出、错误。 |
| `runs/provider_calls.jsonl` | provider 调用轻量日志：provider、model、耗时、状态、token。 |
| `runs/provider_usage.json` | 根据 provider_calls 刷新的累计用量。 |
| `runs/model_io/{request_id}.json` | 完整模型输入输出，含 prompt、context、payload、raw response。 |
| `runs/model_io/index.jsonl` | model_io 索引。 |
| `runs/agent_output_violations/{request_id}.json` | Agent 输出契约违规，例如内部 Agent 反问。 |
| `memory/chapters/{NNN}/context_report*.json` | 检索上下文报告，说明 included/excluded/context visibility。 |
| `*.bak_*` | 重要文件覆盖或 state/timeline apply 前备份。 |

日志脱敏规则：

- 不写 HTTP headers。
- 不写 Authorization。
- 不写真实 API Key 或 env value。
- 但会包含小说正文、用户指令和 hidden truth。不要提交 `runs/`。

## 4. 重构前 checklist

在动代码前先回答：

- 改动是否改变 workspace 文件格式？如果是，需要 schema/migration/JSON Schema。
- 改动是否改变 Agent 输出？如果是，需要 prompt/parser/output guard/tests。
- 改动是否写文件？如果是，需要 atomic write、默认不覆盖、force/overwrite、备份、项目锁。
- 改动是否涉及 API Key？如果是，需要 security test，确保只保存 env name。
- 改动是否影响 CLI/Web 共用逻辑？如果是，必须放 core service，CLI/Web 只做薄包装。
- 改动是否影响 accepted/archive 内容？如果是，不能原地修改归档内容。

## 5. 新特性开发流程

推荐顺序：

1. 写或更新 schema。
2. 写 core service options/result 和纯函数。
3. 写 mock provider 测试。
4. 接 CLI。
5. 接 Web API / Web UI，如需要。
6. 加 validation / doctor / integration docs。
7. 跑非真实 API 全量测试。
8. 如涉及真实 provider，再单独跑 real_api 标记测试。

测试命令：

```bash
conda run -n py312 pytest tests/test_<area>.py -q
conda run -n py312 pytest -m "not real_api and not web_e2e" -q
conda run -n py312 ruff check .
conda run -n py312 mypy src
```

## 6. Prompt 调整 checklist

调整 prompt 后必须确认：

- JSON Agent 是否仍明确“只输出 JSON”。
- Markdown Agent 是否仍明确“只输出正文，不要解释/大纲/JSON”。
- 是否仍禁止修改 canon/state/timeline。
- 是否仍保护 hidden truth。
- 是否仍说明内部任务不能反问上游 Agent。
- `tests/test_prompts.py` 是否覆盖关键约束。
- 是否需要更新 `docs/AGENT_PROMPT_ASSEMBLY.md`。

## 7. 文件保护 checklist

写入正式文件前确认：

- 新建文件：父目录存在，使用 atomic write。
- 覆盖文件：默认拒绝；`--force` 或 `--overwrite` 才允许。
- 重要文件覆盖：先备份。
- state/timeline apply：同时备份，失败回滚。
- session archive：复制并写 sha256 manifest，不再原地改。
- Web 写 API：必须持有项目锁。

## 8. 安全检查

运行：

```bash
novel doctor --project <project> --json --quiet
conda run -n py312 pytest tests/test_security.py -q
```

检查重点：

- `.env.example` 只包含变量名或空值，不写真实 key。
- `config/agents.yaml` 和 `config/embeddings.yaml` 只写 env name。
- 示例项目不包含 raw API key。
- 错误输出、provider logs、model_io logs 不泄漏 Authorization/API Key。

## 9. 真实 API smoke 建议

真实 API 测试不要混入普通 CI。建议：

1. 在本机 `.env.real` 配好变量。
2. 用临时 workspace 运行，不污染示例项目。
3. 所有 Agent 先用同一 provider 验证最短流程，再分 agent 调参。
4. 失败后保留 `runs/` 作为证据。
5. 成功标准至少包括：两章 accepted、`novel validate` 通过、Markdown export 成功。

普通回归仍使用：

```bash
conda run -n py312 pytest -m "not real_api and not web_e2e" -q
```
