# 安全策略与威胁模型

> 适用范围：WriterYang 0.1.x 本地单用户版本；最近核验：2026-07-18

## 安全边界

WriterYang 假设操作系统账户、小说项目目录和本机进程可信。它不假设模型输出、Provider 响应、workspace 自由文本或浏览器请求头可信。产品不提供多用户隔离和公网服务能力。

主要风险包括：Provider 密钥泄漏、路径逃逸、模型输出污染 Canon、恶意 workspace prompt injection、并发写损坏、超大/异常 Provider 响应、公开暴露本地 Web，以及依赖供应链风险。

## API Key 决策

根据项目所有者的明确决定，WriterYang 允许在项目根目录 `.env` 明文保存 API Key，以换取本地维护和查询便利。这是接受的本地产品风险，不再被视为待迁移缺陷。

已有保护：`.env*` 和备份不被 Git 跟踪；Web 文件树、读文件 API、日志、trace、导出和错误响应排除/脱敏密钥；Setup 尽量设置 `0600`；YAML 只保存环境变量名。

残余风险由使用者承担：拥有项目目录读权限的本机进程、云同步软件和备份系统仍可读取明文；备份会增加副本数量；`0600` 在部分平台可能不完全等价。高敏感使用者应只通过进程环境变量注入，不使用 Setup 落盘，并清理既有 `.env`/备份。

## Web 安全

- 普通模式在建 socket 前强制 loopback host；
- API 请求必须具有合法本机 Host；Origin 只作为额外 CSRF 信号，不作为认证；
- 不读取 `X-Forwarded-*` 来扩大信任；
- workspace 文件访问执行 resolve/relative allowlist，并排除 `.env`、备份、索引和隐藏文件；
- 未知异常返回 HTTP 500、通用消息和 request ID，stack 只写本地日志。

不要通过 SSH tunnel、端口映射或反向代理公开当前 Web UI。如未来增加 remote mode，必须先引入显式认证、TLS/代理指南、CSRF 防护、project root allowlist 与认证失败审计。

## 模型与数据完整性

所有 inter-agent 输出经 strict schema；正文结构化 envelope 校验后才确定性渲染。workspace 文本以 untrusted data delimiter 注入。高风险路由使用 structured decision、confidence gate、scope validation 和保守 fallback。

正式接受和导出要求 Audit、artifact lineage 与 hash fresh；state/timeline 通过 Session projection 和 transaction journal 更新，避免模型输出直接写 Canon。

## 资源与可观测性

Provider 响应有字节上限，SSE 增量解析并支持取消；重试遵守 `Retry-After` 或使用 exponential backoff + jitter。JSONL 使用加锁 EventWriter、轮转与 retention。默认日志不保存正文、prompt 或 hidden truth；`WRITERYANG_MODEL_IO_MODE=full` 会扩大隐私风险，只应短时诊断使用。

## 供应链

支持 Python 版本有上界；`requirements/constraints.txt` 固定已验证直接工具链；Dependabot 提交升级 PR；CI 运行 `pip-audit`，GitHub Actions 固定到完整 commit SHA。任何漏洞忽略都必须记录原因、影响范围和过期时间。

## 漏洞报告

不要在公开 issue 中提交真实 API Key、完整 `.env`、未脱敏 Provider 响应或小说隐私正文。报告应包含版本、操作系统、最小复现步骤、预期/实际行为和已脱敏 request ID。确认泄漏后应先吊销并轮换相关密钥，再清理本地/云端备份。
