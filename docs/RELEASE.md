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

运行全量测试：

```bash
conda run -n py312 pytest
```

测试不能依赖真实 API Key。

## 3. API Key 安全

发布前搜索是否误提交了真实密钥：

```bash
rg "sk-[A-Za-z0-9_-]+" .
rg "api_key:|OPENAI_API_KEY=.*|WRITER_API_KEY=.*" .
```

项目文件可以包含 `OPENAI_API_KEY` 这类环境变量名，但不能包含真实 key 值。

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

当前发布流程尚未自动化。请在本地冒烟测试通过后，再按项目选定的包仓库流程发布。
