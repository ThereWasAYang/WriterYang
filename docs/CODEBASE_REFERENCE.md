# 代码库参考手册

本文是代码级地图。它不替代源码，但说明每个主要文件、class 和 function 的职责，帮助新开发者或大模型 Agent 快速定位修改点。

覆盖口径：公共 class、service function、CLI/Web 入口和跨模块 helper 会逐项说明；私有 helper 按所在模块的职责分组说明，并在对应模块列出关键函数名。新增 Python 文件、schema、Agent prompt 或 CLI/Web API 后，必须同步更新本文和相关专题文档。

## 1. 顶层文件

| 路径 | 作用 | 常见修改场景 |
| --- | --- | --- |
| `pyproject.toml` | Python 包配置、依赖、dev 依赖、ruff/mypy/pytest 配置、console script。 | 新增依赖、调整 lint/type/test 配置、改版本发布配置。 |
| `README.md` | 用户入口文档。 | 新命令、新工作流、新配置说明。 |
| `CHANGELOG.md` | 版本变更记录。 | 每轮可见能力变化。 |
| `CONTRIBUTING.md` | 贡献指南。 | 协作规范、测试命令、安全要求。 |
| `.github/workflows/tests.yml` | CI：pytest、build、secret scan、ruff、blocking mypy、Web E2E。 | 改 CI 阶段或 Python 版本。 |
| `.github/workflows/release.yml` | tag 触发 GitHub Release 构建。 | 发布流程调整。 |
| `schemas/*.schema.json` | 从 Pydantic models 导出的 JSON Schema。 | schema 变化后重新导出。 |
| `scripts/` | 确定性工具脚本。 | 一键安装、本地质量门禁、pre-push hook 安装、Session smoke、provider ping、debug bundle、Web UI smoke、项目健康报告。 |

## 2. 包入口

### `src/novel/__init__.py`

定义包版本 `__version__`。发布、`novel --version` 和 packaging 测试依赖它。

### `src/novel/__main__.py`

支持 `python -m novel`。通常只转发到 `novel.cli:main`。

## 2.1 Python 源文件覆盖清单

本文覆盖以下 Python 源文件。新增文件时应同步更新本节和对应说明：

- `src/novel/cli.py`
- `src/novel/cli_shared.py`
- `src/novel/cli_commands/generation.py`
- `src/novel/cli_commands/memory.py`
- `src/novel/cli_commands/orchestrator.py`
- `src/novel/cli_commands/preview.py`
- `src/novel/cli_commands/project_system.py`
- `src/novel/cli_commands/search.py`
- `src/novel/cli_commands/session.py`
- `src/novel/cli_commands/revision_session.py`
- `src/novel/web_api/__init__.py`
- `src/novel/web_api/common.py`
- `src/novel/web_api/config.py`
- `src/novel/web_api/deps.py`
- `src/novel/web_api/envelope.py`
- `src/novel/web_api/generation.py`
- `src/novel/web_api/inspection.py`
- `src/novel/web_api/memory.py`
- `src/novel/web_api/preview.py`
- `src/novel/web_api/router.py`
- `src/novel/web_api/session.py`
- `src/novel/web_api/revision_session.py`
- `src/novel/web_server.py`
- `src/novel/core/__init__.py`
- `src/novel/core/agent_defaults.py`
- `src/novel/core/agent_output.py`
- `src/novel/core/app_logging.py`
- `src/novel/core/artifact_store.py`
- `src/novel/core/budget.py`
- `src/novel/core/auditing.py`
- `src/novel/core/canon.py`
- `src/novel/core/chapter_memory.py`
- `src/novel/core/chapter_versions.py`
- `src/novel/core/command_bus.py`
- `src/novel/core/consistency.py`
- `src/novel/core/context_budget.py`
- `src/novel/core/drafting.py`
- `src/novel/core/embeddings.py`
- `src/novel/core/env.py`
- `src/novel/core/exporting.py`
- `src/novel/core/gender.py`
- `src/novel/core/inspection.py`
- `src/novel/core/inspiration.py`
- `src/novel/core/io.py`
- `src/novel/core/json_extract.py`
- `src/novel/core/json_schema.py`
- `src/novel/core/locking.py`
- `src/novel/core/lifecycle.py`
- `src/novel/core/management.py`
- `src/novel/core/memory_repair/__init__.py`
- `src/novel/core/memory_repair/apply.py`
- `src/novel/core/memory_repair/deps.py`
- `src/novel/core/memory_repair/generation.py`
- `src/novel/core/memory_repair/impact.py`
- `src/novel/core/memory_repair/models.py`
- `src/novel/core/memory_repair/preflight.py`
- `src/novel/core/memory_repair/service.py`
- `src/novel/core/memory_repair/validation.py`
- `src/novel/core/memory_repair_mock.py`
- `src/novel/core/memory_repair_ops.py`
- `src/novel/core/memory_repair_rules.py`
- `src/novel/core/model_io.py`
- `src/novel/core/orchestrator.py`
- `src/novel/core/plan_refs.py`
- `src/novel/core/planning.py`
- `src/novel/core/polishing.py`
- `src/novel/core/projection.py`
- `src/novel/core/previewing.py`
- `src/novel/core/prompts.py`
- `src/novel/core/provider_config.py`
- `src/novel/core/providers.py`
- `src/novel/core/revision.py`
- `src/novel/core/revision_workflow.py`
- `src/novel/core/markdown_blocks.py`
- `src/novel/core/runtime_config.py`
- `src/novel/core/schemas.py`
- `src/novel/core/search.py`
- `src/novel/core/security.py`
- `src/novel/core/session.py`
- `src/novel/core/setup_guide.py`
- `src/novel/core/setting_change_followup.py`
- `src/novel/core/state_change_values.py`
- `src/novel/core/state_update.py`
- `src/novel/core/structured_generation.py`
- `src/novel/core/task_registry.py`
- `src/novel/core/transactions.py`
- `src/novel/core/world_state.py`
- `src/novel/core/style_guide.py`
- `src/novel/core/timeutil.py`
- `src/novel/core/usage.py`
- `src/novel/core/validation.py`
- `src/novel/core/web_launcher.py`
- `src/novel/core/workflow.py`
- `src/novel/core/workflow_runtime.py`
- `src/novel/core/workspace.py`

## 3. CLI 层

CLI 是薄包装：解析参数、处理 `--json/--quiet/--project`、拿项目锁、调用 core service、格式化输出。

### `src/novel/cli.py`

- `build_parser()`：定义所有 CLI 命令、子命令和参数。
- `main(argv)`：命令分发入口，只解析参数、应用 `--project` alias，并通过 `_COMMAND_HANDLERS` 调用对应 handler。
- `_COMMAND_HANDLERS`：顶层命令到 `cli_commands/` handler 的 dispatch map，避免在 `main()` 中复用不同 result 类型。

### `src/novel/cli_shared.py`

通用 CLI helper：

- `_add_agent_runtime_args()`：给 Agent 命令统一加 `--agent-config`、`--provider`、`--model`、`--dry-run-provider`。
- `_add_integration_args()` / `_add_integration_args_recursive()`：给命令递归加 `--json`、`--quiet`、`--project`。
- `_apply_project_alias()`：把 `--project` 映射为内部 `path`。
- `_success()` / `_failure()` / `_print_json()`：统一文本和 JSON 输出。
- `_safe_message()`：错误消息脱敏。
- `_command_lock()`：写命令加项目锁。
- `_print_dry_run_provider()`：显示将使用的 provider 配置，不调用 API。
- `_validation_payload()` / `_status_payload()`：把 core result 转为 CLI JSON payload。
- `_format_usage_summary()`：格式化 provider usage。
- `_resolve_web_port()`：普通 `novel web` 端口解析，读取项目配置。
- `_cmd_web_launch()`：启动器专用入口，读取 `WriterYang_WebUI.config.json`，端口占用时临时选择下一个空闲端口。
- `completion_script()`：生成 shell completion。
- `run_doctor()` / `format_doctor_result()` / `_doctor_*()`：环境、项目、配置、安全检查。
- `_audit_issue_lines()`：把 audit issue 展示给用户。

### `src/novel/cli_commands/`

- `project_system.py`：`init`、`validate`、`migrate`、`schema`、`completion`、`doctor`、`status`、`usage`、`show`、`web`。
- `search.py`：`index`、`search`。
- `memory.py`：`memory-repair`、`setting-change`、`chapter-memory`。
- `orchestrator.py`：`ask`。
- `session.py`：`session` 及其子命令 payload/rewrite/route 展示。
- `generation.py`：`inspire`、`canon`、章节计划/写作/润色/审核/修订、state update、accept、generate、export。

开发建议：

- 新命令先有 core service，再接 CLI。
- 写命令必须进入 `_command_lock()`。
- JSON 输出不要手写字符串，走 `_success()` / `_failure()`。

## 4. Web 层

### `src/novel/web_server.py`

本地 HTTP server。负责：

- 启动静态页面和 API。
- 读取默认端口或 CLI 传入端口。
- 端口冲突时给清晰错误。
- 从 `web_static/` 安全读取 `index.html`、`app.css` 和 `app_*.js`，并通过 `/static/...` 提供静态资源。
- 对 HTML、静态资源和 API 响应发送 no-cache headers，避免浏览器继续显示旧版 Web UI。
- 对 Web API 请求做 HTTP 层保护：限制 POST 请求体大小，并校验 `/api/*` 的 `Host` / `Origin` 属于本机同源。
- 不包含业务逻辑。

### `src/novel/web_api/`

本地 JSON API 包，保留 `from novel.web_api import handle_api_request` 入口。核心模块：

- `__init__.py`：导出 `handle_api_request()`、`_locked_write()`、`web_launcher` 和 `WebAPIError`，兼容仍在测试中使用的 monkeypatch 路径。
- `router.py`：统一 API 入口、GET/POST 路由表、项目锁和失败日志。
- `envelope.py`：统一响应模型和 `_success()` / `_failure()` 导出。
- `common.py`：响应 envelope、路径白名单、请求解析、provider usage 注入和共享 helper。
- `generation.py`：章节计划、写作、润色、审核、导出、章节记忆和章节文件保存。
- `config.py`：文风、provider/embedding 配置、项目初始化和 Web 初始引导。
- `memory.py`：inspiration、canon、设定变更和项目管家 Web API。
- `session.py`：Session start/revise/approve/run/cancel/accept/archive、进度和 rewrite event API。
- `inspection.py`：项目状态、文件树、运行日志、provider 摘要、状态/时间线、diff 和 audit annotation。

主要行为：

- `handle_api_request()`：统一 API 入口；先解析 method/path/body，再通过 `_get_routes()` / `_post_routes()` 路由表分发到 handler。
- `_locked_write()`：Web 写操作项目锁；锁、usage marker 和失败日志必须使用同一个 route root resolver。
- `_success()` / `_failure()`：统一 `{ok,data,error}` 响应结构。
- `WebAPIError`：带稳定 code、HTTP status、details 的 API 异常。
- `_runtime_summary()`：返回 Web server 当前 Python 路径、环境名和包版本，帮助确认 Web UI 是否运行在安装器创建的新环境。
- `_runtime_summary()`：返回 Web server 当前 Python 路径、环境名和包版本，帮助确认 Web UI 是否运行在安装器创建的新环境。
- `_list_projects()`：列出给定根目录下可打开的小说项目，不读取 `.env*`。
- `get_project_status()` / `format_canon()` / `_list_chapters()`：分别支持项目状态、canon 摘要和章节列表 API。
- `summarize_provider_usage()`：为 Web usage 面板返回 provider 调用和 token 统计。
- `_init_project()`：Web 端初始化小说项目，调用 `init_workspace()`，不复制 workspace 创建逻辑。
- `_inspire()`：Web 端生成 inspiration，调用 Inspiration service。
- `_canon_suggest()` / `_canon_apply()`：Web 端 canon proposal 和 apply，调用 Canon service 并保持 proposal/apply 分离。
- `_validate_project()` / `_validation_message_payload()`：只读项目检查 API，复用 `validate_project()`，返回 errors/warnings 摘要供 Web UI 显示。
- `_search_api()`：Web 项目搜索入口，复用 `search_project()`；默认 FTS，只有 `use_vector=1` 才会触发真实 embedding 检索和必要的向量刷新。
- `_plan_chapter()`、`_write_chapter()`、`_polish_chapter()`、`_audit_chapter()`、`_generate_chapter()`：调用对应 core service。
- `_export_markdown()` / `_export_docx()`：调用 Markdown / DOCX export。
- `_save_chapter_file()`：Web 编辑器保存章节版本，追加 revision log。
- `_style_guide()` / `_save_style_guide()` / `_generate_style_guide()`：读取、保存和生成 `memory/style_guide.md` 草稿；生成接口调用 `core/style_guide.py`，只返回 Markdown 草稿，不直接写正式文件。
- `_save_provider_config()`：保存非密钥 provider 配置，写前校验和备份。
- `_setup_default_provider()` / `_setup_embedding()` / `_setup_web_port()`：Web 初始引导 API；默认 API 和 embedding 调用 `core/setup_guide.py`，启动器端口调用 `core/web_launcher.py`；真实 key 写项目 `.env`，响应只返回 env 名和测试结果。
- `_setup_recommend_port()` / `_setup_open_web()`：推荐可用端口并返回 Web UI URL；Web 场景不另起服务端。
- `_index_refresh()`：调用 `refresh_search_index()`，刷新关键词索引或显式刷新真实 embedding 向量，并返回最新 search status。
- `_chapter_memory_generate()` / `_chapter_memory_rebuild()`：Web 端生成单章或批量补全 stale/missing ChapterMemory。
- `_session_*()`：session start/revise/approve/run/accept/archive API。
- `_revision_*()`：accepted 章节 block 列表、Revision Session start/show/run/accept API；业务逻辑全部复用 `core/revision_workflow.py`。
- `_session_progress_api()` / `_session_cancel()`：读取 `progress.json` 的脱敏进度摘要，并写入协作式取消请求；取消接口不走项目写锁，避免被正在运行的 `session run` 阻塞。
- `_session_rewrite_event_summary()`：读取自动打回重写记录，供 Web Session 面板和轮询接口展示。
- `_memory_repair_suggest()` / `_memory_repair_apply()` / `_settings_change_suggest()` / `_settings_change_answer()`：项目管家 proposal、设定变更澄清和 apply API，调用 `core/memory_repair/`，不在 Web 层直接 patch 文件。
- `_session_revise_audit()` / `_session_retry_rewrite()` / `_session_undo_rewrite()`：Audit 复审、基于新审核重试打回、撤回打回并恢复快照。
- `_management_events()` / `_management_event_summary()`：读取 `memory/management_events.jsonl`，供 Web 显示后台状态/时间线/记忆刷新。
- `_file_tree()`：列出 workspace 白名单文件，排除 `.env*`、缓存、索引、备份。
- `_read_workspace_file()` / `_read_chapter_file()`：安全读取文件。
- `_runs_summary()` / `_provider_call_summary()` / `_model_io_summary()`：运行和模型日志摘要。
- `_provider_config_summary()` / `_sanitize_config()` / `_collect_env_names()`：脱敏展示 profile/task/embedding 配置。
- `/api/search-status` 对应 `search_index_status()`；只返回 env 名称和是否缺失，不返回真实 env 值。
- `/api/usage` 对应 `summarize_provider_usage()`；Web 用量统计页展示总调用、成功/失败、token，以及按 Task / Provider / Model 的摘要。
- `/api/projects`、`/api/session`、`/api/setup/open-web` 是稳定 HTTP 契约，详见 `docs/INTEGRATION.md`；创作路径统一使用 Session API，低层生成端点已删除。
- `_state_timeline_summary()` / `_state_timeline_visual_summary()`：状态和时间线可视化摘要。
- `_audit_annotations()` / `_locate_quote()`：audit evidence 定位正文。
- `_workspace_diff()`：版本 diff。
- `_safe_workspace_file()` / `_safe_config_file()` / `_is_safe_*()`：路径白名单和穿越防护。

### `src/novel/web_static/index.html`

无构建 vanilla 前端的页面结构。只保留 DOM、页面分区和表单控件，通过 `/static/app.css` 和按顺序加载的 `/static/app_*.js` 引入样式与交互逻辑。包含：

- 顶部主导航：主页、创作工作台、文风设置、小说状态管理、模型与检索配置、运行日志 / 项目文件。
- 主页：项目路径输入、打开/刷新、项目检查、新建项目、项目初始引导、项目状态、章节列表和下一步提示。
- 创作工作台：创作输入、Session 主流程、Accepted 章节局部修订、当前任务进度、协作式取消、自动打回记录、章节对照、章节编辑器、audit 定位和 revision diff。
- 文风设置：编辑 `memory/style_guide.md`，也可输入自然语言文风方向并调用 `style_guide` Agent 生成草稿填入编辑器。
- 小说状态管理：canon 摘要、状态/时间线、项目管家和后台管理动态。
- 模型与检索配置：Profile 模型配置、FTS / embedding 状态和索引刷新。
- 运行日志 / 项目文件：安全文件树、只读文件预览、运行日志和章节文件查看。
- 项目初始引导面板支持保存默认 API、可选 embedding API、推荐并保存 Web 端口，以及打开当前 Web UI 地址。
- Session 面板支持创建大纲、修改大纲、批准大纲、开始写作、当前任务进度、协作式取消、按 Audit/用户意见修订、认可和归档。
- 项目管家面板支持生成 memory repair / setting change proposal；设定变更信息不足时展示 clarification 问题，补充后继续生成 proposal；确认 apply 前不会改正式 memory。
- 检索索引面板显示 FTS / embedding 状态，支持本地刷新关键词索引，也支持显式刷新真实 embedding 向量索引。

### `src/novel/web_static/app.css`

Web UI 样式文件。包含顶栏、主导航、页面网格、面板、表单、章节预览、文件树、状态标签、时间线卡片和移动端单列布局。修改视觉布局时优先改这里，不要把新样式重新塞回 `index.html`。

### `src/novel/web_static/app_*.js`

Web UI 交互脚本按功能拆分，不引入打包器：

- `src/novel/web_static/app_state.js`
- `src/novel/web_static/app_workbench.js`
- `src/novel/web_static/app_workspace.js`
- `src/novel/web_static/app_config.js`
- `src/novel/web_static/app_render.js`
- `src/novel/web_static/app_bootstrap.js`

职责划分：

- `app_state.js`：全局状态、通用 helper、API 调用、主页状态渲染和基础 artifact 预览。
- `app_workbench.js`：创作工作台、Session 行为、inspiration/canon、设定变更和项目初始引导动作。
- `app_workspace.js`：项目检查、章节记忆、导出、文件查看、章节对照、编辑器、audit 定位、运行日志、搜索和用量统计。
- `app_config.js`：provider/embedding 配置、状态/时间线、diff 和 tab 切换。
- `app_render.js`：HTML escaping、时间格式化、busy 状态、Session 进度、下一步提示、validation、audit 和 rewrite event 渲染。
- `app_bootstrap.js`：事件绑定、本地存储恢复、初始加载和 resize/observer 绑定。

这些脚本仍共享一个普通浏览器全局作用域。整体负责：

- 通用 `$()`、`apiGet()`、`apiPost()`、`setMessage()` 等工具函数。
- 主页面切换、页内 tab 切换和状态保持。
- 项目打开、刷新、初始化和项目检查。
- Session 创建、修改大纲、批准、运行、修订、认可、归档、打回记录展示、进度轮询和取消请求。
- 通用长任务状态：`withBusy()` 显示已用时、维护最近操作列表，并保留取消/刷新类按钮可用。
- 章节编辑器：离开页面前未保存提醒，`Ctrl/Cmd+S` 保存新版本。
- 章节对照、编辑器、audit evidence 定位、diff、文件树读取。
- 文风设置页：`loadStyleGuide()` / `saveStyleGuide()` / `restoreStyleGuideTemplate()` / `generateStyleGuideDraft()` 读取、保存、恢复模板和生成 AI 文风草稿；生成草稿只替换编辑器内容，保存仍走 `/api/style-guide`。
- Provider / embedding 配置、索引刷新、状态/时间线、项目管家和运行日志 API 调用。
- Profile 模型配置页用表单编辑 `provider`、`model`、`base_url_env`、`api_key_env`、`max_tokens`、`max_context_tokens`、`timeout_seconds` 等非密钥能力字段。配置页使用专用两栏布局，Profile 配置面板比检索索引面板更宽；4 个 profile 通过“继承 default”checkbox 控制 `inherit_default`。勾选时默认完整继承 default，只有 Profile 高级 JSON 中显式保留的 patch 会覆盖模型能力和容量参数；取消勾选时复制当前生效配置并保存独立完整配置。任务级覆盖放在折叠区，默认隐藏，提供 `temperature`、`thinking.type`、`reasoning` 表单控件，并保留高级 JSON 用于单 task 模型覆盖。`/api/provider-config` 会返回 `effective_profiles`、`effective_tasks` 和 provider 参数 capability；default/profile 生效配置不会暴露 task-only 字段，右侧显示当前 profile/task 的生效配置来源和最终非密钥配置，完整脱敏 JSON 收进调试折叠区。真实 API Key 仍只通过 `.env` / 初始引导管理。
- Embedding API 配置页块复用 `/api/setup/embedding`，要求用户每次重填 Base URL、API Key、provider、模型名、`dimensions` 和 `batch_size`；保存前用当前批量和维度做真实 provider 验证，保存成功后清空输入框，并自动调用 `/api/index/refresh` 刷新语义向量索引。
- 项目检查按钮调用 `/api/validate`，把 errors/warnings 摘要写入主页状态、顶部检查摘要、调试页文件查看和下一步提示。
- 自动打回区域支持选择 rewrite event、查看被打回原文、纠正 Audit 理解并重新审核、根据新审核重试打回、撤回打回。
- `renderNextStep()`：根据项目状态、validation 结果和 session 状态显示下一步操作建议。

前端脚本只做前端状态和 API 调用，不应复制 core 业务逻辑。新增 Web 能力时，后端逻辑仍应放在 `web_api/` / `core/`，前端只负责收集输入和展示结果。

## 5. Core 基础设施模块

### `core/__init__.py`

Core 包标记文件。当前不导出业务 API；调用方应从具体 service 模块导入函数，避免形成隐式公共接口。

### `core/schemas.py`

所有 Pydantic schema 的集中定义。主要类型：

- 配置：`ProjectConfig`、`AgentConfig`、`AgentConfigPatch`、`AgentsConfig`、`EmbeddingProviderConfig`、`EmbeddingsConfig`、`ThinkingConfig`。
- inspiration/style/canon：`InspirationBrief`、`GeneratedStyleGuide`、`CanonProposal`、`Character`、`Location`、`Item`、`WorldRule`、`HiddenTruth`、`ForeshadowingThread` 及对应 file model。
- state/timeline：`EntityState`、`CharacterState`、`ItemState`、`LocationState`、`TimelineEvent`、`TimelineNarrativePosition`、`TimelineStoryPosition`、`TimelineFile`。`narrative_position` 表示正文呈现顺序，`story_position` 表示故事世界顺序。
- chapter：`ChapterPlan`、`ChapterScene`、`RequiredContext`、`ChapterMetadata`。
- audit：`AuditReport`、`AuditIssue`、`AuditEvidence`。
- workflow/session/export：`AgentRunLog`、`AgentRunStep`、`CreationSession`、`CreationOutline`、`CreationArchiveManifest`、`ExportSourceChapter`、`ExportRecord`、`ExportManifest`。
- revision/context：`RevisionLog`、`RevisionRecord`、`ContextBundle`、`ContextItem`、`ContextExclusion`。
- state update：`StateUpdateProposal`、`StateChange`、`StateUpdateApplyLog`。
- session/memory management：`SessionProgress`、`SessionProgressEvent`、`SessionRewriteEvent`、`SessionRewriteEvents`、`MemoryRepairProposal`、`MemoryRepairOperation`、`MemoryRepairApplyLog`、`ManagementEvent`。

辅助：

- `FlexibleModel`：允许模型兼容额外字段。
- `SchemaVersionedModel`：统一 `schema_version`。
- `_require_unique_values()`：Pydantic validator 使用的唯一性检查。
- `json_dumps_compact()`：紧凑 JSON 序列化。

### `core/io.py`

统一文件 I/O：

- `load_json()` / `load_yaml()`：读原始 JSON/YAML。
- `load_json_model()` / `load_yaml_model()`：读取并校验 Pydantic model。
- `atomic_write_text()` / `atomic_write_bytes()`：同目录临时文件、fsync、`os.replace` 原子写。
- `atomic_write_json()` / `atomic_write_model_json()` / `atomic_write_yaml()`：结构化写入。
- `append_jsonl()`：追加 JSONL 事件或调用日志。
- `backup_file()` / `backup_if_exists()`：时间戳备份。
- `_fsync_directory()`：确保目录项刷盘。

所有重要写入必须使用本模块。

### `core/locking.py`

项目锁：

- `ProjectLock`：上下文管理器，创建 `.writeryang.lock`，退出释放。
- `ProjectLockInfo`：锁文件内容，包含 pid、task、created_at。
- `ProjectLockError`：锁冲突错误。
- `read_project_lock()`：读取当前锁。
- `_pid_exists()` / `_parse_timestamp()` 等 helper：判断陈旧锁。

写命令和 Web 写 API 必须加锁；只读命令不加锁。

### `core/security.py`

安全扫描：

- `SecurityFinding` / `SecurityScanResult`：扫描结果。
- `scan_security()`：扫描 tracked files 和配置文件。
- `validate_env_example()`：检查 `.env.example` 不含真实值。
- `validate_secret_config_file()`：检查 agents/embeddings 配置不误写 raw key。
- `redact_secret_text()`：脱敏文本。
- `_scan_file()` / `_scan_config_value()`：具体检测逻辑。

用于 `novel doctor` 和 CI。

### `core/json_schema.py`

JSON Schema 导出：

- `SchemaDefinition`：schema 名称、model、输出文件。
- `schema_payloads()`：生成所有 schema payload。
- `export_json_schemas()`：写入 `schemas/*.schema.json`。
- `model_output_schema_payload()`：按 `json_schema_name` 查找 Agent 输出 schema，供 provider payload 使用。
- `model_output_schema_skeleton()`：把 schema 压缩成 prompt skeleton，供 `json_object` JSON mode guard 使用。
- `strict_model_output_schema_payload()`：生成 strict-compatible schema；无法安全转换时抛错，让 provider fail fast。

## 6. Provider 和模型调用

### `core/providers.py`

主要类型：

- `ModelRequest`：Agent 请求。
- `TokenUsage`：prompt/completion/total token。
- `ModelResponse`：模型内容、原始响应、token、reasoning。
- `ModelProvider`：抽象接口。
- `MockProvider`：测试 provider，支持响应序列和 stream chunks。
- `LoggingModelProvider`：包裹 provider，写 model_io 日志；request 段记录 `prompt_version`，stream 原始响应只保留 chunk 数、finish chunk 和 usage chunk。写入后调用 model_io 保留策略，默认裁剪旧日志。
- `OpenAICompatibleProvider`：OpenAI Chat Completions 兼容实现。
- `ProviderFactory`：根据 `AgentConfig` 创建 provider。
- `ProviderError` 及子类：env、HTTP、auth、rate limit、timeout、network、response 错误。

关键函数：

- `OpenAICompatibleProvider.from_config()`：读取 env、默认 base URL、provider 私有字段。
- `OpenAICompatibleProvider._payload()`：组装请求 payload，包括 `thinking`、`response_format` 和按 provider 解析后的 `json_response_format`。
- `_ensure_json_mode_messages()`：`json_object` 结构化调用时自动补充标准 JSON mode guard 和紧凑 schema skeleton，兼容 DeepSeek 等服务端校验。
- `_model_response_from_openai_raw()`：解析 OpenAI 格式返回。
- `_stream_content_from_line()`：解析 SSE chunk。
- `_redact_data()` / `_redact_text()`：日志脱敏。

### `core/model_io.py`

Model I/O 生命周期：

- `model_io_retention_policy_from_env()`：读取 `WRITERYANG_MODEL_IO_MAX_FILES`、`WRITERYANG_MODEL_IO_MAX_BYTES` 和 `WRITERYANG_MODEL_IO_MODE`。
- `compact_model_io_payload()`：metadata 模式下省略 prompt、正文和 raw response。
- `prune_model_io_dir()`：按最近条数和总体积裁剪旧 `runs/model_io/*.json`，并同步重写 `index.jsonl`。

### `core/provider_config.py`

- `ProviderOverrides`：CLI 临时覆盖 provider/model。
- `ProviderDescriptor`：dry-run-provider 展示结构，包含非密钥的 `json_response_format`。
- `default_agent_config_path()`：默认 `config/agents.yaml`。
- `load_agents_config()`：读取 `AgentsConfig`。
- `resolve_agent_config()`：按 task name 解析 `task -> profile -> default` 配置；真实调用会合并项目 `.env` 和系统环境变量。
- `create_agent_provider()`：创建 provider 并包 `LoggingModelProvider`；非 mock provider 会读取项目根目录 `.env`。
- `describe_agent_provider()`：返回安全配置摘要。

### `core/env.py`

项目级环境变量文件：

- `project_env_path()`：返回项目根目录 `.env`。
- `read_project_env_file()`：解析本地 `.env`，支持基础引号转义。
- `load_project_env()`：把项目 `.env` 与当前进程环境合并，供 provider、doctor、validation 和 Web 状态使用。
- `write_project_env_values()`：安全写入 `.env`，保留既有键，写前备份，写后尽量设为 `0600`。

`.env` 是本地私密运行文件，文件树、日志、导出和 git 都必须排除。

### `core/setup_guide.py`

项目初始引导：

- `SetupGuideError`：初始引导配置或连通性测试失败。
- `ProviderSetupResult` / `EmbeddingSetupResult` / `PortSetupResult`：引导步骤返回结构。
- `configure_default_provider()`：测试并保存顶层 default API；真实 key 写 `.env`，`config/agents.yaml` 只写 env 名。
- `update_default_agent_config()`：更新 `config/agents.yaml.default` 并运行 secret config 检查。
- `configure_embedding_provider()`：可选测试并保存 embedding provider。
- `update_embedding_config()`：更新 `config/embeddings.yaml.active_provider` 和 provider 条目。
- `is_port_available()` / `find_available_port()`：检查和推荐 Web UI 端口。
- `configure_web_port()` / `update_project_web_port()`：写入 `project.yaml.web.default_port`。
- `_ping_model_provider()` / `_ping_embedding_provider()`：最小真实连通性测试，失败时不打印密钥。

### `core/web_launcher.py`

- `WebLauncherConfig`：启动器级 Web UI 端口配置，保存到未追踪的 `WriterYang_WebUI.config.json`。
- `save_web_launcher_port_config()`：保存启动器端口前验证端口可用；当前 Web UI 正在使用的端口允许保存。
- `recommend_web_launcher_port()` / `find_available_port()`：为启动器端口设置推荐可保存端口。
- `_write_web_launcher()`：生成动态 Web UI 启动器，推广初期面向 macOS / Linux 的 `WriterYang_WebUI.command`；Windows 适配暂缓，不作为当前发布支持平台。启动器下次启动时读取 config 文件。

### `core/embeddings.py`

Embedding provider：

- `EmbeddingProvider`：抽象接口。
- `LocalHashEmbeddingProvider`：本地 hash embedding，仅用于测试和离线 fixture，不作为真实业务 fallback。
- `OpenAIEmbeddingProvider`：兼容 embedding API；适配 DashScope text-embedding-v4 和 Zhipu embedding-3。DashScope provider 或 DashScope compatible base URL 会自动发送 `encoding_format: "float"`，并把 text-embedding-v3/v4 的运行时请求批量限制到文档上限 10。
- `EmbeddingProviderFactory`：按 `EmbeddingsConfig` 创建 provider。
- `create_embedding_provider()`：外部调用入口；默认读取项目 `.env`，只有显式 `provider_name="local_hash"` 时返回本地 hash provider，缺失真实配置不再自动 fallback。
- `local_embedding_vector()`：本地向量生成。
- `_vectors_from_openai_raw()`：解析 embedding 返回。

## 7. Agent 输出守卫

### `core/agent_defaults.py`

- `DEFAULT_AGENT_CONFIG`：顶层 `default` API 推荐默认值，只包含 provider/model/env 和容量类字段。
- `PROFILE_NAMES`：允许配置的 4 个能力 profile。
- `TASK_TO_PROFILE`：task 到 profile 的固定映射。
- `PROFILE_CONFIG_DEFAULTS`：旧模板曾注入的 profile 容量默认值，仅用于迁移时识别并剥离 legacy patch，不再作为现行默认来源。
- `TASK_BUSINESS_DEFAULTS`：各 task 的 `temperature`、`thinking`、`reasoning` 业务默认值。
- `inherited_profile_config_patch()`：生成 `inherit_default: true` 的 profile patch，用于 workspace 初始化、setup guide 和 Web API 保存；不写字段时完整跟随 `default`。

### `core/agent_output.py`

- `AgentInvocationContext`：agent name、caller、interaction mode、task、chapter、session。
- `AgentOutputContract`：目标输出类型、schema、是否允许提问、是否允许 JSON。
- `AgentOutputContractError`：输出契约错误。
- `generate_with_output_guard()`：统一 provider 调用、输出校验、一次 repair retry、violation log。
- `validate_agent_output()`：检测空输出、内部反问、模型自述、JSON/Markdown 类型不符、工作区语言。
- `build_output_contract_repair_prompt()`：生成输出契约修复 prompt。
- `write_agent_output_violation_log()`：写 `runs/agent_output_violations/`。

开发新 Agent 时应复用此模块。

### `core/json_extract.py` / `core/structured_generation.py`

结构化 JSON 输出的公共后处理：

- `strip_code_fence()` / `extract_json_object()`：统一处理 fenced JSON、前后夹杂文本和缺失 JSON object。
- `JsonExtractionError`：只表示“文本里没有可抽取的 JSON object”；业务模块应包装为自己的领域异常。
- `generate_json_with_repair()`：统一二层 repair 编排，执行 provider 调用、parse/validate、构造 repair prompt、repair retry、再次 parse/validate。
- `JsonRepairExhaustedError`：repair retry 后仍不合法时抛出；调用方决定是领域异常还是保守 fallback。
- Audit workflow 仍在 `audit_chapter()` 保留领域边界归一：真实模型把 `audited_file` 误填成章节标题等非文件名文本时，按本次请求文件名修正；明确的 `draft.md` / `polished.md` 错配仍交给 precheck。

### `core/app_logging.py`

轻量应用日志：

- `log_app_warning()`：写入项目 `runs/app.log`，每行一个脱敏 JSON object。
- 只记录安全摘要，例如 `event`、`request_id`、workflow、status/code、repair_id、相对路径和截断错误；不写 prompt、response、章节正文或完整用户输入。
- Web API 异常、memory repair fallback/preflight/rollback 等模型外失败路径应调用它。完整模型输入输出仍只在 `runs/model_io/`。

## 8. 写作业务模块

### `core/workspace.py`

初始化 workspace：

- `InitOptions` / `InitResult`：初始化参数和结果。
- `init_workspace()`：创建项目目录、默认配置和空 memory 文件。
- `_workspace_dirs()`：默认目录列表。
- `_project_yaml()` / `_agents_yaml()` / `_embeddings_yaml()`：默认配置内容。
- `_write_new_file()`：避免覆盖已有用户文件。

### `core/style_guide.py`

默认文风设置：

- `DEFAULT_STYLE_GUIDANCE`：`memory/style_guide.md` 缺失时注入 prompt 的运行时兜底。
- `default_style_guide_markdown()`：`novel init` 和 Web UI 缺失文件预览使用的完整默认模板。
- `generate_style_guide()`：调用 `style_guide` provider，要求模型输出 `GeneratedStyleGuide` JSON，再由本地 renderer 生成 Markdown 草稿。
- `load_style_guide_provider()`：读取 `style_guide` task 配置，经 `loremaster` profile 解析 provider。
- `render_generated_style_guide_markdown()`：把结构化文风建议渲染成 `memory/style_guide.md` 使用的中文 Markdown 分区。

### `core/context_budget.py`

Prompt 上下文预算化：

- `ContextBudgetView`：预算化后的完整条目、digest、focus ID 和裁剪状态。
- `project_context_budget()`：读取 `project.yaml.context_budget`，无配置时使用默认关闭策略。
- `select_timeline_view()` / `select_state_view()`：保留 focus 实体/事件和近章内容，远期内容折叠成 digest；红线任务不泄漏 author-only 内容。
- `render_timeline_prompt_text()` / `render_state_prompt_text()`：给 Agent prompt 渲染 state/timeline；未裁剪时保持原 JSON 文本。

### `core/inspiration.py`

灵感/弱总纲：

- `InspirationOptions` / `InspirationResult` / `InspirationError`。
- `run_inspiration_agent()`：读取 project 和用户输入，生成 inspiration.md/json。
- `build_inspiration_system_prompt()` / `build_inspiration_user_prompt()`。
- `_ensure_markdown()`、`_brief_from_response()`、`_try_parse_brief_json()`：输出处理。`--json` 时本地从 Markdown 或合法 JSON 派生 `InspirationBrief`，不依赖 provider JSON mode；模型意外返回 `outline` / `markdown` JSON 包装时会先解包成 Markdown。
- `default_mock_inspiration_markdown()`：mock provider 默认响应。

### `core/canon.py`

设定管理：

- `CanonSuggestOptions` / `CanonSuggestResult` / `CanonApplyResult` / `CanonFiles` / `CanonError`。
- `suggest_canon()`：生成 CanonProposal。
- `apply_canon_proposal()`：内存合并、校验、写 canon、失败回滚。
- `load_canon_files()` / `write_canon_files()`。
- `format_canon_summary()`：给 prompt 和展示使用。
- `build_canon_user_prompt()` / `parse_canon_proposal()`。
- `_generate_canon_proposal_with_repair()`：输出守卫 + schema repair。
- `validate_canon_proposal()`：重复 ID、跨类型冲突、hidden truth 泄漏。
- `_normalize_canon_proposal_data()` 等 helper：低风险形状归一化。

### `core/planning.py`

章节计划：

- `ChapterPlanningOptions` / `ChapterPlanningResult` / `PlanningError`。
- `plan_chapter()`：生成 `plan.json` 和 `plan.md`。
- `build_planning_user_prompt()`：组装 project、inspiration、style、canon、state、timeline、instruction、search context 和 ChapterMemory context。
- `parse_chapter_plan()`：解析和校验 `ChapterPlan`。
- `_generate_chapter_plan_with_repair()`：输出守卫 + schema/reference repair。
- `_validate_plan_for_write()` / `_plan_reference_errors()`：写入前阻断缺失引用。
- `render_plan_markdown()`：可读 plan.md。

### `core/plan_refs.py`

ChapterPlan 引用提取：

- `plan_focus_entity_ids()`：从 `required_context`、scene participants/location 和 structured fields 提取 focus 实体。
- `plan_timeline_event_ids()`：提取 plan 直接引用的 timeline event。
- `plan_related_timeline_event_ids()` / `KEY_TIMELINE_EVENT_ROLES`：基于结构化 focus entity ID 召回关键历史/记忆类 timeline event，不做自然语言事件 ID 猜测。
- `plan_search_terms()`：把 goal、summary、must_include、scene beats 等稳定信息转成检索 query 片段。
- `default_mock_chapter_plan_json()`：测试 fixture。

### `core/drafting.py`

初稿：

- `ChapterDraftingOptions` / `ChapterDraftingResult` / `DraftingError`。
- `write_chapter_draft()`：读取 plan/canon/state/timeline/style/inspiration，生成 `draft.md`。
- `build_writer_user_prompt()`：组装 Writer Agent 输入，并注入红线保护后的 ChapterMemory context。
- `render_draft_markdown()`：写 YAML front matter。
- `_clean_body()`：清除代码块包装。
- `default_mock_draft_body()`。

### `core/polishing.py`

润色：

- `ChapterPolishingOptions` / `ChapterPolishingResult` / `DraftDocument` / `PolishingError`。
- `polish_chapter()`：读取 draft/plan/context，生成 `polished.md`。
- `resolve_edit_mode()`：解析 light/normal/deep。
- `read_markdown_with_front_matter()`：读取章节 Markdown metadata。
- `build_polish_user_prompt()` / `render_polished_markdown()` / `_clean_polished_body()`。

### `core/auditing.py`

一致性审核：

- `ChapterAuditOptions` / `ChapterAuditResult` / `AuditContext` / `PrecheckResult` / `AuditError`。
- `audit_chapter()`：读取章节资料、跑 deterministic precheck、调用 Audit Agent、写 `audit.json`。
- `load_audit_context()`：加载 plan、draft/polished、style、canon、state、timeline、search context。
- `run_deterministic_prechecks()`：文件/schema/front matter/canon/state/timeline/consistency 检查。
- `_validate_audited_body_against_plan()`：正文是否明显偏离 plan 的 deterministic 检查；hidden truth 提前揭示由 `core/consistency.py` 检查。
- `build_audit_user_prompt()` / `parse_audit_report()`。
- `_generate_audit_report_with_repair()`：输出守卫 + schema repair。
- `combine_audit_reports()`：合并 deterministic 和模型审核。
- `_status_for_issues()`：severity 到 overall_status 的策略。

### `src/novel/core/audit_localization.py`

Audit 作者可读文案适配：

- `localize_audit_report_for_author()`：对少量历史 mock summary 和外部英文 suggested_fix 做兼容转换；现行 deterministic audit/validation 在源头直接产中文。
- `localize_audit_issue_for_author()` / `localize_session_rewrite_issue_for_author()`：用于 Web/CLI 只读展示 audit 和 rewrite event，不再维护 consistency 文案的第二套翻译表。
- `localize_canon_validation_message()`：保持 identity；canon validation 由 `validation.py` 与 `consistency.py` 源头输出中文。

### `core/gender.py`

角色性别事实工具：

- `canonical_gender()`：把 `男/男性/male/m`、`女/女性/female/f` 归一到 `男` / `女`；无法明确时返回 `None`。
- `has_male_marker()` / `has_female_marker()`：识别 `次子`、`长女`、`某家次子` 等明确性别/排行短语。
- `infer_character_gender()` / `infer_gender_from_character_payload()`：供 consistency audit 与 memory repair 共用的角色性别推断。
- `strip_explicit_gender_tags()`：memory repair 写入 `Character.gender` 后剥离冗余显式性别 tag，保留家族排行等业务 tag。

### `core/consistency.py`

确定性一致性引擎：

- `ConsistencySnapshot`：聚合 canon/state/timeline/chapter artifacts。
- `ConsistencyFinding` / `ConsistencyResult`：确定性问题输出。
- `check_chapter_consistency()`：章节级一致性检查。
- `check_canon_consistency()`：canon-only consistency 检查，供 `validate_canon()` 和 audit precheck 复用。
- `check_project_consistency()`：项目级闭环检查，包含 canon-only 检查、project-level state/timeline 检查和章节闭环检查。
- `_check_character_knowledge()`：角色已知/未知信息链。
- `_check_item_flow()`：物品 holder/location 和 possession 双向一致。
- `_check_timeline_order()`：timeline narrative order、story-world causes/effects 和 scene 边界。
- `_check_hidden_truth_body_exposure()` / `_check_reader_visible_hidden_truth_leaks()`：hidden truth 暴露边界。
- `_check_character_gendered_references()`：保守检查正文中明显性别化指代是否与 canon 的 `gender`、`appearance.gender` 或明确排行短语冲突。
- `_check_chapter_loop()` / `_check_accepted_chapter_loop()`：plan/draft/polished/audit/state/metadata 闭环。

### `core/state_change_values.py`

状态变更旧值比较工具：

- `compare_state_change_old_value()`：统一 state update apply 和 consistency validate 的 `old_value` 语义，支持数字字符串等价，并把缺失 entity state 视为尚未跟踪而非冲突。
- `current_state_value_for_change()` / `state_values_equivalent()`：提取当前 state 字段值并执行宽松等价比较。

### `core/state_update.py`

状态和时间线更新：

- `StateUpdateProposeOptions` / `StateUpdateApplyOptions` / `AcceptChapterOptions`。
- `StateUpdateProposeResult` / `StateUpdateApplyResult` / `AcceptChapterResult`。
- `propose_state_update()`：生成 proposal，不改正式 state/timeline。
- `apply_state_update()`：校验 proposal、备份、应用 state/timeline、失败回滚。
- `accept_chapter()`：检查 audit、必要时 propose/apply、标记 accepted，并 best-effort 生成 ChapterMemory。`chapter_memory.strict_accept=true` 只把记忆失败升级为 error 事件/警告，不阻断已经完成的 accepted 状态。
- `validate_state_update_proposal()`：引用和冲突校验。
- `apply_state_changes_to_state()`：把 state_changes 应用到 EntityState。
- `mark_chapter_accepted()` / `write_chapter_metadata()`。
- `build_state_update_user_prompt()` / `parse_state_update_proposal()`。
- `_generate_state_update_proposal_with_repair()`：输出守卫 + schema repair。

### `core/chapter_versions.py`

章节正文版本文件工具：

- `latest_chapter_version_path()`：选择 `draft.vN.md` / `polished.vN.md` 中最新版本，找不到时回退基础文件。
- `next_chapter_version_path()`：生成下一版 `draft.vN.md` / `polished.vN.md` 输出路径。
- `is_allowed_chapter_version_name()`：校验 Web/CLI 可编辑的章节版本文件名。

### `core/chapter_memory.py`

已接受章节的结构化检索记忆：

- `generate_chapter_memory()`：读取 accepted `polished.md`、plan、audit、state proposal/apply log 和 timeline，生成 `chapter_memory.json`。
- `load_chapter_memory_provider()`：读取 `chapter_memory` task 配置，经 `clerk` profile 解析 provider。
- `build_deterministic_chapter_memory()`：provider 不可用或模型输出无效时的保守 fallback。
- `validate_chapter_memory()`：检查章节状态、source path、正文 sha、timeline id 和 source_refs，并强制重算 source sha。
- `chapter_memory_freshness_warnings()`：热路径轻量检查 source polished 是否存在、accepted、sha 是否匹配；当 `chapter_memory.json` 比 source `polished.md` 新时用 mtime 快捷路径跳过整篇 sha。
- `load_chapter_memories()`：按章节加载历史记忆，默认跳过 stale 记忆。
- `render_chapter_memory_prompt_text()`：为 Plot 渲染全局/重点记忆，为 Writer 渲染读者可见和安全连续性视图。

### `core/revision.py`

修订：

- `ChapterRevisionOptions` / `ChapterRevisionResult` / `RevisionLoopOptions` / `RevisionLoopResult` / `RevisionContext`。
- `revise_chapter()`：根据 instruction 或 audit 生成版本文件。
- `revise_chapter_loop()`：受最大轮数和人工确认限制的循环修订。
- `load_revision_context()`：加载 plan、source markdown、audit、style、canon、state、timeline；默认 source markdown 使用最新 `draft.vN.md` / `polished.vN.md`，也支持显式 source file。
- `build_revision_user_prompt()` / `render_revised_markdown()`。
- `_revision_output_path()`：选择 `draft.vN.md` 或 `polished.vN.md`。
- `_append_revision_log()`：更新 revision_log。

### `core/session.py`

协作式创作 session：

- `SessionStartOptions`、`SessionRunOptions`、`SessionInstructionOptions`、`SessionActionOptions`、`SessionResult`。
- `start_session()`：创建 session 和 outline proposal。
- `show_session()`：读取 session。
- `revise_outline()`：按用户意见重新生成 outline proposal。
- `approve_outline()`：冻结 approved outline。
- `run_session()`：生成正文、润色、审核、自动修复、state proposal，并在章节级安全边界写入进度事件、检查协作式取消请求。
- `revise_content()`：按用户意见或 audit issue 修订内容。用户意见先由 orchestrator 编排层调用 `intent_router` task 路由；剧情级修改回到 Plot 重写 plan，写作实现级修改走 Writer/Polish 重写正文，局部表达修改才走 Revision 版本补丁。之后统一提升/重审/重建 state proposal。
- `_resolve_content_revision_route()` / `_audit_driven_revision_route()`：把用户反馈或 audit issue 转为 `RevisionRouteDecision`；audit 驱动路径依赖结构化 audit repair route，不再扫描自然语言关键词决定是否重写大纲。
- `_replan_and_rewrite_chapter()`：剧情级反馈路径，重写本章 `plan.json` 并重新生成正文。
- `_rewrite_chapter_with_writer()`：写作实现级反馈路径，保留 plan，重写 `draft.md` / `polished.md`。
- `revise_audit()`：读取 rewrite event 的 rejected snapshot，把用户纠正意见传给 Audit Agent，重写 `audit.json` 并记录 `audit_revision_history`。
- `retry_rewrite()`：基于最新 audit issue 再次执行 revision rewrite 或 plot replan，提升当前稿并重跑 audit。
- `undo_rewrite()`：恢复 rewrite event 的 rejected snapshot，备份当前稿，重跑 audit，并更新 undo 状态。
- `accept_session()`：应用状态更新并标记章节 accepted。
- `archive_session()`：归档 approved outline、最终正文、audit、state update 和 manifest。
- `load_session_progress()` / `request_session_cancel()`：读取 `memory/sessions/{session_id}/progress.json`，或写入 `cancel_requested`；取消不会强行中断当前 LLM HTTP 调用。
- `load_rewrite_events()`：读取 `memory/sessions/{session_id}/rewrite_events.json`。
- `_generate_chapter_content()`：单章 writer/polish/audit 调度。
- `_auto_repair_chapter()`：正文层 medium/high/critical 自动修复，生成 `polished.vN.md`。
- `_promote_revision_to_polished()`：把修订版本提升为当前 `polished.md` 后再重跑 audit。
- `_auto_replan_chapter()` / `_should_replan_chapter()`：连续修复仍失败或结构化证据明确指向计划层问题时回退 Plot Agent 重写本章计划；`_should_replan_chapter()` 只检查 `source_layer=plan` 或 `evidence.source=plan.json`，不扫描 issue 文本。
- `_start_rewrite_event()` / `_update_rewrite_event()`：自动打回前保存原文快照、记录打回原因，并在复审后更新 completed/unresolved/failed 状态。
- `_has_hard_issues()`：判定阻断 issue。
- `_session_instruction()`：把 session intent 转为内部 Agent instruction。

### `core/management.py`

后台管理事件日志：

- `record_management_event()`：向 `memory/management_events.jsonl` 追加脱敏事件。状态更新 proposal/apply、timeline update、memory repair 和 chapter accepted 都应调用它。
- `load_management_events()`：读取最近事件，供 CLI/Web UI 显示。
- `management_events_path()`：返回事件日志路径。

### `core/memory_repair/`

orchestrator 项目管家修复 proposal 包，保留 `from novel.core.memory_repair import ...` public API：

- `MemoryRepairError`：proposal/apply 失败。
- `MemoryRepairSuggestResult` / `MemoryRepairApplyResult` / `SettingChangeSuggestionResult`：service 返回结构。
- `suggest_memory_repair()`：根据结构化 `MemoryRepairDecision` 和当前项目文件生成 `MemoryRepairProposal`、Markdown 摘要和 diff 预览；默认不修改正式 memory。写 proposal 前会执行目标 schema preflight；setting_change 还会执行 Character.role 语义 preflight，阻止身份/排行/职业短语写入叙事角色字段。
- `suggest_setting_change_interactive()` / `answer_setting_change_clarification()`：设定变更多轮澄清入口；只有创作意图不足、替换/删除目标不唯一或剧情含义存在真实歧义时保存 `memory/repairs/clarifications/{clarification_id}/session.json`，补充后再生成 proposal。
- `generate_memory_change_clarification_decision()` / `parse_memory_change_clarification_decision()`：调用 `memory_repair` task provider 输出结构化 clarification gate；提问只通过 schema 返回，不在最终 patch 阶段自然语言提问。澄清问题不得要求用户提供目标文件、字段、visibility、JSON Pointer 或完整文件结构。
- `generate_memory_repair_decision()` / `parse_memory_repair_decision()`：调用 `memory_repair` task provider 输出 target files、JSON Pointer operations、confidence 和 assumptions。信息不足时返回空 operations，不用关键词硬猜正式 patch；对模型常见的安全 add 路径错误会在 Pydantic 校验前归一，例如 `/characters/char_x` 转为 `/characters/-` 并补齐可推断的 `file/reason`。
- `apply_memory_repair()`：校验 proposal，限制白名单文件，先执行 schema/semantic preflight，再按 JSON Pointer 应用 `add/replace/remove`，备份目标文件，atomic write，运行 validate；失败时写失败 apply log 并尝试回滚。
- `_memory_repair_user_prompt()` / `_memory_pointer_index()`：组装 MemoryRepairDecision prompt，注入目标文件结构、集合 key、现有条目的 index/id/name 和 JSON Pointer 路径示例；集合字段提示来自当前 schema，避免 hidden_truths/foreshadowing 字段漂移，并说明 `Character.role` 只表示叙事角色、身份短语应进入 `tags`。
- `render_memory_repair_markdown()`：把 proposal 渲染为用户可读说明。
- `service.py`：proposal 生成、设定变更交互、clarification session 和 orchestration。
- `generation.py`：结构化生成、JSON 解析、prompt 和 pointer index。
- `apply.py`：proposal apply、Markdown 渲染和决策清洗。
- `impact.py`：批次合并、影响分析、follow-up action 和 proposal notes。
- `preflight.py`：目标 schema / semantic preflight、自动修复和安全重试辅助。
- `validation.py`：白名单、schema 校验、路径/id helper。
- `models.py`：错误类型和 result dataclass。
- `core/memory_repair_rules.py`：白名单文件、domain 映射、collection key、schema hint、设定变更映射规则和 Character.role 语义规则。
- `core/memory_repair_mock.py`：仅用于 mock/config fixture 的启发式测试路径，不作为真实业务推断路径。
- `core/memory_repair_ops.py`：JSON Pointer patch 执行器和备份恢复工具。

### `core/workflow.py`

底层端到端章节流水线：

- `GenerateChapterOptions` / `GenerateChapterResult` / `WorkflowError`。
- `generate_chapter()`：依次 plan/write/polish/audit，写 run log。
- `_run_plan_step()` / `_run_write_step()` / `_run_polish_step()` / `_run_audit_step()`：各步骤封装。
- `_resume_existing_step()`：从已有 artifact 继续。
- `_new_run_log()` / `_write_run_log()` / `_fail_run()` / `_complete()`：run log 生命周期。
- `_load_provider_for_step()`：按 step 选择 agent provider。

### `core/orchestrator.py`

结构化决策 proposer：

- `propose_ask_command()`：把自然语言意图转换为 strict `CommandProposal`，执行预算预检，但不直接调用领域 mutation service。
- `decide_ask_intent()` / `parse_ask_intent_decision()`：调用 `intent_router` task provider 输出 `AskIntentDecision`，作为 `novel ask` 主路径。
- `route_revision_request()`：调用 `intent_router` task provider 输出 `RevisionRouteDecision`，用于把用户修订意见分为 `plot_replan`、`writer_rewrite`、`revision_patch`。
- `route_audit_repair()` / `parse_audit_repair_route_decision()`：调用 `intent_router` task provider 或结构化确定性规则输出 `AuditRepairRouteDecision`，用于 Audit 后自动打回分流。
- `parse_revision_route_decision()`：解析和归一化路由 JSON；失败时由 `route_revision_request()` repair retry 一次，仍失败则保守 fallback。
- `load_intent_router_provider()`：读取 `intent_router` task 配置，创建带 model I/O 日志的 provider。
- `build_revision_route_user_prompt()`：组装修订路由判定 prompt。
- 模型不可用时的 Ask fallback 只识别显式低风险请求并降低置信度；不存在通用 keyword classifier，也不会执行高风险动作。

### `core/workflow_runtime.py`

静态 workflow 运行时与可观测性：

- `WORKFLOW_DEFINITIONS`：声明 Creation/Revision 的稳定节点序列和 Task ID。
- `workflow_runtime_scope()`：创建或恢复 `runs/{workflow_run_id}/run.json`；同一 Session 后续命令复用原 `workflow_run_id`。
- `WorkflowRuntime.execute_node()`：记录 command/model node 的 parent、Task/Profile、Provider/model、prompt hash、输入输出引用、预算快照、状态和错误。
- Provider 调用自动嵌套在当前 command node 下；Command Bus 是唯一公开命令入口。

### `core/runtime_config.py`

运行时配置归一化：

- `normalize_polish_mode()`：把 CLI/Web 的 `single-pass` 等输入归一为 schema 使用的 `single_pass`。
- `project_polish_mode()`：读取项目 `polish.mode`，缺省为 `single_pass`。

## 9. 检索、展示、导出

### `core/search.py`

搜索和 ContextBundle：

- `rebuild_search_index()`：全量构建 JSON/SQLite/manifest 搜索索引，可选真实 embedding。
- `refresh_search_index()`：增量刷新新增、修改、删除文档；默认只更新 FTS，`with_embeddings=True` 时刷新真实 embedding。
- `_write_search_index_update()`：rebuild/refresh 共享的内部写索引流程，统一写 JSON、SQLite 和 manifest。
- `search_index_status()`：返回 FTS 和 embedding freshness 状态，供 CLI/Web 状态栏使用；`search_project(use_vector=True)` 会在真实向量缺失或过期时先调用 embedding refresh。
- `search_project()`：关键词/字段/类型/章节搜索。
- `retrieve_context()`：旧的检索入口。
- `retrieve_context_bundle()`：结构化上下文检索，按 ChapterPlan 扩展实体引用。
- ChapterPlan 显式 `timeline_event_ids` 优先级最高；focus entity 关联的关键历史/记忆类 timeline event 会以较低优先级补充进入 ContextBundle。
- `chapter_memory.json` 作为 `chapter_memory` 类型入索引，检索命中只作为 accepted 正文/canon/state/timeline 的导航指针；Writer 任务会隐藏原始 excerpt，避免泄漏 hidden truth。
- `write_context_report()`：写 `context_report*.json`。
- `_include_entity_context()`、`_include_related_events()`、`_include_related_hidden_material()`：补充 canon/state/timeline/hidden material。
- `_maybe_include_hidden_truth()`：按 task visibility 控制 hidden truth。
- `_score_document()`、`_highlight()`：关键词打分和高亮。
- `_load_embedding_provider()`：显式加载 embedding provider；默认拒绝把 `local_hash` 当作真实业务语义检索。

### `core/inspection.py`

只读展示：

- `ProjectStatus`：项目状态摘要。
- `get_project_status()`：标题、最新章节、inspiration、canon/timeline/run log 数量。
- `format_status()`、`format_characters()`、`format_timeline()`、`format_state()`、`format_canon()`：CLI 可读展示。
- `find_latest_run_log()`：最近 run log。

### `core/exporting.py`

导出：

- `MarkdownExportOptions` / `DocxExportOptions`。
- `MarkdownExportResult` / `DocxExportResult` / `ExportedChapter`。
- `collect_export_chapters()`：按 accepted、范围、指定章节选择 polished.md。
- `export_markdown()` / `render_markdown_export()`：Markdown 导出。
- `export_docx()` / `render_docx_export()`：DOCX 导出。
- `update_export_manifest()`：写 `exports/export_manifest.json`，记录 source hash。
- `parse_chapter_selector()`：解析 `--chapters 1,2,3`。

### `core/usage.py`

Provider 用量统计：

- `UsageBucket` / `UsageSummary`。
- `summarize_provider_call_log()`：全量读取 `provider_calls.jsonl`，汇总 total、by_task、by_provider、by_model 和 by_status。
- `refresh_provider_usage_summary()` / `refresh_provider_usage_summary_for_log()`：增量刷新 `provider_usage.json`；日志截断或缓存失配时自动全量重算。
- `provider_usage_path()` / `provider_call_log_path()`：默认路径。

## 10. Validation

### `core/validation.py`

项目校验：

- `ValidationMessage` / `ValidationReport` / `LoadedProject`。
- `validate_project()`：全项目校验入口。
- `validate_canon()`：只校验 canon。
- `_load_project_files()`：加载项目结构。
- `_validate_loaded_project()`：组合校验。
- `_validate_schema_versions()`：版本字段。
- `_validate_duplicate_ids()`：重复 ID。
- `_validate_agent_names()` / `_validate_single_agent_config()`：profile/task 配置。
- `_validate_embedding_config()`：embedding 配置。
- `_validate_references()`、`_validate_state_references()`、`_validate_state_update_proposal_references()`：跨文件引用。
- `_validate_chapter_outputs()` / `_validate_single_chapter_output()`：plan/draft/polished/audit/state proposal。
- `_validate_run_and_export_outputs()` / `_validate_session_outputs()`：run/export/session。
- `_validate_consistency_findings()`：项目级 consistency engine 输出接入。

## 11. Prompt 文件

| 文件 | 对应 Agent | 重点约束 |
| --- | --- | --- |
| `prompts/inspiration_system.txt` | Inspiration | 弱总纲，不写正文，不反问内部任务。 |
| `prompts/style_guide_system.txt` | StyleGuide | 只输出 GeneratedStyleGuide JSON，抽象总结高层风格，不引用原文或复刻作者。 |
| `prompts/canon_system.txt` | Canon | CanonProposal JSON，稳定 ID，hidden truth 不进 reader visible。 |
| `prompts/planning_system.txt` | Plot | ChapterPlan JSON，不写正文，不改 state/timeline，不发明引用。 |
| `prompts/writer_system.txt` | Writer | 只写正文 Markdown，不输出解释/JSON，不泄漏 hidden truth。 |
| `prompts/polish_system.txt` | Polish | 只输出润色正文，保留核心事实，不改设定。 |
| `prompts/audit_system.txt` | Audit | 只输出 AuditReport JSON，检查一致性。 |
| `prompts/state_update_system.txt` | StateUpdate | 只输出 StateUpdateProposal JSON，根据正文实际发生事件提取。 |
| `prompts/chapter_memory_system.txt` | ChapterMemory | 只输出 ChapterMemory JSON，带 source_refs 和 visibility，强调不是正式事实源。 |
| `prompts/revision_system.txt` | Revision | 只输出修订正文，按 instruction/audit 修复，不改 state/timeline。 |

模板加载在 `core/prompts.py`：

- `PROMPT_VERSION`：当前最新聚合 prompt 版本。
- `PROMPT_VERSIONS`：逐模板版本映射，覆盖每个非 partial prompt。
- `prompt_template_version(name)`：返回单个模板版本，调用方写入 `ModelRequest.prompt_version`，最终进入 `runs/model_io/{request_id}.json`。
- `load_prompt_template(name)`：按名称读取 `.txt`，并解析 `{{partial:name}}` 共享片段。
- `prompts/partials/*.txt`：只放共享规则片段，例如 ContextBundle 长期记忆说明和内部任务不反问约束。

## 12. Tests 目录

| 文件 | 覆盖范围 |
| --- | --- |
| `tests/test_workspace.py` | `novel init` 和默认 workspace 文件。 |
| `tests/test_cli.py` / `test_integration_cli.py` | CLI 参数、JSON/quiet/project、doctor、lock。 |
| `tests/test_agent_output.py` | Agent 输出契约守卫、内部任务反问拦截和 violation log。 |
| `tests/test_json_extract.py` / `tests/test_structured_generation.py` | JSON object 抽取和结构化输出 repair helper。 |
| `tests/test_provider_config.py` / `test_providers.py` | provider 配置、真实 provider fake HTTP、日志、脱敏、stream。 |
| `tests/test_real_api.py` | 真实 API 标记测试。 |
| `tests/test_embeddings.py` / `test_real_embeddings.py` | embedding provider、真实 embedding 标记测试。 |
| `tests/test_inspiration.py` | Inspiration Agent。 |
| `tests/test_canon.py` | Canon proposal/apply/validate/show。 |
| `tests/test_planning.py` | ChapterPlan 生成、引用阻断、repair。 |
| `tests/test_drafting.py` | Writer draft、front matter、output guard。 |
| `tests/test_polishing.py` | Polish edit mode、front matter、覆盖保护。 |
| `tests/test_auditing.py` | AuditReport、deterministic precheck、output guard。 |
| `tests/test_state_update.py` | State proposal/apply/accept、回滚、冲突。 |
| `tests/test_chapter_memory.py` | ChapterMemory schema、accept 集成、fallback、上下文注入和检索。 |
| `tests/test_workflow.py` | workflow runtime 内部 Task 流水线和 run log。 |
| `tests/test_workflow_tools.py` | 工具脚本帮助输出、dry-run 行为、CLI 契约和确定性脚本边界。 |
| `tests/test_session.py` | Creation Session 状态机、归档、安全。 |
| `tests/test_orchestrator.py` | `novel ask` 和 handoff trace。 |
| `tests/test_memory_repair.py` | 项目管家 memory repair proposal/apply、白名单 patch、preflight 和回滚日志。 |
| `tests/test_search.py` | search index、ContextBundle、hidden truth visibility。 |
| `tests/test_exporting.py` | Markdown/DOCX export 和 manifest。 |
| `tests/test_validation.py` | 全项目 validation 和一致性闭环。 |
| `tests/test_json_schema.py` | JSON Schema 导出。 |
| `tests/test_artifact_store.py` | immutable artifact、path guard 和 freshness。 |
| `tests/test_projection.py` | Session state/timeline projection 与并发 base 检查。 |
| `tests/test_transactions.py` | journal commit、故障回滚和 crash recovery。 |
| `tests/test_security.py` | secret scanner、env/config 安全。 |
| `tests/test_web.py` / `test_web_e2e.py` | Web API、前端、E2E marker。 |
| `tests/test_prompts.py` | prompt 关键约束。 |
| `tests/test_packaging.py` | version、examples、README/CI/release。 |
| `tests/test_install_script.py` | 一键安装脚本、环境命名、Web UI 启动器和子 shell 行为。 |
| `tests/test_setup_guide.py` | 项目初始引导、默认 API/embedding/端口配置和脱敏。 |
| `tests/test_revision.py` | 章节修订、修订版本文件、revision log 和 session 修订路径。 |
| `tests/test_v01_polish.py` | v0.1 稳定性、CLI/文件安全/文档一致性回归。 |
| `tests/test_developer_docs.py` | 开发者文档覆盖范围、prompt 文档和命令一致性。 |

测试 helper：

- `tests/conftest.py`：pytest 标记和公共配置。
- `tests/provider_fixtures.py`：mock provider fixture。

## 13. Scripts

- `install_writeryang.py`：一键创建独立 conda/venv 环境，并用 editable 模式安装 WriterYang；同时生成固定环境的 Web UI 启动器。
- `check_local.py`：本地质量门禁，组合 pytest、ruff、mypy、secret scan、build、twine。
- `install_git_hooks.py`：设置 `core.hooksPath=.githooks`，让 tracked pre-push hook 在推送前运行本地门禁；支持 `--dry-run`。
- `smoke_session.py`：创建临时项目并用 CLI 跑完整 Session smoke；`--model` 会补写临时项目 default model，供 Session 子命令继承。
- `debug_bundle.py`：收集脱敏排障包；会移除已知密钥值，但 bundle 仍可能包含小说正文、隐藏设定和模型 I/O 摘要，不应外发或提交。
- `provider_ping.py`：检查 agent/embedding provider 配置，显式允许后可做真实最小调用。
- `webui_smoke.py`：启动本地 Web UI 并用 Playwright 跑最小浏览器流程。
- `capture_webui_guide_screenshots.py`：启动本地 Web UI，用 mock 临时项目生成用户手册截图。
- `project_health.py`：聚合 validate/status/usage、章节审核、Session 和导出状态。

这些脚本只组合现有 CLI/core，不复制业务决策。

## 14. 配置和模板

### `config/agents.yaml`

真实项目应配置顶层 `default` API；新项目默认写入 `inherit_default: true` 的 4 个 profile patch，未显式写入 profile 字段时完整跟随 `default`，运行时按 `task -> profile -> default` 解析。旧 `agents:` 任务键不再兼容，任务级覆盖写入 `tasks` 高级区。完整独立 profile/task 配置或 `default` 支持：

- `provider`
- `base_url_env`
- `api_key_env`
- `model`
- `json_response_format`
- `max_context_tokens`
- `max_tokens`
- `timeout_seconds`
- `max_retries`

`reasoning`、`thinking.type`、`temperature` 是 task-only 字段；写入 `default` 或 `profiles.*` 会被 schema 拒绝。

解析和默认值在 `schemas.AgentConfig`、`schemas.AgentConfigPatch`、`provider_config.py`、`providers.py`。`mock` provider 仅用于显式测试/调试入口，不应作为真实项目 `default`。

### `config/embeddings.yaml`

embedding provider 配置。推荐配置 DashScope text-embedding-v4、Zhipu embedding-3 或 OpenAI-compatible 真实接口。DashScope text-embedding-v4 模板使用 `dimensions: 2048` 和 `batch_size: 10`；`local_hash` 只用于测试 fixture。

初始化模板由 `novel init` 基于 `core/workspace.py` 生成。涉及模板字段、默认 provider、目录结构或初始 memory 文件的改动，需要同步 `tests/test_workspace.py` 和 packaging smoke 测试。

## 15. 修改点速查

| 目标 | 优先修改 |
| --- | --- |
| 新 CLI 命令 | core service -> `cli.py::build_parser()` -> 新增 `_cmd_*()` handler -> 登记 `_COMMAND_HANDLERS` -> tests |
| 新 Web API | core service -> `web_api/router.py::handle_api_request()` -> frontend -> tests |
| 新 Agent | prompt txt -> core service -> provider config -> schema -> tests |
| 新 schema 文件 | `schemas.py` -> `json_schema.py` -> `schemas/*.schema.json` -> validation tests |
| Provider 适配 | `providers.py` payload/parse -> config schema -> provider tests |
| Prompt 改动 | `src/novel/prompts/*.txt` -> `tests/test_prompts.py` -> `AGENT_PROMPT_ASSEMBLY.md` |
| 文件写入安全 | `io.py`、`locking.py`、调用 service |
| 一致性规则 | `consistency.py` -> `auditing.py` / `validation.py` |
| Audit 作者文案 | `audit_localization.py` -> `auditing.py` / Web/CLI 展示 -> audit/web tests |
| search/context | `search.py` -> Agent service use_search_context -> tests |
| export | `exporting.py` -> CLI/Web -> manifest tests |

## 16. schema v3 工作流核心（2026-07-11）

### `src/novel/core/contracts/`

- `common.py`：`StrictModel`、schema v3 常量、Task/Profile/Artifact 枚举。
- `artifacts.py`：`ArtifactRef`、Audit/State Proposal binding 与 `ChapterLifecycle`。
- `state.py`：projection checkpoint、`AcceptanceCommit` 与 transaction journal schema。
- `revisions.py`：`SegmentSelection`、`SegmentPatch` 与独立 `RevisionSession` phase schema。
- `sessions.py`：目标 Session phase 和合法 transition table。
- `commands.py`、`decisions.py`、`tracing.py`：typed command、路由 proposal 和 workflow budget 契约。

### `src/novel/core/artifact_store.py`

创建不可变章节 artifact，执行 project-relative path guard、SHA-256 校验、working output 冻结和 `lifecycle.json` freshness 诊断。

### `src/novel/core/projection.py` 与 `src/novel/core/world_state.py`

初始化 Session-local state/timeline snapshot，逐章 deterministic 应用 State Proposal 并记录 checkpoint；Writer、Polish、Audit、Revision 和 State Update 可显式读取 projection，而不是提前修改 canonical memory。

### `src/novel/core/transactions.py` 与 `src/novel/core/lifecycle.py`

`transactions.py` 管理 PREPARED/APPLYING/COMMITTED/ROLLED_BACK journal、staging、backup 和恢复。`lifecycle.py` 在 Session 接受前完成全量 preflight，生成 pending Chapter Memory/Acceptance，并把所有章节与 canonical state/timeline 作为一个事务提交；正式导出复用同一 freshness guard。

### `src/novel/core/task_registry.py`

集中记录当前四个 Profile 与 Task、Prompt、输出 artifact、上下文权限和风险等级的映射，避免文档与运行时职责表继续漂移。

### `src/novel/core/markdown_blocks.py` 与 `src/novel/core/revision_workflow.py`

`markdown_blocks.py` 解析 front matter 之外的 Markdown block，生成稳定 selection hash，并在合成 patch 时验证 source、selected、prefix、suffix 与范围外字节。`revision_workflow.py` 管理 accepted candidate selection、Revision Agent structured patch、candidate freeze、Audit、revision-mode State Proposal、projection、pending Chapter Memory 和 transaction acceptance。Creation Session 不再保存 `scope_type`/`segment_range`，也没有 segment 执行分支。

### `src/novel/core/previewing.py`

构建非正式 Preview Package。只读取指定 working `draft.md`/`polished.md`，固定写入 `exports/previews/{preview_id}/`，并生成 strict `PreviewManifest`。该 service 不调用 production lifecycle guard，也绝不更新 `exports/export_manifest.json`。

### `src/novel/core/command_bus.py`

注册公开 typed command handler，统一 confirmation gate、项目锁、CLI/Web 结果结构与 `DomainError`。Session、Revision、Memory/Setting Change、Preview 和 Production Export 的 adapter 不再自行调用领域 mutation service。

### `src/novel/core/budget.py`

通过 `WorkflowBudgetTracker` 与 context variable 维护 workflow-wide 章节数、模型调用、Provider attempt、自动修订轮次和 token usage。Command Bus 会从 Creation/Revision Session 继承累计 usage；Provider 与 Session runtime 在真实消耗点记账，超限抛出 `WorkflowBudgetExceeded`。

### `src/novel/core/workflow_runtime.py`

持久化 `WorkflowRun` 和 `WorkflowNodeRun`。Creation/Revision Session 保存同一个 `workflow_run_id`，人工审批后的后续 command 会继续向原 trace 追加节点；每个模型节点都继承 command parent，并记录调用前后的全局预算。

### `src/novel/core/setting_change_followup.py`

负责 Setting Change 应用后的 Session follow-up 决策与执行。原 Web 私有 `_sync_setting_change_session` 已删除；同步失败返回可恢复 checkpoint 或 manual review，不由 adapter 吞错。
