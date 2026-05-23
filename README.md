# WriterYang

WriterYang 是一个面向中文长篇小说创作的 AI 辅助写作工具。它不是单纯的聊天写作器，而是把灵感、设定、人物、地点、物品、时间线、章节计划、正文、润色稿、审核报告和导出结果保存为可编辑的 Markdown / JSON / YAML 文件。

当前版本重点是本地 CLI 和最小 Web UI。所有测试都使用 `MockProvider`，不依赖真实 API Key。

## 安装

开发安装：

```bash
conda run -n py312 python -m pip install -e ".[dev]"
```

普通本地安装：

```bash
python -m pip install .
```

检查命令：

```bash
novel --version
novel --help
```

## 运行测试和构建

```bash
conda run -n py312 pytest
conda run -n py312 python -m build
```

构建产物会生成到 `dist/`。

## 创建小说项目

```bash
novel init "雨夜旧车站"
novel init "雨夜旧车站" --path ./rain-station
```

也可以验证内置示例项目：

```bash
novel validate --path examples/rain_station
novel status --path examples/rain_station
```

示例项目的 `config/agents.yaml` 是真实 API 配置模板，默认使用 OpenAI-compatible 字段，并显式关闭 `thinking`。如果只想离线试用或跑 mock 流程，可以参考 `config/agents.mock.yaml`，或者在命令中传入 `--provider mock`。

生成文件默认不会静默覆盖已有用户数据。

## 校验和查看项目

```bash
novel validate --path ./rain-station
novel migrate --path ./rain-station
novel schema export --output schemas
novel status --path ./rain-station
novel show characters --path ./rain-station
novel show timeline --path ./rain-station
novel show state --path ./rain-station
```

项目根目录的 `schemas/*.schema.json` 是从 Pydantic models 生成的 JSON Schema，可供外部工具校验项目 JSON/YAML 结构。

## Provider 配置

每个 agent 可以在 `config/agents.yaml` 中使用独立模型配置。项目文件只保存环境变量名，不保存真实 API Key。

```yaml
agents:
  writer:
    provider: "openai_compatible"
    base_url_env: "WRITER_BASE_URL"
    api_key_env: "WRITER_API_KEY"
    model: "writer-model-name"
    reasoning: "high"
    max_context_tokens: 128000
    temperature: 0.9
    timeout_seconds: 120
    max_retries: 1
    thinking:
      type: "disabled"
```

支持的 agent 名称包括 `orchestrator`、`inspiration`、`canon`、`plot`、`writer`、`polish`、`audit`、`state_update`。
`thinking.type` 默认为 `disabled`。对于 DeepSeek 等 OpenAI-compatible 接口，可以设置为 `enabled` 或 `disabled`，请求体会发送 `{"thinking": {"type": "..."}}`。

示例项目提供两个配置文件：

- `examples/rain_station/config/agents.yaml`：真实 API 模板，适合复制到新项目后替换模型名和环境变量名。
- `examples/rain_station/config/agents.mock.yaml`：mock provider 模板，适合测试、文档示例和无 API Key 的本地演示。

生成命令支持临时覆盖：

```bash
novel write-chapter 1 --path ./rain-station --agent-config config/agents.yaml --provider mock
novel write-chapter 1 --path ./rain-station --model temporary-model --dry-run-provider
```

### Agent 作用和模型配置建议

不同 agent 对模型能力的要求不同。实际部署时不一定所有 agent 都要用同一个大模型：结构化、低温、校验类任务更看重稳定性和格式遵守；正文生成和润色更看重语言质量、长上下文和风格控制。

| Agent | 作用 | 能力重点 | 推荐配置 | 推荐原因 |
| --- | --- | --- | --- | --- |
| `orchestrator` | 根据用户请求选择工作流，串联 plan/write/polish/audit/state/export 等步骤。 | 指令理解、任务拆解、稳定遵守流程。上下文窗口中等即可。 | `reasoning: medium`，`thinking.type: disabled`，`temperature: 0.2-0.5`，`max_context_tokens: 64000-128000`。 | 编排任务需要判断步骤，但不应过度发散；低到中温度能减少错误路由。 |
| `inspiration` | 根据用户输入生成灵感、主题、氛围和弱总纲。 | 创意发散、中文表达、弱约束生成。 | `reasoning: medium`，`thinking.type: disabled`，`temperature: 0.7-0.9`，`max_context_tokens: 32000-64000`。 | 灵感阶段允许更多发散，但仍要避免生成过强剧情约束。 |
| `canon` | 从 inspiration 生成或补充人物、地点、物品、世界规则、隐藏真相和伏笔 proposal。 | 结构化 JSON、设定一致性、ID 稳定性。 | `reasoning: medium`，`thinking.type: disabled`，`temperature: 0.3-0.6`，`max_context_tokens: 64000`。 | canon 需要创造力，但更需要稳定 schema 输出和不重复 ID。 |
| `plot` | 生成章节计划 `plan.json` / `plan.md`。 | 长上下文、剧情逻辑、伏笔控制、结构化输出。 | `reasoning: high`，`thinking.type: disabled` 或复杂项目设为 `enabled`，`temperature: 0.4-0.7`，`max_context_tokens: 128000`。 | 章节计划要综合 inspiration、canon、state、timeline，推理和上下文要求较高。 |
| `writer` | 根据章节计划生成初稿 `draft.md`。 | 中文长文生成、风格保持、角色声音、长上下文。 | `reasoning: high`，`thinking.type: disabled`，`temperature: 0.7-1.0`，`max_context_tokens: 128000`，`timeout_seconds: 120-180`。 | 正文需要更高语言多样性，温度可高一些；但一般不建议开启思考输出模式，避免影响小说正文纯净度。 |
| `polish` | 根据初稿、计划和风格要求生成润色稿 `polished.md`。 | 中文文学表达、节奏控制、保留事实不漂移。 | `reasoning: medium`，`thinking.type: disabled`，`temperature: 0.5-0.8`，`max_context_tokens: 128000`。 | 润色要改善语言但不能改剧情事实，温度不宜过高。 |
| `audit` | 审核章节与 canon、state、timeline、plan、style 是否冲突，输出 `audit.json`。 | 严格指令遵守、结构化 JSON、细节比对、低幻觉。 | `reasoning: low-medium`，复杂项目可 `high`；`thinking.type: disabled` 或 `enabled`；`temperature: 0-0.3`，`max_context_tokens: 64000-128000`。 | 审核是判定类任务，应降低随机性；复杂长篇可提高 reasoning 或开启 thinking 来增强一致性检查。 |
| `state_update` | 从通过审核的章节中提取状态变化和时间线事件。 | 信息抽取、结构化 JSON、引用一致性。 | `reasoning: low-medium`，`thinking.type: disabled`，`temperature: 0-0.3`，`max_context_tokens: 64000`。 | 状态更新不应创造正文中没有发生的事件，低温更稳定。 |

通用建议：

- `thinking.type` 默认用 `disabled`。只有在 `plot`、`audit` 这类复杂推理/一致性检查任务明显不稳定时，再为对应 agent 单独改成 `enabled`。
- `writer` 和 `polish` 通常不建议开启思考模式。它们的输出要直接写入 Markdown 文件，模型额外的分析性内容会增加清洗风险。
- `temperature` 越高，语言和创意越发散；越低，结构化输出和一致性越稳定。JSON 输出类 agent 建议低温，正文类 agent 可以中高温。
- `max_context_tokens` 对 `plot`、`writer`、`polish`、`audit` 更重要，因为这些步骤会读取 plan、canon、state、timeline 和正文。
- `timeout_seconds` 对 `writer`、`polish` 建议更高。长章节生成本身耗时更长，过短会导致真实 API 测试和实际写作中断。

## 生成灵感

```bash
novel inspire "一个雨夜旧车站里传来已经停播多年的广播声" --path ./rain-station --provider mock --overwrite
novel inspire --input input.txt --path ./rain-station --provider mock --json --quiet --overwrite
```

命令会写入 `memory/inspiration.md`。使用 `--json` 时，也会写入 `memory/inspiration.json` 并在 stdout 输出机器可读 JSON；自动化调用建议搭配 `--quiet`。

## 管理 Canon

```bash
novel canon suggest --path ./rain-station --provider mock
novel canon suggest --path ./rain-station --provider mock --output canon-proposal.json
novel canon apply canon-proposal.json --path ./rain-station
novel canon validate --path ./rain-station
novel canon show --path ./rain-station
```

`novel show canon` 也保留为兼容别名。Canon 写入采用 proposal-first 流程，`apply` 会拒绝重复 ID，不会静默覆盖已有设定。

## 章节计划、写作、润色、审核

生成章节计划：

```bash
novel plan-chapter 1 --path ./rain-station --provider mock
novel plan-chapter 2 --path ./rain-station --provider mock --instruction "这一章要让主角第一次怀疑沈鹿的身份，但不要揭示真相"
novel plan-chapter 3 --path ./rain-station --provider mock --input chapter3_request.txt
```

写初稿：

```bash
novel write-chapter 1 --path ./rain-station --provider mock
novel write-chapter 1 --path ./rain-station --provider mock --target-words 3000
novel write-chapter 2 --path ./rain-station --provider mock --instruction "加强压抑感，减少解释性文字"
novel write-chapter 3 --path ./rain-station --provider mock --input chapter3_writing_request.txt
```

润色：

```bash
novel polish-chapter 1 --path ./rain-station --provider mock
novel polish-chapter 1 --path ./rain-station --provider mock --light-edit
novel polish-chapter 1 --path ./rain-station --provider mock --deep-edit
novel polish-chapter 1 --path ./rain-station --provider mock --keep-length
```

审核：

```bash
novel audit-chapter 1 --path ./rain-station --provider mock
novel audit-chapter 1 --path ./rain-station --provider mock --strict
novel audit-chapter 1 --path ./rain-station --provider mock --focus canon --focus timeline
novel audit-chapter 1 --path ./rain-station --provider mock --audited-file draft.md --force
```

以上命令默认拒绝覆盖已有输出文件；需要明确传入 `--force`。

## 修订章节

```bash
novel revise-chapter 1 --path ./rain-station --provider mock --instruction "加强悬疑感，但不要改变结尾事件"
novel revise-chapter 1 --path ./rain-station --provider mock --from-audit
novel revise-chapter 1 --path ./rain-station --provider mock --target draft --instruction "压缩解释性文字"
```

修订默认保存为版本文件，例如 `polished.v2.md` 或 `draft.v2.md`，并更新 `revision_log.json`。

## 状态和时间线更新

状态更新默认先生成 proposal，不直接修改 `current_state.json` 或 `timeline.json`：

```bash
novel propose-state-update 1 --path ./rain-station --provider mock
novel apply-state-update 1 --path ./rain-station
novel accept-chapter 1 --path ./rain-station
novel accept-chapter 1 --path ./rain-station --propose --provider mock
```

proposal 文件会保存为 `memory/chapters/{chapter_number}/state_update_proposal.json`。
`apply-state-update` 会在写入前为 state 和 timeline 创建时间戳备份，并写入 `state_update_apply_log.json`。如果写入失败，会尝试从备份回滚。高危审核问题会阻止接受章节，除非显式传入 `--allow-issues`。
接受章节后会写入结构化状态文件 `memory/chapters/{chapter_number}/metadata.json`，同时保留 `polished.md` front matter 中的 `status: accepted` 以兼容导出流程。

## 一键生成章节流水线

```bash
novel generate-chapter 1 --path ./rain-station --provider mock
novel generate-chapter 1 --path ./rain-station --provider mock --target-words 3000
novel generate-chapter 1 --path ./rain-station --provider mock --stop-after plan
novel generate-chapter 1 --path ./rain-station --provider mock --stop-after write
novel generate-chapter 1 --path ./rain-station --provider mock --skip-polish
novel generate-chapter 1 --path ./rain-station --provider mock --skip-audit
novel generate-chapter 1 --path ./rain-station --provider mock --resume
novel generate-chapter 1 --path ./rain-station --provider mock --force
```

每次运行都会写入 `runs/run_*.json`。如果某一步失败，run log 会记录失败状态和错误信息。
`--resume` 会复用已经存在的步骤产物并继续执行，适合从 `plan` 或 `write` 之后恢复流水线；`--force` 会重新生成目标文件。

## 搜索和可解释上下文

```bash
novel index rebuild --path ./rain-station
novel search "林澈" --path ./rain-station --type character
novel search "旧车站广播" --path ./rain-station --type event --limit 5
novel search "破损车票" --path ./rain-station --type chapter --json
```

搜索索引位于 `memory/search_index.json`，可以随时重建。当前使用关键词匹配，不使用向量数据库。

规划、写作、审核可以选择加入检索上下文：

```bash
novel plan-chapter 1 --path ./rain-station --provider mock --use-search-context
novel write-chapter 1 --path ./rain-station --provider mock --use-search-context
novel audit-chapter 1 --path ./rain-station --provider mock --use-search-context
```

## 受控编排

```bash
novel ask "请为第1章生成章节计划" --path ./rain-station --provider mock
novel ask "请写第1章初稿" --path ./rain-station --provider mock
novel ask "请审核第1章一致性" --path ./rain-station --provider mock
novel ask "请为第1章生成章节计划" --path ./rain-station --provider mock --dry-run
```

`novel ask` 是规则化 orchestrator，不做自由多 agent 辩论。它会记录 `handoff_trace` 到 run log。

## 导出

默认只导出 accepted 的 `polished.md` 章节：

```bash
novel export markdown --path ./rain-station
novel export markdown --path ./rain-station --chapters 1,2,3
novel export markdown --path ./rain-station --from 1 --to 10
novel export markdown --path ./rain-station --include-unaccepted
novel export markdown --path ./rain-station --output exports/book.md --title "雨夜旧车站" --force
```

Word 导出：

```bash
novel export docx --path ./rain-station
novel export docx --path ./rain-station --chapters 1,2,3
novel export docx --path ./rain-station --include-unaccepted
novel export docx --path ./rain-station --output exports/book.docx --title "雨夜旧车站" --force
```

导出会更新 `exports/export_manifest.json`。Word 导出依赖 `python-docx`。

## 最小 Web UI

```bash
novel web --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765`。Web UI 可以输入项目路径，查看状态和 canon，列出章节，触发计划、写作、润色、审核、Markdown 导出，并查看生成文件。Web API 调用同一套 core service，不返回真实 API Key。

## 外部 Agent / openclaw 调用

自动化工具建议使用 `--project --json --quiet`：

```bash
novel status --project ./rain-station --json --quiet
novel ask "请为第1章生成章节计划" --project ./rain-station --provider mock --json --quiet
```

更多说明见 [docs/INTEGRATION.md](docs/INTEGRATION.md) 和 [docs/openclaw_tool_manifest.json](docs/openclaw_tool_manifest.json)。

## 发布检查

发布前请参考 [docs/RELEASE.md](docs/RELEASE.md)。核心检查包括：

```bash
conda run -n py312 pytest
conda run -n py312 python -m build
novel validate --path examples/rain_station
```

版本变化记录见 [CHANGELOG.md](CHANGELOG.md)。
JSON Schema 文件位于 `schemas/`，也可以通过 `novel schema export --output schemas` 重新生成。

真实 API 冒烟测试需要本地 `.env.real`，该文件会被 `.gitignore` 忽略。推荐变量名：

```bash
WRITERYANG_REAL_BASE_URL=
WRITERYANG_REAL_API_KEY=
WRITERYANG_REAL_MODEL=
```

也兼容以下 DeepSeek 变量名：

```bash
DEEPSEEK_BASE_URL=
DEEPSEEK_API_KEY=
DEEPSEEK_V4PRO_MODEL=
```

运行真实 API 测试：

```bash
conda run -n py312 pytest -m real_api
```

## FAQ

### 测试需要真实 API Key 吗？

不需要。测试使用 `MockProvider`，不会调用真实 AI API。

### API Key 放在哪里？

放在环境变量中。项目配置只保存变量名，例如 `OPENAI_API_KEY`，不要把真实 key 写入项目文件。

### 为什么命令拒绝覆盖文件？

为了保护用户数据。生成类命令默认不静默覆盖，确实需要替换时使用 `--force`、`--overwrite` 或版本化修订输出。

### 是否已经是完整 MCP server？

不是。当前提供稳定 CLI contract 和轻量 tool manifest，完整 MCP server 留到后续版本。
