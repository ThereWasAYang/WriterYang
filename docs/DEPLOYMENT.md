# 部署与运行说明

> WriterYang 0.1.x 是本地桌面/命令行软件，不是公网 Web 服务。

## 支持环境

- CPython 3.11、3.12、3.13；
- Linux 为完整 CI 主矩阵，macOS 有文件系统与 Web 安全 smoke；
- Windows 提供安装脚本，但当前没有完整 CI 承诺，使用前应自行执行本地质量门禁。

## 安装

开发安装：

```bash
python -m pip install -c requirements/constraints.txt -e ".[dev]"
python scripts/check_local.py --keep-going
```

用户安装可使用 README 中的平台安装脚本，或从已验证 wheel 安装。安装后运行：

```bash
novel --version
novel doctor --project .
```

## 本地 Web

```bash
novel web --path ./my-novel --host 127.0.0.1 --port 8765
```

服务只能绑定 loopback。若端口冲突，选择其他端口；不要使用端口转发、反向代理或容器映射把它暴露到局域网/互联网。当前版本没有账号、Bearer token、TLS、CSRF public-mode 或 project root allowlist，因此不支持公开部署。

停止服务使用 `Ctrl-C`；命令会输出简洁停止提示，不显示 traceback。

## 数据与备份

小说 workspace 是部署数据本身。备份前停止写操作，至少保存 `project.yaml`、`config/`、`memory/` 与需要的 `exports/`。`runs/` 可用于诊断但不是恢复小说事实的必要来源。

项目 `.env` 可能包含明文 API Key。备份到云盘、NAS 或外置介质前应评估该介质权限；共享备份时必须移除 `.env` 和 `*.bak_*`。恢复后运行 `novel validate` 和 `novel doctor`。

## 升级与回滚

项目仍处于 alpha，当前 schema 不承诺历史兼容。升级前备份 workspace；若 release notes 声明 breaking change，按该版本说明处理。代码回滚不等同于 workspace schema 回滚，不要用旧版本直接打开已被新版本写入的工作区。

## 发布门禁

发布至少执行 Ruff、Mypy、coverage、离线 pytest、Web E2E、dependency audit、secret scan、build、twine check、fresh wheel schema/resource smoke。具体命令见 `RELEASE.md`。
