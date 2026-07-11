# CLI 命令参考

本文档列出 WriterYang 推广初期常用 CLI 命令。当前推广支持平台为 macOS / Linux；Windows 适配暂缓，不建议在 Windows 上按本文档执行生产创作流程。

## 环境检查

```bash
novel --version
novel doctor
python scripts/install_writeryang.py --dry-run
python scripts/install_writeryang.py --web-port 9000
python scripts/install_writeryang.py --no-web
```

临时验证模板项目：

```bash
tmp_project="$(mktemp -d)/writeryang-template"
novel init "模板校验" --path "$tmp_project" --no-guide
novel validate --path "$tmp_project"
```

## 项目初始化

```bash
novel init "青灯客栈" --path ./qingdeng-inn --no-guide
novel init "青灯客栈" --path ./qingdeng-inn
novel validate --path ./qingdeng-inn
novel status --path ./qingdeng-inn
novel schema export --output schemas
```

`novel init` 会生成 schema v3 的 `project.yaml`、配置、`memory/`、`runs/`、`transactions/` 和 `exports/`。旧 schema 工作区会被直接拒绝；不提供历史迁移命令或兼容读取分支。真实项目建议先完成默认 API 配置；mock 流程可用 `--provider mock`。

## 创作流程

```bash
novel inspire "雨夜客栈里，一盏青灯照出二十年前失踪剑客的影子。" --path ./qingdeng-inn --provider mock --overwrite
novel canon suggest --path ./qingdeng-inn --provider mock --output ./qingdeng-canon.json
novel canon apply ./qingdeng-canon.json --path ./qingdeng-inn
novel canon show --path ./qingdeng-inn
novel session start "写第一章雨夜开场" --path ./qingdeng-inn --chapters 1 --provider mock
```

Plot、Writer、Polish、Audit 和 State Update 都是 workflow runtime 内部 Task，不再提供 `plan-chapter`、`write-chapter`、`polish-chapter`、`audit-chapter`、`state-update` 或 `generate-chapter` 公开命令。这样可确保大纲审批、Audit、State/Timeline 更新和 Acceptance Commit 不会被绕过。

`novel canon show --path ...` 是当前推荐的 Canon 查看命令；如旧脚本仍使用兼容别名，应逐步改为该命令。

## Session 工作流

```bash
novel session start "写第一章雨夜开场" --path ./qingdeng-inn --chapters 1 --provider mock
novel session revise-outline <session_id> --path ./qingdeng-inn --instruction "加强悬疑钩子" --provider mock
novel session approve-outline <session_id> --path ./qingdeng-inn
novel session run <session_id> --path ./qingdeng-inn --provider mock
novel session revise-content <session_id> --path ./qingdeng-inn --instruction "按审核意见修订" --provider mock
novel session accept <session_id> --path ./qingdeng-inn
novel session archive <session_id> --path ./qingdeng-inn
```

Web UI 与 CLI 都把 typed command 交给同一 Command Bus。CLI 只负责输入和结果格式化，Web 只负责 HTTP envelope 与界面展示。

多章 Session 在接受前使用 `memory/sessions/<session_id>/projection/` 中的 state/timeline 投影。`session accept` 会在一个 transaction journal 中提交全部章节、canonical state/timeline、Chapter Memory 和 acceptance；不要用逐章接受代替多章 Session 提交。

## Accepted 章节局部修订

```bash
novel revision-session blocks 1 --path ./qingdeng-inn
novel revision-session start 1 --blocks 2-3 --instruction "让雨声和脚步声更紧迫" --path ./qingdeng-inn
novel revision-session show <revision_session_id> --path ./qingdeng-inn
novel revision-session run <revision_session_id> --path ./qingdeng-inn --provider mock
novel revision-session accept <revision_session_id> --path ./qingdeng-inn
```

只可对已认可且仍为 canonical 最新章节的正文创建局部修订。`blocks` 返回稳定 block 编号、类型、预览和 hash；`start` 冻结授权范围；`run` 生成 structured patch、合成完整 candidate 并重新 Audit/构建 state projection；`accept` 事务性提交同一 candidate。Creation Session 不再接受 `--chapter` 或 `--segments`。

## 设定变更和项目管家

```bash
novel ask "第2章 event_wrong_current 其实是回忆，不是当前行动" --path ./qingdeng-inn --provider mock
novel ask "第2章 event_wrong_current 其实是回忆，不是当前行动" --path ./qingdeng-inn --provider mock --confirm
novel memory-repair suggest "修正 timeline 中的错误事件定位" --path ./qingdeng-inn --provider mock
novel memory-repair apply ./qingdeng-inn/memory/repairs/repair_xxx/proposal.json --path ./qingdeng-inn
novel setting-change suggest "新增人物沈微" --path ./qingdeng-inn --provider mock
novel setting-change answer <clarification_id> --answer "目标是 char_lin_che" --path ./qingdeng-inn --provider mock
novel setting-change apply ./qingdeng-inn/memory/repairs/repair_xxx/proposal.json --path ./qingdeng-inn
```

设定变更和 memory repair 都先生成 proposal，不直接修改正式 memory；只有显式 apply 才会写入文件。失败时会写 apply log，并尝试从备份回滚。

`novel ask` 默认只输出 strict `CommandProposal`，包含 command、风险、预计模型调用次数、确认要求和 `WorkflowBudget`。只读低风险 command 可自动执行；创建 Session、生成 repair proposal 等有成本动作，以及 apply、accept、Production Export 等高风险动作，都要在确认 proposal 后添加 `--confirm`。可用 `--max-chapters`、`--max-agent-calls`、`--max-provider-attempts`、`--max-auto-revision-rounds`、`--max-input-tokens` 和 `--max-output-tokens` 限制整个 workflow。proposal 与 intent-router 调用会写入 `runs/{workflow_run_id}/`；`--dry-run` 只阻止 command 执行和 domain artifact 写入，不关闭审计 trace。

## 搜索与上下文

```bash
novel index rebuild --path ./qingdeng-inn
novel index rebuild --path ./qingdeng-inn --with-embeddings
novel search "青灯 剑客" --path ./qingdeng-inn
novel search "青灯 剑客" --path ./qingdeng-inn --use-vector
```

默认使用 FTS 关键词检索。Embedding 语义检索需要先配置 `config/embeddings.yaml` 和对应 API Key 环境变量。

## 导出和用量

```bash
novel export markdown --path ./qingdeng-inn --force
novel export docx --path ./qingdeng-inn --force
novel usage --path ./qingdeng-inn
novel usage --path ./qingdeng-inn --json
```

正式导出只包含存在 fresh `acceptance.json` 的 `accepted.md`。导出时会重新计算正文、candidate、Audit、State Proposal 与 acceptance artifact 的 hash；任一 lineage 边 stale 都会拒绝导出。`--include-unaccepted` 已删除，未接受正文不能进入正式导出。

未接受 working candidate 使用独立 Preview Package：

```bash
novel preview package --path ./qingdeng-inn --chapters 1,2 --source polished
novel preview package --path ./qingdeng-inn --from 1 --to 3 --source draft
```

Preview 固定输出到 `exports/previews/<preview_id>/preview.md` 与 `manifest.json`，manifest 明确包含 `package_kind=preview`、`production_eligible=false`。该命令不会读取、创建或更新 `exports/export_manifest.json`。

## Web UI

```bash
novel web --path ./qingdeng-inn --host 127.0.0.1 --port 8765
novel web --path ./qingdeng-inn --open
```

安装器生成的 `WriterYang_WebUI.command` 使用 `WriterYang_WebUI.config.json` 保存下次启动端口。普通 `novel web --path ...` 的默认端口来自项目 `project.yaml.web.default_port`，命令行 `--port` 会覆盖它。

## 本地检查脚本

```bash
python scripts/check_local.py
python scripts/smoke_session.py --path ./qingdeng-inn
python scripts/debug_bundle.py --root ./qingdeng-inn --output ./debug-bundle
python scripts/provider_ping.py --path ./qingdeng-inn
```

`debug_bundle.py` 收集的包可能包含小说正文、隐藏设定和作者指令，不应提交到 Git 或发送给无关人员。
