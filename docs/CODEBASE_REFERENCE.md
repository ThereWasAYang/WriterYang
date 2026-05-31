# 代码库参考手册

本文是代码级地图。它不替代源码，但说明每个主要文件、class 和 function 的职责，帮助新开发者或大模型 Agent 快速定位修改点。

覆盖口径：公共 class、service function、CLI/Web 入口和跨模块 helper 会逐项说明；私有 helper 按所在模块的职责分组说明，并在对应模块列出关键函数名。新增 Python 文件、schema、Agent prompt、CLI/Web API 或 workflow skill 后，必须同步更新本文和相关专题文档。

## 1. 顶层文件

| 路径 | 作用 | 常见修改场景 |
| --- | --- | --- |
| `pyproject.toml` | Python 包配置、依赖、dev 依赖、ruff/mypy/pytest 配置、console script。 | 新增依赖、调整 lint/type/test 配置、改版本发布配置。 |
| `README.md` | 用户入口文档。 | 新命令、新工作流、新配置说明。 |
| `CHANGELOG.md` | 版本变更记录。 | 每轮可见能力变化。 |
| `CONTRIBUTING.md` | 贡献指南。 | 协作规范、测试命令、安全要求。 |
| `.github/workflows/tests.yml` | CI：pytest、build、secret scan、ruff、mypy。 | 改 CI 阶段或 Python 版本。 |
| `.github/workflows/release.yml` | tag 触发 GitHub Release 构建。 | 发布流程调整。 |
| `schemas/*.schema.json` | 从 Pydantic models 导出的 JSON Schema。 | schema 变化后重新导出。 |
| `examples/rain_station/` | 雨夜旧车站示例项目。 | README smoke、真实 provider 配置模板。 |
| `examples/wuxia_mountain_sect/` | 武侠长篇示例项目。 | 中文用户配置参考、validate 示例。 |
| `skills/` | 仓库级工作流技能。 | 给人类开发者和外部大模型 Agent 提供固定维护、排障、真实 API smoke、Web QA、发布流程。 |
| `scripts/` | 确定性工具脚本。 | 一键安装、本地质量门禁、Session smoke、provider ping、debug bundle、Web UI smoke、项目健康报告。 |

## 2. 包入口

### `src/novel/__init__.py`

定义包版本 `__version__`。发布、`novel --version` 和 packaging 测试依赖它。

### `src/novel/__main__.py`

支持 `python -m novel`。通常只转发到 `novel.cli:main`。

## 2.1 Python 源文件覆盖清单

本文覆盖以下 Python 源文件。新增文件时应同步更新本节和对应说明：

- `src/novel/cli.py`
- `src/novel/web_api.py`
- `src/novel/web_server.py`
- `src/novel/core/__init__.py`
- `src/novel/core/agent_output.py`
- `src/novel/core/auditing.py`
- `src/novel/core/canon.py`
- `src/novel/core/consistency.py`
- `src/novel/core/drafting.py`
- `src/novel/core/embeddings.py`
- `src/novel/core/env.py`
- `src/novel/core/exporting.py`
- `src/novel/core/inspection.py`
- `src/novel/core/inspiration.py`
- `src/novel/core/io.py`
- `src/novel/core/json_schema.py`
- `src/novel/core/locking.py`
- `src/novel/core/management.py`
- `src/novel/core/memory_repair.py`
- `src/novel/core/migration.py`
- `src/novel/core/orchestrator.py`
- `src/novel/core/planning.py`
- `src/novel/core/polishing.py`
- `src/novel/core/prompts.py`
- `src/novel/core/provider_config.py`
- `src/novel/core/providers.py`
- `src/novel/core/revision.py`
- `src/novel/core/schemas.py`
- `src/novel/core/search.py`
- `src/novel/core/security.py`
- `src/novel/core/session.py`
- `src/novel/core/setup_guide.py`
- `src/novel/core/state_update.py`
- `src/novel/core/usage.py`
- `src/novel/core/validation.py`
- `src/novel/core/workflow.py`
- `src/novel/core/workspace.py`

## 3. CLI 层：`src/novel/cli.py`

CLI 是薄包装：解析参数、处理 `--json/--quiet/--project`、拿项目锁、调用 core service、格式化输出。

主要函数：

- `build_parser()`：定义所有 CLI 命令、子命令和参数。
- `main(argv)`：命令分发入口，调用对应 core service。
- `_add_agent_runtime_args()`：给 Agent 命令统一加 `--agent-config`、`--provider`、`--model`、`--dry-run-provider`。
- `_add_integration_args()` / `_add_integration_args_recursive()`：给命令递归加 `--json`、`--quiet`、`--project`。
- `_apply_project_alias()`：把 `--project` 映射为内部 `path`。
- `_success()` / `_failure()` / `_print_json()`：统一文本和 JSON 输出。
- `_safe_message()`：错误消息脱敏。
- `_command_lock()`：写命令加项目锁。
- `_print_dry_run_provider()`：显示将使用的 provider 配置，不调用 API。
- `_validation_payload()` / `_status_payload()`：把 core result 转为 CLI JSON payload。
- `_format_usage_summary()`：格式化 provider usage。
- `_resolve_web_port()`：Web 端口解析，读取项目配置并处理冲突提示。
- `completion_script()`：生成 shell completion。
- `run_doctor()` / `format_doctor_result()` / `_doctor_*()`：环境、项目、配置、安全检查。
- `_run_session_command()` / `_session_payload()` / `_session_low_issue_lines()`：session 子命令处理。
- `_audit_issue_lines()`：把 audit issue 展示给用户。

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
- 从 `web_static/` 安全读取 `index.html`、`app.css`、`app.js`，并通过 `/static/...` 提供静态资源。
- 对 HTML、静态资源和 API 响应发送 no-cache headers，避免浏览器继续显示旧版 Web UI。
- 不包含业务逻辑。

### `src/novel/web_api.py`

本地 JSON API。核心 class/function：

- `WebErrorPayload` / `WebResponsePayload`：统一 `{ok,data,error}` 形态。
- `WebAPIError`：带稳定 code、HTTP status、details 的 API 异常。
- `handle_api_request()`：路由分发入口。
- `_success()` / `_failure()`：统一响应结构。
- `_locked_write()`：Web 写操作项目锁。
- `_runtime_summary()`：返回 Web server 当前 Python 路径、环境名和包版本，帮助确认 Web UI 是否运行在安装器创建的新环境。
- `_list_projects()`：列出给定根目录下可打开的小说项目，不读取 `.env*`。
- `get_project_status()` / `format_canon()` / `_list_chapters()`：分别支持项目状态、canon 摘要和章节列表 API。
- `summarize_provider_usage()`：为 Web usage 面板返回 provider 调用和 token 统计。
- `_init_project()`：Web 端初始化小说项目，调用 `init_workspace()`，不复制 workspace 创建逻辑。
- `_inspire()`：Web 端生成 inspiration，调用 Inspiration service。
- `_canon_suggest()` / `_canon_apply()`：Web 端 canon proposal 和 apply，调用 Canon service 并保持 proposal/apply 分离。
- `_validate_project()` / `_validation_message_payload()`：只读项目检查 API，复用 `validate_project()`，返回 errors/warnings 摘要供 Web UI 显示。
- `_plan_chapter()`、`_write_chapter()`、`_polish_chapter()`、`_audit_chapter()`、`_generate_chapter()`：调用对应 core service。
- `_export_markdown()`：调用 Markdown export。
- `_save_chapter_file()`：Web 编辑器保存章节版本，追加 revision log。
- `_save_provider_config()`：保存非密钥 provider 配置，写前校验和备份。
- `_setup_default_provider()` / `_setup_embedding()` / `_setup_web_port()`：Web 初始引导 API；调用 `core/setup_guide.py`，真实 key 写项目 `.env`，响应只返回 env 名和测试结果。
- `_setup_recommend_port()` / `_setup_open_web()`：推荐可用端口并返回 Web UI URL；Web 场景不另起服务端。
- `_index_refresh()`：调用 `refresh_search_index()`，刷新关键词索引或显式刷新真实 embedding 向量，并返回最新 search status。
- `_session_*()`：session start/revise/approve/run/accept/archive API。
- `_session_rewrite_event_summary()`：读取自动打回重写记录，供 Web Session 面板和轮询接口展示。
- `_memory_repair_suggest()` / `_memory_repair_apply()`：项目管家 proposal 和 apply API，调用 `core/memory_repair.py`，不在 Web 层直接 patch 文件。
- `_session_revise_audit()` / `_session_retry_rewrite()` / `_session_undo_rewrite()`：Audit 复审、基于新审核重试打回、撤回打回并恢复快照。
- `_management_events()` / `_management_event_summary()`：读取 `memory/management_events.jsonl`，供 Web 显示后台状态/时间线/记忆刷新。
- `_file_tree()`：列出 workspace 白名单文件，排除 `.env*`、缓存、索引、备份。
- `_read_workspace_file()` / `_read_chapter_file()`：安全读取文件。
- `_runs_summary()` / `_provider_call_summary()` / `_model_io_summary()`：运行和模型日志摘要。
- `_provider_config_summary()` / `_sanitize_config()` / `_collect_env_names()`：脱敏展示 agent/embedding 配置。
- `/api/search-status` 对应 `search_index_status()`；只返回 env 名称和是否缺失，不返回真实 env 值。
- `_state_timeline_summary()` / `_state_timeline_visual_summary()`：状态和时间线可视化摘要。
- `_audit_annotations()` / `_locate_quote()`：audit evidence 定位正文。
- `_workspace_diff()`：版本 diff。
- `_safe_workspace_file()` / `_safe_config_file()` / `_is_safe_*()`：路径白名单和穿越防护。

### `src/novel/web_static/index.html`

无构建 vanilla 前端的页面结构。只保留 DOM、页面分区和表单控件，通过 `/static/app.css` 和 `/static/app.js` 引入样式与交互逻辑。包含：

- 顶部主导航：主页、创作工作台、小说状态管理、模型与检索配置、运行日志 / 项目文件。
- 主页：项目路径输入、打开/刷新、项目检查、新建项目、项目初始引导、项目状态、章节列表和下一步提示。
- 创作工作台：创作输入、Session 主流程、自动打回记录、章节对照、章节编辑器、audit 定位和 revision diff。
- 小说状态管理：canon 摘要、状态/时间线、项目管家和后台管理动态。
- 模型与检索配置：Agent 模型配置、FTS / embedding 状态和索引刷新。
- 运行日志 / 项目文件：安全文件树、只读文件预览、运行日志和章节文件查看。
- 项目初始引导面板支持保存默认 API、可选 embedding API、推荐并保存 Web 端口，以及打开当前 Web UI 地址。
- Session 面板支持创建大纲、修改大纲、批准大纲、开始写作、按 Audit/用户意见修订、认可和归档。
- 项目管家面板支持生成 memory repair proposal、确认 apply，并显示后台管理动态。
- 检索索引面板显示 FTS / embedding 状态，支持本地刷新关键词索引，也支持显式刷新真实 embedding 向量索引。

### `src/novel/web_static/app.css`

Web UI 样式文件。包含顶栏、主导航、页面网格、面板、表单、章节预览、文件树、状态标签、时间线卡片和移动端单列布局。修改视觉布局时优先改这里，不要把新样式重新塞回 `index.html`。

### `src/novel/web_static/app.js`

Web UI 交互脚本。负责：

- 通用 `$()`、`apiGet()`、`apiPost()`、`setMessage()` 等工具函数。
- 主页面切换、页内 tab 切换和状态保持。
- 项目打开、刷新、初始化和项目检查。
- Session 创建、修改大纲、批准、运行、修订、认可、归档和打回记录展示。
- 章节对照、编辑器、audit evidence 定位、diff、文件树读取。
- Provider / embedding 配置、索引刷新、状态/时间线、项目管家和运行日志 API 调用。
- Agent 模型配置页用表单编辑 `provider`、`model`、`base_url_env`、`api_key_env`、`thinking.type`、`temperature`、`max_tokens` 等非密钥字段。配置页使用专用两栏布局，Agent 配置面板比检索索引面板更宽；`model` 是和 `provider` 同级的普通输入框，`base_url_env`、`api_key_env` 占满表单宽度。右侧显示当前 Agent 的生效配置来源和最终非密钥配置，完整脱敏 JSON 收进调试折叠区；“恢复继承 default”通过 `/api/provider-config` 的 `clear_agents` 删除该 Agent 覆盖项。真实 API Key 仍只通过 `.env` / 初始引导管理。
- Embedding API 配置页块复用 `/api/setup/embedding`，要求用户每次重填 Base URL、API Key 和模型名；保存成功后清空输入框，并自动调用 `/api/index/refresh` 刷新语义向量索引。
- 项目检查按钮调用 `/api/validate`，把 errors/warnings 摘要写入主页状态、顶部检查摘要、调试页文件查看和下一步提示。
- 自动打回区域支持选择 rewrite event、查看被打回原文、纠正 Audit 理解并重新审核、根据新审核重试打回、撤回打回。
- `renderNextStep()`：根据项目状态、validation 结果和 session 状态显示下一步操作建议。

`app.js` 只做前端状态和 API 调用，不应复制 core 业务逻辑。新增 Web 能力时，后端逻辑仍应放在 `web_api.py` / `core/`，前端只负责收集输入和展示结果。

## 5. Core 基础设施模块

### `core/__init__.py`

Core 包标记文件。当前不导出业务 API；调用方应从具体 service 模块导入函数，避免形成隐式公共接口。

### `core/schemas.py`

所有 Pydantic schema 的集中定义。主要类型：

- 配置：`ProjectConfig`、`AgentConfig`、`AgentConfigPatch`、`AgentsConfig`、`EmbeddingProviderConfig`、`EmbeddingsConfig`、`ThinkingConfig`。
- inspiration/canon：`InspirationBrief`、`CanonProposal`、`Character`、`Location`、`Item`、`WorldRule`、`HiddenTruth`、`ForeshadowingThread` 及对应 file model。
- state/timeline：`EntityState`、`CharacterState`、`ItemState`、`LocationState`、`TimelineEvent`、`TimelineNarrativePosition`、`TimelineStoryPosition`、`TimelineFile`。`narrative_position` 表示正文呈现顺序，`story_position` 表示故事世界顺序。
- chapter：`ChapterPlan`、`ChapterScene`、`RequiredContext`、`ChapterMetadata`。
- audit：`AuditReport`、`AuditIssue`、`AuditEvidence`。
- workflow/session/export：`AgentRunLog`、`AgentRunStep`、`CreationSession`、`CreationOutline`、`CreationArchiveManifest`、`ExportSourceChapter`、`ExportRecord`、`ExportManifest`。
- revision/context：`RevisionLog`、`RevisionRecord`、`ContextBundle`、`ContextItem`、`ContextExclusion`。
- state update：`StateUpdateProposal`、`StateChange`、`StateUpdateApplyLog`。
- session/memory management：`SessionRewriteEvent`、`SessionRewriteEvents`、`MemoryRepairProposal`、`MemoryRepairOperation`、`MemoryRepairApplyLog`、`ManagementEvent`。

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
- `backup_file()` / `backup_if_exists()`：时间戳备份。
- `atomic_write_text_with_backup()`：备份后写文本。
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

### `core/migration.py`

schema version 迁移：

- `migrate_project()`：补齐缺失 `schema_version`，拒绝更新版本项目。
- `MigrationResult` / `MigrationError`：结果和错误。
- `_schema_versioned_*_paths()`：列出要迁移的 YAML/JSON。
- `_migrate_yaml()` / `_migrate_json()`：迁移单个 YAML/JSON 文件。
- `_set_schema_version()`：补齐或更新 schema version。
- `_migrate_timeline_events()`：把旧 timeline event 补齐为 narrative/story 双轨结构。
- `_reject_newer_version()`：拒绝高于当前工具版本的项目文件，避免降级写坏数据。

### `core/json_schema.py`

JSON Schema 导出：

- `SchemaDefinition`：schema 名称、model、输出文件。
- `schema_payloads()`：生成所有 schema payload。
- `export_json_schemas()`：写入 `schemas/*.schema.json`。

## 6. Provider 和模型调用

### `core/providers.py`

主要类型：

- `ModelRequest`：Agent 请求。
- `TokenUsage`：prompt/completion/total token。
- `ModelResponse`：模型内容、原始响应、token、reasoning。
- `ModelProvider`：抽象接口。
- `MockProvider`：测试 provider，支持响应序列和 stream chunks。
- `LoggingModelProvider`：包裹 provider，写完整 model_io 日志。
- `OpenAICompatibleProvider`：OpenAI Chat Completions 兼容实现。
- `ProviderFactory`：根据 `AgentConfig` 创建 provider。
- `ProviderError` 及子类：env、HTTP、auth、rate limit、timeout、network、response 错误。

关键函数：

- `OpenAICompatibleProvider.from_config()`：读取 env、默认 base URL、provider 私有字段。
- `OpenAICompatibleProvider._payload()`：组装请求 payload，包括 `thinking`、`response_format`。
- `_ensure_json_mode_messages()`：`json_object` 结构化调用时，如果 prompt 中没有 `json` 字样，自动补充 JSON 输出提示，兼容 DeepSeek 等服务端校验。
- `_model_response_from_openai_raw()`：解析 OpenAI 格式返回。
- `_stream_content_from_line()`：解析 SSE chunk。
- `_redact_data()` / `_redact_text()`：日志脱敏。

### `core/provider_config.py`

- `ProviderOverrides`：CLI 临时覆盖 provider/model。
- `ProviderDescriptor`：dry-run-provider 展示结构。
- `default_agent_config_path()`：默认 `config/agents.yaml`。
- `load_agents_config()`：读取 `AgentsConfig`。
- `resolve_agent_config()`：按 agent name、fallback、override 解析配置；真实调用会合并项目 `.env` 和系统环境变量。
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

### `core/embeddings.py`

Embedding provider：

- `EmbeddingProvider`：抽象接口。
- `LocalHashEmbeddingProvider`：本地 hash embedding，仅用于测试和离线 fixture，不作为真实业务 fallback。
- `OpenAIEmbeddingProvider`：兼容 embedding API；适配 DashScope text-embedding-v4 和 Zhipu embedding-3。
- `EmbeddingProviderFactory`：按 `EmbeddingsConfig` 创建 provider。
- `create_embedding_provider()`：外部调用入口；默认读取项目 `.env`，只有显式 `provider_name="local_hash"` 时返回本地 hash provider，缺失真实配置不再自动 fallback。
- `local_embedding_vector()`：本地向量生成。
- `_vectors_from_openai_raw()`：解析 embedding 返回。

## 7. Agent 输出守卫

### `core/agent_output.py`

- `AgentInvocationContext`：agent name、caller、interaction mode、task、chapter、session。
- `AgentOutputContract`：目标输出类型、schema、是否允许提问、是否允许 JSON。
- `AgentOutputContractError`：输出契约错误。
- `generate_with_output_guard()`：统一 provider 调用、输出校验、一次 repair retry、violation log。
- `validate_agent_output()`：检测空输出、内部反问、模型自述、JSON/Markdown 类型不符、工作区语言。
- `build_output_contract_repair_prompt()`：生成输出契约修复 prompt。
- `write_agent_output_violation_log()`：写 `runs/agent_output_violations/`。

开发新 Agent 时应复用此模块。

## 8. 写作业务模块

### `core/workspace.py`

初始化 workspace：

- `InitOptions` / `InitResult`：初始化参数和结果。
- `init_workspace()`：创建项目目录、默认配置和空 memory 文件。
- `_workspace_dirs()`：默认目录列表。
- `_project_yaml()` / `_agents_yaml()` / `_embeddings_yaml()`：默认配置内容。
- `_write_new_file()`：避免覆盖已有用户文件。

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
- `build_planning_user_prompt()`：组装 project、inspiration、style、canon、state、timeline、instruction、search context。
- `parse_chapter_plan()`：解析和校验 `ChapterPlan`。
- `_generate_chapter_plan_with_repair()`：输出守卫 + schema/reference repair。
- `_validate_plan_for_write()` / `_plan_reference_errors()`：写入前阻断缺失引用。
- `render_plan_markdown()`：可读 plan.md。
- `default_mock_chapter_plan_json()`：测试 fixture。

### `core/drafting.py`

初稿：

- `ChapterDraftingOptions` / `ChapterDraftingResult` / `DraftingError`。
- `write_chapter_draft()`：读取 plan/canon/state/timeline/style/inspiration，生成 `draft.md`。
- `build_writer_user_prompt()`：组装 Writer Agent 输入。
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
- `_validate_audited_body_against_plan()` / `_validate_hidden_truth_not_revealed()`：正文和 hidden truth 检查。
- `build_audit_user_prompt()` / `parse_audit_report()`。
- `_generate_audit_report_with_repair()`：输出守卫 + schema repair。
- `combine_audit_reports()`：合并 deterministic 和模型审核。
- `_status_for_issues()`：severity 到 overall_status 的策略。

### `core/consistency.py`

确定性一致性引擎：

- `ConsistencySnapshot`：聚合 canon/state/timeline/chapter artifacts。
- `ConsistencyFinding` / `ConsistencyResult`：确定性问题输出。
- `check_chapter_consistency()`：章节级一致性检查。
- `check_project_consistency()`：项目级闭环检查。
- `_check_character_knowledge()`：角色已知/未知信息链。
- `_check_item_flow()`：物品 holder/location 和 possession 双向一致。
- `_check_timeline_order()`：timeline narrative order、story-world causes/effects 和 scene 边界。
- `_check_hidden_truth_body_exposure()` / `_check_reader_visible_hidden_truth_leaks()`：hidden truth 暴露边界。
- `_check_chapter_loop()` / `_check_accepted_chapter_loop()`：plan/draft/polished/audit/state/metadata 闭环。

### `core/state_update.py`

状态和时间线更新：

- `StateUpdateProposeOptions` / `StateUpdateApplyOptions` / `AcceptChapterOptions`。
- `StateUpdateProposeResult` / `StateUpdateApplyResult` / `AcceptChapterResult`。
- `propose_state_update()`：生成 proposal，不改正式 state/timeline。
- `apply_state_update()`：校验 proposal、备份、应用 state/timeline、失败回滚。
- `accept_chapter()`：检查 audit、必要时 propose/apply、标记 accepted。
- `validate_state_update_proposal()`：引用和冲突校验。
- `apply_state_changes_to_state()`：把 state_changes 应用到 EntityState。
- `mark_chapter_accepted()` / `write_chapter_metadata()`。
- `build_state_update_user_prompt()` / `parse_state_update_proposal()`。
- `_generate_state_update_proposal_with_repair()`：输出守卫 + schema repair。

### `core/revision.py`

修订：

- `ChapterRevisionOptions` / `ChapterRevisionResult` / `RevisionLoopOptions` / `RevisionLoopResult` / `RevisionContext`。
- `revise_chapter()`：根据 instruction 或 audit 生成版本文件。
- `revise_chapter_loop()`：受最大轮数和人工确认限制的循环修订。
- `load_revision_context()`：加载 plan、source markdown、audit、style、canon、state、timeline。
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
- `run_session()`：生成正文、润色、审核、自动修复、state proposal。
- `revise_content()`：按用户意见或 audit issue 生成修订版本，提升为当前 `polished.md`，重跑 audit，并在通过后重建 state proposal。
- `revise_audit()`：读取 rewrite event 的 rejected snapshot，把用户纠正意见传给 Audit Agent，重写 `audit.json` 并记录 `audit_revision_history`。
- `retry_rewrite()`：基于最新 audit issue 再次执行 revision rewrite 或 plot replan，提升当前稿并重跑 audit。
- `undo_rewrite()`：恢复 rewrite event 的 rejected snapshot，备份当前稿，重跑 audit，并更新 undo 状态。
- `accept_session()`：应用状态更新并标记章节 accepted。
- `archive_session()`：归档 approved outline、最终正文、audit、state update 和 manifest。
- `load_rewrite_events()`：读取 `memory/sessions/{session_id}/rewrite_events.json`。
- `_generate_chapter_content()`：单章 writer/polish/audit 调度。
- `_auto_repair_chapter()`：正文层 medium/high/critical 自动修复，生成 `polished.vN.md`。
- `_promote_revision_to_polished()`：把修订版本提升为当前 `polished.md` 后再重跑 audit。
- `_auto_replan_chapter()` / `_should_replan_chapter()`：连续修复仍失败或计划层问题时回退 Plot Agent 重写本章计划。
- `_start_rewrite_event()` / `_update_rewrite_event()`：自动打回前保存原文快照、记录打回原因，并在复审后更新 completed/unresolved/failed 状态。
- `_has_hard_issues()`：判定阻断 issue。
- `_session_instruction()`：把 session intent 转为内部 Agent instruction。

### `core/management.py`

后台管理事件日志：

- `record_management_event()`：向 `memory/management_events.jsonl` 追加脱敏事件。状态更新 proposal/apply、timeline update、memory repair 和 chapter accepted 都应调用它。
- `load_management_events()`：读取最近事件，供 CLI/Web UI 显示。
- `management_events_path()`：返回事件日志路径。

### `core/memory_repair.py`

orchestrator 项目管家修复 proposal：

- `MemoryRepairError`：proposal/apply 失败。
- `MemoryRepairSuggestResult` / `MemoryRepairApplyResult`：service 返回结构。
- `suggest_memory_repair()`：根据用户自然语言和当前项目文件生成 `MemoryRepairProposal`、Markdown 摘要和 diff 预览；默认不修改正式 memory。
- `apply_memory_repair()`：校验 proposal，限制白名单文件，按 JSON Pointer 应用 `add/replace/remove`，备份目标文件，atomic write，运行 validate；失败时写失败 apply log 并尝试回滚。
- `render_memory_repair_markdown()`：把 proposal 渲染为用户可读说明。
- `_infer_target_files()` / `_infer_operations()`：MVP 级安全推断。当前只做低风险 timeline event role 修复；复杂修复应让用户编辑 proposal。
- `_apply_operations_to_data()` / `_apply_operation()` / `_resolve_pointer_parent()`：JSON Pointer patch 执行器。
- `_validate_file_model()`：用对应 schema 校验修改后的白名单文件。
- `_restore_backups()`：apply 失败后恢复已备份文件。

### `core/workflow.py`

底层端到端章节流水线：

- `GenerateChapterOptions` / `GenerateChapterResult` / `WorkflowError`。
- `generate_chapter()`：依次 plan/write/polish/audit，写 run log。
- `_run_plan_step()` / `_run_write_step()` / `_run_polish_step()` / `_run_audit_step()`：各步骤封装。
- `_resume_existing_step()`：从已有 artifact 继续。
- `_new_run_log()` / `_write_run_log()` / `_fail_run()` / `_complete()`：run log 生命周期。
- `_load_provider_for_step()`：按 step 选择 agent provider。

### `core/orchestrator.py`

受控编排：

- `OrchestratorPlan` / `OrchestratorOptions` / `OrchestratorResult` / `HandoffTraceEntry`。
- `orchestrate()`：执行或 dry-run。
- `plan_orchestration()`：根据自然语言请求生成计划。
- `classify_request()`：关键词任务分类。
- `handoff_rules_text()`：可见 handoff 规则。
- `_execute_plan()` / `_execute_task()`：调用对应 core service。
- `_validate_handoff_trace()` / `_check_limits()`：安全限制。
- `_write_run_log()`：写 orchestrator run log。

## 9. 检索、展示、导出

### `core/search.py`

搜索和 ContextBundle：

- `rebuild_search_index()`：全量构建 JSON/SQLite/manifest 搜索索引，可选真实 embedding。
- `refresh_search_index()`：增量刷新新增、修改、删除文档；默认只更新 FTS，`with_embeddings=True` 时刷新真实 embedding。
- `search_index_status()`：返回 FTS 和 embedding freshness 状态，供 CLI/Web 状态栏使用；`search_project(use_vector=True)` 会在真实向量缺失或过期时先调用 embedding refresh。
- `search_project()`：关键词/字段/类型/章节搜索。
- `retrieve_context()`：旧的检索入口。
- `retrieve_context_bundle()`：结构化上下文检索，按 ChapterPlan 扩展实体引用。
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
- `summarize_provider_call_log()`：读取 `provider_calls.jsonl`。
- `refresh_provider_usage_summary()` / `refresh_provider_usage_summary_for_log()`：刷新 `provider_usage.json`。
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
- `_validate_agent_names()` / `_validate_single_agent_config()`：agent 配置。
- `_validate_embedding_config()`：embedding 配置。
- `_validate_references()`、`_validate_state_references()`、`_validate_state_update_proposal_references()`：跨文件引用。
- `_validate_chapter_outputs()` / `_validate_single_chapter_output()`：plan/draft/polished/audit/state proposal。
- `_validate_run_and_export_outputs()` / `_validate_session_outputs()`：run/export/session。
- `_validate_consistency_findings()`：项目级 consistency engine 输出接入。

## 11. Prompt 文件

| 文件 | 对应 Agent | 重点约束 |
| --- | --- | --- |
| `prompts/inspiration_system.txt` | Inspiration | 弱总纲，不写正文，不反问内部任务。 |
| `prompts/canon_system.txt` | Canon | CanonProposal JSON，稳定 ID，hidden truth 不进 reader visible。 |
| `prompts/planning_system.txt` | Plot | ChapterPlan JSON，不写正文，不改 state/timeline，不发明引用。 |
| `prompts/writer_system.txt` | Writer | 只写正文 Markdown，不输出解释/JSON，不泄漏 hidden truth。 |
| `prompts/polish_system.txt` | Polish | 只输出润色正文，保留核心事实，不改设定。 |
| `prompts/audit_system.txt` | Audit | 只输出 AuditReport JSON，检查一致性。 |
| `prompts/state_update_system.txt` | StateUpdate | 只输出 StateUpdateProposal JSON，根据正文实际发生事件提取。 |
| `prompts/revision_system.txt` | Revision | 只输出修订正文，按 instruction/audit 修复，不改 state/timeline。 |

模板加载在 `core/prompts.py`：

- `load_prompt_template(name)`：按名称读取 `.txt`。
- `render_prompt_template(name, **values)`：简单 format 渲染。

## 12. Tests 目录

| 文件 | 覆盖范围 |
| --- | --- |
| `tests/test_workspace.py` | `novel init` 和默认 workspace 文件。 |
| `tests/test_cli.py` / `test_integration_cli.py` | CLI 参数、JSON/quiet/project、doctor、lock。 |
| `tests/test_agent_output.py` | Agent 输出契约守卫、内部任务反问拦截和 violation log。 |
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
| `tests/test_workflow.py` | `generate-chapter` 流水线和 run log。 |
| `tests/test_workflow_tools.py` | 工作流 skill 和工具脚本的渐进式加载、确定性脚本边界。 |
| `tests/test_session.py` | Creation Session 状态机、归档、安全。 |
| `tests/test_orchestrator.py` | `novel ask` 和 handoff trace。 |
| `tests/test_memory_repair.py` | 项目管家 memory repair proposal/apply 和白名单 patch。 |
| `tests/test_search.py` | search index、ContextBundle、hidden truth visibility。 |
| `tests/test_exporting.py` | Markdown/DOCX export 和 manifest。 |
| `tests/test_validation.py` | 全项目 validation 和一致性闭环。 |
| `tests/test_json_schema.py` | JSON Schema 导出。 |
| `tests/test_migration.py` | schema version 迁移。 |
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

## 13. Skills 和 Scripts

### `skills/`

- `writeryang-maintainer/SKILL.md`：代码修改、测试、文档、GitHub 同步规则。
- `writeryang-workflow-debug/SKILL.md`：session/audit/state/provider 失败排查顺序。
- `writeryang-real-api-smoke/SKILL.md`：真实 provider smoke 的安全边界和验收标准。
- `writeryang-web-ui-qa/SKILL.md`：Web UI 改动后的浏览器 QA 流程。
- `writeryang-release/SKILL.md`：发版前检查。
- `writeryang-agent-orchestrator/SKILL.md`：Orchestrator / Session 的用户协商、handoff 和状态机边界。
- `writeryang-agent-inspiration/SKILL.md`：Inspiration Agent 的输入、弱方向产物和 inspire 排查入口。
- `writeryang-agent-canon/SKILL.md`：Canon Agent 的 proposal、apply、stable ID 和 hidden truth 边界。
- `writeryang-agent-plot/SKILL.md`：Plot Agent 的 ChapterPlan 输入输出、引用校验和 context report 边界。
- `writeryang-agent-writer/SKILL.md`：Writer Agent 的 draft 产物、front matter 和输出守卫边界。
- `writeryang-agent-polish/SKILL.md`：Polish Agent 的 edit mode、事实保持和 polished 产物边界。
- `writeryang-agent-audit/SKILL.md`：Audit Agent 的 AuditReport、deterministic checks 和 severity policy。
- `writeryang-agent-state-update/SKILL.md`：State Update Agent 的 proposal、apply log、state/timeline 冲突边界。
- `writeryang-agent-revision/SKILL.md`：Revision Agent 的版本文件、revision log 和 session 修订边界。

Agent skill 只作为开发者和外部 Agent 的边界说明，不参与小说生成 prompt。创意生成仍由 `src/novel/prompts/` 和 core service 动态组装，不能把剧情、文风或人物塑造固化成脚本。

### `scripts/`

- `install_writeryang.py`：一键创建独立 conda/venv 环境，并用 editable 模式安装 WriterYang；同时生成固定环境的 Web UI 启动器。
- `check_local.py`：本地质量门禁，组合 pytest、ruff、mypy、secret scan、build、twine。
- `smoke_session.py`：创建临时项目并用 CLI 跑完整 Session smoke。
- `debug_bundle.py`：收集脱敏排障包。
- `provider_ping.py`：检查 agent/embedding provider 配置，显式允许后可做真实最小调用。
- `webui_smoke.py`：启动本地 Web UI 并用 Playwright 跑最小浏览器流程。
- `capture_webui_guide_screenshots.py`：启动本地 Web UI，用 mock 临时项目生成用户手册截图。
- `project_health.py`：聚合 validate/status/usage/chapter/session/export 健康摘要。

这些脚本只组合现有 CLI/core，不复制业务决策。

## 14. 配置和示例

### `config/agents.yaml`

真实项目应配置顶层 `default` API；每个 agent 可以只写差异字段并继承 `default`。完整配置或 `default` 支持：

- `provider`
- `base_url_env`
- `api_key_env`
- `model`
- `reasoning`
- `thinking.type`
- `max_context_tokens`
- `max_tokens`
- `temperature`
- `timeout_seconds`
- `max_retries`

解析和默认值在 `schemas.AgentConfig`、`schemas.AgentConfigPatch`、`provider_config.py`、`providers.py`。`mock` provider 仅用于显式测试/调试入口，不应作为真实项目 `default`。

### `config/embeddings.yaml`

embedding provider 配置。推荐配置 DashScope text-embedding-v4、Zhipu embedding-3 或 OpenAI-compatible 真实接口。`local_hash` 只用于测试 fixture。

### `examples/rain_station/`

雨夜旧车站示例。用于 README smoke、真实 DeepSeek 模板、mock 模板。

### `examples/wuxia_mountain_sect/`

武侠长篇示例。配置项带中文注释，适合新用户复制参考。

## 15. 修改点速查

| 目标 | 优先修改 |
| --- | --- |
| 新 CLI 命令 | core service -> `cli.py::build_parser()` -> `cli.py::main()` -> tests |
| 新 Web API | core service -> `web_api.py::handle_api_request()` -> frontend -> tests |
| 新 Agent | prompt txt -> core service -> provider config -> schema -> tests |
| 新 schema 文件 | `schemas.py` -> `json_schema.py` -> `schemas/*.schema.json` -> validation tests |
| Provider 适配 | `providers.py` payload/parse -> config schema -> provider tests |
| Prompt 改动 | `src/novel/prompts/*.txt` -> `tests/test_prompts.py` -> `AGENT_PROMPT_ASSEMBLY.md` |
| 文件写入安全 | `io.py`、`locking.py`、调用 service |
| 一致性规则 | `consistency.py` -> `auditing.py` / `validation.py` |
| search/context | `search.py` -> Agent service use_search_context -> tests |
| export | `exporting.py` -> CLI/Web -> manifest tests |
