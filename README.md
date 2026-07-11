# WriterYang

WriterYang 是一个面向中文长篇小说创作的 AI 辅助写作工具。它把灵感、设定、人物、地点、物品、时间线、章节计划、正文、润色稿、审核报告和导出结果保存为可编辑的 Markdown / JSON / YAML 文件，并用 multi-agent workflow 串起创作、审核和项目记忆维护。

当前版本重点是本地 CLI 和本地 Web UI。新用户推荐优先使用 Web UI 的 Session 流程；CLI 保留给高级使用、调试、自动化和外部工具集成。所有测试默认使用 `MockProvider`，不依赖真实 API Key。

## 环境配置

推广初期仅面向 macOS / Linux 使用和验收。Windows 适配暂缓；仓库中保留了部分实验脚本和底层入口，但 Windows 运行期还没有完成全链路验收，本轮不作为推广支持平台。Windows 用户建议等后续版本补齐适配后再使用。

当前支持 Python 3.11-3.13，推荐 3.12。建议为 WriterYang 创建独立 Python 环境，不要直接使用系统 Python，也不要复用已有项目环境。

## 安装

macOS / Linux 推荐使用一键安装脚本：

```bash
./install.sh
```

脚本会优先使用 conda 创建 `WriterYang_YYMMDD` 格式的新环境；没有 conda 时回退到 `.venv/WriterYang_YYMMDD`。安装完成后会以 editable 模式安装当前源码目录，自动寻找 Web UI 可用端口，打印地址并打开浏览器。

安装器会生成 `WriterYang_WebUI.command` 和同目录的 `WriterYang_WebUI.config.json`。之后可以双击启动器打开 Web UI；启动器会固定使用安装脚本创建的新环境，并从 config 文件读取下次启动端口。Web UI 中保存端口会先验证端口可用，再更新这个 config 文件。

常用安装参数：

```bash
./install.sh --web-port 9000
./install.sh --no-web
./install.sh --no-open-web
./install.sh --no-activate-shell
python scripts/install_writeryang.py --dry-run
python scripts/install_writeryang.py --dev
```

手动创建环境：

```bash
conda create -n writeryang "python>=3.11,<3.14" -y
conda activate writeryang
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

不使用 conda 时：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

检查安装：

```bash
novel --version
novel doctor
```

## 10 分钟路径

完整新手流程见 [新手快速开始](docs/QUICKSTART.md)。最短 mock 流程：

```bash
novel init "青灯客栈" --path ./qingdeng-inn --no-guide
novel inspire "雨夜客栈里，一盏青灯照出二十年前失踪剑客的影子。" --path ./qingdeng-inn --provider mock --overwrite
novel canon suggest --path ./qingdeng-inn --provider mock --output ./qingdeng-canon.json
novel canon apply ./qingdeng-canon.json --path ./qingdeng-inn
novel session start --path ./qingdeng-inn --chapters 1 --intent "写第一章" --provider mock
novel session approve-outline <session_id> --path ./qingdeng-inn
novel session run <session_id> --path ./qingdeng-inn --provider mock
novel session accept <session_id> --path ./qingdeng-inn
novel export markdown --path ./qingdeng-inn --force
```

当前工作区 schema 为 v3。旧 schema 项目会被直接拒绝，不提供 migrate 命令。正式导出只读取带有效 `acceptance.json` 和 artifact lineage 的 `accepted.md`；未接受内容不能进入正式导出。

命令参考见 [CLI 命令参考](docs/CLI_COMMANDS.md)，Web UI 图文流程见 [Web UI 小白图文使用指南](docs/WEB_UI_USER_GUIDE.md)。

## Web UI

安装脚本默认会启动 Web UI。也可以手动运行：

```bash
novel web --path ./qingdeng-inn --host 127.0.0.1 --port 8765
novel web --path ./qingdeng-inn --open
```

Web UI 使用同一套 core logic，不会把真实 API Key 返回到前端。主要页面：

- 主页：初始化项目、打开项目、项目检查、导出、运行环境和下一步提示。
- 创作工作台：Session 大纲协商、正文生成、审核修订、认可归档、章节对照和设定变更。
- 文风设置：编辑长期文风，也可让 `style_guide` Agent 生成草稿。
- 小说状态管理：Canon 摘要、状态、时间线和后台管理动态。
- 模型与检索配置：Profile 模型配置、Embedding API 配置、FTS / embedding 索引刷新。
- 运行日志 / 项目文件：查看项目文件、运行日志、provider 调用和用量统计。

Web API 默认限制 POST 请求体为 32MB，可用 `WRITERYANG_WEB_MAX_BODY_BYTES` 调整。Web server 还会校验 `/api/*` 请求的本机 Host / Origin，避免非本机页面读取或写入本地项目内容。

## 模型配置

真实创作建议在 `config/agents.yaml` 配置顶层 `default`，让各 profile 通过 `inherit_default: true` 继承。离线测试使用命令行 `--provider mock` 覆盖。

| Profile | 默认用途 |
| --- | --- |
| `scribe` | 正文写作、润色和修订 |
| `architect` | 灵感扩展、章节计划和结构化推理 |
| `loremaster` | Canon、状态、时间线和一致性审核 |
| `clerk` | 摘要、整理、轻量校验和辅助任务 |

Provider 适配与 Agent logic 分离。`deepseek`、`zai` 和 OpenAI-compatible provider 的私有参数由 provider adapter 决定是否进入 payload；API Key 只读取环境变量或项目 `.env`，不要写入 YAML、JSON、Markdown 或日志。

## 日志与用量

Provider 调用元数据写入 `runs/provider_calls.jsonl`，不记录真实 API Key。完整模型输入输出写入 `runs/model_io/{request_id}.json`，并追加摘要到 `runs/model_io/index.jsonl`。

`runs/model_io/` 默认保留最近 500 份完整日志，并限制总体积约 200MB；写入新日志后会 best-effort 清理更旧的 model I/O 文件并同步裁剪 `index.jsonl`。可用 `WRITERYANG_MODEL_IO_MAX_FILES`、`WRITERYANG_MODEL_IO_MAX_BYTES` 调整；设为 `0` 表示关闭对应上限。若只需要轻量排障，可设置 `WRITERYANG_MODEL_IO_MODE=metadata`，此时 prompt、正文和 raw response 会被省略。

用量统计会增量刷新 `runs/provider_usage.json`：

```bash
novel usage --path ./qingdeng-inn
novel usage --path ./qingdeng-inn --json
```

## 文档地图

- [新手快速开始](docs/QUICKSTART.md)：从安装到 mock 全流程。
- [CLI 命令参考](docs/CLI_COMMANDS.md)：常用命令、调试命令和脚本入口。
- [Web UI 小白图文使用指南](docs/WEB_UI_USER_GUIDE.md)：面向非技术作者的浏览器流程。
- [模型配置最佳实践](docs/MODEL_CONFIG_BEST_PRACTICES.md)：Provider、Profile、任务级覆盖和成本控制。
- [手动编辑 Memory 指南](docs/MEMORY_EDITING.md)：如何安全改 Markdown / JSON memory。
- [调试与重构指南](docs/DEBUGGING_AND_REFACTORING.md)：日志、问题定位、测试和重构边界。
- [开发者指南](docs/DEVELOPER_GUIDE.md)：目录结构、扩展 workflow、CLI/Web 入口。
- [代码库参考](docs/CODEBASE_REFERENCE.md)：模块级索引。
- [Agent Prompt 组装说明](docs/AGENT_PROMPT_ASSEMBLY.md)：prompt、context 和 schema 约束。
- [外部 Agent 集成](docs/INTEGRATION.md)：JSON contract 和 openclaw manifest。
- [发布流程](docs/RELEASE.md)：发布前检查、构建和 GitHub Release。
- [更新日志](CHANGELOG.md)：版本变化记录。

## 测试和发布检查

常用本地检查：

```bash
python -m pytest -m "not real_api and not web_e2e" -q
python -m pytest -m web_e2e -q
ruff check src tests scripts
mypy src scripts
python -m build
python -m twine check dist/*
```

脚本入口：

- `scripts/check_local.py`：本地检查聚合入口。
- `scripts/smoke_session.py`：mock Session smoke flow。
- `scripts/debug_bundle.py`：收集调试包，注意包内可能包含小说正文。
- `scripts/provider_ping.py`：真实 provider 连通性检查。

发布前还应运行项目内 secret scan，确认没有 API Key 或 token 进入仓库。

## FAQ

### 测试需要真实 API Key 吗？

不需要。默认测试使用 `MockProvider`。真实 API 测试带 `real_api` marker，需要本地显式配置后才运行。

### API Key 放在哪里？

放在环境变量或项目 `.env`。不要把真实 API Key 写入项目文件、示例 YAML、README、issue、日志或测试 fixtures。

### Windows 现在能推广使用吗？

不能。仓库中保留了部分 Windows 入口脚本，但推广初期只支持 macOS / Linux。Windows 适配会在后续版本完成运行期修复、CI 和真机验收后再开放。

### 为什么命令拒绝覆盖文件？

WriterYang 默认把中间产物当作可审阅的创作资料。会覆盖文件的命令通常需要显式 `--force` 或先写到新路径。

### 是否已经是完整 MCP server？

不是。当前重点是本地 CLI、Web UI 和外部 Agent JSON contract；`docs/openclaw_tool_manifest.json` 用于描述可被外部工具调用的命令边界。

## License

Apache-2.0. Copyright 2026 ThereWasAYang.
