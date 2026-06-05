## 变更摘要

- 

## 测试

- [ ] `pytest -m "not real_api" -q`
- [ ] `ruff check .`
- [ ] `mypy src scripts`
- [ ] `python -m build`
- [ ] `twine check dist/*`

## 文件安全

- [ ] 未提交 API Key、token、cookie、`.env.real`
- [ ] 写文件行为保持默认不覆盖
- [ ] 如修改示例项目，已运行 `novel validate`

## 文档

- [ ] README/docs/examples 已同步
- [ ] 新命令或新配置已说明中文用法
