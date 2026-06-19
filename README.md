# WriterYang

WriterYang 是一个面向中文长篇小说创作的 AI 辅助写作工具。它不是单纯的聊天写作器，而是把灵感、设定、人物、地点、物品、时间线、章节计划、正文、润色稿、审核报告和导出结果保存为可编辑的 Markdown / JSON / YAML 文件。

当前版本重点是本地 CLI 和可用的本地 Web UI。新用户推荐优先走 Web UI 的 Session 流程；CLI 保留给高级使用、调试、自动化和外部工具集成。所有测试都使用 `MockProvider`，不依赖真实 API Key。

## 环境配置

建议为 WriterYang 创建一个独立 Python 3.12 环境，不要直接使用系统 Python，也不要复用已有项目环境。

推荐使用一键安装脚本：

```bash
./install.sh
```

它会自动检测 conda；如果本机有 conda，会优先创建 `WriterYang_YYMMDD` 格式的新环境，例如 `WriterYang_260531`。如果当天同名环境已经存在，会自动使用 `WriterYang_26053101`、`WriterYang_26053102` 这样的后缀。没有 conda 时，脚本会回退到 `.venv/WriterYang_YYMMDD`。

安装完成后，脚本会以 editable 模式安装当前源码目录。之后你更新代码或拉取新版本后，只需要重启 Web UI，就会加载新的源码和前端静态文件，不需要每次重新安装。脚本还会自动寻找可用 Web UI 端口，打印完整地址并弹出浏览器。默认从 `8765` 开始；如果端口被占用，会自动尝试下一个端口。Web server 会在当前终端前台运行，按 `Ctrl+C` 停止。

脚本还会生成 `WriterYang_WebUI.command` 启动器和同目录的 `WriterYang_WebUI.config.json`。之后不懂命令行的用户可以直接双击启动器打开 Web UI；启动器会固定使用安装脚本创建的新环境，并从 config 文件读取下次启动端口。Web UI 中保存端口会先验证端口可用，再更新这个 config 文件；如果下次启动时该端口临时被占用，启动器会自动改用下一个空闲端口并提示用户重新保存端口。Web server 停止后，交互式终端会进入一个已经激活新环境的子 shell，后续 `novel ...` 命令默认走这个新环境；输入 `exit` 可以回到原终端。

也可以直接运行 Python 入口：

```bash
python scripts/install_writeryang.py
python scripts/install_writeryang.py --dev
python scripts/install_writeryang.py --dry-run
python scripts/install_writeryang.py --web-port 9000
python scripts/install_writeryang.py --no-open-web
python scripts/install_writeryang.py --no-web
python scripts/install_writeryang.py --no-activate-shell
python scripts/install_writeryang.py --launcher-path ./WriterYang_WebUI.command
```

脚本默认安装运行依赖；开发者需要测试、lint、mypy、build 等工具时使用 `--dev`。如果不希望安装后启动 Web UI，使用 `--no-web`；如果只是不想自动弹出浏览器，使用 `--no-open-web`。如果不想在安装后进入新环境子 shell，使用 `--no-activate-shell`。

如果你之前使用旧版本安装脚本创建过环境，Web UI 可能仍在读取环境 `site-packages` 里的旧副本。解决方式是重新运行 `./install.sh` 创建新环境，或者在旧环境中执行一次：

```bash
python -m pip install -e .
```

如果你使用的是脚本进入的新环境子 shell，可以直接运行：

```bash
novel --version
novel doctor
```

如果你关闭了子 shell，也可以手动激活新环境，或使用 `conda run -n <环境名> novel ...`。

### 手动安装

如果你不想使用一键脚本，也可以手动创建环境。

使用 conda：

```bash
conda create -n writeryang python=3.12 -y
conda activate writeryang
python -m pip install --upgrade pip
```

如果不使用 conda，也可以用 Python 自带的 venv：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

后续命令都假设你已经激活了这个新环境。你可以用下面命令确认当前 Python 路径：

```bash
python --version
python -c "import sys; print(sys.executable)"
```

## 安装

如果已经按上面的“一键安装脚本”完成安装，可以跳过本节。

开发安装：

```bash
python -m pip install -e ".[dev]"
```

普通本地固定安装：

```bash
python -m pip install .
```

固定安装会复制一份代码到当前环境，适合发布包验证；如果你希望 `git pull` 或本地修改后立即生效，请使用 editable 安装。

检查命令：

```bash
novel --version
novel --help
```

安装完成后，再进入下面的用户指南或开发者文档。

## 新手入口

- [Web UI 小白图文使用指南](docs/WEB_UI_USER_GUIDE.md)：面向不懂代码和命令行的作者，用截图说明浏览器工作台的创作全流程。
- [新手快速开始](docs/QUICKSTART.md)：用 `mock` provider 跑通 10 分钟测试流程，不需要 API Key；真实创作请先配置 `config/agents.yaml` 的 `default` API。
- [作者如何手动编辑 memory 文件](docs/MEMORY_EDITING.md)：说明 inspiration、style、canon、state、timeline、章节文件的人工编辑边界。
- [模型配置最佳实践](docs/MODEL_CONFIG_BEST_PRACTICES.md)：按 agent 说明模型能力、temperature、max tokens、context 和 thinking 开关建议。

## 开发者入口

- [开发者指南](docs/DEVELOPER_GUIDE.md)：工程结构、分层原则、新功能开发、BUG 定位和安全重构流程。
- [代码库参考手册](docs/CODEBASE_REFERENCE.md)：逐模块说明入口文件、core service、schema、prompt、tests 和主要函数职责。
- [Agent Prompt 组装说明](docs/AGENT_PROMPT_ASSEMBLY.md)：说明每个 Agent 的 system/user prompt 如何由项目文件和上下文组装。
- [调试与重构手册](docs/DEBUGGING_AND_REFACTORING.md)：常见故障路径、日志位置、provider 调试和重构 checklist。
- `scripts/`：确定性工具脚本，组合现有 CLI/API，避免手工串流程和漏检查。

## 运行测试和构建

```bash
pytest
python -m build
```

构建产物会生成到 `dist/`。

本地复现 CI 的推荐入口：

```bash
python scripts/check_local.py --skip-build
python scripts/check_local.py
python scripts/check_local.py --strict-mypy
python scripts/install_git_hooks.py --dry-run
```

`mypy src scripts` 是阻断式类型门禁；`scripts/check_local.py` 默认会因为 mypy 失败返回非零。`--strict-mypy` 仍可使用，但现在只是兼容旧命令的显式写法。GitHub Actions 会阻断 pytest、ruff、mypy、secret scan、build 和 Web E2E。

如需在本地推送前自动跑完整门禁，可以执行 `python scripts/install_git_hooks.py`。它会设置 `core.hooksPath=.githooks`，使 tracked `pre-push` hook 在 `git push` 前运行 `python scripts/check_local.py`。只有确有必要时才用 `WRITERYANG_SKIP_PRE_PUSH=1 git push` 跳过；CI 仍是最终兜底。

工作流和排障脚本：

```bash
python scripts/smoke_session.py --provider mock --json
python scripts/project_health.py --project ./rain-station
python scripts/debug_bundle.py --project ./rain-station --output /tmp/writeryang-debug --zip --json
python scripts/provider_ping.py --project ./rain-station --provider config --json
python scripts/webui_smoke.py --dry-run --json
python scripts/install_git_hooks.py --dry-run --json
```

这些脚本只组合现有 CLI/core，不复制章节生成、审核或状态更新业务逻辑。真实 API ping 或 smoke 需要显式传入 `--allow-network` 或选择真实 provider；输出会脱敏，不打印 API Key。

## 创建小说项目

```bash
novel init "雨夜旧车站"
novel init "雨夜旧车站" --path ./rain-station
```

交互式终端中，`novel init` 创建项目后会默认进入“项目初始引导”：

- 输入一组 OpenAI-compatible API Key、base URL 和模型名，作为所有 profile 的缺省 API 配置。
- 工具会先做一次连通性测试；通过后才把真实 key 写入项目根目录 `.env`，并把 `config/agents.yaml` 的顶层 `default` 指向对应环境变量名。
- 可选配置 embedding API；跳过后仍可使用关键词/FTS 检索。
- 选择 CLI Web UI 默认端口；如果端口被占用会自动推荐下一个可用端口，并写入 `project.yaml`。这个端口只影响 `novel web --path ...`，不影响 `WriterYang_WebUI.command` 的启动器配置。
- 最后默认打开 Web UI。

`.env` 是本地私密运行文件，会被 `.gitignore`、Web 文件树、导出和日志排除。`config/agents.yaml` 不保存真实密钥；之后可以在这个文件中为 4 个 profile 或少数 task 覆盖模型、`thinking.type`、`temperature`、`max_tokens`、超时和重试等参数。

如果你只是跑 mock 教程或自动化脚本，不想进入引导：

```bash
novel init "雨夜旧车站" --path ./rain-station --no-guide
```

也可以验证当前初始化模板。模板由 `novel init` 基于当前代码生成，不在仓库中保存固定样板项目：

```bash
tmp_project="$(mktemp -d)/writeryang-template"
novel init "模板校验" --path "$tmp_project" --no-guide
novel validate --path "$tmp_project"
novel status --path "$tmp_project"
```

如果只想离线测试流程，在命令中显式传入 `--provider mock`，不要把 `mock` 写成真实项目的 `default`。

生成文件默认不会静默覆盖已有用户数据。

## 中文长篇小说默认工作流

推荐工作流已经改为“协作式创作 Session”。用户先和工具确认本次创作范围，系统生成可协商大纲；用户批准大纲后才自动进入写作、润色、审核和状态更新 proposal；用户认可最终内容后再接受和归档。

```text
init -> inspire -> canon suggest/apply -> session start/outline -> approve outline -> session run -> user review -> session accept/archive -> export
```

对应命令：

```bash
novel init "新书名" --path ./my-novel
novel inspire "一句或一段原始灵感" --path ./my-novel --provider config --overwrite
novel canon suggest --path ./my-novel --provider config --output canon-proposal.json
novel canon apply canon-proposal.json --path ./my-novel
novel session start "写第1章：雨夜旧车站，建立调查动机" --path ./my-novel --chapters 1 --provider config
novel session approve-outline <session_id> --path ./my-novel
novel session run <session_id> --path ./my-novel --provider config
novel session revise-content <session_id> --path ./my-novel --provider config --instruction "加强压抑感，减少解释"
novel session revise-content <session_id> --path ./my-novel --provider config --from-audit
novel session accept <session_id> --path ./my-novel --provider config
novel session archive <session_id> --path ./my-novel
novel export markdown --path ./my-novel --toc --force
```

人工编辑建议：

- `inspire` 后可以手动改 `memory/inspiration.md`，让弱总纲更贴近作者意图。
- `canon suggest` 后先看 proposal，再 `apply`；不要把隐藏真相写进读者可见摘要。
- `session start` 后先看 `memory/sessions/{session_id}/outline_proposal.md`，不满意就 `session revise-outline`。
- `session start` 和 `session revise-outline` 只写 `memory/sessions/{session_id}/plans/{NNN}/` 下的草稿章节计划，不覆盖正式 `memory/chapters/{NNN}/plan.*`。
- `session approve-outline` 会把草稿计划提交为正式章节计划；如果对应章节已存在 `plan.json` / `plan.md`，需要明确传入 `--force` 或在 Web UI 勾选“允许覆盖已有产物”。
- `session approve-outline` 后，后续写作必须遵守 approved outline。
- `session run` 会自动写作、润色、审核；audit 发现 medium/high/critical 问题时会自动尝试修复。运行时会把阶段级进度写入 `memory/sessions/{session_id}/progress.json`，Web UI 用它显示当前阶段、章节、轮次、已用时和最近事件。
- Web UI 可以请求取消正在运行的 Session。取消是协作式的：系统只写入 `cancel_requested`，不会强行中断正在进行的 LLM HTTP 调用；任务会在当前章节或自动修复轮结束后的安全边界停止，最终进度变为 `cancelled`。
- 每次自动打回都会记录到 `memory/sessions/{session_id}/rewrite_events.json`，并把被打回的 `polished.md` 快照保存到 `memory/sessions/{session_id}/rejections/`，方便作者确认打回原因是否合理。
- 自动修复超过轮数后，session 会停在 `status=needs_revision`、`content_status=needs_revision`，用户可查看 audit、rewrite events 和 revision log；在 Web UI 中应优先查看“自动打回重写记录”，再使用“按 Audit 修订内容”，不要通过新建 session 覆盖已有产物来绕过问题。
- 如果你认为 Audit 对时间线、状态或正文语义理解错了，不要直接删改 `audit.json`。使用 `novel session revise-audit <session_id> <event_id> --instruction "..."` 或 Web UI 的“纠正 Audit 理解并重新审核”，让 Audit Agent 带着你的纠正意见复审；复审后再用 `session retry-rewrite` 或 Web UI 的“根据新审核重新打回”触发重写。必要时可用 `session undo-rewrite` 或 Web UI 的“撤回本次打回”恢复被打回原文快照。
- low 级别 audit issue 不会被静默自动修改，会展示给作者；作者可选择直接接受，或运行 `session revise-content <session_id> --from-audit` 生成修订版本。
- 用户看到最终内容后用 `session revise-content` 提意见；系统生成新版本，提升为当前 `polished.md`，重跑 audit，并重新生成 state proposal，不覆盖归档内容。
- `session accept` 后才应用状态更新并标记章节 accepted；`session archive` 会复制本次创作文件并记录 sha256。
- `state/timeline` 默认通过 proposal 更新；不建议直接改正式 state/timeline，除非你清楚引用关系。
- 发现 timeline/state/canon 等项目记忆写错时，推荐把问题交给 orchestrator 项目管家：`novel ask "第2章 event_x 其实是回忆，不是当前行动"`。它会生成 `memory/repairs/{repair_id}/proposal.json` 和 `proposal.md`，用户确认后用显式命令 `novel memory-repair apply <repair_id>` 应用；普通自然语言 fallback 不会执行 apply，以避免误操作。所有后台状态/时间线/记忆刷新会写入 `memory/management_events.jsonl` 并在 CLI/Web UI 中展示。
- 自然语言设定变更推荐使用 `novel setting-change suggest "..." --path ./rain-station --provider config`。如果创作意图、替换/删除目标或剧情含义不清楚，Agent 会先生成 `clarify_...` 澄清问题；用 `novel setting-change answer clarify_... --answer "..."` 继续，直到生成可审查的 `memory/repairs/{repair_id}/proposal.json`。提示词会包含当前 memory 文件结构和 JSON Pointer 路径索引，目标文件、字段、visibility 和 JSON Pointer 由系统负责选择，用户不需要手动提供。新增人物的 `role` 只表示叙事角色；“谢家长女”“江湖散人”这类身份信息应进入 `tags`，生成和应用前都会做对应 preflight。
- 归档后的内容默认不可变；如需修改，应创建新的 revision session。
- 底层 `plan-chapter/write-chapter/polish-chapter/audit-chapter` 仍保留给调试和高级用户，但日常创作推荐用 `novel ask` / `novel session`。
- 真实 API 的结构化输出可能第一次不符合 schema；Canon、ChapterPlan、Audit、StateUpdate 会自动做一次 repair retry。仍失败时，先看错误摘要和 `runs/` 日志。

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
核心 YAML/JSON 文件都包含 `schema_version`。当前项目未商用，不保留旧 timeline 格式迁移；`novel migrate --path ./rain-station` 只用于给缺失版本号的本地文件补齐当前 `schema_version`，遇到更新版本会拒绝写入。

系统 prompt 模板位于 `src/novel/prompts/`，会随包一起分发。关键约束有单元测试保护，避免后续修改时丢失“不要修改 canon/state/timeline”“不要提前揭示 hidden_truths”“只输出 JSON/正文”等边界。

## Provider 配置

真实项目必须在 `config/agents.yaml` 中配置至少一个顶层 `default` API。新项目默认只暴露 4 个可独立配模型的能力 profile：`scribe`、`architect`、`loremaster`、`clerk`。运行时仍按 `writer`、`audit`、`intent_router` 等 task 调用不同 prompt，但模型解析统一走 `task -> profile -> default`。项目文件只保存环境变量名，不保存真实 API Key。

```yaml
default:
  provider: "deepseek"
  base_url_env: "WRITERYANG_REAL_BASE_URL"
  api_key_env: "WRITERYANG_REAL_API_KEY"
  model: "deepseek-chat"
  json_response_format: "auto"
  reasoning: "medium"
  max_context_tokens: 128000
  max_tokens: 24000
  temperature: 0.5
  timeout_seconds: 120
  max_retries: 1
  thinking:
    type: "disabled"
profiles:
  scribe:
    inherit_default: true
    reasoning: "high"
    max_context_tokens: 128000
    max_tokens: 24000
    temperature: 0.7
    thinking:
      type: "disabled"
  architect:
    inherit_default: true
    reasoning: "high"
    max_context_tokens: 128000
    max_tokens: 8192
    temperature: 0.3
    thinking:
      type: "disabled"
tasks:
  intent_router:
    temperature: 0
```

支持的 profile 名称包括 `scribe`、`architect`、`loremaster`、`clerk`。常用 task 包括 `writer`、`polish`、`revision`、`plot`、`audit`、`inspiration`、`style_guide`、`canon`、`state_update`、`chapter_memory`、`intent_router`、`memory_repair`、`setup`。一般只配置 profile；只有要单独控制高杠杆任务，例如 `intent_router`，才在 `tasks` 下写 patch。

`provider` 字段当前支持以下值：

| provider | 用途 | 说明 |
| --- | --- | --- |
| `openai` | 标准 OpenAI API | 默认 base URL 为 `https://api.openai.com/v1`，`json_response_format:auto` 会解析为 `response_format: json_schema`，不发送厂商私有 `thinking`。需要强约束时可显式配置 `json_schema_strict`。 |
| `openai_compatible` | 通用 OpenAI Chat Completions 兼容接口 | 需要配置 `base_url_env`，`auto` 会解析为较通用的 `response_format: json_object`，不发送厂商私有 `thinking`。适合尚未做专门适配的第三方兼容服务。 |
| `deepseek` | DeepSeek 官方 API | 默认 base URL 为 `https://api.deepseek.com`，会发送 DeepSeek 支持的 `thinking.type`，并解析返回中的 `reasoning_content`。结构化输出使用官方 JSON Output：`response_format: json_object`，并自动追加 JSON guard 和紧凑 schema skeleton。 |
| `zai` | 智谱 / GLM 官方 API | 默认 base URL 为 `https://open.bigmodel.cn/api/paas/v4`，会发送智谱 GLM 支持的 `thinking.type`，并解析返回中的 `reasoning_content`。结构化输出默认使用 `json_object`。 |
| `mock` | 测试 / 调试 | 不调用真实 API，仅用于自动化测试、离线 smoke 和文档演示。真实创作不要把它作为 default。 |

解析顺序是：显式 `--provider mock` 测试覆盖 > task override > task 内置业务默认 > profile 配置 > `default`。profile 勾选 `inherit_default: true` 时继承 `default` 的 provider/model/base URL/API env，并可覆盖 `reasoning`、`thinking`、`temperature`、`max_tokens`、`max_context_tokens`、`timeout_seconds`、`max_retries`、`json_response_format` 等调用参数；取消继承后保存完整独立 profile。旧的 `agents:` 任务键配置已经移除，`novel validate`、`novel doctor` 和 Web UI 的“Profile 模型配置”页会提示新 schema 问题。

`thinking.type` 默认为 `disabled`。当前只有 `deepseek` 和 `zai` 会把该字段发送到请求体，格式为 `{"thinking": {"type": "..."}}`。标准 `openai` 和通用 `openai_compatible` 不发送这个厂商字段。

`json_response_format` 用于控制结构化 Agent 的 provider payload，取值为 `auto`、`json_object`、`json_schema`、`json_schema_strict`。默认 `auto` 保持兼容：`openai` 使用 `json_schema`，`deepseek` / `zai` / `openai_compatible` 使用 `json_object`。[DeepSeek 官方 JSON Output](https://api-docs.deepseek.com/zh-cn/guides/json_mode) 要求请求体设置 `response_format: {"type":"json_object"}`，并且 prompt 中包含 `json` 和期望结构示例；WriterYang 会自动追加标准 JSON mode guard 和紧凑 schema skeleton。`json_schema_strict` 只允许显式 opt-in；DeepSeek / ZAI 下配置 strict 会在本地报清晰错误，不会发出请求。

Web UI 会根据当前 `provider` 标记参数是否会进入 provider payload：不会生效的字段显示为 `NA` 并禁用。`thinking.type` 只在 `deepseek` / `zai` 下可编辑；`reasoning` 只在 `deepseek` 且 `thinking.type: enabled` 时发送为 `reasoning_effort`；`temperature` 在 `deepseek + thinking enabled` 和 `mock` 下显示 `NA`。`timeout_seconds`、`max_retries` 是本地 HTTP 调用参数，仍会生效并保持可编辑。

Provider 发送请求前会对最终 messages 做 CJK-aware prompt token 粗估；估算值超过当前 `max_context_tokens` 时会抛出 `ProviderContextLimitError` 并中断任务，不会继续调用真实 API。`project.yaml.context_budget.enabled` 默认仍为 `false`：当前预算策略过小，远小于主流模型上下文窗口，容易限制模型能力。后续计划改成动态上下文预算系统后再重新开启预算裁剪。

新项目的 `config/agents.yaml` 和 `config/embeddings.yaml` 由 `novel init` 使用当前模板生成。真实创作建议配置顶层 `default`，让各 profile 通过 `inherit_default: true` 继承；离线测试使用命令行 `--provider mock` 覆盖。

厂商差异：

- `deepseek`：默认 base URL 为 `https://api.deepseek.com`，发送 `thinking.type`；开启 thinking 时会发送 `reasoning_effort`，并避免发送无效的 `temperature`；响应中的 `reasoning_content` 会保存在 provider 原始响应和 `ModelResponse.reasoning_content` 中，不混入正文。
- `zai`：默认 base URL 为 `https://open.bigmodel.cn/api/paas/v4`，发送 `thinking.type`；响应中的 `reasoning_content` 会保存在 provider 原始响应和 `ModelResponse.reasoning_content` 中，不混入正文。

Provider 调用日志会写入项目的 `runs/provider_calls.jsonl`。这是轻量元数据日志，只记录 agent、provider、model、endpoint、耗时、重试次数、状态、错误类型、token 用量、`finish_reason` 和对应的 `model_io_path`，不记录真实 API Key。

为了方便 debug，每次 Agent 模型调用还会把完整输入输出写入 `runs/model_io/{request_id}.json`，并追加摘要到 `runs/model_io/index.jsonl`。完整日志包含 system prompt、user prompt、上下文、`prompt_version`、发送给 provider 的安全请求体、模型正文输出、reasoning 内容、`finish_reason`、原始响应摘要和错误信息；流式响应的原始日志只保留 chunk 数、finish chunk 和 usage chunk，避免把重复 SSE 元数据大量落盘。日志不会写入 HTTP header、Authorization、真实 API Key 或环境变量值。注意：这些日志会包含小说正文、隐藏设定和作者指令，仅适合本地排查，默认不应提交到 Git。

每次真实 provider 调用完成后，工具会根据调用日志刷新 `runs/provider_usage.json`，用于实时统计当前小说项目的累计调用量和 token 消耗。可以用下面的命令查看：

```bash
novel usage --path ./rain-station
novel usage --path ./rain-station --json
```

如果某个厂商响应没有返回 token usage，统计中会把该调用计入 `unknown_token_call_count`，但不会阻断主流程。

生成命令支持临时覆盖：

```bash
novel write-chapter 1 --path ./rain-station --agent-config config/agents.yaml --provider config
novel write-chapter 1 --path ./rain-station --model temporary-model --dry-run-provider
```

### Profile 作用和模型配置建议

不同 task 对 prompt 和输出 schema 的要求不同，但真正需要独立选模型的是 4 个能力 profile。实际部署时可先只配置 `default`，再按下面的 profile 分别优化成本、上下文和输出长度。

| Profile | 合并的 task | 能力重点 | 推荐配置 |
| --- | --- | --- | --- |
| `scribe` | `writer`、`polish`、`revision` | 中文长文生成、文风保持、角色声音、事实保持、长输出。 | `reasoning: medium-high`，`thinking.type: disabled`，`temperature: 0.5-0.8`，`max_tokens: 16000-32000`，`max_context_tokens: 128000`。 |
| `architect` | `plot`、`audit` | 长上下文剧情推理、一致性核对、伏笔控制、结构化 JSON。 | `reasoning: high`，复杂项目可 `thinking.type: enabled`，`temperature: 0-0.5`，`max_context_tokens: 128000+`，`max_tokens: 8192`。 |
| `loremaster` | `inspiration`、`style_guide`、`canon` | 创意构思、中文表达、稳定 JSON/ID、低频设定生成。 | `reasoning: medium`，`thinking.type: disabled`，`temperature: 0.4-0.8`，`max_context_tokens: 64000`，`max_tokens: 8192`。 |
| `clerk` | `state_update`、`chapter_memory`、`intent_router`、`memory_repair`、`setup` | 低创意抽取、分类路由、JSON patch、快速稳定、成本可控。 | `reasoning: low-medium`，`thinking.type: disabled`，`temperature: 0-0.3`，`max_context_tokens: 64000`，`max_tokens: 4096-8192`。 |

通用建议：

- `thinking.type` 默认用 `disabled`。只有在 `plot`、`audit` 这类复杂推理/一致性检查任务明显不稳定时，再为对应 agent 单独改成 `enabled`。
- `writer`、`polish` 和 `revision` 通常不建议开启思考模式。它们的输出要直接写入 Markdown 文件，模型额外的分析性内容会增加清洗风险。
- `temperature` 越高，语言和创意越发散；越低，结构化输出和一致性越稳定。JSON 输出类 agent 建议低温，正文类 agent 可以中高温。
- `max_context_tokens` 对 `plot`、`writer`、`polish`、`revision`、`audit` 更重要，因为这些步骤会读取 plan、canon、state、timeline 和正文。
- `max_tokens` 控制单次输出长度。`writer` / `polish` / `revision` 建议更高，结构化 JSON 类 agent 建议较低。
- `timeout_seconds` 对 `writer`、`polish`、`revision` 建议更高。长章节生成或修订本身耗时更长，过短会导致真实 API 测试和实际写作中断。

## 生成灵感

```bash
novel inspire "一个雨夜旧车站里传来已经停播多年的广播声" --path ./rain-station --provider config --overwrite
novel inspire --input input.txt --path ./rain-station --provider config --json --quiet --overwrite
```

命令会写入 `memory/inspiration.md`。使用 `--json` 时，也会写入本地派生的 `memory/inspiration.json` 并在 stdout 输出机器可读 JSON；自动化调用建议搭配 `--quiet`。Web UI 默认只生成 `inspiration.md`，并且只会自动覆盖初始化生成的空白灵感模板，不会静默覆盖作者已写内容。

## 管理 Canon

```bash
novel canon suggest --path ./rain-station --provider config
novel canon suggest --path ./rain-station --provider config --output canon-proposal.json
novel canon apply canon-proposal.json --path ./rain-station
novel canon validate --path ./rain-station
novel canon show --path ./rain-station
```

`novel show canon` 也保留为兼容别名。Canon 写入采用 proposal-first 流程，`apply` 会拒绝重复 ID，不会静默覆盖已有设定。

## 章节计划、写作、润色、审核

生成章节计划：

```bash
novel plan-chapter 1 --path ./rain-station --provider config
novel plan-chapter 2 --path ./rain-station --provider config --instruction "这一章要让主角第一次怀疑沈鹿的身份，但不要揭示真相"
novel plan-chapter 3 --path ./rain-station --provider config --input chapter3_request.txt
```

写初稿：

```bash
novel write-chapter 1 --path ./rain-station --provider config
novel write-chapter 1 --path ./rain-station --provider config --target-words 3000
novel write-chapter 2 --path ./rain-station --provider config --instruction "加强压抑感，减少解释性文字"
novel write-chapter 3 --path ./rain-station --provider config --input chapter3_writing_request.txt
```

润色：

```bash
novel polish-chapter 1 --path ./rain-station --provider config
novel polish-chapter 1 --path ./rain-station --provider config --light-edit
novel polish-chapter 1 --path ./rain-station --provider config --deep-edit
novel polish-chapter 1 --path ./rain-station --provider config --keep-length
```

审核：

```bash
novel audit-chapter 1 --path ./rain-station --provider config
novel audit-chapter 1 --path ./rain-station --provider config --strict
novel audit-chapter 1 --path ./rain-station --provider config --focus canon --focus timeline
novel audit-chapter 1 --path ./rain-station --provider config --audited-file draft.md --force
```

以上命令默认拒绝覆盖已有输出文件；需要明确传入 `--force`。

## 修订章节

```bash
novel revise-chapter 1 --path ./rain-station --provider config --instruction "加强悬疑感，但不要改变结尾事件"
novel revise-chapter 1 --path ./rain-station --provider config --from-audit
novel revise-chapter 1 --path ./rain-station --provider config --target draft --instruction "压缩解释性文字"
```

修订默认保存为版本文件，例如 `polished.v2.md` 或 `draft.v2.md`，并更新 `revision_log.json`。

## 状态和时间线更新

状态更新默认先生成 proposal，不直接修改 `current_state.json` 或 `timeline.json`：

`timeline.json` 使用双轨时间线：`story_position` 记录故事世界内的真实时间，`narrative_position` 记录事件在正文中的呈现章节/场景。尚未在正文揭示的背景/前史事件可以没有 `narrative_position`；不要使用 `chapter: 0` 表示开篇前。倒序、插叙、回忆和多线叙事应保持已揭示事件的 narrative 顺序递增；只有明确填写了同一故事线的 `story_position.order` 时，工具才会把 causes/effects 的先后关系作为硬冲突检查。

```bash
novel propose-state-update 1 --path ./rain-station --provider config
novel apply-state-update 1 --path ./rain-station
novel accept-chapter 1 --path ./rain-station
novel accept-chapter 1 --path ./rain-station --propose --provider config
```

proposal 文件会保存为 `memory/chapters/{chapter_number}/state_update_proposal.json`。
`apply-state-update` 会在写入前为 state 和 timeline 创建时间戳备份，并写入 `state_update_apply_log.json`。如果写入失败，会尝试从备份回滚。medium/high/critical 审核问题会阻止接受章节，除非显式传入 `--allow-issues`。low 问题会保留给作者决定是否修复。
推荐流程是先 `propose-state-update`，人工检查 proposal，再 `apply-state-update`，最后 `accept-chapter`。如果 apply log 已存在且状态为 `applied`，`accept-chapter` 只标记章节 accepted，不会重复应用 state/timeline 更新。
如果想一步完成，可以使用 `novel accept-chapter 1 --path ./rain-station --propose --provider config`；这会在缺少 proposal 时生成并应用。
接受章节后会写入结构化状态文件 `memory/chapters/{chapter_number}/metadata.json`，同时保留 `polished.md` front matter 中的 `status: accepted` 以兼容导出流程。
接受章节还会 best-effort 生成 `memory/chapters/{chapter_number}/chapter_memory.json`。它会记录读者可见摘要、剧情节点、状态变化、时间线事件、未解决线索和检索指针，并注入后续 `plot` / `writer` 上下文；如果模型配置不可用或输出无效，会降级为 deterministic fallback 并写入 warnings。ChapterMemory 只用于压缩上下文和引导检索，不能替代 `canon`、`current_state`、`timeline` 或 accepted `polished.md`。`accepted` 就是章节进入正式小说事实源；`session archive` 只是复制快照，不会把 ChapterMemory 改成另一种正式状态。`chapter_memory.strict_accept: true` 只会把生成失败升级为 error 级管理事件和醒目 warning，不会回滚或阻断已经完成的章节接受；修复方式是重新运行 `chapter-memory generate`，或在 Web UI 中刷新章节记忆。
接受章节还会尝试生成 `canon_drift_proposal.json`，用于补登本章新出现的角色、地点、物品、规则或伏笔；该 proposal 不会自动 apply，仍需要用户确认后走 `canon apply`。

可以手动查看或重建章节记忆：

```bash
novel chapter-memory show 1 --path ./rain-station
novel chapter-memory generate 1 --path ./rain-station --provider config --force
novel chapter-memory rebuild --path ./rain-station --provider config --missing-only
```

Web UI 的章节列表会显示缺失或 stale 的章节记忆，并提供单章“生成/刷新记忆”和批量“补全 / 刷新章节记忆”入口。

## 一键生成章节流水线

```bash
novel generate-chapter 1 --path ./rain-station --provider config
novel generate-chapter 1 --path ./rain-station --provider config --target-words 3000
novel generate-chapter 1 --path ./rain-station --provider config --stop-after plan
novel generate-chapter 1 --path ./rain-station --provider config --stop-after write
novel generate-chapter 1 --path ./rain-station --provider config --polish-mode auto
novel generate-chapter 1 --path ./rain-station --provider config --polish-mode review-gate
novel generate-chapter 1 --path ./rain-station --provider config --skip-audit
novel generate-chapter 1 --path ./rain-station --provider config --resume
novel generate-chapter 1 --path ./rain-station --provider config --force
```

每次运行都会写入 `runs/run_*.json`。如果某一步失败，run log 会记录失败状态和错误信息。
默认 `polish.mode` 是 `single_pass`：Writer 生成 `draft.md` 后直接提升为可审计的 `polished.md`，并标记 `polish_skipped: true`。需要 Writer -> Polish -> Audit 时使用 `--polish-mode auto`；需要停在初稿给人工审阅时使用 `--polish-mode review-gate`。旧 `--skip-polish` 仍作为 single-pass 兼容别名。
`--resume` 会复用已经存在的步骤产物并继续执行，适合从 `plan` 或 `write` 之后恢复流水线；`--force` 会重新生成目标文件。

## 搜索和可解释上下文

```bash
novel index rebuild --path ./rain-station
novel index refresh --path ./rain-station
novel index rebuild --path ./rain-station --with-embeddings --embedding-provider dashscope
novel index refresh --path ./rain-station --with-embeddings --embedding-provider dashscope
novel index status --path ./rain-station
novel search "林澈" --path ./rain-station --type character
novel search "旧车站广播" --path ./rain-station --type event --limit 5
novel search "破损车票" --path ./rain-station --type chapter --chapter 1 --highlight --json
novel search "旧车站未解决线索" --path ./rain-station --type chapter_memory --chapter 1
novel search "旧物修复师" --path ./rain-station --use-vector --embedding-provider dashscope
```

搜索索引位于 `memory/search_index.json`、`memory/search_index.sqlite` 和 `memory/search_index_manifest.json`。当前实现包括：

- 中文检索增强：对连续中文文本生成 2-gram / 3-gram 检索 token。
- 字段权重：`id`、标题、类型、路径、正文使用不同权重评分。
- 过滤：支持 `--type character/location/item/event/chapter/chapter_memory/all` 和 `--chapter`。
- 高亮：`--highlight` 会返回 `<mark>...</mark>` 标记的 excerpt。
- SQLite FTS：`memory/search_index.sqlite` 中包含 FTS5 表。
- freshness manifest：每个文档记录 `sha256`、`mtime`、索引时间和 FTS / embedding 状态。普通 `novel search` 会在 FTS 缺失或过期时自动刷新关键词索引；显式启用 `--use-vector` 或上下文检索选择 `--vector-context on` 时，会先刷新缺失或过期的真实 embedding 向量。
- 向量表：SQLite 中可选保存真实 embedding 向量。真实 embedding 只在用户显式刷新向量索引或显式启用 vector 检索时调用，不会作为普通 FTS 搜索的隐式成本。
- ChapterMemory：`chapter_memory.json` 会作为 `chapter_memory` 类型入索引，检索权重高于普通章节文件；命中结果只作为导航指针，具体事实仍需回到 accepted `polished.md`、canon、state 或 timeline 校验。

默认可靠路径是关键词 + SQLite FTS。`local_hash` 只用于测试和离线开发 fixture，不作为真实创作的语义检索 fallback。没有配置真实 embedding API 时，`--use-vector` 会给出清晰错误；Web UI 状态栏会标红提示“当前无法使用基于 embedding 的语义检索；普通关键词搜索仍可用”。项目初始化后，也可以在 Web UI 的“模型与检索配置”页重新填写 Embedding Base URL、API Key、provider、模型名、`dimensions` 和 `batch_size`；保存前会用当前参数做连通性验证，保存成功会清空输入框并自动刷新语义向量索引。DashScope `text-embedding-v4` 默认按文档上限使用 `dimensions: 2048` 和 `batch_size: 10`。

Embedding 配置位于 `config/embeddings.yaml`，格式示例：

```yaml
schema_version: 2
active_provider: "dashscope"
providers:
  test_local_hash:
    provider: "local_hash"
    model: "local-hash-v1"
    dimensions: 32
  dashscope:
    provider: "dashscope"
    base_url_env: "DASHSCOPE_EMBEDDING_BASE_URL"
    api_key_env: "DASHSCOPE_API_KEY"
    model: "text-embedding-v4"
    dimensions: 2048
    batch_size: 10
    timeout_seconds: 30
    max_retries: 1
  zhipu:
    provider: "zhipu"
    base_url_env: "ZHIPU_EMBEDDING_BASE_URL"
    api_key_env: "ZHIPU_API_KEY"
    model: "embedding-3"
    dimensions: 2048
```

`embedding provider` 当前支持：

| provider | 用途 | 默认 base URL |
| --- | --- | --- |
| `local_hash` | 测试 / 离线 fixture | 无网络调用；不推荐真实创作使用 |
| `dashscope` | 阿里 DashScope `text-embedding-v4` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `zhipu` | 智谱 `embedding-3` | `https://open.bigmodel.cn/api/paas/v4` |
| `openai` | 标准 OpenAI embeddings | `https://api.openai.com/v1` |
| `openai_compatible` | 其他 OpenAI-compatible embeddings | 必须通过 `base_url_env` 配置 |

真实 embedding API 使用 OpenAI-compatible `/embeddings` 请求形态。API Key 通过环境变量或项目 `.env` 读取，不写入 `config/embeddings.yaml`、搜索索引或错误消息。DashScope provider 或 DashScope compatible base URL 会自动发送 `encoding_format: "float"`；旧配置里超过 DashScope 批量上限的 `batch_size` 会在实际请求时限制到 10，避免索引刷新因批量超限失败。

规划、写作、审核可以选择加入检索上下文：

```bash
novel plan-chapter 1 --path ./rain-station --provider config --use-search-context
novel write-chapter 1 --path ./rain-station --provider config --use-search-context
novel audit-chapter 1 --path ./rain-station --provider config --use-search-context
novel write-chapter 1 --path ./rain-station --provider config --use-search-context --vector-context auto
```

`--use-search-context` 默认使用结构化实体扩展 + FTS 补充。`--vector-context auto` 只在真实 embedding 配置完整时加入语义召回；`--vector-context on` 会强制尝试，旧 `--use-vector-context` 是兼容别名；`off` 会关闭语义召回。如果真实 embedding 向量缺失或过期，工具会先自动刷新向量索引。没有配置真实 embedding API 时会给出清晰错误或 warning，不会用 `local_hash` 冒充真实语义检索。

## 受控编排

```bash
novel ask "写第1章：雨夜旧车站，建立调查动机" --path ./rain-station --provider config
novel session show <session_id> --path ./rain-station
novel session approve-outline <session_id> --path ./rain-station
novel session run <session_id> --path ./rain-station --provider config
novel ask "请为第1章生成章节计划" --path ./rain-station --provider config --dry-run
```

`novel ask` 是规则化 orchestrator，不做自由多 agent 辩论。非 dry-run 时，它会创建 Creation Session 和大纲 proposal；dry-run 仍只显示分类和 handoff 计划，不写文件。

## 导出

默认只导出 accepted 的 `polished.md` 章节：

```bash
novel export markdown --path ./rain-station
novel export markdown --path ./rain-station --chapters 1,2,3
novel export markdown --path ./rain-station --from 1 --to 10
novel export markdown --path ./rain-station --include-unaccepted
novel export markdown --path ./rain-station --output exports/book.md --title "雨夜旧车站" --force
novel export markdown --path ./rain-station --toc --volume-title "第一卷 雨声"
novel export markdown --path ./rain-station --chapter-number-style arabic
```

Markdown 导出支持：

- `--toc`：生成 Markdown 目录。
- `--volume-title`：在目录和正文前插入卷标题。
- `--chapter-number-style chinese|arabic|chapter|plain`：控制章节标题编号样式，默认 `chinese`，例如 `第一章`。

Word 导出：

```bash
novel export docx --path ./rain-station
novel export docx --path ./rain-station --chapters 1,2,3
novel export docx --path ./rain-station --include-unaccepted
novel export docx --path ./rain-station --output exports/book.docx --title "雨夜旧车站" --force
```

导出会更新 `exports/export_manifest.json`。manifest 会记录导出文件、章节列表，以及每个源 `polished.md` 的相对路径、标题、accepted 状态和 `sha256`，方便追踪导出来源。Word 导出依赖 `python-docx`；当前优先完善 Markdown 导出，DOCX 暂不继续优化排版。

## 最小 Web UI

```bash
novel web --path ./rain-station
novel web --path ./rain-station --host 127.0.0.1 --port 9000
novel web --path ./rain-station --open
```

普通 `novel web --path ...` 的默认端口从项目的 `project.yaml` 读取：

```yaml
web:
  default_port: 8765
```

如果没有配置，默认使用 `8765`。命令行 `--port` 会覆盖项目配置。端口被占用时，命令会给出清晰错误提示并退出，例如建议改用 `novel web --port 8766`。

`WriterYang_WebUI.command` 使用启动器配置 `WriterYang_WebUI.config.json`，不读取小说项目的 `project.yaml.web.default_port`。Web UI 页面里的端口设置更新的是启动器配置；保存时会验证端口可用。若启动器启动时发现配置端口已被占用，会临时改用下一个空闲端口，并在运行环境面板提醒用户重新保存端口。

打开 `http://127.0.0.1:8765`。如果希望 CLI 启动服务时自动打开浏览器，可以使用 `novel web --open`；默认和 `--no-open` 都不会自动打开。Web UI 顶部有 6 个主页面：主页、创作工作台、文风设置、小说状态管理、模型与检索配置、运行日志 / 项目文件。Web API 调用同一套 core service，不返回真实 API Key。长任务执行时会显示已用时和最近操作；当前任务相关按钮会临时禁用，但取消当前 Session 任务和刷新类按钮仍可用。完成后会保留真实返回消息和 Session 状态，不会被自动刷新覆盖。

面向非技术作者的浏览器操作流程见：[Web UI 小白使用指南](docs/WEB_UI_USER_GUIDE.md)。日常创作推荐使用“创作工作台”：创建大纲 -> 修改/批准大纲 -> 开始写作 -> 按 Audit 或用户意见修订 -> 认可 -> 归档 -> 导出。

当前 Web 工作台还支持：

- 主页：初始化项目、打开项目、项目初始引导、项目检查、项目摘要、章节列表、导出 Markdown / DOCX 和下一步提示。
- 创作工作台：生成灵感、Canon 建议、Session 大纲协商、正文生成、审核修订、认可归档、章节对照、章节编辑器、Audit 定位和 Revision diff。
- 文风设置：编辑长期生效的 `memory/style_guide.md`，保存时自动备份旧文件；也可输入自然语言文风方向，让 `style_guide` Agent 生成 Markdown 草稿并填入编辑器，确认保存前不会写入文件。单章临时文风仍写在创作工作台的聊天 / 指令里。
- 小说状态管理：Canon 摘要、状态和时间线、项目管家和后台管理动态。
- 模型与检索配置：Profile 模型配置、Embedding API 重新配置、embedding 状态、FTS / embedding 索引刷新。
- 运行日志 / 项目文件：项目搜索、安全文件树、只读文件预览、章节文件查看、运行日志、用量统计和 model I/O 摘要。
- Inspiration / Canon：可生成 `memory/inspiration.md`，生成 canon proposal，并显式 apply proposal。Web UI 灵感默认走 Markdown 弱总纲，不为 Inspiration 强制开启 provider JSON mode；需要 `inspiration.json` 时可用 CLI 的 `--json` 或后续工具派生。
- 章节对照：只读查看 `plan.json`、`draft.md`、`polished.md`、`audit.json`。
- 章节编辑器：可编辑 `draft.md` / `polished.md`，保存时默认创建 `draft.v2.md` / `polished.v2.md` 等版本文件，并记录 `revision_log.json`，不原地覆盖旧稿；有未保存修改时离开页面会提示，`Ctrl/Cmd+S` 会保存新版本。
- Audit 定位：读取 `audit.json` 的 evidence quote，定位到正文中的行列位置；找不到时显示无法定位。
- Revision diff：只读展示两个工作区文件的 unified diff，适合对比版本稿。
- 运行日志：查看 `runs/*.json` 和 provider 调用安全摘要。
- 项目搜索：在 Web UI 中搜索角色、地点、物品、时间线事件和章节文本。默认使用 FTS；语义检索模式为 `auto` 时只在 embedding 配置完整时启用，兼容勾选“强制使用 embedding 语义检索”时会按 `on` 处理。
- 用量统计：读取 `/api/usage`，展示 provider calls、成功/失败次数、token 汇总，以及按 Agent / Provider / Model 的统计；stream 调用会尽量记录 provider 返回的 usage 和 `finish_reason`。
- 导出：主页可导出 Markdown 或 DOCX，可指定章节列表、章节范围、标题、输出路径、是否包含未 accepted 章节，以及是否覆盖已有导出文件。
- Profile 模型配置：用表单展示并允许编辑 `scribe`、`architect`、`loremaster`、`clerk` 的非密钥字段，例如 provider、model、base_url_env、api_key_env、temperature、thinking、timeout；勾选“继承 default”时继承 default 并保留 profile patch，取消后保存为独立完整配置。任务级覆盖在高级区，默认隐藏，用于 `intent_router` 等少数 task 单独换模型。当前 provider 不会使用的字段显示 `NA` 并禁用。只显示环境变量名和是否存在，不显示真实值，保存前会校验并备份。
- Embedding API 配置：在“模型与检索配置”页重新测试并保存语义检索 API。已配置成功时默认收起输入框，显示“Embedding API 已配置”、当前 provider、模型名、`dimensions` 和 `batch_size`；点击“修改配置”后重新填写 Base URL、API Key、provider、模型名和参数。API Key 只写入项目 `.env`，保存前会用当前批量和维度验证真实 API，保存成功后清空输入框并自动刷新语义向量索引。
- 如果页面提示“Web UI 后台版本不匹配”，通常是更新代码后只刷新了浏览器页面、没有重启正在运行的 Web UI 后台进程。请停止旧后台，重新用当前安装环境启动 Web UI，然后刷新页面；前端不会用旧接口响应猜测 Agent 或 embedding 配置状态。
- 状态和时间线：以表格、章节分组和物品/角色状态摘要查看 `current_state.json`、`timeline.json`。
- Session 面板：显示当前 session id、outline/content 状态和章节范围；创建新 session 时会清空旧 id 并使用服务端返回的新 id。
- 当前任务进度：Session 写作期间显示阶段、章节、轮次、已用时和最近事件；“取消当前 Session 任务”只会在安全边界生效，不会立刻打断当前 LLM 请求。
- 自动打回重写记录：当 Audit 把正文打回重写时，显示第几章第几轮、打回原因、系统动作和“查看被打回原文”按钮；如果你认为打回不合理，应检查对应 `audit.json`、`memory/state/timeline.json`、`current_state.json` 和 canon 文件。
- 项目管家：用自然语言说明 timeline/state/canon 的错误，生成可审查 repair proposal；设定变更描述不清楚时会先展示 Agent 的澄清问题，补充后再生成 proposal。确认应用前不会改正式 memory 文件。后台管理动态会显示状态更新、时间线更新、记忆修复 proposal/apply 等事件。
- Audit 复审控制：选择 rewrite event 后，可以纠正 Audit 理解并重新审核、根据新审核重新打回，或撤回本次打回并恢复原文快照。
- 下一步提示：根据项目状态、session 状态和项目检查结果提示下一步操作，降低误点旧 session 或跳过审核的风险。
- Session 大纲修订：大纲不满意时，在聊天 / 指令框输入修改意见并点击“修改大纲”；满意后再批准大纲。
- Session 修订：当内容停在 `needs_revision` 时，可点击“按 Audit 修订内容”直接根据当前 `audit.json` 修订；也可以在聊天/指令框输入意见后点击“按用户意见修订内容”。用户意见会先由 Orchestrator 结构化路由：剧情结构变化回到 Plot 重写大纲，人物刻画/节奏/风格问题由 Writer/Polish 重写正文，只有指定局部语句表达才交给 Revision。修订后系统会重审并刷新 state proposal。

Web API 统一返回：

```json
{
  "ok": true,
  "data": {}
}
```

错误返回包含稳定错误码和 request id：

```json
{
  "ok": false,
  "error": {
    "code": "invalid_project",
    "message": "...",
    "details": {},
    "request_id": "web_..."
  }
}
```

## 外部 Agent / openclaw 调用

自动化工具建议使用 `--project --json --quiet`：

```bash
novel status --project ./rain-station --json --quiet
novel doctor --project ./rain-station --json --quiet
novel ask "请为第1章生成章节计划" --project ./rain-station --provider config --json --quiet
```

更多说明见 [docs/INTEGRATION.md](docs/INTEGRATION.md) 和 [docs/openclaw_tool_manifest.json](docs/openclaw_tool_manifest.json)。

CLI 集成约定：

- 所有子命令都会注入 `--project`、`--json`、`--quiet`。
- JSON 错误输出包含稳定 `error.code`、兼容字段 `error.type` 和 `error.exit_code`。
- `novel doctor` 会检查依赖、项目结构、schema validation、API 环境变量和 tracked 文件 secret scan，不输出真实密钥值。
- `novel completion bash|zsh|fish` 会输出基础 shell completion 脚本。
- 写入类命令会使用项目锁 `.writeryang.lock`，避免两个进程同时修改同一小说项目；异常退出留下的陈旧锁会自动清理。

## 发布检查

发布前请参考 [docs/RELEASE.md](docs/RELEASE.md)。核心检查包括：

```bash
pytest
pytest tests/test_web.py
pytest -m web_e2e
python -m build
tmp_project="$(mktemp -d)/writeryang-template"
novel init "模板校验" --path "$tmp_project" --no-guide
novel validate --path "$tmp_project"
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

智谱 GLM 可使用：

```bash
ZAI_BASE_URL=
ZAI_API_KEY=
ZAI_MODEL=
```

运行真实 API 测试：

```bash
python scripts/smoke_session.py --provider config --chapters 1 --model "$WRITERYANG_REAL_MODEL" --keep --json
pytest -m real_api
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

## License

Copyright 2026 ThereWasAYang.

本项目基于 [Apache License 2.0](LICENSE) 开源。该协议适用于整个仓库，包括代码、文档和 schemas。
