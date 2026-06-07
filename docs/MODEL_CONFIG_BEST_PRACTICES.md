# 模型配置最佳实践

WriterYang 支持给不同 agent 配不同模型。推荐原则是：结构化和审核类任务低温、稳定；正文和润色类任务更重视中文表达、长上下文和风格控制。

## Provider 字段

真实项目必须先配置 `config/agents.yaml` 顶层 `default` API。未单独配置 API 的 Agent 会继承 `default`，只覆盖温度、输出长度、thinking 等差异字段。新项目可通过 CLI/Web 的“项目初始引导”完成这一步：真实 API Key 写入项目 `.env`，YAML 只保存环境变量名。

```yaml
default:
  provider: "deepseek"
  base_url_env: "WRITERYANG_REAL_BASE_URL"
  api_key_env: "WRITERYANG_REAL_API_KEY"
  model: "deepseek-chat"
  json_response_format: "auto"
  thinking:
    type: "disabled"
  temperature: 0.5
  max_tokens: 8192
agents:
  writer:
    temperature: 0.9
    max_tokens: 24000
  audit:
    temperature: 0.2
```

解析顺序：显式 `--provider mock` 测试覆盖 > Agent 覆盖合并 `default` > fallback Agent 覆盖合并 `default` > 仅使用 `default`。缺少 `default` 时，`validate`、`doctor` 和 Web UI 会告警；运行到未配置 Agent 时会失败。

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

结构化 Agent 会在 `ModelRequest.json_schema_name` 中声明目标 schema。`json_response_format` 控制 provider adapter 如何把这个目标传给模型：

- `auto`：默认值。`openai` 解析为 `json_schema`；`deepseek`、`zai`、`openai_compatible` 解析为 `json_object`。
- `json_object`：发送 `response_format: {"type":"json_object"}`，并自动追加标准 JSON mode guard 和紧凑 schema skeleton。[DeepSeek 官方 JSON Output](https://api-docs.deepseek.com/zh-cn/guides/json_mode) 要求 prompt 中包含 `json` 和期望结构示例，推荐 DeepSeek 使用此模式。
- `json_schema`：发送非 strict JSON schema。适合标准 OpenAI 或明确支持该参数的 OpenAI-compatible 服务。
- `json_schema_strict`：仅用于显式 opt-in。当前只允许 `openai` 和通用 `openai_compatible` 尝试；DeepSeek / ZAI 会在本地拒绝。OpenAI strict 会先把 schema 转成 strict-compatible 子集，无法转换时 fail fast，不发请求。

推荐保持 `auto`。只有在确认目标 provider 支持对应参数，并且已有真实 API smoke 覆盖后，再对单个 Agent 覆盖 `json_response_format`。

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

## 按 Agent 配置建议

| Agent | 作用 | 能力重点 | 推荐配置 |
| --- | --- | --- | --- |
| `orchestrator` | 判断任务并串联工作流。 | 指令理解、流程稳定。 | `temperature: 0.2-0.5`，`reasoning: medium`，`max_context_tokens: 64000-128000`。 |
| `inspiration` | 生成灵感和弱总纲。 | 创意、中文表达、弱约束。 | `temperature: 0.7-0.9`，`reasoning: medium`，`max_tokens: 4096-8192`。 |
| `canon` | 生成设定 proposal。 | JSON 稳定、ID 稳定、设定一致。 | `temperature: 0.3-0.6`，`reasoning: medium`，`max_tokens: 8192`。 |
| `plot` | 生成章节计划。 | 长上下文、剧情推理、伏笔控制。 | `temperature: 0.4-0.7`，`reasoning: high`，`max_context_tokens: 128000`。 |
| `writer` | 生成初稿。 | 中文长文、角色声音、叙事节奏。 | `temperature: 0.7-1.0`，`max_tokens: 16000-32000`，`timeout_seconds: 120-180`。 |
| `polish` | 润色初稿。 | 文风、节奏、事实保持。 | `temperature: 0.5-0.8`，`max_tokens: 16000-32000`。 |
| `audit` | 一致性审核。 | 低幻觉、细节比对、JSON 输出。 | `temperature: 0-0.3`，复杂项目可 `thinking.type: enabled`。 |
| `revision` | 根据用户意见或 audit issues 修订版本稿。 | 事实保持、定向修复、中文表达。 | `temperature: 0.4-0.7`，`reasoning: medium-high`，`max_tokens: 16000-32000`。 |
| `state_update` | 提取状态和时间线变化。 | 信息抽取、引用一致性。 | `temperature: 0-0.3`，`reasoning: low-medium`。 |
| `chapter_memory` | 生成 accepted 章节检索记忆。 | 结构化摘要、来源引用、可见性分级。 | `temperature: 0-0.2`，`reasoning: low-medium`，`max_tokens: 4096-8192`。 |

## 参数解释

- `model`：模型名。不同厂商不同，请以厂商控制台为准。
- `base_url_env`：base URL 环境变量名。专门适配的 provider 可以不写，使用默认 URL。
- `api_key_env`：API Key 环境变量名，必填。
- `reasoning`：推理强度提示。并非所有厂商都支持，当前主要用于 provider 适配。
- `thinking.type`：`enabled` 或 `disabled`。当前仅 `deepseek`、`zai` 会发送该厂商字段。
- `max_context_tokens`：用于说明该 agent 可承载的上下文规模，后续检索和截断策略会参考。
- `max_tokens`：控制模型最大输出长度。
- `temperature`：随机性。越低越稳定，越高越发散。
- `timeout_seconds`：请求超时。长章节写作建议更高。
- `max_retries`：失败重试次数。

## 推荐落地策略

1. 先配置顶层 `default` 真实 API，并用 `novel doctor --project <project>` 检查环境变量是否存在。
2. Agent 默认继承 `default`；只给 `writer`、`plot`、`revision`、`audit` 等重点 Agent 覆盖差异参数。
3. JSON 输出类 Agent 先低温测试，确认 schema 稳定。
4. 正文类 Agent 再调温度和 `max_tokens`。
5. 如需离线熟悉流程，显式传 `--provider mock`，不要把 mock 写成真实项目 default。
6. 真实 API 上线前运行：

```bash
novel doctor --project <project>
novel validate --path <project>
novel write-chapter 1 --path <project> --dry-run-provider
```
