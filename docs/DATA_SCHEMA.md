# 数据结构说明（DATA_SCHEMA.md）

## 1. 目的

本文定义 AI 小说写作工具使用的核心数据结构。

系统将长期项目记忆保存为可编辑的 Markdown 和 JSON 文件。所有 Agent 都应读取并写入这些 schema，而不是只依赖聊天历史。

主要目标：

- 保持小说 canon 一致。
- 追踪角色、地点、物品、事件、timeline、hidden truth 和 state change。
- 让 Agent 之间交换结构化输出。
- 让所有生成数据都可编辑、可搜索、可 diff、可测试。
- 支持通过 Pydantic、Zod 或 JSON Schema 进行校验。

字段级真相以 `src/novel/core/schemas.py` 中的 Pydantic model 为准；`schemas/*.schema.json` 由这些 model 导出。本文侧重说明设计意图、人类可编辑约定和重要持久化路径，避免手写字段清单与生成 schema 漂移。

---

## 2. 设计原则

### 2.1 优先保证人类可编辑

所有持久化数据都应便于用户阅读和编辑。

优先格式：

- Markdown 用于正文较多的文档。
- JSON 用于结构化 state、canon、timeline 和 Agent 输出。
- YAML 用于 project 和 Agent 配置。

### 2.2 稳定 ID

每个重要实体都必须有稳定的 `id`。

示例：

```text
char_lin_che
loc_old_station
item_broken_ticket
event_001
thread_wet_ticket
```

ID 创建后不应再改变。

推荐 ID 前缀：

```text
char_      character（角色）
loc_       location（地点）
item_      item（物品）
event_     timeline event（timeline 事件）
change_    state change（状态变化）
thread_    foreshadowing thread（伏笔线）
truth_     hidden truth（隐藏真相）
style_     style profile（风格档案）
run_       agent run（Agent 运行）
```

### 2.3 Canon 与 State

系统必须区分：

- **Canon**：关于故事世界的相对稳定事实。
- **State**：故事中某个具体时间点的当前状态。

Canon 数据示例：

```json
{
  "id": "char_lin_che",
  "name": "林澈",
  "role": "protagonist",
  "reader_visible_summary": "年轻的旧物修复师，性格沉静。",
  "private_author_notes": "他小时候曾在旧车站失踪三天，但本人没有完整记忆。"
}
```

State 数据示例：

```json
{
  "entity_id": "char_lin_che",
  "chapter": 5,
  "health": "轻伤",
  "mental_state": "焦虑",
  "location_id": "loc_old_station"
}
```

### 2.4 读者可见信息与隐藏信息

有些信息对读者可见；有些信息是隐藏的，只应由 planning、audit 和后台 Agent 使用。

允许的 visibility 值：

```json
"reader_visible"
"hidden"
"partially_revealed"
```

含义：

- `reader_visible`：读者已经知道。
- `hidden`：只有系统和作者知道。
- `partially_revealed`：已有暗示，但尚未完全揭示。

### 2.5 尽可能 append-only

Timeline event、audit report、run log 和 chapter record 通常应保持追加写入。

除非用户明确要求修订早期章节，否则不要重写历史记录。

---

## 3. 项目文件布局

推荐项目布局：

```text
novel-project/
  project.yaml

  config/
    agents.yaml

  memory/
    inspiration.md
    inspiration.json
    style_guide.md
    search_index.json

    canon/
      characters.json
      world.json
      locations.json
      items.json
      hidden_truths.json
      foreshadowing.json

    state/
      current_state.json
      timeline.json

    chapters/
      001/
        plan.json
        plan.md
        draft.md
        draft.v2.md
        polished.md
        polished.v2.md
        audit.json
        state_update_proposal.json
        state_update_apply_log.json
        chapter_memory.json
        revision_log.json
      002/
        plan.json
        plan.md
        draft.md
        polished.md
        audit.json

  runs/
    run_*.json

  exports/
    novel.md
    novel.docx
    export_manifest.json
```

生成文件可能在对应 workflow step 执行前不存在。
`memory/search_index.json` 是可生成、可重建的文件。`polished.v2.md`
这类带版本的章节文件由 revision workflow 创建，用于避免静默覆盖旧稿。
已接受章节会在 `polished.md` front matter 中标记 `status: accepted` 和
`accepted_at`。已接受章节也可以包含 `chapter_memory.json`。它是供后续
Plot/Writer prompt 使用的结构化检索指南，不是权威事实来源；事实校验应以
canon、current_state、timeline 和已接受的 `polished.md` 为准。

---

## 4. 通用类型

### 4.1 EntityId

稳定的 string ID。

规则：

- 使用小写字母、数字和下划线。
- 尽可能按类型添加前缀。
- 不使用空格。
- 除非用户明确要求 migration，否则创建后不要改名。

示例：

```text
char_lin_che
loc_old_station
item_black_umbrella
event_001
```

### 4.2 Visibility

允许值：

```json
"reader_visible"
"hidden"
"partially_revealed"
```

### 4.3 Importance

允许值：

```json
"low"
"medium"
"high"
"critical"
```

### 4.4 通用 Status

允许值：

```json
"active"
"inactive"
"resolved"
"unresolved"
"deprecated"
```

### 4.5 Timestamp

使用 ISO 8601 格式。

示例：

```text
2026-05-22T00:00:00Z
```

---

## 5. ProjectConfig

文件：

```text
project.yaml
```

用途：

保存项目级基础配置。

示例：

```yaml
project_id: "novel_sample_project"
title: "新书名"
language: "zh-CN"
genre:
  - "悬疑"
  - "都市奇幻"
target_length:
  type: "long_novel"
  planned_chapters: 80
narration:
  pov: "third_person_limited"
  tense: "past"
default_style_profile_id: "style_default"
created_at: "2026-05-22T00:00:00Z"
updated_at: "2026-05-22T00:00:00Z"
```

必填字段：

- `project_id`
- `title`
- `language`
- `genre`
- `narration`
- `created_at`
- `updated_at`

说明：

- `project_id` 应保持稳定。
- `title` 后续可以修改。
- `genre` 可以包含多个值。

---

## 6. AgentConfig

文件：

```text
config/agents.yaml
```

用途：

定义默认 model API 和 per-agent 配置。真实项目应定义顶层 `default`；新项目会为标准 Agent 写入 `inherit_default: true` 和 default 参数快照。运行时如果看到 `inherit_default: true`，会直接使用当前 `default`，不会使用快照里的陈旧字段。

示例：

```yaml
default:
  provider: deepseek
  base_url_env: WRITERYANG_REAL_BASE_URL
  api_key_env: WRITERYANG_REAL_API_KEY
  model: deepseek-chat
  json_response_format: auto
  reasoning: medium
  thinking:
    type: disabled
  max_context_tokens: 128000
  max_tokens: 8192
  temperature: 0.5
  timeout_seconds: 60
  max_retries: 1

agents:
  orchestrator:
    inherit_default: true
    provider: deepseek
    base_url_env: WRITERYANG_REAL_BASE_URL
    api_key_env: WRITERYANG_REAL_API_KEY
    model: deepseek-chat
    json_response_format: auto
    reasoning: medium
    max_context_tokens: 128000
    max_tokens: 8192
    temperature: 0.5
    timeout_seconds: 60
    max_retries: 1

  audit:
    inherit_default: false
    provider: deepseek
    base_url_env: WRITERYANG_REAL_BASE_URL
    api_key_env: WRITERYANG_REAL_API_KEY
    model: deepseek-chat
    json_response_format: auto
    reasoning: low
    max_context_tokens: 128000
    max_tokens: 8192
    temperature: 0.2
    timeout_seconds: 60
    max_retries: 1
```

规则：

- 永远不要在此文件中保存原始 API key。
- 这里只保存环境变量名。
- `default` config 是真实项目中每个 Agent 的 fallback。
- `inherit_default: true` 只能用于非 `default` Agent，表示该 Agent 运行时完全使用当前 `default`；保存 default 时 Web API 会刷新这些 Agent 的参数快照。
- `inherit_default: false` 表示该 Agent 是独立完整配置，可覆盖 provider、model、base URL、reasoning mode、thinking、token limit 和 temperature。
- 没有 `inherit_default` 的旧 partial override 仍按历史规则与 `default` 合并，用于兼容旧项目。
- 每个 Agent 都可以覆盖 `json_response_format`。推荐保持 `auto`；`openai` 默认解析为 `json_schema`，`deepseek` / `zai` / `openai_compatible` 默认解析为 `json_object`。
- `mock` 仅用于测试和显式 debug run；不要把它作为真实项目默认值。
- 实现层应在运行 Agent 前验证所需环境变量是否存在。

`default` 或完整 per-agent config 中的必填字段：

- `provider`
- `model`
- `api_key_env`

推荐字段：

- `base_url_env`
- `json_response_format`
- `reasoning`
- `max_context_tokens`
- `temperature`
- `timeout_seconds`
- `max_retries`

---

## 7. StyleGuide

文件：

```text
memory/style_guide.md
```

用途：

定义写作风格、语气、叙事约束和用户偏好。

推荐 Markdown 结构：

```markdown
# 风格指南

## 整体风格

## 叙事 POV

## 文体要求

## 对白要求

## 节奏

## 避免事项

## 示例段落

## 修订记录
```

可选的机器可读版本：

```json
{
  "id": "style_default",
  "tone": ["冷静", "克制", "略带诗性"],
  "pov": "third_person_limited",
  "pace": "medium",
  "dialogue_style": "subtle",
  "avoid": ["过度解释", "网络爽文腔", "频繁使用感叹号"],
  "preferred_sentence_style": "中短句为主，关键场景允许长句"
}
```

---

## 8. InspirationBrief

文件：

```text
memory/inspiration.md
```

可选的机器可读版本：

```json
{
  "id": "inspiration_001",
  "source_type": "user_text",
  "source_summary": "用户想写一个从雨夜旧车站和神秘歌声开始的悬疑故事。",
  "themes": ["记忆", "失踪", "时间", "执念"],
  "mood": ["潮湿", "孤独", "神秘"],
  "weak_outline": "故事围绕一个旧物修复师展开。他在雨夜旧车站发现异常线索，并逐渐揭开母亲失踪与时间交叠的真相。",
  "constraints": [
    "不要一开始就解释超自然规则",
    "悬疑感优先于爽感"
  ],
  "created_at": "2026-05-22T00:00:00Z"
}
```

必填字段：

- `id`
- `source_type`
- `source_summary`
- `themes`
- `weak_outline`

推荐字段：

- `mood`
- `constraints`
- `created_at`

---

## 9. Character

文件：

```text
memory/canon/characters.json
```

用途：

保存 canonical 角色信息。

示例：

```json
{
  "characters": [
    {
      "id": "char_lin_che",
      "name": "林澈",
      "aliases": ["阿澈"],
      "role": "主角",
      "reader_visible_summary": "年轻的旧物修复师，性格沉静。",
      "private_author_notes": "他小时候曾在旧车站失踪三天，但本人没有完整记忆。",
      "appearance": {
        "age": 27,
        "gender": "男",
        "description": "身形偏瘦，常穿深色外套。"
      },
      "personality": {
        "traits": ["克制", "敏感", "执拗"],
        "flaws": ["不愿求助", "习惯隐瞒痛苦"],
        "desires": ["查清母亲失踪真相"],
        "fears": ["被遗忘", "再次失去重要的人"]
      },
      "relationships": [
        {
          "target_id": "char_shen_lu",
          "type": "盟友",
          "reader_visible": true,
          "description": "两人在旧车站相遇，彼此试探但逐渐信任。"
        }
      ],
      "abilities": [
        {
          "name": "旧物感知",
          "description": "触碰旧物时能看见残留记忆。",
          "limitations": "只能看到碎片，且会造成头痛。"
        }
      ],
      "secrets": [
        {
          "id": "secret_lin_che_missing_days",
          "visibility": "hidden",
          "description": "林澈失踪的三天其实发生在另一个时间层。",
          "planned_reveal": "第30章前后"
        }
      ],
      "tags": ["主角", "旧物修复", "失忆"]
    }
  ]
}
```

角色必填字段：

- `id`
- `name`
- `role`
  - 语义：叙事角色，不是家族身份、门派身份、排行、职业或江湖身份。
  - 新生成设定默认使用中文叙事角色值：`主角`、`主要人物`、`配角`、`次要人物`；历史数据中的 `protagonist`、`supporting`、`minor`、`antagonist` 仍兼容。
  - 例如 `谢家长女`、`谢家次子`、`唐门二房之女`、`江湖散人`、`武当俗家弟子` 应写入 `tags`，并可保留在 `reader_visible_summary` 或 `private_author_notes`。
- `reader_visible_summary`

角色推荐字段：

- `aliases`
- `appearance`
- `personality`
- `relationships`
- `abilities`
- `secrets`
- `tags`
- `private_author_notes`

Validation 规则：

- `id` 必须唯一。
- Relationship 的 `target_id` 应引用已有角色。
- Secret ID 在角色内部必须唯一。
- 设定变更 proposal 会额外做 Character.role 语义 preflight：明显身份短语不得写入 `role`，出现在摘要/备注中的身份短语应同步进入 `tags`。

---

## 10. Location

文件：

```text
memory/canon/locations.json
```

用途：

保存 canonical 地点及其属性。

示例：

```json
{
  "locations": [
    {
      "id": "loc_old_station",
      "name": "旧车站",
      "type": "交通设施",
      "reader_visible_summary": "废弃多年的郊区车站，雨夜时偶尔传出广播声。",
      "private_author_notes": "旧车站是两个时间层的交叠点。",
      "parent_location_id": null,
      "connected_location_ids": ["loc_station_tunnel", "loc_rain_bridge"],
      "rules": [
        {
          "id": "rule_station_midnight",
          "description": "午夜后，站台会短暂连通旧时间层。",
          "visibility": "hidden"
        }
      ],
      "tags": ["核心地点", "异常空间"]
    }
  ]
}
```

必填字段：

- `id`
- `name`
- `type`
- `reader_visible_summary`

Validation 规则：

- `id` 必须唯一。
- 如果 `parent_location_id` 不为 null，应引用已有地点。
- `connected_location_ids` 应尽可能引用已有地点。

---

## 11. Item

文件：

```text
memory/canon/items.json
```

用途：

保存重要剧情物品。

示例：

```json
{
  "items": [
    {
      "id": "item_broken_ticket",
      "name": "破损车票",
      "type": "线索",
      "reader_visible_summary": "一张被雨水泡皱的旧车票，只剩半截日期。",
      "private_author_notes": "车票上的完整日期对应林澈失踪的那一天。",
      "origin": "旧车站候车厅",
      "special_properties": [
        {
          "description": "靠近旧车站时会变得潮湿。",
          "visibility": "partially_revealed"
        }
      ],
      "tags": ["线索", "伏笔"]
    }
  ]
}
```

必填字段：

- `id`
- `name`
- `type`
- `reader_visible_summary`

Validation 规则：

- `id` 必须唯一。
- 重要剧情物品也应在 `current_state.json` 中拥有 state entry。

---

## 12. WorldRule

文件：

```text
memory/canon/world.json
```

用途：

保存世界规则、魔法体系、技术体系、社会结构或类型特定约束。

示例：

```json
{
  "world_rules": [
    {
      "id": "rule_memory_residue",
      "name": "旧物残响",
      "description": "强烈情绪会残留在旧物中，被特定的人感知。",
      "visibility": "reader_visible",
      "limitations": [
        "只能感知碎片",
        "不能直接改变过去",
        "感知会消耗精神"
      ],
      "known_by_character_ids": ["char_lin_che"]
    },
    {
      "id": "rule_time_overlap",
      "name": "时间层交叠",
      "description": "某些地点在特定条件下会连接过去的时间层。",
      "visibility": "hidden",
      "limitations": [
        "只能在雨夜或强烈情绪触发时出现",
        "持续时间有限"
      ],
      "known_by_character_ids": []
    }
  ]
}
```

必填字段：

- `id`
- `name`
- `description`
- `visibility`

Validation 规则：

- `id` 必须唯一。
- `known_by_character_ids` 应引用已有角色。

---

## 13. HiddenTruth

文件：

```text
memory/canon/hidden_truths.json
```

用途：

保存重大 hidden truth、反转和仅作者可见事实。

示例：

```json
{
  "hidden_truths": [
    {
      "id": "truth_station_is_overlap_point",
      "title": "旧车站是时间交叠点",
      "description": "旧车站并不是普通废弃建筑，而是两个时间层重合的入口。",
      "related_entity_ids": ["loc_old_station", "char_lin_che"],
      "visibility": "hidden",
      "planned_reveal": {
        "chapter": 28,
        "method": "通过沈鹿的回忆揭示"
      },
      "foreshadowing_ids": ["thread_station_broadcast", "thread_wet_ticket"],
      "importance": "critical"
    }
  ]
}
```

必填字段：

- `id`
- `title`
- `description`
- `visibility`
- `importance`

Validation 规则：

- `id` 必须唯一。
- `related_entity_ids` 应引用已有角色、地点或物品。
- 除非已经揭示，`planned_reveal.chapter` 应大于或等于当前章节。

---

## 14. EntityState

文件：

```text
memory/state/current_state.json
```

用途：

保存角色、物品和地点的当前 state。

示例：

```json
{
  "story_position": {
    "latest_chapter": 5,
    "in_story_time": "第3天，凌晨",
    "summary": "林澈和沈鹿刚从旧车站逃出。"
  },
  "character_states": [
    {
      "entity_id": "char_lin_che",
      "location_id": "loc_rain_bridge",
      "health": "轻伤，左臂擦伤",
      "mental_state": "紧张、困惑、强迫自己冷静",
      "knowledge": [
        "旧车站午夜后会出现异常广播",
        "沈鹿隐瞒了某些关于旧车站的事"
      ],
      "goals": [
        "查清破损车票上的日期",
        "弄清沈鹿的真实身份"
      ],
      "possessions": ["item_broken_ticket"],
      "last_updated_chapter": 5
    }
  ],
  "item_states": [
    {
      "entity_id": "item_broken_ticket",
      "holder_id": "char_lin_che",
      "location_id": null,
      "condition": "潮湿，边缘继续发黑",
      "known_properties": ["靠近旧车站时会变湿"],
      "last_updated_chapter": 5
    }
  ],
  "location_states": [
    {
      "entity_id": "loc_old_station",
      "accessibility": "暂时无法进入",
      "condition": "站台消失，只剩普通废墟",
      "active_events": [],
      "last_updated_chapter": 5
    }
  ]
}
```

必填字段：

- `story_position`
- `character_states`
- `item_states`
- `location_states`

Validation 规则：

- `entity_id` 应尽可能引用已有 canon entity。
- `last_updated_chapter` 不得大于 `story_position.latest_chapter`。
- 除非明确允许，同一物品不应同时拥有 `holder_id` 和 `location_id`。
- 角色 possession 应与对应 item state 匹配。

---

## 15. TimelineEvent

文件：

```text
memory/state/timeline.json
```

用途：

保存故事中已经发生或已经揭示的事件。Timeline 使用两条轨道：

- `narrative_position`：事件在已写章节中出现的位置。
- `story_position`：事件在故事世界中发生的时间。

这种分离允许倒叙、逆时序、回忆和非线性多线叙事，而不会把它们误判为 timeline conflict。

示例：

```json
{
  "events": [
    {
      "id": "event_001",
      "chapter": 1,
      "scene": 2,
      "in_story_time": "第1天，23:40",
      "narrative_position": {
        "chapter": 1,
        "scene": 2,
        "sequence": 1
      },
      "story_position": {
        "time_label": "第1天，23:40",
        "order": 1,
        "thread_id": "main",
        "certainty": "certain"
      },
      "event_role": "current_action",
      "location_id": "loc_old_station",
      "participant_ids": ["char_lin_che"],
      "summary": "林澈在旧车站第一次听见已经停用的广播。",
      "reader_visible": true,
      "causes": [],
      "effects": [
        "林澈开始调查旧车站",
        "破损车票首次出现"
      ],
      "state_change_ids": ["change_001", "change_002"],
      "tags": ["开端", "异常事件"]
    }
  ]
}
```

必填字段：

- `id`
- `chapter`
- `in_story_time`
- `narrative_position`
- `story_position`
- `summary`
- `reader_visible`

推荐字段：

- `scene`
- `event_role`
- `location_id`
- `participant_ids`
- `causes`
- `effects`
- `state_change_ids`
- `tags`

Validation 规则：

- Event ID 必须唯一。
- Event 应按 `narrative_position.chapter`、`narrative_position.scene` 和 `narrative_position.sequence` 排序。
- Legacy `chapter`、`scene`、`in_story_time` 必须分别匹配 `narrative_position.chapter`、`narrative_position.scene` 和 `story_position.time_label`。
- `causes` / `effects` 的顺序只有在两个 event 位于同一 `story_position.thread_id` 且拥有可比较的 `story_position.order` 时，才构成硬性冲突。
- 当真实故事世界顺序未知时，可以省略 `story_position.order`；不要从叙事顺序反推它。
- Participants 应引用已有角色。
- 如果提供 location，应引用已有地点。

对于非线性叙事，`narrative_position` 保持在文本揭示该事件的 chapter/scene；较早或平行的故事内时间写入 `story_position.time_label`。

---

## 16. StateChange

用途：

记录某章如何改变世界 state。

通常在章节写完后生成。

示例：

```json
{
  "state_changes": [
    {
      "id": "change_001",
      "chapter": 1,
      "entity_id": "char_lin_che",
      "field": "knowledge",
      "old_value": [],
      "new_value": ["旧车站午夜后会出现异常广播"],
      "reason": "林澈亲耳听见广播",
      "source": "chapter_001"
    },
    {
      "id": "change_002",
      "chapter": 1,
      "entity_id": "item_broken_ticket",
      "field": "holder_id",
      "old_value": null,
      "new_value": "char_lin_che",
      "reason": "林澈在候车厅捡到破损车票",
      "source": "chapter_001"
    }
  ]
}
```

必填字段：

- `id`
- `chapter`
- `entity_id`
- `field`
- `new_value`
- `reason`
- `source`

推荐字段：

- `old_value`

Validation 规则：

- `entity_id` 应引用已有角色、地点或物品。
- `chapter` 应匹配生成该 change 的章节。

---

## 17. ForeshadowingThread

文件：

```text
memory/canon/foreshadowing.json
```

用途：

追踪线索、伏笔、悬念和 payoff 计划。

示例：

```json
{
  "threads": [
    {
      "id": "thread_wet_ticket",
      "type": "伏笔",
      "title": "破损车票会变湿",
      "introduced_in_chapter": 1,
      "description": "破损车票在靠近旧车站时会变得潮湿。",
      "reader_visible": true,
      "hidden_truth": "车票属于另一个时间层，因此会响应旧车站的时间交叠。",
      "status": "unresolved",
      "planned_payoff": {
        "chapter": 12,
        "description": "林澈发现车票上的水并不是雨水，而是过去某晚的积水。"
      },
      "related_entity_ids": ["item_broken_ticket", "loc_old_station"],
      "importance": "high"
    }
  ]
}
```

必填字段：

- `id`
- `type`
- `title`
- `introduced_in_chapter`
- `description`
- `status`
- `importance`

推荐字段：

- `reader_visible`
- `hidden_truth`
- `planned_payoff`
- `related_entity_ids`

Validation 规则：

- `id` 必须唯一。
- `introduced_in_chapter` 必须是有效章节号。
- `planned_payoff.chapter` 应大于或等于 `introduced_in_chapter`。
- `related_entity_ids` 应尽可能引用已有 entity。

---

## 18. ChapterPlan

文件：

```text
memory/chapters/{chapter_number}/plan.json
```

用途：

保存章节的 structured plan。

示例：

```json
{
  "chapter_number": 6,
  "title": "桥下的回声",
  "goal": "让林澈意识到破损车票与母亲失踪有关。",
  "summary": "林澈回到修复铺，试图烘干车票，却发现车票上的水迹组成了一个日期。",
  "required_context": {
    "canon_entity_ids": ["char_lin_che", "item_broken_ticket"],
    "state_entity_ids": ["char_lin_che", "item_broken_ticket"],
    "timeline_event_ids": ["event_001", "event_008"]
  },
  "scenes": [
    {
      "scene_number": 1,
      "location_id": "loc_repair_shop",
      "participant_ids": ["char_lin_che"],
      "purpose": "展示林澈试图理性处理异常事件",
      "summary": "林澈回到修复铺，用旧台灯照着车票，发现水迹没有蒸发。",
      "emotional_beat": "压抑、怀疑",
      "plot_points": [
        "车票水迹组成日期",
        "日期与母亲失踪当天一致"
      ]
    }
  ],
  "must_include": [
    "车票上的日期",
    "林澈对母亲失踪的回忆"
  ],
  "must_avoid": [
    "直接解释旧车站的完整真相"
  ],
  "expected_state_changes": [
    "林澈知道车票日期与母亲失踪有关"
  ],
  "ending_hook": "窗外传来旧车站广播里的同一首歌。"
}
```

必填字段：

- `chapter_number`
- `title`
- `goal`
- `summary`
- `scenes`
- `must_include`
- `must_avoid`
- `ending_hook`

推荐字段：

- `required_context`
- `expected_state_changes`

Validation 规则：

- `chapter_number` 必须为正数。
- Scene number 应从 1 开始并按顺序递增。
- `location_id` 应尽可能引用已有地点。
- `participant_ids` 应尽可能引用已有角色。

---

## 19. DraftChapter

文件：

```text
memory/chapters/{chapter_number}/draft.md
```

用途：

保存原始章节 draft。

推荐 metadata header：

```markdown
---
chapter_number: 6
title: 桥下的回声
status: draft
created_by: writer_agent
created_at: 2026-05-22T00:00:00Z
---

# 第六章 桥下的回声

正文……
```

规则：

- Draft 内容可在 revision 过程中覆盖。
- 如果后续启用 versioning，应保留 major version。

---

## 20. PolishedChapter

文件：

```text
memory/chapters/{chapter_number}/polished.md
```

用途：

保存润色后的章节。

推荐 metadata header：

```markdown
---
chapter_number: 6
title: 桥下的回声
status: polished
created_by: polish_agent
based_on: draft.md
created_at: 2026-05-22T00:00:00Z
---

# 第六章 桥下的回声

正文……
```

规则：

- 这是 export 的优先来源。
- 在 audit 通过或用户批准已知问题前，不应把润色章节标记为 accepted。

---

## 21. AuditReport

文件：

```text
memory/chapters/{chapter_number}/audit.json
```

用途：

保存一致性、风格、剧情和 state 的 audit 结果。

示例：

```json
{
  "chapter_number": 6,
  "audited_file": "polished.md",
  "overall_status": "needs_revision",
  "summary": "本章整体符合剧情方向，但存在一个物品状态矛盾。",
  "issues": [
    {
      "id": "audit_006_001",
      "severity": "high",
      "type": "state_conflict",
      "description": "第5章结尾车票仍由林澈持有，但本章第2场景写成车票在沈鹿手中。",
      "evidence": [
        {
          "source": "memory/state/current_state.json",
          "quote": "item_broken_ticket holder_id = char_lin_che"
        },
        {
          "source": "memory/chapters/006/polished.md",
          "quote": "沈鹿把那张破损车票放回桌上。"
        }
      ],
      "suggested_fix": "改为林澈把车票放在桌上，沈鹿只是看见它，而不是持有它。"
    }
  ],
  "passed_checks": [
    "style_match",
    "chapter_goal_match",
    "no_major_canon_conflict"
  ],
  "created_at": "2026-05-22T00:00:00Z"
}
```

必填字段：

- `chapter_number`
- `audited_file`
- `overall_status`
- `summary`
- `issues`
- `created_at`

允许的 `overall_status` 值：

```json
"passed"
"needs_revision"
"blocked"
```

允许的 issue severity 值：

```json
"low"
"medium"
"high"
"critical"
```

允许的 issue type 示例：

```json
"canon_conflict"
"state_conflict"
"timeline_conflict"
"style_mismatch"
"plot_logic_issue"
"character_voice_issue"
"continuity_issue"
"premature_reveal"
```

Validation 规则：

- 如果 `overall_status` 是 `passed`，不应存在 `high` 或 `critical` issue。
- 除非 issue type 纯粹用于提示信息，否则每个 issue 都应包含修复建议。

### 21.1 ChapterMemory

文件：

```text
memory/chapters/{chapter_number}/chapter_memory.json
```

用途：

为已 accepted 章节保存结构化、关联 source 的 memory。它压缩 reader-visible
summary、剧情节拍、角色知识变化、state change、timeline event ID、
未关闭线索、foreshadowing、连续性备注、检索提示和生成 warning。

规则：

- 只为 accepted 章节生成。
- 将其视为辅助检索和 context guide，而不是 source of truth。
- 如果它与 canon、current_state、timeline 或 accepted `polished.md` 冲突，
  以这些权威文件为准。
- 每个列表项都应包含 `visibility` 和 `source_refs`；敏感信息不得标记为
  `reader_visible`。
- `source.polished_sha256` 应匹配已 accepted 的 `polished.md`；过期 memory
  应产生 warning，且不应静默替代 source verification。

---

## 22. AgentRunLog

文件：

```text
runs/{run_id}.json
```

用途：

记录一次 generation run 中发生的事情。

示例：

```json
{
  "run_id": "run_20260522_001",
  "task": "generate_chapter",
  "chapter_number": 6,
  "started_at": "2026-05-22T00:00:00Z",
  "ended_at": "2026-05-22T00:03:12Z",
  "status": "completed",
  "steps": [
    {
      "step_id": "step_001",
      "agent": "plot_agent",
      "input_files": [
        "memory/canon/characters.json",
        "memory/state/current_state.json"
      ],
      "output_files": [
        "memory/chapters/006/plan.json"
      ],
      "status": "completed"
    },
    {
      "step_id": "step_002",
      "agent": "writer_agent",
      "input_files": [
        "memory/chapters/006/plan.json"
      ],
      "output_files": [
        "memory/chapters/006/draft.md"
      ],
      "status": "completed"
    }
  ],
  "errors": []
}
```

必填字段：

- `run_id`
- `task`
- `started_at`
- `status`
- `steps`

推荐字段：

- `chapter_number`
- `ended_at`
- `errors`

允许的 status 值：

```json
"pending"
"running"
"completed"
"failed"
"cancelled"
```

---

## 23. ExportManifest

文件：

```text
exports/export_manifest.json
```

用途：

记录已 export 的文件。

示例：

```json
{
  "exports": [
    {
      "id": "export_001",
      "type": "markdown",
      "source_chapters": [1, 2, 3, 4, 5, 6],
      "output_path": "exports/novel.md",
      "created_at": "2026-05-22T00:00:00Z"
    },
    {
      "id": "export_002",
      "type": "docx",
      "source_chapters": [1, 2, 3, 4, 5, 6],
      "output_path": "exports/novel.docx",
      "created_at": "2026-05-22T00:01:00Z"
    }
  ]
}
```

每个 export 的必填字段：

- `id`
- `type`
- `source_chapters`
- `output_path`
- `created_at`

允许的 export type：

```json
"markdown"
"docx"
"html"
"txt"
```

---

## 24. MVP 最小 Schema

首版实现只需要这些 schema：

1. `ProjectConfig`
2. `AgentConfig`
3. `Character`
4. `Location`
5. `Item`
6. `WorldRule`
7. `EntityState`
8. `TimelineEvent`
9. `ChapterPlan`
10. `AuditReport`

如果会拖慢 MVP 开发，不要一次性实现所有 schema。

推荐 MVP 文件：

```text
project.yaml
config/agents.yaml
memory/canon/characters.json
memory/canon/locations.json
memory/canon/items.json
memory/canon/world.json
memory/state/current_state.json
memory/state/timeline.json
memory/chapters/001/plan.json
memory/chapters/001/draft.md
memory/chapters/001/polished.md
memory/chapters/001/audit.json
```

---

## 25. 设定变更 / 记忆修复持久化产物

设定变更和记忆修复共享同一套 proposal/apply 机制。用户输入自然语言后，系统先生成可审查 proposal；只有用户确认 apply 后才会修改正式 memory 文件。

主要路径：

```text
memory/repairs/{repair_id}/proposal.json
memory/repairs/{repair_id}/proposal.md
memory/repairs/{repair_id}/apply_log.json
memory/repairs/clarifications/{clarification_id}/session.json
memory/management_events.jsonl
```

核心 schema：

- `MemoryRepairProposal`：记录 `repair_id`、`change_kind`、原始请求、目标文件、JSON Pointer operations、影响分析、follow-up action、风险等级、置信度、假设和 notes。
- `MemoryRepairApplyLog`：记录 apply 状态、目标文件、备份路径和错误。apply 失败时应尽量从备份回滚。
- `MemoryChangeClarificationSession`：记录澄清会话的问题、用户回答、状态和最终 proposal 路径。
- `MemoryChangeBatchPlan`：把复杂设定变更拆成多个 batch，降低单次模型输出长度和超时风险。
- `MemoryChangeImpact` 与 `MemoryChangeFollowupAction`：记录受影响实体、章节、Session、已认可章节和后续动作建议。

重要约定：

- 允许自动 apply 的目标文件只限白名单 memory JSON：`memory/state/timeline.json`、`memory/state/current_state.json` 和 `memory/canon/*.json` 中的 canon 文件。
- `operations` 使用 JSON Pointer；新增集合元素应使用 `/-` append 路径。
- `change_kind=setting_change` 时，系统会额外做语义 preflight，例如 Character.role 不得写入家族身份、门派身份、排行、职业或江湖身份。
- 已 accepted 或 archived 的章节不会被静默改写；影响分析和 follow-up action 用于提示用户后续是否需要重写、重审或同步 Session。
- 字段级结构请查看生成的 `schemas/memory_repair_proposal.schema.json`、`schemas/memory_change_clarification_session.schema.json`、`schemas/memory_change_batch_plan.schema.json` 和 `schemas/memory_change_impact.schema.json`。

---

## 26. Validation 要求

### 26.1 ID 唯一性

实现层应验证：

- Character ID 唯一。
- Location ID 唯一。
- Item ID 唯一。
- Timeline event ID 唯一。
- Foreshadowing thread ID 唯一。
- Hidden truth ID 唯一。

### 26.2 Reference 完整性

以下情况系统应给出 warning：

- Relationship 指向缺失角色。
- State 引用缺失 entity。
- Timeline event 引用缺失地点。
- Item holder 不存在。
- Foreshadowing thread 引用缺失 hidden truth。
- Character possession 与 item state 冲突。

### 26.3 章节一致性

在把 polished chapter 保存为 accepted 前，系统应检查：

- 不与已知 canon 矛盾。
- 不使用不可能的 item location。
- 不让角色获得尚未取得的知识。
- 除非用户批准，不早于计划揭示 hidden truth。
- 重大事件后更新 state change。
- 不与最新 timeline 矛盾。

### 26.4 Agent output validation

任何输出 JSON 的 Agent 都必须生成匹配相关 schema 的有效数据。

示例：

- Inspiration Agent 输出 `InspirationBrief`。
- Plot Agent 输出 `ChapterPlan`。
- Audit Agent 输出 `AuditReport`。
- State Manager 输出 `StateUpdateProposal`。
- Export Agent 输出 `ExportManifest`。

---

## 27. 未来扩展

未来可增加的 schema：

- `ReaderFeedback`
- `RevisionRequest`
- `VersionSnapshot`
- `SceneCard`
- `DialogueVoiceProfile`
- `ThemeTracker`
- `PublishingMetadata`
- `SeriesBible`
- `UserPreferenceProfile`
- `PromptTemplate`
- `ModelEvaluationReport`

---

## 28. 给 Codex 的实现说明

实现 schema 时：

### Python 版本

使用 Pydantic model。

建议位置：

```text
src/novel_writer/schemas/
  project.py
  agents.py
  canon.py
  state.py
  chapter.py
  audit.py
  export.py
```

### TypeScript 版本

使用 Zod schema。

建议位置：

```text
src/schemas/
  project.ts
  agents.ts
  canon.ts
  state.ts
  chapter.ts
  audit.ts
  export.ts
```

### 通用规则

- Schema definition 与 UI 保持独立。
- 为 validation rule 添加测试。
- 不要静默忽略无效 reference。
- 对可恢复的 reference issue 优先给出 warning。
- 对无效 JSON、缺失必填字段、重复 ID 和不可能状态优先报错。
