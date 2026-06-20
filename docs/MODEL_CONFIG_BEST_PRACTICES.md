# 模型配置最佳实践

WriterYang 支持给不同能力 profile 配不同模型，并允许少数高杠杆 task 做独立覆盖。推荐原则是：结构化和审核类任务低温、稳定；正文和润色类任务更重视中文表达、长上下文和风格控制。

## Provider 字段

真实项目必须先配置 `config/agents.yaml` 顶层 `default` API。新项目默认让 4 个 profile 继承 `default`，YAML 中保存 `profiles` 和可选 `tasks`。运行时按 `task -> profile -> default` 解析；共同窗口、输出长度和超时优先放在 `default`，profile 只在确实需要差异时写 patch。task patch 用于微调 `temperature`、`thinking`、`reasoning`，也允许 `intent_router` 等高杠杆 task 单独覆盖 provider/model/base URL/API env/token/timeout。真实 API Key 写入项目 `.env`，YAML 只保存环境变量名。

```yaml
default:
  provider: "deepseek"
  base_url_env: "WRITERYANG_REAL_BASE_URL"
  api_key_env: "WRITERYANG_REAL_API_KEY"
  model: "deepseek-chat"
  json_response_format: "auto"
  max_context_tokens: 128000
  max_tokens: 24000
  timeout_seconds: 120
profiles:
  scribe:
    inherit_default: true
    max_tokens: 32000
    timeout_seconds: 180
  architect:
    inherit_default: true
  loremaster:
    inherit_default: true
  clerk:
    inherit_default: true
tasks:
  intent_router:
    inherit_default: false
    provider: "deepseek"
    base_url_env: "WRITERYANG_REAL_BASE_URL"
    api_key_env: "WRITERYANG_REAL_API_KEY"
    model: "deepseek-chat"
    json_response_format: "auto"
    thinking:
      type: "disabled"
    max_context_tokens: 128000
    temperature: 0.2
    max_tokens: 8192
    timeout_seconds: 60
```

解析顺序：显式 `--provider mock` 测试覆盖 > task override > task 内置业务默认 > profile 配置 > `default`。`profiles` 只允许 `scribe`、`architect`、`loremaster`、`clerk`；`tasks` 只允许已登记 task。`inherit_default: true` 的 profile 不写 patch 字段时完整跟随 `default`；写入 `max_tokens`、`max_context_tokens`、`timeout_seconds` 等字段时才覆盖对应参数。`temperature`、`thinking`、`reasoning` 是 task-only 字段，只能通过内置 task 默认或 `tasks.<task>` 覆盖；写入 `default` 或 `profiles.*` 会被 schema 拒绝。旧的 `agents:` 任务键配置已经移除，不再有 fallback agent 借用逻辑。缺少 `default`、profile 或 task 配置不完整时，`validate`、`doctor` 和 Web UI 会告警，运行到未配置 task 时会失败。

`config/agents.yaml` 的 `provider` 当前支持：

- `openai`：标准 OpenAI API，默认 `https://api.openai.com/v1`。
- `openai_compatible`：通用 Chat Completions 兼容接口，需要 `base_url_env`。
- `deepseek`：DeepSeek 官方 API，支持 `thinking.type`。
- `zai`：智谱 GLM 官方 API，默认 `https://open.bigmodel.cn/api/paas/v4`，支持 `thinking.type`。
- `mock`：仅测试/调试，不调用 API。真实创作不要把它作为 `default`。

真实 API Key 只能放在环境变量中或项目根目录 `.env` 中。配置文件只写环境变量名，例如：

```yaml
api_key_env: "WRITER_API_KEY"
```

## JSON 输出格式

结构化 task 会在 `ModelRequest.json_schema_name` 中声明目标 schema。`json_response_format` 控制 provider adapter 如何把这个目标传给模型：

- `auto`：默认值。`openai` 解析为 `json_schema`；`deepseek`、`zai`、`openai_compatible` 解析为 `json_object`。
- `json_object`：发送 `response_format: {"type":"json_object"}`，并自动追加标准 JSON mode guard 和紧凑 schema skeleton。[DeepSeek 官方 JSON Output](https://api-docs.deepseek.com/zh-cn/guides/json_mode) 要求 prompt 中包含 `json` 和期望结构示例，推荐 DeepSeek 使用此模式。
- `json_schema`：发送非 strict JSON schema。适合标准 OpenAI 或明确支持该参数的 OpenAI-compatible 服务。
- `json_schema_strict`：仅用于显式 opt-in。当前只允许 `openai` 和通用 `openai_compatible` 尝试；DeepSeek / ZAI 会在本地拒绝。OpenAI strict 会先把 schema 转成 strict-compatible 子集，无法转换时 fail fast，不发请求。

推荐保持 `auto`。只有在确认目标 provider 支持对应参数，并且已有真实 API smoke 覆盖后，再对 profile 或单个 task 覆盖 `json_response_format`。

Web UI 会按 provider 限制 `json_response_format`：DeepSeek / ZAI 只允许 `auto` 或 `json_object`；OpenAI 和通用 OpenAI-compatible 可以使用现有四个取值。不可用值会在保存前被拒绝。

## Thinking 开关

默认建议：

```yaml
thinking:
  type: "disabled"
```

原因：

- `writer`、`polish` 的输出会直接写入 Markdown，关闭 thinking 可降低把分析性内容混入正文的风险。
- `canon`、`state_update`、`chapter_memory` 需要严格 JSON，关闭 thinking 更容易保持输出干净。
- `plot`、`audit` 在复杂长篇中可以单独改成 `enabled`，用于增强推理和一致性检查。
- 这些字段只在 task 覆盖中调整。运行时 `reasoning` 只在 DeepSeek 且 `thinking.type: enabled` 时作为 `reasoning_effort` 发送；DeepSeek 开启 thinking 后不会发送 `temperature`。

## 按 Profile 配置建议

| Profile | 合并的 task | 能力重点 | 推荐配置 |
| --- | --- | --- | --- |
| `scribe` | `writer`、`polish`、`revision` | 中文长文生成、文风保持、角色声音、事实保持、长输出。 | `max_tokens: 16000-32000`，`max_context_tokens: 128000`，较长 `timeout_seconds`。 |
| `architect` | `plot`、`audit` | 长上下文剧情推理、一致性核对、伏笔控制、结构化 JSON。 | `max_context_tokens: 128000+`，`max_tokens: 8192`，结构化输出稳定优先。 |
| `loremaster` | `inspiration`、`style_guide`、`canon` | 创意构思、中文表达、稳定 JSON/ID、低频设定生成。 | `max_context_tokens: 64000`，`max_tokens: 8192`，中文表达和成本平衡。 |
| `clerk` | `state_update`、`chapter_memory`、`intent_router`、`memory_repair`、`setup` | 低创意抽取、分类路由、JSON patch、快速稳定、成本可控。 | `max_context_tokens: 64000`，`max_tokens: 4096-8192`，低延迟和低成本优先。 |

表中的 `max_tokens`、`max_context_tokens`、`timeout_seconds` 属于 default/profile 能力参数。Profile 勾选 `inherit_default: true` 后默认跟随 `default`；需要让某个 profile 与 default 不同时，再在 profile patch 中覆盖这些参数。task override 默认只建议覆盖 `temperature`、`thinking`、`reasoning`，只有 `intent_router`、`memory_repair` 等确实需要独立模型时再写完整 task 配置。

## 参数解释

- `model`：模型名。不同厂商不同，请以厂商控制台为准。
- `base_url_env`：base URL 环境变量名。专门适配的 provider 可以不写，使用默认 URL。
- `api_key_env`：API Key 环境变量名，必填。
- `reasoning`：推理强度提示。并非所有厂商都支持，当前主要用于 provider 适配。
- `thinking.type`：`enabled` 或 `disabled`。当前仅 `deepseek`、`zai` 会发送该厂商字段。
- `max_context_tokens`：用于说明该 profile 或 task 可承载的上下文规模，后续检索和截断策略会参考。
- `max_tokens`：控制模型最大输出长度。
- `temperature`：随机性。越低越稳定，越高越发散。
- `timeout_seconds`：请求超时。长章节写作建议更高。
- `max_retries`：失败重试次数。

## 推荐落地策略

1. 先配置顶层 `default` 真实 API，并用 `novel doctor --project <project>` 检查环境变量是否存在。
2. 4 个 profile 默认继承 `default`；共同上下文、输出长度和超时先调 `default`，只有 profile 需要差异时再写 profile patch。
3. JSON 输出类 task 先低温测试，确认 schema 稳定。
4. 正文类 task 先用内置温度默认；如果某个 task 仍有明显差异，再在 Web UI 的“任务级覆盖”区或 `tasks.<task>` 中写 `temperature`、`thinking`、`reasoning` patch 或完整覆盖。
5. 如需离线熟悉流程，显式传 `--provider mock`，不要把 mock 写成真实项目 default。
6. 真实 API 上线前运行：

```bash
novel doctor --project <project>
novel validate --path <project>
novel write-chapter 1 --path <project> --dry-run-provider
```
