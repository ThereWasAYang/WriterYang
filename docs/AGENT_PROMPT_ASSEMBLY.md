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

Provider 解析时先读取 `config/agents.yaml` 顶层 `default` API，再合并当前 Agent 的差异字段；显式 `--provider mock` 会绕过真实 API 配置，仅用于测试/调试。

`ModelRequest` 字段：

- `system_prompt`：来自 `src/novel/prompts/{agent}_system.txt`。
- `user_prompt`：由对应 `build_*_user_prompt()` 函数组装。
- `context`：通常放 canon summary 或项目摘要，会进入 provider messages。
- `json_schema_name`：结构化输出 Agent 会设置，例如 `ChapterPlan`。使用 `json_object` 的 OpenAI-compatible provider 会自动在消息中补充明确的 `JSON` 输出提示，避免 DeepSeek 等服务端拒绝 JSON mode。
- `request_id`：自动生成，用于 `runs/model_io/` 和 provider log 关联。

内部 Agent 一律走 `AgentOutputContract`：

- JSON Agent：必须输出 JSON；不允许 Markdown 包装、解释、反问。
- Markdown Agent：必须输出正文 Markdown；不允许 JSON、分析说明、大纲、反问。
- Orchestrator/session 用户协商阶段可以使用 `user_facing` 语义，允许向作者提问。

如果内部 Agent 第一次输出“请补充/请确认/是否需要”之类问题，`generate_with_output_guard()` 会写 violation log，并自动追加一次 repair prompt 要求直接产出目标 artifact。第二次仍失败则不写正式文件。

通用上下文策略：

- 系统 prompt 通过 `core/prompts.py::load_prompt_template()` 解析 `{{partial:name}}`，共享片段放在 `src/novel/prompts/partials/`；ContextBundle 说明统一称为“系统检索出的长期记忆参考”，不假设 FTS 和 embedding 一定同时存在。
- `core/prompts.py` 维护聚合 `PROMPT_VERSION` 和逐模板 `PROMPT_VERSIONS`；修改单个 prompt 时必须更新对应模板版本。
- `--vector-context auto|on|off` 控制语义召回；`auto` 只在真实 embedding provider 配置完整且环境变量齐全时启用，失败会回退 FTS 并写入 ContextBundle warning。旧 `--use-vector-context` 是 `on` 的兼容别名。
- 检索 query 会拼接章节号、用户 instruction、ChapterPlan 的 goal/summary/must_include/scenes 等稳定信息。
- ChapterPlan 明确引用的 `timeline_event_ids` 会最高优先级进入 ContextBundle；此外，检索层会按结构化 focus entity ID 召回关键历史/记忆类 timeline event，不用自然语言关键词猜事件 ID。
- state/timeline 进入 prompt 前先走 `core/context_budget.py`：focus 实体和近 N 章保留全量，远期内容折叠成 digest；小项目未裁剪时仍渲染原 JSON，保护兼容性。

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

- `generate_with_output_guard()` 使用 Markdown contract，不为 Inspiration 额外开启 provider JSON mode；`--json` 只表示本地从 Markdown 派生并写入 `memory/inspiration.json`。
- `_ensure_markdown()` 确保 Markdown 非空；如果模型意外返回 `{"outline": "..."}` / `{"markdown": "..."}` 这类 JSON 包装，会先解包为 Markdown。
- `_brief_from_response()` 优先解析合法 `InspirationBrief` JSON，否则从 Markdown 小节提取 `InspirationBrief`。

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
- `validate_canon_proposal()` 检查重复 ID、跨类型冲突和 hidden truth 泄漏；canon drift proposal 可引用既有 canon ID，但新增对象仍不能和既有 ID 冲突。
- schema/validation 失败后 `_generate_canon_proposal_with_repair()` 再请求一次。
- `accept_chapter()` 后会触发 canon drift 检测，只在 `memory/chapters/{NNN}/canon_drift_proposal.json` 写人工确认用 proposal，不自动 apply canon。

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
- 已 accepted 章节的 `chapter_memory.json` 概览和重点记忆

Prompt 组装：

- `build_planning_user_prompt()` 写入项目、目标章节、schema 必填字段、引用规则、用户要求、预算化 state/timeline 视图，以及 ChapterMemory 检索导航上下文。
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
- 已 accepted 章节的 `chapter_memory.json` 红线保护视图

Prompt 组装：

- `build_writer_user_prompt()` 写入项目、章节、目标字数、用户写作要求、临时文风、ChapterPlan、style guide、canon、预算化 state/timeline、inspiration，以及只包含读者可见摘要、安全连续性提示和检索指针的 ChapterMemory。
- 正文输出要求写在 system prompt 和 user prompt：只输出可放入 `draft.md` 的 Markdown body，不要 YAML front matter、JSON、大纲、分析。

输出处理：

- Markdown contract：`chapter draft Markdown body`。
- 使用 stream 调用并合并正文。
- `_clean_body()` 去掉代码块包装。
- `render_draft_markdown()` 加 YAML front matter。
- `generate-chapter` 和 session 的默认 `polish.mode=single_pass` 会把 writer 的 `draft.md` 正文提升为 `polished.md`，front matter 标记 `created_by: writer_agent`、`polish_skipped: true`，然后继续 audit。

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

- `build_polish_user_prompt()` 写入 edit mode、是否保持长度、用户要求、ChapterPlan、draft metadata/body、style、canon、预算化 state/timeline、inspiration。
- edit mode 由 `resolve_edit_mode()` 解析：`light`、`normal`、`deep`。
- Polish Agent 仍可通过显式 `polish-chapter`、`--polish-mode auto` 或 Web UI 的“自动润色”开关运行；`review_gate` 会停在 `draft.md` 等待人工处理，不自动 audit。

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

- `build_audit_user_prompt()` 写入审核文件、严格模式、审核重点、deterministic summary、章节正文、plan、style、canon、预算化 state/timeline。
- system prompt 要求只输出 `AuditReport` JSON，并检查 canon/state/timeline/hidden truth/plan/style；timeline 审核必须区分 `narrative_position` 和 `story_position`，不能把倒序、插叙或回忆本身当作硬冲突。
- 当 medium+ 问题无法确认时，Audit Agent 可输出 `need_context` 请求章节正文、实体上下文或 query 上下文；编排层最多补取 1 轮、每轮最多 3 个请求，并写 `audit_recall.json`。

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

- `build_state_update_user_prompt()` 要求根据正文实际发生事件提取状态变化，并同时输出 timeline event 的正文呈现顺序 `narrative_position` 与故事世界顺序 `story_position`；输入使用预算化 state/timeline。
- 明确不要创造正文中没有发生的重大事件、不要修改 canon、无法判断写入 warnings。

输出处理：

- JSON contract：`StateUpdateProposal`。
- `parse_state_update_proposal()` 归一化常见别名，例如 `location -> location_id`。
- `validate_state_update_proposal()` 检查引用、重复 ID、物品 holder/location 冲突。
- `apply_state_update()` 写入前备份 state/timeline，失败时回滚。

## 9. ChapterMemory Agent

- Service：`core/chapter_memory.py`
- System prompt：`prompts/chapter_memory_system.txt`
- Provider loader：`load_chapter_memory_provider()`
- 入口函数：`generate_chapter_memory()`
- 触发方式：`accept_chapter()` 在 state/timeline 应用成功并标记 accepted 后 best-effort 触发；也可用 `novel chapter-memory generate/rebuild` 或 Web UI 章节列表手动重建。`chapter_memory.strict_accept: true` 只把失败记录为 error 级管理事件和醒目 warning，不阻断已经完成的 accepted 状态。
- 输出：`memory/chapters/{NNN}/chapter_memory.json`

输入来源：

- `project.yaml`
- accepted `memory/chapters/{NNN}/polished.md`
- `plan.json`
- 可选 `audit.json`
- 可选 `state_update_proposal.json`
- 可选 `state_update_apply_log.json`
- `memory/state/timeline.json`

Prompt 组装：

- `build_chapter_memory_user_prompt()` 写入来源文件 path/sha、ChapterPlan、AuditReport、StateUpdateProposal、ApplyLog、accepted 正文 metadata/body 和本章 timeline events。
- system/user prompt 都强调 ChapterMemory 只是压缩上下文和检索导航，不是事实源；冲突时以 canon、current_state、timeline 和 accepted `polished.md` 为准。
- 每个列表项必须带 `visibility` 和 `source_refs`；隐藏或敏感信息不得伪装成 `reader_visible`。

输出处理：

- JSON contract：`ChapterMemory`。
- `parse_chapter_memory()` 解析 provider JSON 后强制覆盖章节号、状态、来源 path/sha 和 generation_status。
- provider 不可用或输出无效时，`build_deterministic_chapter_memory()` 从 accepted 正文、plan、state proposal 和 timeline 生成保守 fallback，并写 warnings；writer 可见摘要只来自 accepted 正文，不直接使用 plan summary。
- `validate_chapter_memory()` 检查 source 文件、accepted 状态、`polished_sha256`、timeline id 和 `source_refs`；prompt/Web 热路径使用 freshness 轻量检查，必要时才重新计算 source sha。

## 10. Revision Agent

- Service：`core/revision.py`
- System prompt：`prompts/revision_system.txt`
- Provider loader：`load_revision_provider()`
- 入口函数：`revise_chapter()`
- 适用范围：低风险局部表达补丁。剧情结构变化应回到 Plot；人物刻画、铺垫、节奏、风格等写作实现问题应回到 Writer/Polish。
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

- `build_revision_user_prompt()` 写入源正文、用户修改要求、blocking audit issue 摘要、完整 audit report、style、canon、预算化 state/timeline。
- 如果 `--from-audit`，要求逐条修复 medium/high/critical issues，优先应用 suggested_fix，并避免原 evidence quote 以同一问题形式保留。

输出处理：

- Markdown contract：`revised chapter Markdown body`。
- 默认保存为新版本，不覆盖原稿；Session 流程会把通过修订产生的 `polished.vN.md` 提升为当前 `polished.md` 后重审。
- `_append_revision_log()` 记录版本来源、instruction、audit issue ids、provider。

## 11. Orchestrator

- Service：`core/orchestrator.py`
- System prompt：
  - `prompts/orchestrator_ask_intent_system.txt`：`novel ask` 的用户意图结构化分类。
  - `prompts/orchestrator_revision_route_system.txt`：用户修订意见路由。
  - `prompts/audit_repair_route_system.txt`：Audit 阻断问题的自动修复分流。
- 入口函数：`orchestrate()`、`plan_orchestration()`、`decide_ask_intent()`、`route_revision_request()`、`route_audit_repair()`。
- CLI：`novel ask`

输入来源：

- 用户自然语言 request。
- 可选 chapter number、provider、dry-run、安全限制。

行为：

- `decide_ask_intent()` 调用 Orchestrator provider 输出 `AskIntentDecision`。`classify_request()` 只保留为 dry-run/mock/provider 不可用时的 fallback。
- 关键词分类只能作为低风险 fallback。用户自然语言可能随意且包含错别字，高风险决策必须走结构化 Orchestrator/model decision、schema 校验和保守 fallback，不能只靠硬编码关键词。fallback 不得执行 memory repair apply、accept/archive、state/timeline/canon 写入等高风险动作。
- `HANDOFF_RULES` 限制允许的 agent handoff。
- dry-run 只输出计划，不写文件。
- 非 dry-run 调用对应底层 service。
- 用户对已生成内容提出修改意见时，`route_revision_request()` 调用 Orchestrator provider 输出 `RevisionRouteDecision` JSON；路由只能是 `plot_replan`、`writer_rewrite`、`revision_patch`。
- 路由输出解析或 Pydantic 校验失败时会 repair retry 一次；仍失败则保守 fallback 为 `writer_rewrite`，只有明确局部语句替换才 fallback 为 `revision_patch`。
- route decision 会写入 session 的 `revision_route_history`，并通过 Web UI/CLI 展示。
- 当请求被识别为 memory repair 时，orchestrator 作为项目管家调用 `core/memory_repair.py`：先生成 `MemoryRepairDecision`，再写 `MemoryRepairProposal`，不直接修改正式 memory；用户确认后通过显式 `memory-repair apply` 或结构化 `memory_repair_apply` 决策再 apply。
- memory repair 不是创意生成。它只读取项目文件、生成白名单 JSON Pointer operations、写 proposal/apply log，并通过 `management_events.jsonl` 通知用户后台记忆刷新。
- `setting-change suggest` 会先调用 `MemoryChangeClarificationDecision` 澄清 gate：只有创作意图本身不足、替换/删除目标不唯一或存在剧情含义歧义时，才返回 `needs_clarification` 和 1-3 个问题，保存到 `memory/repairs/clarifications/{clarification_id}/session.json`；用户通过 `setting-change answer` 或 Web UI 补充后再继续。
- 澄清 gate 不得要求用户选择目标文件、字段、visibility 或 JSON Pointer。人物/地点/物品/world/hidden_truths/foreshadowing 的默认映射由系统根据当前结构完成；新实体无 exact id/name/alias 匹配时默认新增。
- 最终生成 `MemoryRepairDecision` 时仍是 internal JSON task，`allow_user_questions=False`；需要追问只能通过 clarification schema 表达。memory repair / setting change prompt 会注入当前 memory 文件结构、集合 key、现有条目的 index/id/name 和 JSON Pointer 路径索引，模型不应再要求用户提供现有文件完整结构。数组新增必须使用 `/collection/-`，例如 `/characters/-` 或 `/hidden_truths/-`。
- setting change prompt 明确 `Character.role` 只表示叙事角色，新生成内容默认用 `主角`、`主要人物`、`配角`、`次要人物`；`谢家长女`、`唐门二房之女`、`江湖散人`、`武当俗家弟子` 等家族/门派/排行/职业身份必须进入 `tags`，可同步写入摘要或作者备注。生成和 apply 前都会做 setting_change 专用 semantic preflight；该 guard 只拦已知字段语义漂移，不用于用户意图路由。

注意：

- 当前推荐作者入口是 session；`ask` 主要用于创建或引导协作流程。
- Orchestrator 只有在修订路由等受控决策点调用模型；仍必须显式区分 user-facing 对话和 internal task 调度。

## 12. Creation Session

- Service：`core/session.py`
- 入口函数：`start_session()`、`revise_outline()`、`approve_outline()`、`run_session()`、`revise_content()`、`revise_audit()`、`retry_rewrite()`、`undo_rewrite()`、`accept_session()`、`archive_session()`。
- 文件：
  - `memory/sessions/{session_id}/session.json`
  - `outline_proposal.json/md`
  - `approved_outline.json/md`
  - `rewrite_events.json`
  - `rejections/chapter_{NNN}_round_{R}_before.md`
  - `memory/archive/{session_id}/manifest.json`

流程：

1. `start_session()` 创建 session，并调用 Plot Agent 为章节范围生成 outline proposal。
2. `revise_outline()` 把用户意见合并进 intent，重新生成 outline proposal。
3. `approve_outline()` 复制 proposal 为 approved outline。
4. `run_session()` 默认调用 Writer，把 `draft.md` 提升为 `polished.md` 后 Audit；配置 `polish.mode=auto` 或前端开启“自动润色”时才运行 Polish Agent。medium/high/critical issue 触发自动修复循环。每次打回前先记录 `rewrite_events.json`，并保存被打回的 `polished.md` 快照。正文问题先修订并提升 `polished.vN.md` 为当前 `polished.md` 后重审；连续失败或计划层问题会回退 Plot Agent 重写本章计划。
5. `revise_audit()` 用用户纠正意见重新审核被打回原文，写入 `audit_revision_history`。
6. `retry_rewrite()` 基于最新 audit 再次执行正文修订或重写计划；`undo_rewrite()` 恢复被打回快照并重审。
7. `revise_content()` 处理作者反馈或 audit issue。用户反馈先由 Orchestrator 判定：剧情级修改重写 plan 并重新 writer/polish/audit；写作实现级修改保留 plan、重写 draft/polished/audit；局部表达修改才调用 Revision Agent 生成版本稿并提升当前稿。audit 通过后重建 state proposal，仍有 medium/high/critical 时保持 `needs_revision`。
8. `accept_session()` 应用 state update 并标记 accepted。
9. `archive_session()` 复制本次创作文件并写 sha256 manifest。

Session 层是用户协作入口。它可以要求用户批准大纲和最终内容；底层内部 Agent 不能直接问用户。

## 13. Search Context、ChapterMemory 和 hidden truth

`core/search.py::retrieve_context_bundle()` 返回 `ContextBundle`：

- `included`：可进入 prompt 的上下文条目。
- `excluded`：被排除的条目，尤其是 hidden truth。
- `visibility`：`reader_visible`、`author_only`、`hidden_truth`、`audit_only`。
- `reason` / `priority` / `source`：用于解释检索结果。

策略：

- `plan` 和 `audit` 可以看到 hidden truth，但必须标记为内部参考。
- `write` 默认不把 hidden truth 原文放进 prompt。
- 如果开启 `--use-search-context`，默认使用 ChapterPlan 实体扩展 + 关键词/SQLite FTS 补充，并写 `context_report*.json` 供追踪。
- `chapter_memory.json` 会作为 `chapter_memory` 类型进入检索；Writer 的 ContextBundle 只拿到“这是导航指针，需要回源校验”的安全摘录，不直接注入可能包含 hidden truth 的原始 JSON excerpt。
- `--vector-context auto` 是默认语义召回策略：真实 embedding 配置和环境变量完整时启用，否则只用 FTS；`on` 强制尝试语义召回，`off` 关闭。`local_hash` 只允许显式测试路径，不作为真实业务 fallback。

## 14. Prompt 和日志排查

真实模型输出异常时看：

1. `runs/model_io/index.jsonl` 找 request_id。
2. 打开 `runs/model_io/{request_id}.json` 看 system prompt、user prompt、context、payload、response。
3. 如果被输出守卫拦截，看 `runs/agent_output_violations/{request_id}.json`。
4. 如果 provider 失败，看 `runs/provider_calls.jsonl`。

这些日志会包含小说正文、隐藏设定和用户指令。不要提交到 Git。
