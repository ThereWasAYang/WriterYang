# 贡献指南

感谢你愿意改进 WriterYang。本项目初期主要面向中文长篇小说作者，issue、PR、文档和示例优先使用中文；代码标识、协议名、模型名等专业术语可以保留英文。

## 开发环境

推荐创建独立 Python 3.12 环境：

```bash
conda create -n writeryang-dev python=3.12 -y
conda activate writeryang-dev
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

常用检查：

```bash
pytest -m "not real_api" -q
ruff check .
mypy src
python -m build
python -m twine check dist/*
```

当前 `mypy src` 会真实输出类型问题，但 CI 中暂时是 informational 非阻断检查；本地一键检查默认也不因 mypy 失败而失败。需要把 mypy 作为本地硬门禁时，运行：

```bash
python scripts/check_local.py --strict-mypy
```

真实 API 测试必须显式标记为 `real_api`，不能作为普通测试的依赖。

## 代码原则

- CLI、Web API 和 Web UI 必须复用 `src/novel/core/` 中的核心逻辑。
- Agent 输出优先使用 Pydantic schema 和 JSON/YAML/Markdown 文件，不把状态藏在数据库里。
- 写文件默认不覆盖；需要覆盖时必须有明确参数、atomic write、必要备份和项目锁。
- 不提交 API Key、token、cookie 或 `.env.real`。配置文件只能保存环境变量名。
- 修改 prompt、schema、provider、state/timeline 逻辑时必须补测试。

## 文档和示例

- 面向作者和普通使用者的文档尽量用中文。
- 示例项目必须能通过 `novel validate --path examples/<name>`。
- `AGENTS.md` 以及 `docs/PRODUCT_SPEC.md`、`docs/ARCHITECTURE.md`、`docs/DATA_SCHEMA.md`、`docs/WORKFLOW.md`、`docs/ROADMAP.md` 是内部开发上下文，不应进入发布文档。

## Pull Request

提交 PR 前请确认：

- 已运行非真实 API 测试。
- 已运行 lint/type check；注意 mypy 当前为真实但非阻断检查。
- 没有提交本地密钥、缓存、构建产物或项目锁。
- README、docs、examples 与实际 CLI 命令一致。
