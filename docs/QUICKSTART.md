# 新手快速开始

本教程用 `mock` provider 跑完整测试流程，不需要真实 API Key。`mock` 只适合离线熟悉工具和自动化测试；真实小说项目应先配置 `config/agents.yaml` 顶层 `default` API，再使用 `--provider config`。本教程的目标是在 10 分钟内理解 WriterYang 的工作方式：所有中间结果都写入本地 Markdown / JSON / YAML 文件，作者可以随时检查和手动编辑。

## 1. 创建独立环境并安装

建议先使用一键脚本创建独立环境并安装：

```bash
./install.sh
```

脚本安装完成后会以 editable 模式安装当前源码目录，并自动寻找可用端口，打印 Web UI 地址并弹出浏览器。默认从 `8765` 开始；端口被占用会自动换下一个。Web server 会在当前终端前台运行，按 `Ctrl+C` 停止。

editable 模式的含义是：之后你更新 WriterYang 源码后，只需要重启 Web UI，就会加载新版本。如果你之前用旧脚本安装过，Web UI 一直显示旧界面，可以重新运行 `./install.sh`，或进入旧环境后执行 `python -m pip install -e .`。

安装器还会生成 `WriterYang_WebUI.command` 和同目录的 `WriterYang_WebUI.config.json`。以后可以直接双击启动器打开 Web UI，它会固定使用本次安装创建的新环境，并从 config 文件读取下次启动端口。Web UI 中保存端口会先验证端口可用，再更新这个 config 文件；如果下次启动时端口被占用，启动器会临时改用下一个空闲端口并提醒你重新保存端口。Web server 停止后，终端会进入一个已激活新环境的子 shell；后续 `novel ...` 命令默认使用这个新环境，输入 `exit` 回到原终端。想指定安装时的起始端口可用：

```bash
./install.sh --web-port 9000
```

如果只想安装，不想立即打开 Web UI：

```bash
./install.sh --no-web
```

如果不想安装器进入新环境子 shell：

```bash
./install.sh --no-activate-shell
```

如果你是开发者，需要测试、lint、mypy、build 等工具：

```bash
python scripts/install_writeryang.py --dev
```

如果你仍在安装器进入的新环境子 shell 中，可以直接检查：

```bash
novel --version
novel doctor
```

如果已经退出子 shell，则先按脚本输出的 `conda activate ...` 激活环境，或使用 `conda run -n <环境名> novel ...`。

也可以手动创建一个新的 Python 3.12 环境：

```bash
conda create -n writeryang python=3.12 -y
conda activate writeryang
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
novel --version
```

不使用 conda 的用户可以改用 `python3.12 -m venv .venv`，激活后再运行同样的 `python -m pip install -e ".[dev]"`。

## 2. 创建项目

```bash
novel init "青灯客栈" --path ./qingdeng-inn --no-guide
```

本教程使用 `mock` provider，因此跳过真实 API 初始引导。真实项目可以直接运行 `novel init "书名" --path ./my-novel`，按提示配置默认 API、可选 embedding 和 CLI Web UI 默认端口。

生成目录包括：

- `project.yaml`：作品基本信息。
- `config/agents.yaml`：各 agent 的模型配置，只保存环境变量名；真实项目必须配置顶层 `default` API。
- `memory/inspiration.md`：灵感和弱总纲。
- `memory/canon/*.json`：人物、地点、物品、世界规则、隐藏真相和伏笔。
- `memory/state/*.json`：当前状态和时间线。
- `memory/chapters/`：章节计划、初稿、润色稿、审核报告。
- `runs/`：运行日志。
- `exports/`：导出结果。

## 3. 写入灵感

```bash
novel inspire "雨夜客栈里，一盏青灯忽然照出二十年前失踪剑客的影子。" --path ./qingdeng-inn --provider mock --overwrite
```

检查文件：

```bash
cat ./qingdeng-inn/memory/inspiration.md
```

## 4. 生成并应用 canon

```bash
novel canon suggest --path ./qingdeng-inn --provider mock --output ./qingdeng-canon.json
novel canon apply ./qingdeng-canon.json --path ./qingdeng-inn
novel canon show --path ./qingdeng-inn
```

`suggest` 默认只生成 proposal；`apply` 才会写入正式 canon 文件。

## 5. 生成章节

```bash
novel plan-chapter 1 --path ./qingdeng-inn --provider mock
novel write-chapter 1 --path ./qingdeng-inn --provider mock
novel polish-chapter 1 --path ./qingdeng-inn --provider mock
novel audit-chapter 1 --path ./qingdeng-inn --provider mock
```

也可以一键跑流水线：

```bash
novel generate-chapter 1 --path ./qingdeng-inn --provider mock --force
```

## 6. 接受章节并更新状态

只有审核通过或你明确允许问题继续时，才建议接受章节。

```bash
novel propose-state-update 1 --path ./qingdeng-inn --provider mock
novel apply-state-update 1 --path ./qingdeng-inn
novel accept-chapter 1 --path ./qingdeng-inn --allow-issues
```

## 7. 导出 Markdown

```bash
novel export markdown --path ./qingdeng-inn --include-unaccepted --toc --force
```

默认导出到 `exports/novel.md`，同时更新 `exports/export_manifest.json`。

## 8. 常用检查

```bash
novel validate --path ./qingdeng-inn
novel status --path ./qingdeng-inn
novel doctor --project ./qingdeng-inn
```

如果你手动改了 `memory/` 里的文件，至少运行一次 `validate`。
