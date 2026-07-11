# 作者如何手动编辑 memory 文件

WriterYang 的设计目标之一是让作者能直接编辑项目记忆。AI 生成结果不是黑盒数据库，而是普通文件。手动编辑后建议运行：

```bash
novel validate --path <project>
novel status --path <project>
```

如果改动影响某章正文，应创建 Revision Session 并重新 Audit。

## 基本原则

- ID 要稳定。已经被章节计划、状态、时间线引用的 `character/location/item/event` ID 不要随意改名。
- 读者可见信息和作者内部信息要分开。`hidden_truths.json` 的秘密不要写进 `reader_visible_summary`。
- 修改 canon 后，检查 state/timeline 是否还引用存在的 ID。
- 修改 state/timeline 后，检查章节 metadata、audit 和 export 是否仍然符合当前进度。
- 不要把 API Key 写入任何 memory、config 或 run log 文件。

## `memory/inspiration.md`

适合手动补充：

- 主题和氛围。
- 想保留的故事方向。
- 明确不想写的内容。
- 作者对整体节奏的要求。

不建议写成固定章节大纲。灵感文件应保持“弱总纲”，留给后续 canon 和章节计划细化。

## `memory/style_guide.md`

Web UI 顶部“文风设置”页可以直接编辑这个文件。保存时会先备份旧文件，再写入新的长期文风设置。

适合写：

- 叙事视角和时态。
- 语言密度、对白比例、动作描写偏好。
- 禁用词、禁用语气、禁用套路。
- 武侠、悬疑、都市、奇幻等类型的文风边界。

如果只是某一章临时要求，优先用命令参数 `--style-note` 或 `--instruction`，不要把临时要求写入长期 style guide。

## `memory/canon/*.json`

Canon 是长期设定。常见文件：

- `characters.json`：人物。
- `locations.json`：地点。
- `items.json`：物品。
- `world.json`：世界规则。
- `hidden_truths.json`：隐藏真相。
- `foreshadowing.json`：伏笔线。

编辑建议：

- 新增实体时使用小写下划线 ID，例如 `char_yan_qingci`。
- `reader_visible_summary` 只写读者当前或最终可见的概括。
- `private_author_notes` 可以写作者内部备注。
- `hidden_truths` 可以关联人物、地点、物品或世界规则，但不要直接泄漏到读者可见摘要。
- `foreshadowing_threads.related_entity_ids` 应引用已有实体 ID。

编辑后运行：

```bash
novel canon validate --path <project>
novel validate --path <project>
```

## `memory/state/current_state.json`

State 是“故事当前已经发生到哪里”。适合记录：

- 人物当前位置、健康、心理状态、已知信息、目标、持有物。
- 物品持有人、所在地点、状态、已知属性。
- 地点可达性、状态、正在发生的事件。
- `story_position.latest_chapter` 和当前故事时间。

不要把未来计划写进 current state。未来计划应放在 plan、hidden truths 或 foreshadowing。

## `memory/state/timeline.json`

Timeline 记录故事世界中已经确定存在的事件，包括尚未正文揭示的背景/前史。事件应尽量包含：

- `id`：稳定事件 ID。
- `summary`：事件摘要。
- `narrative_position`：正文呈现顺序，包含 chapter / scene / sequence；如果事件尚未在正文揭示，应省略或设为 `null`，不要使用 `chapter: 0`。
- `story_position`：故事世界顺序，包含 time_label / order / thread_id / certainty；非线性叙事无法确认真实顺序时可以不填 order。
- `participant_ids`：参与人物 ID。
- `location_id`：地点 ID。
- `state_change_ids`：相关状态变化 ID。

如果新增 timeline event，确认 ID 不重复、人物/地点引用存在，并区分正文呈现顺序与故事世界顺序。倒序、插叙、回忆应把 `narrative_position` 写成正文出现位置，不要为了故事内时间倒退章节或场景；开篇前/背景事件的时间写入 `story_position.time_label`。

## `memory/chapters/{chapter}/`

常见文件：

- `plan.json`：结构化章节计划。
- `plan.md`：给作者看的章节计划。
- `draft.md`：初稿。
- `polished.md`：润色稿。
- `audit.json`：一致性审核报告。
- `metadata.json`：章节状态。
- `revision_log.json`：修订记录。

编辑建议：

- 手动改 `draft.md` 或 `polished.md` 后，保留 YAML front matter。
- 不要让 `chapter_number` 与目录编号不一致。
- 改正文后通过 `revision-session run` 重新 Audit。
- 不要自行创建编号版本文件。未接受工作稿由 Session 冻结为 `candidates/candidate_{artifact_id}.md`；已认可章节必须通过 `revision-session` 创建范围受控的 immutable candidate。

## 推荐人工编辑流程

1. 改文件。
2. 运行 `novel validate --path <project>`。
3. 如果改了 canon，运行 `novel canon validate --path <project>`。
4. 如果改了已认可章节正文，运行 `novel revision-session start/run` 生成范围受控的 candidate 并重新 Audit。
5. 确认无阻塞问题后执行 `revision-session accept`，再重新 export。
