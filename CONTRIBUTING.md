# 贡献指南

## 开发环境

需要 Python 3.11 或更高版本（本项目的下限是 3.11，请不要使用 3.12 才有的语法）。

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## 运行检查

```bash
ruff check .
ruff format --check .
pytest
```

三项都必须通过。提交前请先本地跑一遍。CI 会在 Python 3.11 与 3.12 上跑同样的命令。

## 代码约定

- 运行期依赖只有 `aiogram` 与 `httpx`。新增依赖需要先讨论。
- RSS 解析用标准库 `xml.etree.ElementTree`，不要引入 feedparser。
- 面向用户的消息文本用中文；标识符、注释、docstring 用英文。
- 数据库里的时间戳一律是 ISO 8601 UTC 字符串。
- 测试不得访问网络或真实的 Telegram API：用假实现注入（见
  `tests/test_notifier.py` 里的 `FakeBot` 与 `tests/test_poller.py` 里的 `FakeSource`）。

## 提交规范

提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

- `feat:` 新功能
- `fix:` 修 bug
- `docs:` 文档
- `refactor:` 重构
- `test:` 测试
- `chore:` 杂项

## 提交前自检

- 新增功能必须带测试。
- 不得提交 `config.toml`、`.env` 或任何数据库文件（`.gitignore` 已经覆盖）。
- 改动部署流程时同步更新 `docs/deployment.md`。
