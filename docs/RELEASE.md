# 发布检查清单

发布 WriterYang 前请按本清单检查。

推广初期发布仅面向 macOS / Linux。Windows 适配暂缓，发布说明和用户文档不得把 Windows 写成已验收支持平台。

## 0. schema v3 破坏性变化

- 只接受 schema v3 工作区；不发布 migration 工具。
- 核对 CLI/Web 不再暴露绕过 Acceptance、Audit 或 Polish policy 的历史参数。
- 用至少两章的 mock Session 验证 projection checkpoint、全 Session transaction acceptance 和 production export lineage。
- 修改一个已接受 candidate/`accepted.md` 字节，确认正式导出会拒绝 stale 内容。
- 检查 `transactions/` 中不存在未恢复的 PREPARED/APPLYING journal。

## 1. 版本号

确认以下位置版本一致：

- `pyproject.toml` 的 `[project].version`
- `src/novel/__init__.py` 的 `__version__`

运行：

```bash
novel --version
```

## 2. 测试

建议在干净的独立 Python 环境中执行发布检查。当前支持 Python 3.11-3.13，发布机推荐使用 3.12：

```bash
conda create -n writeryang-release "python>=3.11,<3.14" -y
conda activate writeryang-release
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

运行全量测试：

```bash
pytest
ruff check .
mypy src scripts
pytest -m web_e2e -q
```

测试不能依赖真实 API Key。`mypy src scripts` 是阻断式类型门禁；发布前必须保持 0 errors。

## 3. Windows 适配重启前置清单

当前推广期不支持 Windows。后续重启 Windows 适配时，至少完成下列运行期修复和验收后，才允许在 README、Quickstart 或 Release Notes 中声明 Windows 可用：

- 修复 `core/locking.py` 的持锁进程探活逻辑：Windows 上不要用 `os.kill(pid, 0)` 探活，应改为基于 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` 等只查询不终止的实现，并覆盖持锁进程存活、进程退出和 PID 复用场景。
- 将 Web 启动器写入收敛到 `core/web_launcher.py` 单一信源，按平台分发 `.command` / `.bat` / `.ps1` 或等价入口；保存端口时不得用 bash-only 写入器覆盖 Windows 启动器。
- 恢复并验收 Windows 安装入口，优先保证无需调整 PowerShell 执行策略的 `install.bat` 路径；`install.ps1` 只能作为明确说明前置条件的补充入口。
- 修复安装器激活脚本中 `nt` 分支的环境 PATH 拼接，确保虚拟环境目录前缀正确。
- 增加 `windows-latest` CI，并至少覆盖安装、CLI smoke、Web 端口保存、启动器生成和锁文件恢复测试。

## 4. API Key 安全

发布前搜索是否误提交了真实密钥：

```bash
python -c "from pathlib import Path; from novel.core.security import scan_security; r=scan_security(Path('.')); assert r.ok, [(f.code, str(f.path), f.line) for f in r.findings]"
```

项目文件可以包含 `OPENAI_API_KEY` 这类环境变量名，但不能包含真实 key 值。`.env.example` 只能包含空值变量名，例如 `OPENAI_API_KEY=`。

## 5. 初始化模板

验证当前 `novel init` 模板：

```bash
tmp_project="$(mktemp -d)/writeryang-template"
novel init "模板校验" --path "$tmp_project" --no-guide
novel validate --path "$tmp_project"
novel status --path "$tmp_project"
```

## 6. 构建包

安装开发依赖并构建：

```bash
python -m pip install -e ".[dev]"
python -m build
python -m twine check dist/*
```

预期产物：

```text
dist/writeryang-<version>.tar.gz
dist/writeryang-<version>-py3-none-any.whl
```

## 7. 本地安装冒烟测试

创建干净虚拟环境，安装 wheel，然后运行：

```bash
python -m pip install dist/writeryang-0.1.0-py3-none-any.whl
novel --version
novel init "Smoke Test" --path /tmp/writeryang-smoke
novel validate --path /tmp/writeryang-smoke
```

## 8. Release Notes

发布前记录：

- 版本号。
- 发布日期。
- 新增功能。
- 破坏性变更，如有。
- 已知限制。

## 9. 发布

当前默认发布目标是 GitHub Release。

1. 确认 `CHANGELOG.md` 中对应版本条目完整。
2. 确认 `pyproject.toml` 和 `src/novel/__init__.py` 版本一致。
3. 本地运行测试、Web E2E、lint、type check、build 和 `twine check`。
4. 创建 tag：

```bash
git tag v0.1.1
git push origin v0.1.1
```

5. GitHub Actions 的 `Release` workflow 会重新运行 secret scan、非真实 API 测试、Web E2E、lint、type check、build，并把 `dist/*` 上传到 GitHub Release。

本仓库暂不配置 PyPI token 或 Trusted Publishing。需要 PyPI 发布时，再单独增加发布目标和平台侧授权。
