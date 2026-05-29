# Agent Prompt 组装说明

本文说明 WriterYang 每个 Agent 的输入由哪些文件和对象组装、system prompt 从哪里读取、user prompt 如何拼接、输出写到哪里，以及输出如何校验。

## 1. 统一调用链

典型 Agent 调用链：

```text
CLI/Web/Session
  -> load_*_provider()
  -> create_agent_provider(config/agents.yaml, agent_name)
  -> LoggingModelProvider
  -> service 构造 ModelRequest
  -> generate_with_output_guard()
  -> provider.generate() / provider.stream()
  -> parse / clean / schema validate
  -> atomic write artifact
```

`ModelRequest` 字段：

- `system_prompt`：来自 `src/novel/prompts/{agent}_system.txt`。
- `user_prompt`：由对应 `build_*_user_prompt()` 函数组装。
- `context`：通常放 canon summary 或项目摘要，会进入 provider messages。
- `json_schema_name`：结构化输出 Agent 会设置，例如 `ChapterPlan`。
- `request_id`：自动生成，用于 `runs/model_io/` 和 provider log 关联。

内部 Agent 一律走 `AgentOutputContract`：

- JSON Agent：必须输出 JSON；不允许 Markdown 包装、解释、反问。
- Markdown Agent：必须输出正文 Markdown；不允许 JSON、分析说明、大纲、反问。
- Orchestrator/session 用户协商阶段可以使用 `user_facing` 语义，允许向作者提问。

如果内部 Agent 第一次输出“请补充/请确认/是否需要”之类问题，`generate_with_output_guard()` 会写 violation log，并自动追加一次 repair prompt 要求直接产出目标 artifact。第二次仍失败则不写正式文件。

## 2. Inspiration Agent

- Service：`core/inspiration.py`
- System prompt：`prompts/inspiration_system.txt`
- Provider loader：`load_inspiration_provider()`
- 入口函数：`run_inspiration_agent()`
- 输出：
  - `memory/inspiration.md`
  - 可选 `memory/inspiration.json`

输入来源：

- `project.yaml`：标题、语言、类型、叙事信息。
- 用户直接文本或 `--input` 文件。

Prompt 组装：

- `build_inspiration_user_prompt(project, source_text)` 写入项目标题、语言、类型和用户原始灵感。
- 明确要求生成 Markdown 弱总纲，包含 `Source Summary`、`Themes`、`Mood`、`Weak Outline`、`Constraints`、潜在角色/地点/冲突。

输出处理：

- `generate_with_output_guard()` 使用 Markdown contract；`--json` 时允许 JSON payload。
- `_ensure_markdown()` 确保 Markdown 非空。
- `_brief_from_response()` 优先解析 JSON，否则从 Markdown 小节提取 `InspirationBrief`。

## 3. Canon Agent

- Service：`core/canon.py`
- System prompt：`prompts/canon_system.txt`
- Provider loader：`load_canon_provider()`
- 入口函数：`suggest_canon()`
- 输出：
  - 默认 stdout proposal。
  - `--output` 时保存 proposal JSON。
  - `canon apply` 后写入 `memory/canon/*.json`。

输入来源：

- `project.yaml`
- `memory/inspiration.md`
- 可选 `memory/inspiration.json`
- `memory/style_guide.md`
- 已存在 canon summary。

Prompt 组装：

- `build_canon_user_prompt()` 显式列出 `CanonProposal` JSON 结构。
- 要求稳定 ID 前缀：`char_`、`loc_`、`item_`、`rule_`、`truth_`、`thread_`。
- 要求 hidden truths 不混入 reader visible 字段。

输出处理：

- `AgentOutputContract(output_kind="json", json_schema_name="CanonProposal")`。
- `parse_canon_proposal()` 提取 JSON 并做低风险归一化。
- `validate_canon_proposal()` 检查重复 ID、跨类型冲突和 hidden truth 泄漏。
- schema/validation 失败后 `_generate_canon_proposal_with_repair()` 再请求一次。

## 4. Plot / Chapter Planning Agent

- Service：`core/planning.py`
- System prompt：`prompts/planning_system.txt`
- Provider loader：`load_planning_provider()`
- 入口函数：`plan_chapter()`
- 输出：
  - `memory/chapters/{NNN}/plan.json`
  - `memory/chapters/{NNN}/plan.md`

输入来源：

- `project.yaml`
- `memory/inspiration.md`
- 可选 `memory/inspiration.json`
- `memory/style_guide.md`
- canon summary
- `memory/state/current_state.json`
- `memory/state/timeline.json`
- 用户 `--instruction` / `--input`
- 可选 `ContextBundle.render_for_prompt()`

Prompt 组装：

- `build_planning_user_prompt()` 写入项目、目标章节、schema 必填字段、引用规则、用户要求。
- 明确禁止写正文、禁止修改 canon/state/timeline、禁止发明不存在的角色/地点/required_context ID。

输出处理：

- JSON contract：`ChapterPlan`。
- `parse_chapter_plan()` 提取 JSON、归一化常见形状、Pydantic 校验。
- `_validate_plan_for_write()` 检查 chapter_number 和跨文件引用。
- 缺失引用会阻断写 `plan.json`，避免错误扩散。

## 5. Writer Agent

- Service：`core/drafting.py`
- System prompt：`prompts/writer_system.txt`
- Provider loader：`load_drafting_provider()`
- 入口函数：`write_chapter_draft()`
- 输出：`memory/chapters/{NNN}/draft.md`

输入来源：

- `project.yaml`
- `memory/chapters/{NNN}/plan.json`
- `memory/inspiration.md`
- 可选 `memory/inspiration.json`
- `memory/style_guide.md`
- canon summary
- `current_state.json`
- `timeline.json`
- 用户 instruction、target words、style note
- 可选 search context

Prompt 组装：

- `build_writer_user_prompt()` 写入项目、章节、目标字数、用户写作要求、临时文风、ChapterPlan、style guide、canon、state、timeline、inspiration。
- 正文输出要求写在 system prompt 和 user prompt：只输出可放入 `draft.md` 的 Markdown body，不要 YAML front matter、JSON、大纲、分析。

输出处理：

- Markdown contract：`chapter draft Markdown body`。
- 使用 stream 调用并合并正文。
- `_clean_body()` 去掉代码块包装。
- `render_draft_markdown()` 加 YAML front matter。

## 6. Polish Agent

- Service：`core/polishing.py`
- System prompt：`prompts/polish_system.txt`
- Provider loader：`load_polishing_provider()`
- 入口函数：`polish_chapter()`
- 输出：`memory/chapters/{NNN}/polished.md`

输入来源：

- `project.yaml`
- `plan.json`
- `draft.md`
- `style_guide.md`
- `inspiration.md`
- canon summary
- current state / timeline
- 用户 instruction、style note、keep length、edit mode

Prompt 组装：

- `build_polish_user_prompt()` 写入 edit mode、是否保持长度、用户要求、ChapterPlan、draft metadata/body、style、canon、state、timeline、inspiration。
- edit mode 由 `resolve_edit_mode()` 解析：`light`、`normal`、`deep`。

输出处理：

- Markdown contract：`polished chapter Markdown body`。
- `_clean_polished_body()` 去掉代码块和包装。
- `render_polished_markdown()` 加 front matter，status 为 `polished`。

## 7. Audit Agent

- Service：`core/auditing.py`
- System prompt：`prompts/audit_system.txt`
- Provider loader：`load_audit_provider()`
- 入口函数：`audit_chapter()`
- 输出：`memory/chapters/{NNN}/audit.json`

输入来源：

- `project.yaml`
- `plan.json`
- `draft.md` / `polished.md`
- `style_guide.md`
- `inspiration.md`
- canon summary
- state JSON / timeline JSON
- deterministic precheck summary
- 用户 instruction、strict、focus
- 可选 search context

Prompt 组装：

- `build_audit_user_prompt()` 写入审核文件、严格模式、审核重点、deterministic summary、章节正文、plan、style、canon、state、timeline。
- system prompt 要求只输出 `AuditReport` JSON，并检查 canon/state/timeline/hidden truth/plan/style；timeline 审核必须区分 `narrative_position` 和 `story_position`，不能把倒序、插叙或回忆本身当作硬冲突。

输出处理：

- JSON contract：`AuditReport`。
- `run_deterministic_prechecks()` 先做文件、schema、front matter 和一致性预检查。
- `parse_audit_report()` 归一化 issue id 和 evidence。
- `combine_audit_reports()` 合并模型审核和 deterministic issues。
- `overall_status` 按 severity 收敛：critical -> blocked，medium/high -> needs_revision，low-only 可 passed。

## 8. State Update Agent

- Service：`core/state_update.py`
- System prompt：`prompts/state_update_system.txt`
- Provider loader：`load_state_update_provider()`
- 入口函数：`propose_state_update()`
- 输出：
  - `memory/chapters/{NNN}/state_update_proposal.json`
  - apply 后更新 `memory/state/current_state.json` 和 `memory/state/timeline.json`

输入来源：

- `project.yaml`
- `plan.json`
- `polished.md`
- `audit.json`
- canon summary
- current state JSON
- timeline JSON
- 用户 instruction

Prompt 组装：

- `build_state_update_user_prompt()` 要求根据正文实际发生事件提取状态变化，并同时输出 timeline event 的正文呈现顺序 `narrative_position` 与故事世界顺序 `story_position`。
- 明确不要创造正文中没有发生的重大事件、不要修改 canon、无法判断写入 warnings。

输出处理：

- JSON contract：`StateUpdateProposal`。
- `parse_state_update_proposal()` 归一化常见别名，例如 `location -> location_id`。
- `validate_state_update_proposal()` 检查引用、重复 ID、物品 holder/location 冲突。
- `apply_state_update()` 写入前备份 state/timeline，失败时回滚。

## 9. Revision Agent

- Service：`core/revision.py`
- System prompt：`prompts/revision_system.txt`
- Provider loader：`load_revision_provider()`
- 入口函数：`revise_chapter()`
- 输出：
  - `draft.vN.md` 或 `polished.vN.md`
  - `revision_log.json`

输入来源：

- `project.yaml`
- `plan.json`
- 目标源文件 `draft.md` 或 `polished.md`
- 可选 `audit.json`
- `style_guide.md`
- canon summary
- state / timeline
- 用户 instruction 或 `--from-audit`

Prompt 组装：

- `build_revision_user_prompt()` 写入源正文、用户修改要求、blocking audit issue 摘要、完整 audit report、style、canon、state、timeline。
- 如果 `--from-audit`，要求逐条修复 medium/high/critical issues，优先应用 suggested_fix，并避免原 evidence quote 以同一问题形式保留。

输出处理：

- Markdown contract：`revised chapter Markdown body`。
- 默认保存为新版本，不覆盖原稿；Session 流程会把通过修订产生的 `polished.vN.md` 提升为当前 `polished.md` 后重审。
- `_append_revision_log()` 记录版本来源、instruction、audit issue ids、provider。

## 10. Orchestrator

- Service：`core/orchestrator.py`
- 当前不是 LLM Agent，而是规则式受控编排器。
- 入口函数：`orchestrate()`、`plan_orchestration()`。
- CLI：`novel ask`

输入来源：

- 用户自然语言 request。
- 可选 chapter number、provider、dry-run、安全限制。

行为：

- `classify_request()` 用关键词和章节号识别任务。
- `HANDOFF_RULES` 限制允许的 agent handoff。
- dry-run 只输出计划，不写文件。
- 非 dry-run 调用对应底层 service。

注意：

- 当前推荐作者入口是 session；`ask` 主要用于创建或引导协作流程。
- 如果未来改成真实 LLM orchestrator，必须显式区分 user-facing 对话和 internal task 调度。

## 11. Creation Session

- Service：`core/session.py`
- 入口函数：`start_session()`、`revise_outline()`、`approve_outline()`、`run_session()`、`revise_content()`、`accept_session()`、`archive_session()`。
- 文件：
  - `memory/sessions/{session_id}/session.json`
  - `outline_proposal.json/md`
  - `approved_outline.json/md`
  - `memory/archive/{session_id}/manifest.json`

流程：

1. `start_session()` 创建 session，并调用 Plot Agent 为章节范围生成 outline proposal。
2. `revise_outline()` 把用户意见合并进 intent，重新生成 outline proposal。
3. `approve_outline()` 复制 proposal 为 approved outline。
4. `run_session()` 调用 Writer、Polish、Audit；medium/high/critical issue 触发自动修复循环。正文问题先修订并提升 `polished.vN.md` 为当前 `polished.md` 后重审；连续失败或计划层问题会回退 Plot Agent 重写本章计划。
5. `revise_content()` 处理作者反馈或 audit issue，生成版本稿、提升当前稿、重跑 audit；audit 通过后重建 state proposal，仍有 medium/high/critical 时保持 `needs_revision`。
6. `accept_session()` 应用 state update 并标记 accepted。
7. `archive_session()` 复制本次创作文件并写 sha256 manifest。

Session 层是用户协作入口。它可以要求用户批准大纲和最终内容；底层内部 Agent 不能直接问用户。

## 12. Search Context 和 hidden truth

`core/search.py::retrieve_context_bundle()` 返回 `ContextBundle`：

- `included`：可进入 prompt 的上下文条目。
- `excluded`：被排除的条目，尤其是 hidden truth。
- `visibility`：`reader_visible`、`author_only`、`hidden_truth`、`audit_only`。
- `reason` / `priority` / `source`：用于解释检索结果。

策略：

- `plan` 和 `audit` 可以看到 hidden truth，但必须标记为内部参考。
- `write` 默认不把 hidden truth 原文放进 prompt。
- 如果开启 `--use-search-context`，会写 `context_report*.json` 供追踪。

## 13. Prompt 和日志排查

真实模型输出异常时看：

1. `runs/model_io/index.jsonl` 找 request_id。
2. 打开 `runs/model_io/{request_id}.json` 看 system prompt、user prompt、context、payload、response。
3. 如果被输出守卫拦截，看 `runs/agent_output_violations/{request_id}.json`。
4. 如果 provider 失败，看 `runs/provider_calls.jsonl`。

这些日志会包含小说正文、隐藏设定和用户指令。不要提交到 Git。
