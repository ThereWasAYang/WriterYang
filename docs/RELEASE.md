# 发布检查清单

发布 WriterYang 前请按本清单检查。

## 1. 版本号

确认以下位置版本一致：

- `pyproject.toml` 的 `[project].version`
- `src/novel/__init__.py` 的 `__version__`

运行：

```bash
novel --version
```

## 2. 测试

建议在干净的独立 Python 3.12 环境中执行发布检查：

```bash
conda create -n writeryang-release python=3.12 -y
conda activate writeryang-release
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

运行全量测试：

```bash
pytest
ruff check .
mypy src
```

测试不能依赖真实 API Key。

## 3. API Key 安全

发布前搜索是否误提交了真实密钥：

```bash
python -c "from pathlib import Path; from novel.core.security import scan_security; r=scan_security(Path('.')); assert r.ok, [(f.code, str(f.path), f.line) for f in r.findings]"
```

项目文件可以包含 `OPENAI_API_KEY` 这类环境变量名，但不能包含真实 key 值。`.env.example` 只能包含空值变量名，例如 `OPENAI_API_KEY=`。

## 4. 示例项目

验证内置示例：

```bash
novel validate --path examples/rain_station
novel status --path examples/rain_station
```

## 5. 构建包

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

## 6. 本地安装冒烟测试

创建干净虚拟环境，安装 wheel，然后运行：

```bash
python -m pip install dist/writeryang-0.1.0-py3-none-any.whl
novel --version
novel init "Smoke Test" --path /tmp/writeryang-smoke
novel validate --path /tmp/writeryang-smoke
```

## 7. Release Notes

发布前记录：

- 版本号。
- 发布日期。
- 新增功能。
- 破坏性变更，如有。
- 已知限制。

## 8. 发布

当前默认发布目标是 GitHub Release。

1. 确认 `CHANGELOG.md` 中对应版本条目完整。
2. 确认 `pyproject.toml` 和 `src/novel/__init__.py` 版本一致。
3. 本地运行测试、lint、type check、build 和 `twine check`。
4. 创建 tag：

```bash
git tag v0.1.1
git push origin v0.1.1
```

5. GitHub Actions 的 `Release` workflow 会重新运行 secret scan、测试、lint、type check、build，并把 `dist/*` 上传到 GitHub Release。

本仓库暂不配置 PyPI token 或 Trusted Publishing。需要 PyPI 发布时，再单独增加发布目标和平台侧授权。
