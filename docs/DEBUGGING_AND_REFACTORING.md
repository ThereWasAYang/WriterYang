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
novel session run <session_id> --path <project> --provider mock --force
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
3. 如果项目不是 schema v3，重新初始化工作区并人工迁移需要保留的内容；程序不会执行历史 schema migration。
4. 如果是模型输出不合规，查看对应 `runs/model_io/{request_id}.json`。
5. 写一个最小 JSON fixture 测试，再修 parser/normalizer/schema。

相关代码：

- `core/schemas.py`
- `core/validation.py`
- `core/json_schema.py`
- `core/contracts/common.py`
- `core/artifact_store.py`
- `core/projection.py`
- `core/transactions.py`

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
- `session accept` 阻止继续。

处理：

1. 看 `audit.json.issues` 的 severity/type/evidence/suggested_fix。
2. medium/high/critical 是硬伤，应自动修复或阻断。
3. low 是作者选择项，可展示给用户，不自动强改。
4. 判断 issue 来源：
   - deterministic precheck：通常是文件、引用、状态闭环问题，先修数据或 service。
   - model audit：通常是语义/风格/动机问题，可能要调 prompt 或走 revision。
5. 如果 session 自动修复后仍失败，先看 `memory/sessions/{session_id}/rewrite_events.json`：确认第几轮被打回、触发的 blocking issue、系统执行了修正文还是重写大纲，以及 `rejections/` 中的被打回原文快照。
6. 再检查 `revision_log.json` 记录的 immutable candidate 是否与本轮提升到 `polished.md` 的 artifact 一致，重审后的 `audit.json` 是否仍有 blocker，以及是否因为计划层问题触发了重新 planning。
7. 如果用户认为 Audit 理解错了，先走 `session revise-audit <session_id> <event_id> --instruction ...` 或 Web UI 的“纠正 Audit 理解并重新审核”，不要手改 `audit.json`。复审后再 `session retry-rewrite`，或用 `session undo-rewrite` 恢复 rejected snapshot。
8. 如果 Web UI 停在 `needs_revision`，优先看“自动打回重写记录”和被打回原文；如果打回理由合理，再走“按 Audit 修订内容”。修订后应看到新的版本稿、更新后的 `polished.md`、新的 audit 和新的 `state_update_proposal.json`。
9. 如果问题根源是 timeline/state/canon 写错，让 orchestrator 项目管家生成 memory repair proposal；确认 `memory/repairs/{repair_id}/proposal.md` 和 diff 后再 apply。apply 失败时先看 `apply_log.json`、`management_events.jsonl` 和 `runs/app.log`；`app.log` 只记录脱敏摘要，模型完整输入输出仍看 `runs/model_io/`。
10. 如果普通作者不确定下一步操作，先让他点击 Web UI 的“项目检查”，再看“下一步提示”和“后台管理动态”；这些入口不会泄漏 API Key。
11. 全局 timeline ordering 旧 warning 不应阻断某一章正文修复；真正会阻断的是当前章新增事件倒退、scene 超出 ChapterPlan 范围或引用冲突。
12. 对 accepted/export 相关问题，确认 state update apply log 和 metadata 是否一致。

相关代码：

- `core/auditing.py`
- `core/consistency.py`
- `core/session.py`
- `core/memory_repair/`
- `core/management.py`
- `core/state_update.py`
- `core/validation.py`

### 2.4 state/timeline 冲突

现象：

- Session 内部 State Proposal Task 被 audit 阻止。
- Session 接受阶段应用 State Proposal 时报告重复 event id、entity id 不存在、old_value 不匹配、物品 holder/location 冲突。
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
2. 运行 `novel session start ... --provider config` 前，使用 `novel doctor --path <project>` 检查 provider 配置；Task 到 Profile 的解析由 runtime 记录到 run log。
3. 确认 `config/agents.yaml` 只保存 env 名，不保存真实 key。
4. 根据 provider 检查默认 base URL：
   - `deepseek`: `https://api.deepseek.com`
   - `zai`: `https://open.bigmodel.cn/api/paas/v4`
   - `openai`: `https://api.openai.com/v1`
5. 如果 DeepSeek / OpenAI-compatible 在结构化调用时报 `Prompt must contain the word 'json'`，先确认当前代码的 provider payload 是否经过 `_ensure_json_mode_messages()`；所有 `response_format: json_object` 调用都应自动补充 JSON 提示。
6. 如果结构化 JSON 长输出漂移，先查看 `config/agents.yaml` 的 `json_response_format`：DeepSeek / ZAI 推荐保持 `auto` 或 `json_object`，不要配置 strict；OpenAI 只有在确认 schema 可 strict 转换且真实 API smoke 通过后，才对 profile 或单个 task 使用 `json_schema_strict`。
7. 看 `runs/provider_calls.jsonl` 的 `error_type`、`http_status`。

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

- `web_api/`
- `web_server.py`
- `web_static/index.html` / `app.css` / `app_*.js`
- `tests/test_web.py`
- `tests/test_web_e2e.py`

## 3. 输出和日志位置

| 文件 | 用途 |
| --- | --- |
| `runs/{workflow_run_id}/run.json` | 一次用户 workflow 的 request/session/surface、预算、node 和 decision 索引。 |
| `runs/{workflow_run_id}/nodes/{node_id}.json` | command/model/deterministic 节点、parent、Task/Profile、hash、retry 和 artifact。 |
| `runs/{workflow_run_id}/decisions/{decision_id}.json` | Ask、Revision Route、Audit Repair Route 等结构化决策。 |
| `runs/provider_calls.jsonl` | provider 调用轻量日志：provider、model、耗时、状态、token。 |
| `runs/provider_usage.json` | 根据 provider_calls 增量刷新的累计用量；日志被截断时会自动重算。 |
| `runs/model_io/{request_id}.json` | 默认只含 trace metadata、内容 hash、token 和状态；显式 full capture 才含 prompt、context、payload、response。 |
| `runs/model_io/index.jsonl` | model_io 索引，会随保留策略裁剪。 |
| `runs/agent_output_violations/{request_id}.json` | Agent 输出契约违规，例如内部 Agent 反问。 |
| `memory/chapters/{NNN}/context_report*.json` | 检索上下文报告，说明 included/excluded/context visibility。 |
| `*.bak_*` | 重要文件覆盖或 state/timeline apply 前备份。 |

日志脱敏规则：

- 不写 HTTP headers。
- 不写 Authorization。
- 不写真实 API Key 或 env value。
- 默认 metadata 模式不包含小说正文、用户指令或 hidden truth，但 trace/decision 仍是本地项目运行资料，不要提交 `runs/`。
- `runs/model_io/` 默认保留最近 500 份、总体积约 200MB；可用 `WRITERYANG_MODEL_IO_MAX_FILES`、`WRITERYANG_MODEL_IO_MAX_BYTES` 调整。只有显式设置 `WRITERYANG_MODEL_IO_MODE=full` 才保存完整内容，开启时必须把正文和隐藏设定落盘视为明确隐私选择。
- Web POST 请求体默认限制为 32MB；可用 `WRITERYANG_WEB_MAX_BODY_BYTES` 调整。调大前先确认本机内存和调用入口可信。

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
pytest tests/test_<area>.py -q
pytest -m "not real_api and not web_e2e" -q
ruff check .
mypy src scripts
python scripts/check_local.py
```

结构化 JSON Agent 的通用修复路径：

- JSON object 抽取统一走 `core/json_extract.py`，业务模块只包装领域异常。
- “生成 -> parse/validate -> repair retry -> parse/validate”统一走 `core/structured_generation.py`；如果 repair 后仍失败，调用方负责选择报错还是 fallback。
- Audit Agent 的 `audited_file` 如果被真实模型误填成章节标题等非文件名文本，应在 `audit_chapter()` 的 provider 输出边界按本次请求的 `draft.md` / `polished.md` 归一；只有模型明确返回另一个合法文件名时才作为 precheck mismatch。
- 非模型失败摘要写 `runs/app.log`；不要把 prompt、response、章节正文、完整用户请求或 API Key 写入该日志。

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
pytest tests/test_security.py -q
```

检查重点：

- `.env.example` 只包含变量名或空值，不写真实 key。
- `config/agents.yaml` 和 `config/embeddings.yaml` 只写 env name。
- 初始化模板不包含 raw API key。
- 错误输出、provider logs、model_io logs 不泄漏 Authorization/API Key。

## 9. 真实 API smoke 建议

真实 API 测试不要混入普通 CI。建议：

1. 在本机 `.env.real` 配好变量。
2. 用临时 workspace 运行，不污染真实项目或固定样板。
3. 所有 task 先用同一 provider 验证最短流程，再按 profile 调参；只有 `intent_router` 等少数 task 确实需要不同模型时才写 `tasks` 覆盖。
4. 失败后保留 `runs/` 作为证据。
5. 评审收口类改动的成功标准至少包括：`provider_ping --allow-network` 通过、`pytest -m real_api` 通过、一章最短 `smoke_session.py --provider config --model <model>` 完成并 `novel validate`、Markdown export 成功。`--model` 会写入临时项目 `config/agents.yaml` 的 default model，确保 Session 子命令也使用同一真实模型。发布前可再跑两章 accepted 的较长 smoke。

普通回归仍使用：

```bash
pytest -m "not real_api and not web_e2e" -q
```
