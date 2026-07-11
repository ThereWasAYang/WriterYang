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
novel plan-chapter 1 --path ./qingdeng-inn --provider mock
novel write-chapter 1 --path ./qingdeng-inn --provider mock
novel polish-chapter 1 --path ./qingdeng-inn --provider mock
novel audit-chapter 1 --path ./qingdeng-inn --provider mock
novel accept-chapter 1 --path ./qingdeng-inn
```

一键生成流水线：

```bash
novel generate-chapter 1 --path ./qingdeng-inn --provider mock
novel generate-chapter 1 --path ./qingdeng-inn --provider config --auto-accept
```

`accept-chapter` 前必须通过 consistency audit。章节归档后会写入 metadata，并更新项目状态和时间线相关记录。

`novel canon show --path ...` 是当前推荐的 Canon 查看命令；如旧脚本仍使用兼容别名，应逐步改为该命令。

## Session 工作流

```bash
novel session start --path ./qingdeng-inn --chapters 1 --intent "写第一章雨夜开场" --provider mock
novel session revise-outline <session_id> --path ./qingdeng-inn --instruction "加强悬疑钩子" --provider mock
novel session approve-outline <session_id> --path ./qingdeng-inn
novel session run <session_id> --path ./qingdeng-inn --provider mock
novel session revise-content <session_id> --path ./qingdeng-inn --instruction "按审核意见修订" --provider mock
novel session accept <session_id> --path ./qingdeng-inn
novel session archive <session_id> --path ./qingdeng-inn
```

Web UI 的创作工作台使用同一套 core session logic。CLI 适合自动化、调试和外部 Agent 调用。

多章 Session 在接受前使用 `memory/sessions/<session_id>/projection/` 中的 state/timeline 投影。`session accept` 会在一个 transaction journal 中提交全部章节、canonical state/timeline、Chapter Memory 和 acceptance；不要用逐章接受代替多章 Session 提交。

## 设定变更和项目管家

```bash
novel ask "第2章 event_wrong_current 其实是回忆，不是当前行动" --path ./qingdeng-inn --provider mock
novel memory-repair suggest "修正 timeline 中的错误事件定位" --path ./qingdeng-inn --provider mock
novel memory-repair apply ./qingdeng-inn/memory/repairs/repair_xxx/proposal.json --path ./qingdeng-inn
novel setting-change suggest "新增人物沈微" --path ./qingdeng-inn --provider mock
novel setting-change answer <clarification_id> --answer "目标是 char_lin_che" --path ./qingdeng-inn --provider mock
novel setting-change apply ./qingdeng-inn/memory/repairs/repair_xxx/proposal.json --path ./qingdeng-inn
```

设定变更和 memory repair 都先生成 proposal，不直接修改正式 memory；只有显式 apply 才会写入文件。失败时会写 apply log，并尝试从备份回滚。

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
