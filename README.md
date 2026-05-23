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

生成文件默认不会静默覆盖已有用户数据。

## 校验和查看项目

```bash
novel validate --path ./rain-station
novel status --path ./rain-station
novel show characters --path ./rain-station
novel show timeline --path ./rain-station
novel show state --path ./rain-station
```

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
```

支持的 agent 名称包括 `orchestrator`、`inspiration`、`canon`、`plot`、`writer`、`polish`、`audit`、`state_update`。

生成命令支持临时覆盖：

```bash
novel write-chapter 1 --path ./rain-station --agent-config config/agents.yaml --provider mock
novel write-chapter 1 --path ./rain-station --model temporary-model --dry-run-provider
```

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
`apply-state-update` 会在写入前为 state 和 timeline 创建时间戳备份。高危审核问题会阻止接受章节，除非显式传入 `--allow-issues`。

## 一键生成章节流水线

```bash
novel generate-chapter 1 --path ./rain-station --provider mock
novel generate-chapter 1 --path ./rain-station --provider mock --target-words 3000
novel generate-chapter 1 --path ./rain-station --provider mock --stop-after plan
novel generate-chapter 1 --path ./rain-station --provider mock --stop-after write
novel generate-chapter 1 --path ./rain-station --provider mock --skip-polish
novel generate-chapter 1 --path ./rain-station --provider mock --skip-audit
novel generate-chapter 1 --path ./rain-station --provider mock --force
```

每次运行都会写入 `runs/run_*.json`。如果某一步失败，run log 会记录失败状态和错误信息。

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

## FAQ

### 测试需要真实 API Key 吗？

不需要。测试使用 `MockProvider`，不会调用真实 AI API。

### API Key 放在哪里？

放在环境变量中。项目配置只保存变量名，例如 `OPENAI_API_KEY`，不要把真实 key 写入项目文件。

### 为什么命令拒绝覆盖文件？

为了保护用户数据。生成类命令默认不静默覆盖，确实需要替换时使用 `--force`、`--overwrite` 或版本化修订输出。

### 是否已经是完整 MCP server？

不是。当前提供稳定 CLI contract 和轻量 tool manifest，完整 MCP server 留到后续版本。
