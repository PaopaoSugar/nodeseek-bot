# NodeSeek 关键词监控 Bot 实施计划

> **For agentic workers:** 按任务顺序实施。每个步骤用 `- [ ]` 复选框跟踪，完成一步勾一步。
> 每个任务结束时都必须产出一个可独立验证的成果。

**Goal:** 实现一个可自托管的 Telegram Bot，监控 NodeSeek 新主题，命中用户关键词后私聊推送。

**Architecture:** 单进程 asyncio，`poller` / `matcher` / `notifier` / `bot` 四个 task 通过内存队列与 SQLite 解耦。SQLite 同时承担持久化和投递队列两个角色，不引入 Redis。

**Tech Stack:** Python 3.11+、aiogram 3、httpx、SQLite（标准库）、pytest、ruff

**Spec:** `docs/superpowers/specs/2026-09-22-nodeseek-tg-bot-design.md`

## Global Constraints

- Python >= 3.11（`tomllib`、`asyncio.TaskGroup` 可用）
- 运行期依赖仅限 `aiogram>=3.4,<4` 与 `httpx>=0.27,<1`。禁止引入 feedparser、pydantic、SQLAlchemy、Redis
- RSS 解析使用标准库 `xml.etree.ElementTree`
- 数据库中所有时间戳为 ISO 8601 UTC 字符串
- 面向用户的消息文本用中文；标识符、注释、docstring 用英文
- ruff 行宽 100，全量类型注解
- 测试与 CI 不得访问网络或真实 Telegram API
- 提交信息遵循 Conventional Commits
- 配置键名与 spec 第 12 节完全一致

**仓库状态：** 当前目录**尚未初始化为 git 仓库**，用户稍后会自行创建并上传。
在仓库建立之前，**跳过每个任务的「提交」步骤**，以该任务最后一步的「验证通过」
作为完成判据。仓库建立时，第一次提交应是已完成内容的整体快照。

**本地开发环境（已实测，有坑）：**

- 开发机是 Windows，**没有安装 Python**。可用的解释器是 Codex 运行时自带的
  `C:\Users\BUBBLES\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`（3.12.14）。
- **不要用 `python -m venv`。** 项目路径含中文（`D:\codex\助手`），
  venv 调起 `ensurepip` 子进程时路径会被编码搞坏，必然失败。
- 改用 `--target` 把依赖装进项目内的 `.deps` 目录，配合 `PYTHONPATH` 使用：

      $PY = "C:\Users\BUBBLES\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
      & $PY -m pip install --target .deps pytest pytest-asyncio ruff httpx aiogram
      $env:PYTHONPATH = "$PWD\.deps"

- **本机跑验证的固定命令（已实测可用）：**

      & $PY -m pytest -q -p win_tmpfix --basetemp=.deps\pytest-tmp
      & $PY -m ruff check .
      & $PY -m ruff format --check src tests

- **`-p win_tmpfix` 不能省。** `.deps\win_tmpfix.py` 是本地专用插件（不进仓库，`.deps` 已被
  gitignore）：本机沙箱用真实 ACL 模拟 POSIX 权限，pytest 用 `mkdir(mode=0o700)` 建的
  `tmp_path` 会变成连属主都读不了的目录；插件把它强制成 `0o777`。Linux 服务器与 CI 不需要它。
- **版本差异风险：** 本机 3.12，目标服务器 3.11.2。
  本地测试全绿不等于服务器能跑，代码不得使用 3.12 才有的语法。
  上线前必须在服务器上跑一次完整测试。

## Review Focus

spec 没有明说、但最可能咬到真实使用者的五类输入。每一条都必须在对应任务里有测试兜住：

1. NodeSeek 返回非 XML（维护页 HTML、空响应、限流页）→ 记为一次轮询失败并退避，不得崩溃，不得删除已有数据
2. 标题含 HTML 特殊字符 `&` `<` `>` → 必须转义后才发送，否则 Telegram 返回 400
3. 单条 item 缺 `<guid>` / `<pubDate>` / `<description>` → 缺 guid 或 pubDate 跳过该条，其余字段缺失置空，不得整轮失败
4. 合并后消息超过单条上限或标题极长 → 必须按 `max_message_chars` 分批，不得发送失败
5. 用户输入空规则、重复规则、超出上限 → 必须给出明确中文反馈，不得静默失败

---

## Task 1: 仓库骨架与工具链

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.editorconfig`, `.python-version`, `LICENSE`, `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`
- Create: `.github/workflows/ci.yml`, `.github/ISSUE_TEMPLATE/bug_report.yml`, `.github/ISSUE_TEMPLATE/feature_request.yml`, `.github/PULL_REQUEST_TEMPLATE.md`
- Create: `src/nodeseek_bot/__init__.py`, `tests/test_smoke.py`

**Interfaces:**
- Produces: `nodeseek_bot.__version__: str`

- [ ] **Step 1: 写失败的测试**

`tests/test_smoke.py`

```python
from nodeseek_bot import __version__


def test_version_is_exposed() -> None:
    assert __version__ == "0.1.0"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_smoke.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'nodeseek_bot'`

- [ ] **Step 3: 创建包与 pyproject**

`src/nodeseek_bot/__init__.py`

```python
"""NodeSeek keyword monitor bot."""

__version__ = "0.1.0"
```

`pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "nodeseek-bot"
version = "0.1.0"
description = "Telegram bot that watches NodeSeek new topics and pushes keyword matches"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
authors = [{ name = "YOUR NAME" }]
keywords = ["nodeseek", "telegram", "rss", "bot"]
classifiers = [
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "License :: OSI Approved :: MIT License",
]
dependencies = [
    "aiogram>=3.4,<4",
    "httpx>=0.27,<1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "ruff>=0.5",
]

[project.scripts]
nodeseek-bot = "nodeseek_bot.__main__:main"

[tool.hatch.build.targets.wheel]
packages = ["src/nodeseek_bot"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 4: 补齐仓库文件**

`.gitignore`

```
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
dist/
build/
*.egg-info/
config.toml
.env
data/
.deps/
.tmp/
*.db
*.db-wal
*.db-shm
```

`.editorconfig`

```
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 4

[*.{yml,yaml,toml}]
indent_size = 2

[*.md]
trim_trailing_whitespace = false
```

`.python-version` 内容为 `3.11`。

`LICENSE` 填入 MIT 正文（`Copyright (c) 2026 YOUR NAME`）。

`CHANGELOG.md`

```markdown
# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]
```

`CONTRIBUTING.md`：写明开发环境安装（`python -m venv .venv`、`pip install -e ".[dev]"`）、
运行 `ruff check .` 与 `pytest`、提交信息用 Conventional Commits。

`SECURITY.md`：写明漏洞请勿开公开 issue，改用 GitHub Security Advisory；
Bot token 泄露应立即在 @BotFather 执行 `/revoke`。

`README.md`：本任务只放标题、一句话简介、许可证占位，正文在 Task 15 补全。

- [ ] **Step 5: 创建 CI 与 GitHub 模板**

`.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: ruff format --check .
      - run: pytest -q
```

`.github/ISSUE_TEMPLATE/bug_report.yml`

```yaml
name: Bug 反馈
description: 报告一个问题
labels: [bug]
body:
  - type: textarea
    id: what-happened
    attributes:
      label: 发生了什么
      description: 请描述实际行为与预期行为
    validations:
      required: true
  - type: textarea
    id: logs
    attributes:
      label: 日志
      description: 请贴出相关日志（务必先删除 bot token）
    validations:
      required: false
  - type: input
    id: version
    attributes:
      label: 版本
    validations:
      required: true
```

`.github/ISSUE_TEMPLATE/feature_request.yml`

```yaml
name: 功能建议
description: 提出一个新功能
labels: [enhancement]
body:
  - type: textarea
    id: problem
    attributes:
      label: 你想解决什么问题
    validations:
      required: true
  - type: textarea
    id: proposal
    attributes:
      label: 你设想的方案
    validations:
      required: false
```

`.github/PULL_REQUEST_TEMPLATE.md`

```markdown
## 改动说明

## 关联 issue

## 自检

- [ ] `ruff check .` 通过
- [ ] `pytest` 通过
- [ ] 未提交任何密钥或 `config.toml`
```

- [ ] **Step 6: 安装并验证**

**服务器与 CI（标准路径）：**

    pip install -e ".[dev]"
    ruff check .
    ruff format --check .
    pytest -q

**本机开发（Windows，无 Python，不可用 venv，见「本地开发环境」）：**

    $PY = "C:\Users\BUBBLES\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    & $PY -m pip install --target .deps pytest pytest-asyncio ruff httpx aiogram
    $env:PYTHONPATH = "$PWD\.deps;$PWD\src"
    & $PY -m pytest -q
    & $PY -m ruff check .

Expected: 全部通过，1 passed

- [ ] **Step 7: 提交**

```bash
git add -A
git commit -m "chore: scaffold repository, tooling and CI"
```

---

## Task 2: 配置加载

**Files:**
- Create: `src/nodeseek_bot/config.py`, `config.example.toml`, `tests/test_config.py`

**Interfaces:**
- Consumes: 无
- Produces: `load_config(path: Path) -> Config`；异常 `ConfigError`；数据类
  `Config`（字段 `telegram` / `poller` / `notifier` / `limits` / `storage`）、
  `TelegramConfig`（`bot_token: str`、`admin_user_ids: list[int]`）、
  `PollerConfig`（`interval_seconds`、`request_timeout_seconds`、`max_backoff_seconds`、
  `overflow_threshold`、`user_agent`、`feed_url`）、
  `NotifierConfig`（`global_rate_per_second`、`max_attempts`、
  `coalesce_window_seconds`、`max_message_chars`）、
  `LimitsConfig`（`max_rules_per_user`、`invite_quota_per_user`）、
  `StorageConfig`（`db_path`、`retention_days`）

- [ ] **Step 1: 写失败的测试**

`tests/test_config.py`

```python
from pathlib import Path

import pytest

from nodeseek_bot.config import ConfigError, load_config

MINIMAL = """
[telegram]
bot_token = "123:abc"
admin_user_ids = [42]
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_are_applied(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, MINIMAL))
    assert cfg.telegram.bot_token == "123:abc"
    assert cfg.telegram.admin_user_ids == [42]
    assert cfg.poller.interval_seconds == 30
    assert cfg.poller.overflow_threshold == 15
    assert cfg.notifier.max_attempts == 3
    assert cfg.notifier.coalesce_window_seconds == 20
    assert cfg.limits.max_rules_per_user == 3
    assert cfg.limits.invite_quota_per_user == 3
    assert cfg.storage.retention_days == 14


def test_overrides_are_read(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            MINIMAL
            + """
[poller]
interval_seconds = 60

[limits]
max_rules_per_user = 5
""",
        )
    )
    assert cfg.poller.interval_seconds == 60
    assert cfg.limits.max_rules_per_user == 5
    assert cfg.poller.request_timeout_seconds == 20


def test_missing_bot_token_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="bot_token"):
        load_config(_write(tmp_path, "[telegram]\nadmin_user_ids = [1]\n"))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.toml")


def test_invalid_toml_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(_write(tmp_path, "this is not = = toml"))


def test_user_agent_is_not_empty(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, MINIMAL))
    assert cfg.poller.user_agent.startswith("Mozilla/5.0")


def test_feed_url_default(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, MINIMAL))
    assert cfg.poller.feed_url == "https://rss.nodeseek.com/"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_config.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'nodeseek_bot.config'`

- [ ] **Step 3: 实现**

`src/nodeseek_bot/config.py`

```python
"""Configuration loading."""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    bot_token: str
    admin_user_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PollerConfig:
    interval_seconds: float = 30.0
    request_timeout_seconds: float = 20.0
    max_backoff_seconds: float = 600.0
    overflow_threshold: int = 15
    user_agent: str = DEFAULT_USER_AGENT
    feed_url: str = "https://rss.nodeseek.com/"


@dataclass(frozen=True, slots=True)
class NotifierConfig:
    global_rate_per_second: float = 25.0
    max_attempts: int = 3
    coalesce_window_seconds: float = 20.0
    max_message_chars: int = 3500


@dataclass(frozen=True, slots=True)
class LimitsConfig:
    max_rules_per_user: int = 3
    invite_quota_per_user: int = 3


@dataclass(frozen=True, slots=True)
class StorageConfig:
    db_path: str = "data/bot.db"
    retention_days: int = 14


@dataclass(frozen=True, slots=True)
class Config:
    telegram: TelegramConfig
    poller: PollerConfig = field(default_factory=PollerConfig)
    notifier: NotifierConfig = field(default_factory=NotifierConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"section [{name}] must be a table")
    return value


def _build(cls: type, payload: dict[str, Any], section: str) -> Any:
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = set(payload) - known
    if unknown:
        raise ConfigError(f"unknown keys in [{section}]: {sorted(unknown)}")
    try:
        return cls(**payload)
    except TypeError as exc:
        raise ConfigError(f"invalid [{section}]: {exc}") from exc


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    telegram_payload = _section(data, "telegram")
    token = telegram_payload.get("bot_token")
    if not token or not isinstance(token, str):
        raise ConfigError("[telegram].bot_token is required")
    if ":" not in token:
        raise ConfigError("[telegram].bot_token does not look like a bot token")

    admin_ids = telegram_payload.get("admin_user_ids", [])
    if not isinstance(admin_ids, list) or not all(isinstance(i, int) for i in admin_ids):
        raise ConfigError("[telegram].admin_user_ids must be a list of integers")

    return Config(
        telegram=TelegramConfig(bot_token=token, admin_user_ids=list(admin_ids)),
        poller=_build(PollerConfig, _section(data, "poller"), "poller"),
        notifier=_build(NotifierConfig, _section(data, "notifier"), "notifier"),
        limits=_build(LimitsConfig, _section(data, "limits"), "limits"),
        storage=_build(StorageConfig, _section(data, "storage"), "storage"),
    )
```

`config.example.toml`

```toml
# 复制为 config.toml 后填写。config.toml 已被 .gitignore 忽略，切勿提交。

[telegram]
# 从 @BotFather 申请
bot_token = ""
# 管理员 Telegram user id，可填多个。用 @userinfobot 可查到自己的 id
admin_user_ids = []

[poller]
# 轮询间隔（秒）。NodeSeek 的 RSS 不做 CDN 缓存，每次请求都会打到源站，
# 请勿设为低于 20 的值。更激进的频率会导致你的 IP 被限流。
interval_seconds = 30
request_timeout_seconds = 20
# 连续失败时的退避上限
max_backoff_seconds = 600
# 单次拉取新增超过此条数即视为可能丢帖并告警（feeds 窗口只有 20 条）
overflow_threshold = 15
feed_url = "https://rss.nodeseek.com/"

[notifier]
# Telegram 全局上限约 30 条/秒，留出余量
global_rate_per_second = 25
max_attempts = 3
# 同一用户在此窗口内的多条命中会被合并成一条消息
coalesce_window_seconds = 20
max_message_chars = 3500

[limits]
max_rules_per_user = 3
invite_quota_per_user = 3

[storage]
db_path = "data/bot.db"
retention_days = 14
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_config.py -q`
Expected: PASS，7 passed

- [ ] **Step 5: 提交**

```bash
git add src/nodeseek_bot/config.py config.example.toml tests/test_config.py
git commit -m "feat: add configuration loading"
```

---

## Task 3: 数据库与 schema

**Files:**
- Create: `src/nodeseek_bot/schema.sql`, `src/nodeseek_bot/db.py`, `src/nodeseek_bot/repository.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `Database(path: str)`，方法 `initialize() -> None`、`close() -> None`，
    上下文管理器 `tx()` 产出 `sqlite3.Connection`
  - `NewItem` 数据类：`guid`、`title`、`title_norm`、`link`、`published_at`、
    `fetched_at`、`author`、`category`、`excerpt`
  - `ItemRepository(db)`：`insert_many(items: list[NewItem]) -> list[str]`
    （只返回真正新插入的 guid）、`recent_titles(hours: int) -> list[tuple[str, str]]`
    （返回 `(title, title_norm)`）、`purge_older_than(days: int) -> int`

- [ ] **Step 1: 写失败的测试**

`tests/test_db.py`

```python
import dataclasses
from pathlib import Path

from nodeseek_bot.db import Database
from nodeseek_bot.repository import ItemRepository, NewItem


def _item(guid: str, title: str = "hello") -> NewItem:
    return NewItem(
        guid=guid,
        title=title,
        title_norm=title.lower(),
        link=f"https://www.nodeseek.com/post-{guid}-1",
        author="tester",
        category="daily",
        published_at="2026-09-21T17:19:26+00:00",
        fetched_at="2026-09-21T17:19:30+00:00",
        excerpt=None,
    )


def _repo(tmp_path: Path) -> tuple[Database, ItemRepository]:
    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    return db, ItemRepository(db)


def test_insert_returns_only_new_guids(tmp_path: Path) -> None:
    db, repo = _repo(tmp_path)
    assert repo.insert_many([_item("1"), _item("2")]) == ["1", "2"]
    assert repo.insert_many([_item("2"), _item("3")]) == ["3"]
    db.close()


def test_optional_fields_may_be_missing(tmp_path: Path) -> None:
    db, repo = _repo(tmp_path)
    item = dataclasses.replace(_item("9"), author=None, category=None, excerpt=None)
    assert repo.insert_many([item]) == ["9"]
    db.close()


def test_recent_titles_returns_title_and_normalised_form(tmp_path: Path) -> None:
    db, repo = _repo(tmp_path)
    repo.insert_many([_item("1", "VPS 补货")])
    assert repo.recent_titles(hours=24) == [("VPS 补货", "vps 补货")]
    db.close()


def test_recent_titles_excludes_old_rows(tmp_path: Path) -> None:
    db, repo = _repo(tmp_path)
    old = dataclasses.replace(_item("1"), fetched_at="2020-01-01T00:00:00+00:00")
    repo.insert_many([old])
    assert repo.recent_titles(hours=24) == []
    db.close()


def test_wal_and_foreign_keys_are_enabled(tmp_path: Path) -> None:
    db, _ = _repo(tmp_path)
    with db.tx() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    db.close()


def test_tx_rolls_back_on_error(tmp_path: Path) -> None:
    import pytest

    db, _ = _repo(tmp_path)
    with pytest.raises(RuntimeError), db.tx() as conn:
        conn.execute("INSERT INTO meta (key, value) VALUES ('k', 'v')")
        raise RuntimeError("boom")
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM meta").fetchone()["n"] == 0
    db.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_db.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'nodeseek_bot.db'`

- [ ] **Step 3: 写 schema**

`src/nodeseek_bot/schema.sql` 完整照抄 spec 第 5 节的 DDL：
`users`、`invites`、`rules`、`items`、`deliveries`、`stats_daily`、`meta`
以及全部索引与 `UNIQUE(item_guid, rule_id)` 约束。

- [ ] **Step 4: 实现 Database**

`src/nodeseek_bot/db.py`

```python
"""SQLite connection management."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    """Owns the process-wide SQLite connection."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("database is not initialized")
        return self._conn

    def initialize(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = self.connection
        conn.execute("BEGIN")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
```

- [ ] **Step 5: 实现 ItemRepository**

`src/nodeseek_bot/repository.py`

```python
"""Data access helpers."""

from __future__ import annotations

from dataclasses import dataclass

from nodeseek_bot.db import Database


@dataclass(frozen=True, slots=True)
class NewItem:
    guid: str
    title: str
    title_norm: str
    link: str
    published_at: str
    fetched_at: str
    author: str | None = None
    category: str | None = None
    excerpt: str | None = None


class ItemRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert_many(self, items: list[NewItem]) -> list[str]:
        inserted: list[str] = []
        with self._db.tx() as conn:
            for item in items:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO items
                        (guid, title, title_norm, link, author, category,
                         published_at, fetched_at, excerpt)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.guid,
                        item.title,
                        item.title_norm,
                        item.link,
                        item.author,
                        item.category,
                        item.published_at,
                        item.fetched_at,
                        item.excerpt,
                    ),
                )
                if cur.rowcount == 1:
                    inserted.append(item.guid)
        return inserted

    def recent_titles(self, hours: int) -> list[tuple[str, str]]:
        with self._db.tx() as conn:
            rows = conn.execute(
                """
                SELECT title, title_norm FROM items
                WHERE fetched_at >= strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', ?)
                ORDER BY published_at DESC
                """,
                (f"-{hours} hours",),
            ).fetchall()
        return [(row["title"], row["title_norm"]) for row in rows]

    def purge_older_than(self, days: int) -> int:
        with self._db.tx() as conn:
            cur = conn.execute(
                """
                DELETE FROM items
                WHERE fetched_at < strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', ?)
                """,
                (f"-{days} days",),
            )
        return cur.rowcount
```

- [ ] **Step 6: 运行测试确认通过**

Run: `pytest tests/test_db.py -q`
Expected: PASS，6 passed

- [ ] **Step 7: 提交**

```bash
git add src/nodeseek_bot/schema.sql src/nodeseek_bot/db.py src/nodeseek_bot/repository.py tests/test_db.py
git commit -m "feat: add sqlite schema and item repository"
```

---

## Task 4: 文本归一化

**Files:**
- Create: `src/nodeseek_bot/normalize.py`, `tests/test_normalize.py`

**Interfaces:**
- Consumes: 无
- Produces: `normalize(text: str) -> str`

- [ ] **Step 1: 写失败的测试**

`tests/test_normalize.py`

```python
import pytest

from nodeseek_bot.normalize import normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("VPS", "vps"),
        ("ＶＰＳ", "vps"),
        ("Ｖps", "vps"),
        ("日本 VPS 补货", "日本 vps 补货"),
        ("Ａ１", "a1"),
        ("", ""),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_normalize_is_idempotent() -> None:
    once = normalize("ＶＰＳ ＡＢＣ")
    assert normalize(once) == once


def test_normalize_keeps_chinese_unchanged() -> None:
    assert normalize("显卡") == "显卡"


def test_normalize_does_not_strip_or_collapse_whitespace() -> None:
    assert normalize("  VPS  ") == "  vps  "
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_normalize.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'nodeseek_bot.normalize'`

- [ ] **Step 3: 实现**

`src/nodeseek_bot/normalize.py`

```python
"""Text normalisation shared by matcher and poller."""

from __future__ import annotations

import unicodedata


def normalize(text: str) -> str:
    """Return a comparison-friendly form of *text*.

    NFKC folds full-width characters onto their ASCII equivalents;
    lowercasing removes case sensitivity.  Traditional/Simplified
    conversion is deliberately not performed.
    """
    return unicodedata.normalize("NFKC", text).lower()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_normalize.py -q`
Expected: PASS，9 passed

- [ ] **Step 5: 提交**

```bash
git add src/nodeseek_bot/normalize.py tests/test_normalize.py
git commit -m "feat: add text normalisation"
```

---

## Task 5: 数据源协议与 NodeSeek 解析器

**Files:**
- Create: `src/nodeseek_bot/sources/__init__.py`, `src/nodeseek_bot/sources/nodeseek.py`
- Create: `tests/fixtures/nodeseek_sample.xml`, `tests/test_parser.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `RawItem` 数据类：`guid: str`、`title: str`、`link: str`、`published_at: datetime`、
    `author: str | None`、`category: str | None`、`excerpt: str | None`
  - `Source` 协议：属性 `name: str`，方法 `async fetch() -> list[RawItem]`
  - `parse_feed(payload: bytes) -> list[RawItem]`，失败抛 `FeedParseError`
  - `NodeSeekSource(feed_url=..., user_agent=..., timeout=...)`，
    方法 `async fetch()` 与 `async aclose()`

- [ ] **Step 1: 准备 fixture**

把抓到的样本存为 `tests/fixtures/nodeseek_sample.xml`。内容是公开 RSS，无需脱敏。

- [ ] **Step 2: 写失败的测试**

`tests/test_parser.py`

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nodeseek_bot.sources.nodeseek import FeedParseError, parse_feed

FIXTURE = Path(__file__).parent / "fixtures" / "nodeseek_sample.xml"

NO_DESCRIPTION = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title><![CDATA[no body]]></title>
    <link>https://www.nodeseek.com/post-1-1</link>
    <guid isPermaLink="false">1</guid>
    <pubDate>Mon, 21 Sep 2026 17:19:26 GMT</pubDate>
  </item></channel></rss>"""

NO_GUID = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title><![CDATA[orphan]]></title>
    <link>https://www.nodeseek.com/post-1-1</link>
    <pubDate>Mon, 21 Sep 2026 17:19:26 GMT</pubDate>
  </item>
  <item><title><![CDATA[ok]]></title>
    <link>https://www.nodeseek.com/post-2-1</link>
    <guid>2</guid>
    <pubDate>Mon, 21 Sep 2026 17:19:26 GMT</pubDate>
  </item></channel></rss>"""

NO_PUBDATE = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title><![CDATA[no date]]></title>
    <link>https://www.nodeseek.com/post-1-1</link>
    <guid>1</guid>
  </item></channel></rss>"""

BAD_DATE = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title><![CDATA[bad date]]></title>
    <link>https://www.nodeseek.com/post-1-1</link>
    <guid>1</guid>
    <pubDate>not a date</pubDate>
  </item></channel></rss>"""

ENTITIES = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title>Rock &amp; Roll &lt;test&gt;</title>
    <link>https://www.nodeseek.com/post-3-1</link>
    <guid>3</guid>
    <pubDate>Mon, 21 Sep 2026 17:19:26 GMT</pubDate>
  </item></channel></rss>"""

CDATA_ENTITIES = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title><![CDATA[Rock &amp; Roll <test>]]></title>
    <link>https://www.nodeseek.com/post-4-1</link>
    <guid>4</guid>
    <pubDate>Mon, 21 Sep 2026 17:19:26 GMT</pubDate>
  </item></channel></rss>"""


def test_parses_all_items_from_fixture() -> None:
    items = parse_feed(FIXTURE.read_bytes())
    assert len(items) >= 15
    assert all(item.guid for item in items)
    assert all(item.title for item in items)
    assert all(item.link.startswith("https://www.nodeseek.com/post-") for item in items)


def test_guid_is_the_numeric_post_id() -> None:
    assert parse_feed(FIXTURE.read_bytes())[0].guid.isdigit()


def test_published_at_is_timezone_aware_and_in_the_past() -> None:
    item = parse_feed(FIXTURE.read_bytes())[0]
    assert item.published_at.tzinfo is not None
    assert item.published_at < datetime.now(UTC)


def test_fixture_contains_an_item_without_description() -> None:
    items = parse_feed(FIXTURE.read_bytes())
    assert any(item.excerpt is None for item in items)


def test_item_without_description_and_extras() -> None:
    item = parse_feed(NO_DESCRIPTION)[0]
    assert item.excerpt is None
    assert item.author is None
    assert item.category is None


def test_item_without_guid_is_skipped() -> None:
    assert [item.guid for item in parse_feed(NO_GUID)] == ["2"]


def test_item_without_pubdate_is_skipped() -> None:
    assert parse_feed(NO_PUBDATE) == []


def test_unparseable_date_is_skipped() -> None:
    assert parse_feed(BAD_DATE) == []


def test_entities_outside_cdata_are_decoded() -> None:
    assert parse_feed(ENTITIES)[0].title == "Rock & Roll <test>"


def test_cdata_content_is_kept_literal() -> None:
    # The real feed wraps titles in CDATA, so "&" arrives as a bare ampersand.
    assert parse_feed(CDATA_ENTITIES)[0].title == "Rock &amp; Roll <test>"


def test_non_xml_payload_raises() -> None:
    with pytest.raises(FeedParseError):
        parse_feed(b"<html><body>maintenance</body></html>")


def test_empty_payload_raises() -> None:
    with pytest.raises(FeedParseError):
        parse_feed(b"")


def test_whitespace_payload_raises() -> None:
    with pytest.raises(FeedParseError):
        parse_feed(b"   \n  ")
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/test_parser.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'nodeseek_bot.sources'`

- [ ] **Step 4: 实现协议**

`src/nodeseek_bot/sources/__init__.py`

```python
"""Feed source abstractions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RawItem:
    guid: str
    title: str
    link: str
    published_at: datetime
    author: str | None = None
    category: str | None = None
    excerpt: str | None = None


class Source(Protocol):
    name: str

    async def fetch(self) -> list[RawItem]:
        """Return the current feed window. Raises on network or parse failure."""
```

- [ ] **Step 5: 实现解析器**

`src/nodeseek_bot/sources/nodeseek.py`

```python
"""NodeSeek RSS source."""

from __future__ import annotations

import email.utils
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import httpx

from nodeseek_bot.sources import RawItem

DC_NS = "http://purl.org/dc/elements/1.1/"
ACCEPT = "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8"


class FeedParseError(Exception):
    """Raised when the payload is not a usable RSS document."""


def _text(node: ET.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    stripped = node.text.strip()
    return stripped or None


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_feed(payload: bytes) -> list[RawItem]:
    if not payload.strip():
        raise FeedParseError("empty payload")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FeedParseError(f"invalid XML: {exc}") from exc

    if root.tag != "rss":
        raise FeedParseError(f"unexpected root element: {root.tag}")

    items: list[RawItem] = []
    for node in root.iter("item"):
        guid = _text(node.find("guid"))
        title = _text(node.find("title"))
        link = _text(node.find("link"))
        published = _parse_date(_text(node.find("pubDate")))
        if not guid or not title or not link or published is None:
            continue
        items.append(
            RawItem(
                guid=guid,
                title=title,
                link=link,
                published_at=published,
                author=_text(node.find(f"{{{DC_NS}}}creator")),
                category=_text(node.find("category")),
                excerpt=_text(node.find("description")),
            )
        )
    return items


class NodeSeekSource:
    name = "nodeseek"

    def __init__(self, *, feed_url: str, user_agent: str, timeout: float) -> None:
        self._feed_url = feed_url
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "User-Agent": user_agent,
                "Accept": ACCEPT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
        )

    async def fetch(self) -> list[RawItem]:
        response = await self._client.get(self._feed_url)
        response.raise_for_status()
        return parse_feed(response.content)

    async def aclose(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 6: 运行测试确认通过**

Run: `pytest tests/test_parser.py -q`
Expected: PASS，13 passed

- [ ] **Step 7: 提交**

```bash
git add src/nodeseek_bot/sources tests/fixtures tests/test_parser.py
git commit -m "feat: add source protocol and nodeseek rss parser"
```

---

## Task 6: 匹配引擎与规则仓储

**Files:**
- Create: `src/nodeseek_bot/matcher.py`, `tests/test_matcher.py`
- Modify: `src/nodeseek_bot/repository.py`（新增 `UserRepository`、`RuleRepository`、
  `DeliveryRepository.create_pending`、`RuleRow`、`RuleLimitExceeded`）

**Interfaces:**
- Consumes: `normalize`、`Database`
- Produces:
  - `expand_rules(keyword_raw: str) -> str | None`（非法输入返回 `None`）
  - `matches(title_norm: str, keyword_norm: str) -> bool`
  - `RuleLimitExceeded` 异常
  - `RuleRow` 数据类：`id`、`user_id`、`keyword_raw`、`keyword_norm`
  - `UserRepository(db)`：`ensure(*, tg_user_id: int, username: str | None) -> int`、
    `set_banned(tg_user_id: int, banned: bool) -> bool`
  - `RuleRepository(db)`：`add(user_id, keyword_raw, *, limit) -> int`、
    `list_for_user(user_id) -> list[RuleRow]`、`delete(user_id, rule_id) -> bool`、
    `clear(user_id) -> int`、`all_enabled() -> list[RuleRow]`、
    `top_keywords(*, limit) -> list[tuple[str, int]]`
  - `DeliveryRepository(db).create_pending(pairs: list[tuple[str, int, int]]) -> int`
    （元组为 `(item_guid, rule_id, user_id)`）

- [ ] **Step 1: 写失败的测试**

`tests/test_matcher.py`

```python
import pytest

from nodeseek_bot.matcher import expand_rules, matches


@pytest.mark.parametrize(
    ("title_norm", "keyword_raw", "expected"),
    [
        ("vps 补货了", "vps", True),
        ("vps 补货了", "VPS", True),
        ("vps 补货了", "显卡", False),
        ("日本 vps 补货", "日本 vps", True),
        ("日本 显卡 补货", "日本 vps", False),
        ("日本vps补货", "日本 vps", True),
        ("vps", "  vps  ", True),
        ("anything", "   ", False),
    ],
)
def test_matches(title_norm: str, keyword_raw: str, expected: bool) -> None:
    rule = expand_rules(keyword_raw)
    result = False if rule is None else matches(title_norm, rule)
    assert result is expected


def test_expand_rules_normalises_and_collapses_whitespace() -> None:
    assert expand_rules("  ＶＰＳ   日本 ") == "vps 日本"


def test_expand_rules_rejects_blank() -> None:
    assert expand_rules("   ") is None


def test_expand_rules_rejects_too_long() -> None:
    assert expand_rules("x" * 65) is None


def test_expand_rules_accepts_boundary_length() -> None:
    assert expand_rules("x" * 64) == "x" * 64
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_matcher.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'nodeseek_bot.matcher'`

- [ ] **Step 3: 实现匹配逻辑**

`src/nodeseek_bot/matcher.py`

```python
"""Keyword matching."""

from __future__ import annotations

from nodeseek_bot.normalize import normalize

MAX_KEYWORD_CHARS = 64


def expand_rules(keyword_raw: str) -> str | None:
    """Normalise user input into a matchable rule.

    Returns ``None`` when the input is blank or implausibly long.
    Terms are whitespace separated and combined with AND.
    """
    stripped = keyword_raw.strip()
    if not stripped or len(stripped) > MAX_KEYWORD_CHARS:
        return None
    terms = normalize(stripped).split()
    if not terms:
        return None
    return " ".join(terms)


def matches(title_norm: str, keyword_norm: str) -> bool:
    """True when every term of *keyword_norm* occurs in *title_norm*."""
    terms = keyword_norm.split()
    if not terms:
        return False
    return all(term in title_norm for term in terms)
```

- [ ] **Step 4: 运行匹配测试确认通过**

Run: `pytest tests/test_matcher.py -q`
Expected: PASS，12 passed

- [ ] **Step 5: 追加仓储测试**

在 `tests/test_db.py` 顶部补 `import pytest`，并追加：

```python
def _user(tmp_path: Path, tg_id: int = 1):
    from nodeseek_bot.repository import UserRepository

    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    return db, UserRepository(db).ensure(tg_user_id=tg_id, username="a")


def test_rule_limit_is_enforced(tmp_path: Path) -> None:
    from nodeseek_bot.matcher import RuleLimitExceeded
    from nodeseek_bot.repository import RuleRepository

    db, uid = _user(tmp_path)
    rules = RuleRepository(db)
    rules.add(uid, "vps", limit=2)
    rules.add(uid, "显卡", limit=2)
    with pytest.raises(RuleLimitExceeded):
        rules.add(uid, "内存", limit=2)
    db.close()


def test_duplicate_rule_is_rejected(tmp_path: Path) -> None:
    from nodeseek_bot.repository import RuleRepository

    db, uid = _user(tmp_path)
    rules = RuleRepository(db)
    rules.add(uid, "vps", limit=3)
    with pytest.raises(ValueError, match="duplicate"):
        rules.add(uid, "VPS", limit=3)
    db.close()


def test_blank_rule_is_rejected(tmp_path: Path) -> None:
    from nodeseek_bot.repository import RuleRepository

    db, uid = _user(tmp_path)
    with pytest.raises(ValueError, match="blank"):
        RuleRepository(db).add(uid, "   ", limit=3)
    db.close()


def test_list_delete_and_clear(tmp_path: Path) -> None:
    from nodeseek_bot.repository import RuleRepository

    db, uid = _user(tmp_path)
    rules = RuleRepository(db)
    rules.add(uid, "vps", limit=3)
    rule_id = rules.list_for_user(uid)[0].id
    assert rules.delete(uid, rule_id) is True
    assert rules.delete(uid, rule_id) is False
    rules.add(uid, "vps", limit=3)
    assert rules.clear(uid) == 1
    assert rules.list_for_user(uid) == []
    db.close()


def test_ensure_is_idempotent(tmp_path: Path) -> None:
    from nodeseek_bot.repository import UserRepository

    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    users = UserRepository(db)
    assert users.ensure(tg_user_id=7, username="a") == users.ensure(
        tg_user_id=7, username="b"
    )
    db.close()


def test_create_pending_is_idempotent(tmp_path: Path) -> None:
    from nodeseek_bot.repository import DeliveryRepository, RuleRepository, UserRepository

    db, repo = _repo(tmp_path)
    repo.insert_many([_item("1")])
    uid = UserRepository(db).ensure(tg_user_id=1, username=None)
    rule_id = RuleRepository(db).add(uid, "vps", limit=3)
    deliveries = DeliveryRepository(db)
    assert deliveries.create_pending([("1", rule_id, uid)]) == 1
    assert deliveries.create_pending([("1", rule_id, uid)]) == 0
    db.close()


def test_all_enabled_excludes_banned_users(tmp_path: Path) -> None:
    from nodeseek_bot.repository import RuleRepository, UserRepository

    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    users = UserRepository(db)
    rules = RuleRepository(db)
    uid = users.ensure(tg_user_id=1, username=None)
    rules.add(uid, "vps", limit=3)
    assert len(rules.all_enabled()) == 1
    users.set_banned(1, True)
    assert rules.all_enabled() == []
    db.close()


def test_top_keywords_counts_subscribers(tmp_path: Path) -> None:
    from nodeseek_bot.repository import RuleRepository, UserRepository

    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    users = UserRepository(db)
    rules = RuleRepository(db)
    for tg_id in (1, 2):
        rules.add(users.ensure(tg_user_id=tg_id, username=None), "vps", limit=3)
    rules.add(users.ensure(tg_user_id=3, username=None), "显卡", limit=3)
    assert rules.top_keywords(limit=5) == [("vps", 2), ("显卡", 1)]
    db.close()
```

- [ ] **Step 6: 实现仓储扩展**

在 `src/nodeseek_bot/repository.py` 追加 `UserRepository`、`RuleRepository`、
`DeliveryRepository` 与 `RuleRow`、`RuleLimitExceeded`。要点：

- `RuleRepository.add` 内部调用 `expand_rules`，返回 `None` 时抛
  `ValueError("blank or oversized keyword")`；条数达到 `limit` 抛 `RuleLimitExceeded`；
  归一化后重复抛 `ValueError("duplicate rule")`。
- `all_enabled` 用 `JOIN users` 过滤 `is_banned = 0`。
- `top_keywords` 用 `GROUP BY keyword_norm ORDER BY n DESC, keyword_norm ASC`。
- `DeliveryRepository.create_pending` 用 `INSERT OR IGNORE`，返回累加的行数。
- `UserRepository.ensure` 用 `INSERT ... ON CONFLICT(tg_user_id) DO UPDATE`
  更新用户名后回查 id。
- `UserRepository.set_banned` 更新 `is_banned` 并返回 `rowcount > 0`。
  它必须在 Task 6 实现，因为 `test_all_enabled_excludes_banned_users` 依赖它。

- [ ] **Step 7: 运行全部测试确认通过**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add src/nodeseek_bot/matcher.py src/nodeseek_bot/repository.py tests/test_matcher.py tests/test_db.py
git commit -m "feat: add keyword matcher and rule repository"
```

---

## Task 7: 轮询器

**Files:**
- Create: `src/nodeseek_bot/poller.py`, `tests/test_poller.py`
- Modify: `src/nodeseek_bot/repository.py`（新增 `MetaRepository`）

**Interfaces:**
- Consumes: `Source`、`RawItem`、`ItemRepository`、`normalize`
- Produces:
  - `MetaRepository(db)`：`get(key: str) -> str | None`、`set(key: str, value: str) -> None`
  - `Poller(source=..., items=..., meta=..., queue=..., config=..., on_alert=...)`，
    属性 `consecutive_failures: int`，方法 `async run_once() -> int`、`async run() -> None`
  - 队列元素类型为 `RawItem`

- [ ] **Step 1: 写失败的测试**

`tests/test_poller.py`

```python
import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nodeseek_bot.config import PollerConfig
from nodeseek_bot.db import Database
from nodeseek_bot.poller import Poller
from nodeseek_bot.repository import ItemRepository, MetaRepository
from nodeseek_bot.sources import RawItem


class FakeSource:
    name = "fake"

    def __init__(self, batches: list[object]) -> None:
        self._batches = batches

    async def fetch(self) -> list[RawItem]:
        if not self._batches:
            return []
        batch = self._batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch  # type: ignore[return-value]


def _item(guid: str) -> RawItem:
    return RawItem(
        guid=guid,
        title=f"标题 {guid}",
        link=f"https://www.nodeseek.com/post-{guid}-1",
        published_at=datetime(2026, 9, 21, 17, 19, 26, tzinfo=UTC),
    )


def _build(
    tmp_path: Path,
    batches: list[object],
    config: PollerConfig | None = None,
) -> tuple[Database, Poller, asyncio.Queue[RawItem], list[str]]:
    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    alerts: list[str] = []

    async def on_alert(message: str) -> None:
        alerts.append(message)

    queue: asyncio.Queue[RawItem] = asyncio.Queue()
    poller = Poller(
        source=FakeSource(batches),
        items=ItemRepository(db),
        meta=MetaRepository(db),
        queue=queue,
        config=config or PollerConfig(),
        on_alert=on_alert,
    )
    return db, poller, queue, alerts


async def test_run_once_enqueues_only_new_items(tmp_path: Path) -> None:
    db, poller, queue, _ = _build(
        tmp_path, [[_item("1"), _item("2")], [_item("2"), _item("3")]]
    )
    assert await poller.run_once() == 2
    assert await poller.run_once() == 1
    drained = [queue.get_nowait().guid for _ in range(queue.qsize())]
    assert drained == ["1", "2", "3"]
    db.close()


async def _run_briefly(poller: Poller) -> None:
    """Drive run() for a moment, then cancel it cleanly."""
    task = asyncio.create_task(poller.run())
    await asyncio.sleep(0.15)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


FAST = PollerConfig(interval_seconds=0.005, max_backoff_seconds=0.01)


async def test_alerts_after_three_consecutive_failures(tmp_path: Path) -> None:
    db, poller, _, alerts = _build(tmp_path, [RuntimeError("boom")] * 4, FAST)
    await _run_briefly(poller)
    assert len(alerts) == 1
    assert "3" in alerts[0]
    db.close()


async def test_success_resets_failure_counter(tmp_path: Path) -> None:
    db, poller, _, alerts = _build(
        tmp_path, [RuntimeError("boom"), RuntimeError("boom"), [_item("1")]], FAST
    )
    await _run_briefly(poller)
    assert alerts == []
    assert poller.consecutive_failures == 0
    db.close()


async def test_overflow_triggers_alert(tmp_path: Path) -> None:
    batch = [_item(str(i)) for i in range(20)]
    db, poller, _, alerts = _build(tmp_path, [batch])
    await poller.run_once()
    assert len(alerts) == 1
    assert "丢帖" in alerts[0]
    db.close()


async def test_last_success_is_persisted(tmp_path: Path) -> None:
    db, poller, _, _ = _build(tmp_path, [[_item("1")]])
    await poller.run_once()
    assert MetaRepository(db).get("last_success_at") is not None
    db.close()


async def test_repeating_the_same_batch_does_not_alert_again(tmp_path: Path) -> None:
    batch = [_item(str(i)) for i in range(20)]
    db, poller, _, alerts = _build(tmp_path, [batch, batch])
    await poller.run_once()
    await poller.run_once()
    assert len(alerts) == 1  # 同一批帖子第二次不会重复插入，也不会再告警
    db.close()


async def test_run_keeps_going_after_a_failure(tmp_path: Path) -> None:
    db, poller, queue, _ = _build(tmp_path, [RuntimeError("boom"), [_item("1")]], FAST)
    await _run_briefly(poller)
    assert queue.qsize() == 1
    db.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_poller.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'nodeseek_bot.poller'`

- [ ] **Step 3: 实现 MetaRepository**

在 `src/nodeseek_bot/repository.py` 追加 `MetaRepository`：
`get` 查询 `meta` 表并返回 `str | None`，`set` 用
`INSERT ... ON CONFLICT(key) DO UPDATE SET value = excluded.value`。

- [ ] **Step 4: 实现轮询器**

`src/nodeseek_bot/poller.py`

```python
"""Background poller."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from nodeseek_bot.config import PollerConfig
from nodeseek_bot.normalize import normalize
from nodeseek_bot.repository import ItemRepository, MetaRepository, NewItem
from nodeseek_bot.sources import RawItem, Source

logger = logging.getLogger(__name__)

ALERT_AFTER_FAILURES = 3


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Poller:
    """Fetches the feed, persists new items and enqueues them for matching."""

    def __init__(
        self,
        *,
        source: Source,
        items: ItemRepository,
        meta: MetaRepository,
        queue: asyncio.Queue[RawItem],
        config: PollerConfig,
        on_alert: Callable[[str], Awaitable[None]],
    ) -> None:
        self._source = source
        self._items = items
        self._meta = meta
        self._queue = queue
        self._config = config
        self._on_alert = on_alert
        self._consecutive_failures = 0
        self._alerted = False

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    async def run_once(self) -> int:
        """Fetch once. Raises on source failure; the caller drives backoff."""
        raw_items = await self._source.fetch()
        fetched_at = _now_iso()
        rows = [
            NewItem(
                guid=item.guid,
                title=item.title,
                title_norm=normalize(item.title),
                link=item.link,
                author=item.author,
                category=item.category,
                published_at=item.published_at.astimezone(UTC).isoformat(
                    timespec="seconds"
                ),
                fetched_at=fetched_at,
                excerpt=item.excerpt,
            )
            for item in raw_items
        ]
        inserted = self._items.insert_many(rows)
        by_guid = {item.guid: item for item in raw_items}
        for guid in inserted:
            await self._queue.put(by_guid[guid])

        self._meta.set("last_success_at", fetched_at)
        self._consecutive_failures = 0
        self._alerted = False

        if len(inserted) >= self._config.overflow_threshold:
            await self._on_alert(
                f"⚠️ 单次轮询新增 {len(inserted)} 条，已接近或超过 feed 的 "
                f"{len(raw_items)} 条窗口，存在丢帖风险。"
                f"建议缩短轮询间隔，或改用窗口更大的数据源。"
            )
        return len(inserted)

    async def _notify_if_needed(self, error: Exception) -> None:
        if self._consecutive_failures < ALERT_AFTER_FAILURES or self._alerted:
            return
        self._alerted = True
        await self._on_alert(
            f"🔴 NodeSeek 轮询已连续失败 {self._consecutive_failures} 次。"
            f"最近错误：{str(error)[:200]}"
        )

    async def run(self) -> None:
        delay = self._config.interval_seconds
        while True:
            try:
                count = await self.run_once()
                if count:
                    logger.info("poll ok, %d new item(s)", count)
                delay = self._config.interval_seconds
            except Exception as exc:  # noqa: BLE001 - one bad poll must not kill the loop
                self._consecutive_failures += 1
                delay = min(delay * 2, self._config.max_backoff_seconds)
                logger.warning(
                    "poll failed (%d in a row), retry in %.1fs: %s",
                    self._consecutive_failures,
                    delay,
                    exc,
                )
                self._meta.set("last_error", str(exc)[:200])
                await self._notify_if_needed(exc)
            await asyncio.sleep(delay)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_poller.py -q`
Expected: PASS，7 passed

- [ ] **Step 6: 提交**

```bash
git add src/nodeseek_bot/poller.py src/nodeseek_bot/repository.py tests/test_poller.py
git commit -m "feat: add polling loop with backoff and overflow detection"
```

---

## Task 8: 消息构造与通知投递

**Files:**
- Create: `src/nodeseek_bot/formatting.py`, `src/nodeseek_bot/notifier.py`
- Create: `tests/test_formatting.py`, `tests/test_notifier.py`
- Modify: `src/nodeseek_bot/repository.py`（`DeliveryRepository` 增加取件与状态更新）

**Interfaces:**
- Consumes: `NotifierConfig`、`Database`
- Produces:
  - `PendingDelivery` 数据类（**定义在 `repository.py`**，`formatting.py` 从那里导入，
    避免循环依赖）：`delivery_id`、`rule_id`、`user_id`、`tg_user_id`、
    `keyword_raw`、`title`、`link`
  - `escape_html(text: str) -> str`
  - `build_messages(pending: list[PendingDelivery], max_chars: int) -> list[str]`
  - `TokenBucket(rate_per_second: float)`，方法 `async acquire() -> None`
  - `DeliveryRepository` 新增：`pending_users() -> list[int]`、
    `claim_for_user(user_id) -> list[PendingDelivery]`、`mark_sent(ids) -> None`、
    `mark_failed(ids, error, max_attempts) -> int`
  - `Notifier(bot=..., deliveries=..., config=..., on_alert=...)`，
    方法 `async drain_once() -> None`、`async run() -> None`

**约定：** `bot` 只需要提供
`async send_message(chat_id, text, *, parse_mode="HTML", disable_web_page_preview=True)`，
便于注入测试替身。

- [ ] **Step 1: 写失败的测试**

`tests/test_formatting.py`

```python
from nodeseek_bot.formatting import PendingDelivery, build_messages, escape_html


def _delivery(index: int, title: str = "标题") -> PendingDelivery:
    return PendingDelivery(
        delivery_id=index,
        rule_id=1,
        user_id=1,
        tg_user_id=100,
        keyword_raw="vps",
        title=title,
        link=f"https://www.nodeseek.com/post-{index}-1",
    )


def test_escape_html_covers_telegram_specials() -> None:
    assert escape_html("Rock & Roll <b>") == "Rock &amp; Roll &lt;b&gt;"


def test_escape_html_leaves_quotes_alone() -> None:
    assert escape_html('say "hi"') == 'say "hi"'


def test_build_messages_numbers_entries() -> None:
    messages = build_messages([_delivery(1, "A"), _delivery(2, "B")], max_chars=3500)
    assert len(messages) == 1
    assert "1. A" in messages[0]
    assert "2. B" in messages[0]
    assert "命中「vps」" in messages[0]


def test_build_messages_escapes_titles() -> None:
    messages = build_messages([_delivery(1, "A & B <x>")], max_chars=3500)
    assert "&amp;" in messages[0]
    assert "<x>" not in messages[0]


def test_build_messages_splits_when_too_long() -> None:
    pending = [_delivery(i, "T" * 200) for i in range(1, 41)]
    messages = build_messages(pending, max_chars=500)
    assert len(messages) > 1
    assert all(len(message) <= 700 for message in messages)


def test_build_messages_handles_one_very_long_title() -> None:
    # 截断保证单条条目始终很小，一条消息装得下，且远低于 Telegram 的硬上限
    messages = build_messages([_delivery(1, "T" * 5000)], max_chars=3500)
    assert len(messages) == 1
    assert "…" in messages[0]
    assert len(messages[0]) < 1000


def test_build_messages_truncates_title() -> None:
    messages = build_messages([_delivery(1, "T" * 300)], max_chars=3500)
    assert "…" in messages[0]


def test_build_messages_with_empty_input() -> None:
    assert build_messages([], max_chars=3500) == []


async def test_token_bucket_limits_rate() -> None:
    import time

    from nodeseek_bot.notifier import TokenBucket

    bucket = TokenBucket(rate_per_second=50)
    start = time.monotonic()
    for _ in range(100):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.5
```

`tests/test_notifier.py`

```python
from pathlib import Path

from nodeseek_bot.config import NotifierConfig
from nodeseek_bot.db import Database
from nodeseek_bot.notifier import Notifier
from nodeseek_bot.repository import (
    DeliveryRepository,
    ItemRepository,
    NewItem,
    RuleRepository,
    UserRepository,
)


class FakeBot:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self._error = error

    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> None:
        if self._error is not None:
            raise self._error
        self.sent.append((chat_id, text))


async def _noop(message: str) -> None:
    return None


def _setup(tmp_path: Path, deliveries: int = 3):
    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    uid = UserRepository(db).ensure(tg_user_id=555, username="u")
    rid = RuleRepository(db).add(uid, "vps", limit=3)
    ItemRepository(db).insert_many(
        [
            NewItem(
                guid=str(i),
                title=f"标题 {i}",
                title_norm=f"标题 {i}",
                link=f"https://www.nodeseek.com/post-{i}-1",
                published_at=f"2026-09-21T17:19:{i:02d}+00:00",
                fetched_at="2026-09-21T17:19:30+00:00",
            )
            for i in range(deliveries)
        ]
    )
    repo = DeliveryRepository(db)
    repo.create_pending([(str(i), rid, uid) for i in range(deliveries)])
    return db, repo


async def test_pending_for_one_user_becomes_a_single_message(tmp_path: Path) -> None:
    db, repo = _setup(tmp_path)
    bot = FakeBot()
    await Notifier(
        bot=bot, deliveries=repo, config=NotifierConfig(), on_alert=_noop
    ).drain_once()
    assert len(bot.sent) == 1
    assert bot.sent[0][0] == 555
    assert bot.sent[0][1].count("https://www.nodeseek.com/post-") == 3
    db.close()


async def test_deliveries_are_marked_sent(tmp_path: Path) -> None:
    db, repo = _setup(tmp_path)
    await Notifier(
        bot=FakeBot(), deliveries=repo, config=NotifierConfig(), on_alert=_noop
    ).drain_once()
    assert repo.pending_users() == []
    db.close()


async def test_no_pending_is_a_noop(tmp_path: Path) -> None:
    db, repo = _setup(tmp_path, deliveries=0)
    bot = FakeBot()
    await Notifier(
        bot=bot, deliveries=repo, config=NotifierConfig(), on_alert=_noop
    ).drain_once()
    assert bot.sent == []
    db.close()


async def test_failure_keeps_delivery_pending_then_marks_failed(tmp_path: Path) -> None:
    db, repo = _setup(tmp_path, deliveries=1)
    alerts: list[str] = []

    async def on_alert(message: str) -> None:
        alerts.append(message)

    config = NotifierConfig(max_attempts=2)
    notifier = Notifier(
        bot=FakeBot(RuntimeError("boom")), deliveries=repo, config=config, on_alert=on_alert
    )
    await notifier.drain_once()
    assert len(repo.pending_users()) == 1
    notifier.cooldown_until.clear()
    await notifier.drain_once()
    assert repo.pending_users() == []
    assert alerts and "失败" in alerts[0]
    db.close()


async def test_rate_limit_sets_cooldown_without_counting_attempt(tmp_path: Path) -> None:
    class RetryAfter(Exception):
        retry_after = 30

    db, repo = _setup(tmp_path, deliveries=1)
    notifier = Notifier(
        bot=FakeBot(RetryAfter()), deliveries=repo, config=NotifierConfig(), on_alert=_noop
    )
    await notifier.drain_once()
    assert len(repo.pending_users()) == 1
    assert notifier.cooldown_until
    db.close()


async def test_sent_delivery_is_not_resent(tmp_path: Path) -> None:
    db, repo = _setup(tmp_path, deliveries=1)
    config = NotifierConfig()
    bot = FakeBot()
    notifier = Notifier(bot=bot, deliveries=repo, config=config, on_alert=_noop)
    await notifier.drain_once()
    notifier.cooldown_until.clear()
    await notifier.drain_once()
    assert len(bot.sent) == 1
    db.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_formatting.py tests/test_notifier.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'nodeseek_bot.formatting'`

- [ ] **Step 3: 实现消息构造**

`src/nodeseek_bot/formatting.py`。关键点：

- `escape_html` 用 `html.escape(text, quote=False)`，只转义 `&` `<` `>`，
  保留引号以免影响可读性。
- 标题超过 120 字符截断并追加 `…`。
- 分批：逐条累加长度，超过 `max_chars` 就开启新消息，
  并保持编号连续。
- 单条超长标题本身仍能单独成批，保证不会出现无法投递的消息。

```python
"""Telegram message construction."""

from __future__ import annotations

import html

from nodeseek_bot.repository import PendingDelivery

HEADER = "🔍 命中「{keyword}」的新帖 {count} 条"
SEPARATOR = "\n\n"
MAX_TITLE_CHARS = 120


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def _title(text: str) -> str:
    if len(text) <= MAX_TITLE_CHARS:
        return text
    return text[: MAX_TITLE_CHARS - 1] + "…"


def _entry(index: int, item: PendingDelivery) -> str:
    return f"{index}. {escape_html(_title(item.title))}\n   {escape_html(item.link)}"


def build_messages(pending: list[PendingDelivery], max_chars: int) -> list[str]:
    """Group deliveries into as few messages as fit under *max_chars*."""
    if not pending:
        return []

    keyword = escape_html(pending[0].keyword_raw)
    messages: list[str] = []
    buffer: list[str] = []
    size = 0
    batch_start = 1

    def flush() -> None:
        nonlocal buffer, size, batch_start
        if not buffer:
            return
        header = HEADER.format(keyword=keyword, count=len(buffer))
        messages.append(header + SEPARATOR + "\n\n".join(buffer))
        batch_start += len(buffer)
        buffer = []
        size = 0

    for item in pending:
        entry = _entry(batch_start + len(buffer), item)
        if buffer and size + len(entry) + 2 > max_chars:
            flush()
            entry = _entry(batch_start, item)
        buffer.append(entry)
        size += len(entry) + 2

    flush()
    return messages
```

- [ ] **Step 4: 实现通知器**

`src/nodeseek_bot/notifier.py`。要点：

- `TokenBucket` 用单调时钟补充令牌，保证全局限速。
- `run()` 每秒扫描一次待发用户。
- `_send_user` 取出该用户全部 pending 合并发送；单条命中因此零额外等待。
- 发送成功后写入合并窗口冷却，窗口内的新命中等待窗口结束再发，
  实现 spec 第 7.2 节的合并语义。
- 异常带有 `retry_after` 属性时视为限流：设冷却，退回 pending，
  **不**增加 attempt 计数。
- 其他异常增加 attempt，达到上限标记 `failed` 并告警。

```python
"""Delivery of matches to Telegram."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from nodeseek_bot.config import NotifierConfig
from nodeseek_bot.formatting import build_messages
from nodeseek_bot.repository import DeliveryRepository

logger = logging.getLogger(__name__)

SCAN_INTERVAL = 1.0


class TokenBucket:
    """Shared rate limiter for every outgoing message."""

    def __init__(self, rate_per_second: float) -> None:
        self._rate = max(rate_per_second, 0.001)
        self._tokens = self._rate
        self._updated = time.monotonic()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            elapsed = now - self._updated
            self._updated = now
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            if self._tokens >= 1:
                self._tokens -= 1
                return
            await asyncio.sleep((1 - self._tokens) / self._rate)


class Notifier:
    def __init__(
        self,
        *,
        bot: object,
        deliveries: DeliveryRepository,
        config: NotifierConfig,
        on_alert: Callable[[str], Awaitable[None]],
    ) -> None:
        self._bot = bot
        self._deliveries = deliveries
        self._config = config
        self._on_alert = on_alert
        self._bucket = TokenBucket(config.global_rate_per_second)
        #: user_id -> monotonic deadline before which we must stay quiet.
        self.cooldown_until: dict[int, float] = {}

    async def drain_once(self) -> None:
        for user_id in self._deliveries.pending_users():
            if time.monotonic() < self.cooldown_until.get(user_id, 0.0):
                continue
            await self._send_user(user_id)

    async def _send_user(self, user_id: int) -> None:
        pending = self._deliveries.claim_for_user(user_id)
        if not pending:
            return
        ids = [item.delivery_id for item in pending]
        chat_id = pending[0].tg_user_id
        try:
            for message in build_messages(pending, self._config.max_message_chars):
                await self._bucket.acquire()
                await self._bot.send_message(
                    chat_id,
                    message,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
        except Exception as exc:  # noqa: BLE001 - classified below
            retry_after = getattr(exc, "retry_after", None)
            logger.warning("send failed for user %s: %s", user_id, exc)
            if retry_after:
                self.cooldown_until[user_id] = time.monotonic() + float(retry_after)
                return
            failures = self._deliveries.mark_failed(
                ids, str(exc)[:200], self._config.max_attempts
            )
            if failures:
                await self._on_alert(f"🔴 有 {failures} 条通知发送失败，已超过重试上限。")
            return

        self._deliveries.mark_sent(ids)
        self.cooldown_until[user_id] = time.monotonic() + self._config.coalesce_window_seconds

    async def run(self) -> None:
        while True:
            try:
                await self.drain_once()
            except Exception:  # noqa: BLE001 - the notifier must never die
                logger.exception("notifier scan failed")
            await asyncio.sleep(SCAN_INTERVAL)
```

- [ ] **Step 5: 补齐 DeliveryRepository**

在 `DeliveryRepository` 追加四个方法：

- `pending_users()`：`SELECT DISTINCT user_id FROM deliveries WHERE status = 'pending'`
- `claim_for_user(user_id)`：JOIN `items`、`rules`、`users`，
  返回 `PendingDelivery` 列表，按 `i.published_at` 升序
- `mark_sent(ids)`：批量 `UPDATE ... SET status='sent', sent_at=..., attempts=attempts+1`，
  用 `IN ({placeholders})` 拼参数
- `mark_failed(ids, error, max_attempts)`：先 `attempts = attempts + 1` 并记 error，
  再 `UPDATE ... SET status='failed' WHERE id IN (...) AND attempts >= ?`，
  返回 `rowcount`（即刚好超限的条数）。
- 占位符用模块级 `_placeholders(count: int) -> str` 生成，
  不要手拼 `?` 串；`PendingDelivery` 定义在 `DeliveryRepository` 之后也没关系，
  `from __future__ import annotations` 会把注解延迟成字符串。

为避免循环导入，把 `PendingDelivery` 定义在 `repository.py`，
`formatting.py` 从 `repository` 导入它。

- [ ] **Step 6: 运行测试确认通过**

Run: `pytest tests/test_formatting.py tests/test_notifier.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/nodeseek_bot/formatting.py src/nodeseek_bot/notifier.py src/nodeseek_bot/repository.py tests/test_formatting.py tests/test_notifier.py
git commit -m "feat: add coalescing notifier with token bucket and retries"
```

---

## Task 9: 运维告警

**Files:**
- Create: `src/nodeseek_bot/alerts.py`, `tests/test_alerts.py`

**Interfaces:**
- Consumes: 任意提供 `async send_message(chat_id, text, **kwargs)` 的对象
- Produces: `AlertDispatcher(bot, admin_user_ids: list[int])`，
  方法 `async send(message: str) -> None`。其 `send` 可直接作为
  `Poller` 与 `Notifier` 的 `on_alert` 参数传入。

- [ ] **Step 1: 写失败的测试**

`tests/test_alerts.py`

```python
from nodeseek_bot.alerts import AlertDispatcher


class FakeBot:
    def __init__(self, fail_for: set[int] | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self._fail_for = fail_for or set()

    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> None:
        if chat_id in self._fail_for:
            raise RuntimeError("blocked")
        self.sent.append((chat_id, text))


async def test_alerts_reach_every_admin() -> None:
    bot = FakeBot()
    await AlertDispatcher(bot, [1, 2]).send("hello")
    assert bot.sent == [(1, "hello"), (2, "hello")]


async def test_one_bad_admin_does_not_block_the_rest() -> None:
    bot = FakeBot(fail_for={1})
    await AlertDispatcher(bot, [1, 2]).send("hello")
    assert bot.sent == [(2, "hello")]


async def test_no_admins_is_a_noop() -> None:
    bot = FakeBot()
    await AlertDispatcher(bot, []).send("hello")
    assert bot.sent == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_alerts.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'nodeseek_bot.alerts'`

- [ ] **Step 3: 实现**

`src/nodeseek_bot/alerts.py`

```python
"""Operator alerting."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AlertDispatcher:
    """Sends operational alerts to every configured admin."""

    def __init__(self, bot: object, admin_user_ids: list[int]) -> None:
        self._bot = bot
        self._admins = list(admin_user_ids)

    async def send(self, message: str) -> None:
        for admin_id in self._admins:
            try:
                await self._bot.send_message(admin_id, message)
            except Exception:  # noqa: BLE001 - one bad admin must not block the rest
                logger.exception("failed to alert admin %s", admin_id)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_alerts.py -q`
Expected: PASS，3 passed

- [ ] **Step 5: 提交**

```bash
git add src/nodeseek_bot/alerts.py tests/test_alerts.py
git commit -m "feat: add operator alert dispatcher"
```

---

## Task 10: 用户指令处理器

**Files:**
- Create: `src/nodeseek_bot/bot/__init__.py`, `src/nodeseek_bot/bot/keyboards.py`, `src/nodeseek_bot/bot/user_handlers.py`
- Create: `tests/test_user_handlers.py`

**Interfaces:**
- Consumes: `UserRepository`、`RuleRepository`、`ItemRepository`、`LimitsConfig`、`expand_rules`、`matches`
- Produces:
  - `HandleResult` 数据类：`text: str`、`markup: InlineKeyboardMarkup | None = None`
  - `rule_list_keyboard(rules: list[RuleRow]) -> InlineKeyboardMarkup`
  - `handle_add(users, rules, limits, tg_user_id, username, keyword) -> HandleResult`
  - `handle_list(users, rules, tg_user_id) -> HandleResult`
  - `handle_delete(users, rules, tg_user_id, rule_id) -> HandleResult`
  - `handle_clear(users, rules, tg_user_id) -> HandleResult`
  - `handle_test(items, tg_user_id, keyword) -> HandleResult`
  - `handle_help() -> HandleResult`

处理器全部是**纯函数**，不依赖 aiogram 的 Dispatcher，因此可以脱离网络测试。

- [ ] **Step 1: 写失败的测试**

`tests/test_user_handlers.py`

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nodeseek_bot.bot.user_handlers import (
    handle_add,
    handle_clear,
    handle_delete,
    handle_help,
    handle_list,
    handle_test,
)
from nodeseek_bot.config import LimitsConfig
from nodeseek_bot.db import Database
from nodeseek_bot.repository import (
    ItemRepository,
    NewItem,
    RuleRepository,
    UserRepository,
)


@pytest.fixture()
def env(
    tmp_path: Path,
) -> Iterator[tuple[Database, UserRepository, RuleRepository, ItemRepository, LimitsConfig]]:
    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    yield db, UserRepository(db), RuleRepository(db), ItemRepository(db), LimitsConfig()
    db.close()


def _uid(users: UserRepository) -> int:
    return users.ensure(tg_user_id=555, username="u")


def _now() -> str:
    """Fresh timestamps: /test only looks at the last 24 hours."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def test_add_accepts_valid_keyword(env) -> None:
    _, users, rules, _, limits = env
    result = handle_add(users, rules, limits, 555, "u", "VPS")
    assert "VPS" in result.text
    assert result.markup is not None
    assert len(rules.list_for_user(_uid(users))) == 1


def test_add_normalises_before_storing(env) -> None:
    _, users, rules, _, limits = env
    handle_add(users, rules, limits, 555, "u", "ＶＰＳ")
    assert rules.list_for_user(_uid(users))[0].keyword_norm == "vps"


def test_add_rejects_blank_keyword(env) -> None:
    _, users, rules, _, limits = env
    assert "不能为空" in handle_add(users, rules, limits, 555, "u", "   ").text


def test_add_rejects_duplicate(env) -> None:
    _, users, rules, _, limits = env
    handle_add(users, rules, limits, 555, "u", "vps")
    assert "已经" in handle_add(users, rules, limits, 555, "u", "VPS").text


def test_add_rejects_over_limit(env) -> None:
    _, users, rules, _, limits = env
    for word in ("vps", "显卡", "内存"):
        handle_add(users, rules, limits, 555, "u", word)
    assert "上限" in handle_add(users, rules, limits, 555, "u", "硬盘").text


def test_list_reports_empty_state(env) -> None:
    _, users, rules, _, _ = env
    assert "还没有" in handle_list(users, rules, 555).text


def test_list_numbers_rules_and_offers_keyboard(env) -> None:
    _, users, rules, _, limits = env
    handle_add(users, rules, limits, 555, "u", "vps")
    handle_add(users, rules, limits, 555, "u", "显卡")
    result = handle_list(users, rules, 555)
    assert "1. vps" in result.text
    assert "2. 显卡" in result.text
    assert result.markup is not None


def test_delete_removes_rule(env) -> None:
    _, users, rules, _, limits = env
    handle_add(users, rules, limits, 555, "u", "vps")
    rule_id = rules.list_for_user(_uid(users))[0].id
    assert "已删除" in handle_delete(users, rules, 555, rule_id).text
    assert rules.list_for_user(_uid(users)) == []


def test_delete_unknown_rule(env) -> None:
    _, users, rules, _, _ = env
    assert "不存在" in handle_delete(users, rules, 555, 99999).text


def test_clear_removes_everything(env) -> None:
    _, users, rules, _, limits = env
    handle_add(users, rules, limits, 555, "u", "vps")
    assert "已清空" in handle_clear(users, rules, 555).text


def test_clear_on_empty_is_reported(env) -> None:
    _, users, rules, _, _ = env
    assert "还没有" in handle_clear(users, rules, 555).text


def test_test_command_counts_matches(env) -> None:
    _, _, _, items, _ = env
    items.insert_many(
        [
            NewItem(
                guid="1",
                title="VPS 补货",
                title_norm="vps 补货",
                link="https://x/1",
                published_at=_now(),
                fetched_at=_now(),
            ),
            NewItem(
                guid="2",
                title="显卡降价",
                title_norm="显卡降价",
                link="https://x/2",
                published_at=_now(),
                fetched_at=_now(),
            ),
        ]
    )
    result = handle_test(items, 555, "vps")
    assert "命中 1 条" in result.text
    assert "VPS 补货" in result.text


def test_test_command_reports_zero(env) -> None:
    _, _, _, items, _ = env
    assert "没有标题命中" in handle_test(items, 555, "不存在").text


def test_test_command_rejects_blank(env) -> None:
    _, _, _, items, _ = env
    assert "不能为空" in handle_test(items, 555, "  ").text


def test_help_mentions_core_commands() -> None:
    text = handle_help().text
    for command in ("/add", "/list", "/clear", "/test", "/help"):
        assert command in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_user_handlers.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'nodeseek_bot.bot'`

- [ ] **Step 3: 实现键盘**

`src/nodeseek_bot/bot/__init__.py` 只放模块 docstring。

`src/nodeseek_bot/bot/keyboards.py`

```python
"""Inline keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from nodeseek_bot.repository import RuleRow


def rule_list_keyboard(rules: list[RuleRow]) -> InlineKeyboardMarkup:
    """One delete button per rule, so users never have to type ids."""
    rows = [
        [InlineKeyboardButton(text=f"❌ {rule.keyword_raw}", callback_data=f"del:{rule.id}")]
        for rule in rules
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
```

- [ ] **Step 4: 实现处理器**

`src/nodeseek_bot/bot/user_handlers.py`。要求：

- `handle_add`：先 `expand_rules` 校验，空白或超长返回
  `"关键词不能为空，且长度不能超过 64 个字符。"`；
  捕获 `RuleLimitExceeded` 返回含 `"上限"` 的提示；
  捕获 `ValueError`（重复）返回含 `"已经"` 的提示。
- `handle_list`：空时返回含 `"还没有"` 的提示；否则
  `"1. xxx"` 编号列表 + `rule_list_keyboard`。
- `handle_delete`：删不到时返回含 `"不存在"` 的提示。
- `handle_clear`：返回 `"✅ 已清空 N 个关键词。"`，N 为 0 时返回含 `"还没有"`。
- `handle_test`：查 `recent_titles(24)` 并用 `matches` 过滤，
  返回 `f"…命中 {n} 条：…"`，最多展示 3 条；无命中返回含 `"没有标题命中"`。
- `handle_help`：列出 `/add`、`/list`、`/clear`、`/test`、`/invite`、`/help`，
  并说明「只匹配标题，多个词用空格分隔表示同时包含」。

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_user_handlers.py -q`
Expected: PASS，15 passed

- [ ] **Step 6: 提交**

```bash
git add src/nodeseek_bot/bot tests/test_user_handlers.py
git commit -m "feat: add user command handlers"
```

---

## Task 11: 邀请制入门

**Files:**
- Modify: `src/nodeseek_bot/repository.py`（新增 `InviteRepository`、`InviteQuotaExceeded`，
  `UserRepository` 增加 `find_by_tg_id` 与 `set_inviter`）
- Modify: `src/nodeseek_bot/bot/user_handlers.py`（新增 `handle_invite`、`handle_start`）
- Modify: `tests/test_user_handlers.py`

**Interfaces:**
- Consumes: `LimitsConfig`
- Produces:
  - `InviteQuotaExceeded` 异常
  - `InviteRepository(db)`：`outstanding(created_by: int) -> int`、
    `issue(created_by: int, *, quota: int) -> list[str]`、`redeem(code: str) -> int | None`
  - `UserRepository.find_by_tg_id(tg_user_id: int) -> int | None`、
    `set_inviter(user_id: int, inviter_id: int) -> None`
  - `handle_invite(users, invites, limits, tg_user_id, username=None) -> HandleResult`
  - `handle_start(users, invites, tg_user_id, username, payload) -> HandleResult`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_user_handlers.py` 追加：

```python
def test_invite_issues_a_code(env) -> None:
    from nodeseek_bot.bot.user_handlers import handle_invite
    from nodeseek_bot.repository import InviteRepository

    _, users, _, _, limits = env
    result = handle_invite(users, InviteRepository(env[0]), limits, 555, "u")
    assert "邀请码" in result.text


def test_invite_respects_quota(env) -> None:
    from nodeseek_bot.bot.user_handlers import handle_invite
    from nodeseek_bot.repository import InviteRepository

    _, users, _, _, limits = env
    invites = InviteRepository(env[0])
    handle_invite(users, invites, limits, 555, "u")
    handle_invite(users, invites, limits, 555, "u")
    handle_invite(users, invites, limits, 555, "u")
    assert "用完" in handle_invite(users, invites, limits, 555, "u").text


def test_redeem_returns_inviter_and_is_single_use(env) -> None:
    from nodeseek_bot.repository import InviteRepository

    db, users, _, _, _ = env
    invites = InviteRepository(db)
    inviter = _uid(users)
    (code,) = invites.issue(created_by=inviter, quota=1)
    assert invites.redeem(code) == inviter
    assert invites.redeem(code) is None


def test_redeem_rejects_unknown_code(env) -> None:
    from nodeseek_bot.repository import InviteRepository

    assert InviteRepository(env[0]).redeem("nope") is None


def test_start_without_payload_welcomes_new_user(env) -> None:
    from nodeseek_bot.bot.user_handlers import handle_start
    from nodeseek_bot.repository import InviteRepository

    db, users, _, _, _ = env
    result = handle_start(users, InviteRepository(db), 777, "new", None)
    assert "邀请码" in result.text


def test_start_without_payload_returns_help_for_known_user(env) -> None:
    from nodeseek_bot.bot.user_handlers import handle_start
    from nodeseek_bot.repository import InviteRepository

    db, users, _, _, _ = env
    _uid(users)
    assert "/add" in handle_start(users, InviteRepository(db), 555, "u", None).text


def test_start_with_valid_code_records_inviter(env) -> None:
    from nodeseek_bot.bot.user_handlers import handle_start
    from nodeseek_bot.repository import InviteRepository

    db, users, _, _, _ = env
    invites = InviteRepository(db)
    inviter = _uid(users)
    (code,) = invites.issue(created_by=inviter, quota=1)
    assert "欢迎" in handle_start(users, invites, 777, "new", code).text
    with db.tx() as conn:
        row = conn.execute(
            "SELECT invited_by FROM users WHERE tg_user_id = 777"
        ).fetchone()
    assert row["invited_by"] == inviter


def test_start_with_invalid_code_is_rejected(env) -> None:
    from nodeseek_bot.bot.user_handlers import handle_start
    from nodeseek_bot.repository import InviteRepository

    db, users, _, _, _ = env
    assert "无效" in handle_start(users, InviteRepository(db), 777, "new", "bad").text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_user_handlers.py -q`
Expected: FAIL，`ImportError: cannot import name 'InviteRepository'`

- [ ] **Step 3: 实现仓储**

在 `src/nodeseek_bot/repository.py` 顶部加 `import secrets`，并追加
`InviteQuotaExceeded`、`InviteRepository`，以及 `UserRepository` 的两个新方法。

- `outstanding` 统计 `used_count < max_uses` 且未过期的码。
- `issue` 先检查 `outstanding >= quota` 则抛 `InviteQuotaExceeded`；
  否则用 `secrets.token_urlsafe(9)` 生成一次性码。
- `redeem` 校验存在、未过期、未用完，成功则 `used_count + 1` 并返回
  `created_by`，否则返回 `None`。

- [ ] **Step 4: 实现用户侧处理器**

在 `src/nodeseek_bot/bot/user_handlers.py` 追加 `handle_invite` 与 `handle_start`：

- `handle_invite`：捕获 `InviteQuotaExceeded`，返回含 `"用完"` 的提示；
  成功时返回码并提示对方发送 `/start <code>`。
- `handle_start`：`payload` 为空时——未知用户返回含 `"邀请码"` 的说明，
  已知用户返回 `handle_help()`；`payload` 有效时兑换、用
  `users.set_inviter` 记录邀请人，返回含 `"欢迎"` 的开场白；
  无效时返回含 `"无效"` 的提示。

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_user_handlers.py -q`
Expected: PASS，23 passed

- [ ] **Step 6: 提交**

```bash
git add src/nodeseek_bot/repository.py src/nodeseek_bot/bot/user_handlers.py tests/test_user_handlers.py
git commit -m "feat: add invite-only onboarding"
```

---

## Task 12: 管理员指令

**Files:**
- Create: `src/nodeseek_bot/bot/admin_handlers.py`, `tests/test_admin_handlers.py`
- Modify: `src/nodeseek_bot/repository.py`（`UserRepository.set_banned`）

**Interfaces:**
- Consumes: 全部仓储、`HandleResult`
- Produces:
  - `AdminDeps` 数据类：`db`、`users`、`rules`、`deliveries`、`items`、`meta`、`admins: list[int]`
  - `handle_admin(deps: AdminDeps, tg_user_id: int, text: str) -> HandleResult`
  - `UserRepository.set_banned(tg_user_id: int, banned: bool) -> bool`

支持的子命令：`/admin stats`、`/admin top`、`/admin users`、
`/admin ban <tg_user_id>`、`/admin unban <tg_user_id>`、`/admin health`。

- [ ] **Step 1: 写失败的测试**

`tests/test_admin_handlers.py`

```python
from pathlib import Path

from nodeseek_bot.bot.admin_handlers import AdminDeps, handle_admin
from nodeseek_bot.db import Database
from nodeseek_bot.repository import (
    DeliveryRepository,
    ItemRepository,
    MetaRepository,
    RuleRepository,
    UserRepository,
)


def _deps(tmp_path: Path, admins: list[int]) -> AdminDeps:
    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    return AdminDeps(
        db=db,
        users=UserRepository(db),
        rules=RuleRepository(db),
        deliveries=DeliveryRepository(db),
        items=ItemRepository(db),
        meta=MetaRepository(db),
        admins=admins,
    )


def test_non_admin_is_rejected(tmp_path: Path) -> None:
    assert "权限" in handle_admin(_deps(tmp_path, [1]), 999, "/admin stats").text


def test_stats_reports_counts(tmp_path: Path) -> None:
    deps = _deps(tmp_path, [1])
    uid = deps.users.ensure(tg_user_id=555, username="u")
    deps.rules.add(uid, "vps", limit=3)
    text = handle_admin(deps, 1, "/admin stats").text
    assert "用户 1" in text
    assert "关键词 1" in text


def test_top_lists_keywords_with_counts(tmp_path: Path) -> None:
    deps = _deps(tmp_path, [1])
    for tg_id in (555, 556):
        uid = deps.users.ensure(tg_user_id=tg_id, username=None)
        deps.rules.add(uid, "vps", limit=3)
    text = handle_admin(deps, 1, "/admin top").text
    assert "vps" in text
    assert "2 人" in text


def test_top_on_empty_state(tmp_path: Path) -> None:
    assert "还没有" in handle_admin(_deps(tmp_path, [1]), 1, "/admin top").text


def test_users_lists_recent(tmp_path: Path) -> None:
    deps = _deps(tmp_path, [1])
    deps.users.ensure(tg_user_id=555, username="alice")
    text = handle_admin(deps, 1, "/admin users").text
    assert "555" in text
    assert "alice" in text


def test_ban_then_unban(tmp_path: Path) -> None:
    deps = _deps(tmp_path, [1])
    deps.users.ensure(tg_user_id=555, username="u")
    assert "已封禁" in handle_admin(deps, 1, "/admin ban 555").text
    assert "已解封" in handle_admin(deps, 1, "/admin unban 555").text


def test_ban_unknown_user(tmp_path: Path) -> None:
    assert "找不到" in handle_admin(_deps(tmp_path, [1]), 1, "/admin ban 404").text


def test_ban_requires_numeric_id(tmp_path: Path) -> None:
    assert "数字" in handle_admin(_deps(tmp_path, [1]), 1, "/admin ban abc").text


def test_health_reports_last_success(tmp_path: Path) -> None:
    deps = _deps(tmp_path, [1])
    deps.meta.set("last_success_at", "2026-09-21T17:19:30+00:00")
    assert "2026-09-21T17:19:30+00:00" in handle_admin(deps, 1, "/admin health").text


def test_health_handles_never_polled(tmp_path: Path) -> None:
    assert "从未" in handle_admin(_deps(tmp_path, [1]), 1, "/admin health").text


def test_unknown_subcommand_lists_help(tmp_path: Path) -> None:
    text = handle_admin(_deps(tmp_path, [1]), 1, "/admin nonsense").text
    assert "stats" in text and "top" in text and "health" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_admin_handlers.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'nodeseek_bot.bot.admin_handlers'`

- [ ] **Step 3: 实现**

`src/nodeseek_bot/bot/admin_handlers.py`

```python
"""Admin command handlers."""

from __future__ import annotations

from dataclasses import dataclass

from nodeseek_bot.bot.user_handlers import HandleResult
from nodeseek_bot.db import Database
from nodeseek_bot.repository import (
    DeliveryRepository,
    ItemRepository,
    MetaRepository,
    RuleRepository,
    UserRepository,
)

TOP_LIMIT = 20
USER_LIST_LIMIT = 50
HELP = (
    "可用指令：\n"
    "/admin stats — 总览\n"
    "/admin top — 关键词订阅排行\n"
    "/admin users — 最近用户\n"
    "/admin ban <tg_user_id>\n"
    "/admin unban <tg_user_id>\n"
    "/admin health — 轮询健康状态"
)


@dataclass(slots=True)
class AdminDeps:
    db: Database
    users: UserRepository
    rules: RuleRepository
    deliveries: DeliveryRepository
    items: ItemRepository
    meta: MetaRepository
    admins: list[int]


def handle_admin(deps: AdminDeps, tg_user_id: int, text: str) -> HandleResult:
    if tg_user_id not in deps.admins:
        return HandleResult("你没有权限执行该指令。")
    parts = text.strip().split()
    command = parts[1].lower() if len(parts) > 1 else ""
    args = parts[2:]

    if command == "stats":
        return HandleResult(_stats(deps))
    if command == "top":
        return HandleResult(_top(deps))
    if command == "users":
        return HandleResult(_users(deps))
    if command == "health":
        return HandleResult(_health(deps))
    if command == "ban" and args:
        return HandleResult(_set_ban(deps, args[0], banned=True))
    if command == "unban" and args:
        return HandleResult(_set_ban(deps, args[0], banned=False))
    return HandleResult(HELP)


def _stats(deps: AdminDeps) -> str:
    with deps.db.tx() as conn:

        def count(sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0])

        users = count("SELECT COUNT(*) FROM users")
        banned = count("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        rules = count("SELECT COUNT(*) FROM rules WHERE enabled = 1")
        items = count("SELECT COUNT(*) FROM items")
        pending = count("SELECT COUNT(*) FROM deliveries WHERE status = 'pending'")
        sent = count("SELECT COUNT(*) FROM deliveries WHERE status = 'sent'")
        failed = count("SELECT COUNT(*) FROM deliveries WHERE status = 'failed'")

    return (
        f"用户 {users}（封禁 {banned}）\n"
        f"关键词 {rules}\n"
        f"已抓取帖子 {items}\n"
        f"投递：待发 {pending} / 已发 {sent} / 失败 {failed}"
    )


def _top(deps: AdminDeps) -> str:
    rows = deps.rules.top_keywords(limit=TOP_LIMIT)
    if not rows:
        return "还没有任何关键词。"
    lines = [f"{i}. {word} — {n} 人" for i, (word, n) in enumerate(rows, start=1)]
    return "关键词订阅排行：\n" + "\n".join(lines)


def _users(deps: AdminDeps) -> str:
    with deps.db.tx() as conn:
        rows = conn.execute(
            """
            SELECT u.tg_user_id, u.username, u.is_banned, p.tg_user_id AS inviter,
                   (SELECT COUNT(*) FROM rules r WHERE r.user_id = u.id) AS rules
            FROM users u LEFT JOIN users p ON p.id = u.invited_by
            ORDER BY u.id DESC LIMIT ?
            """,
            (USER_LIST_LIMIT,),
        ).fetchall()
    if not rows:
        return "还没有用户。"
    lines = [
        f"{row['tg_user_id']} @{row['username'] or '-'} 规则{row['rules']}"
        f"{' 已封禁' if row['is_banned'] else ''}"
        f" 邀请人{row['inviter'] or '-'}"
        for row in rows
    ]
    return f"最近 {USER_LIST_LIMIT} 个用户：\n" + "\n".join(lines)


def _health(deps: AdminDeps) -> str:
    last_success = deps.meta.get("last_success_at") or "从未成功"
    last_error = deps.meta.get("last_error") or "无"
    return f"最后一次成功轮询：{last_success}\n最近错误：{last_error}"


def _set_ban(deps: AdminDeps, raw_id: str, *, banned: bool) -> str:
    try:
        tg_user_id = int(raw_id)
    except ValueError:
        return "请提供数字 user id。"
    if not deps.users.set_banned(tg_user_id, banned):
        return "找不到该用户。"
    return f"已{'封禁' if banned else '解封'} {tg_user_id}。"
```

`UserRepository.set_banned` 已在 Task 6 实现，本任务直接复用。若当时遗漏，按下面补上：

```python
    def set_banned(self, tg_user_id: int, banned: bool) -> bool:
        with self._db.tx() as conn:
            cur = conn.execute(
                "UPDATE users SET is_banned = ? WHERE tg_user_id = ?",
                (1 if banned else 0, tg_user_id),
            )
        return cur.rowcount > 0
```

注意：封禁只阻止后续投递（`all_enabled` 已过滤），**不会**删除历史数据。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_admin_handlers.py -q`
Expected: PASS，11 passed

- [ ] **Step 5: 提交**

```bash
git add src/nodeseek_bot/bot/admin_handlers.py src/nodeseek_bot/repository.py tests/test_admin_handlers.py
git commit -m "feat: add admin commands"
```

---

## Task 13: 组装、匹配循环与 CLI 入口

**Files:**
- Create: `src/nodeseek_bot/runtime.py`, `src/nodeseek_bot/__main__.py`, `tests/test_runtime.py`

**Interfaces:**
- Consumes: 其余全部模块
- Produces:
  - `Runtime` 数据类：`db`、`bot`、`source`、`poller`、`notifier`、`queue`，
    方法 `match_batch(batch) -> int`、`async run() -> None`、
    `async aclose() -> None`（关数据源、Bot session、数据库）、`close() -> None`（只关数据库）
  - `build_runtime(config: Config) -> Runtime`
  - `run_maintenance(config: Config) -> int`
  - `main(argv: list[str] | None = None) -> int`，支持 `--config PATH`、
    `--maintenance`、`--log-level`

- [ ] **Step 1: 写失败的测试**

`tests/test_runtime.py`

```python
from datetime import UTC, datetime
from pathlib import Path

from nodeseek_bot.config import Config, StorageConfig, TelegramConfig
from nodeseek_bot.runtime import build_runtime, run_maintenance


def _config(tmp_path: Path) -> Config:
    return Config(
        telegram=TelegramConfig(bot_token="1:x", admin_user_ids=[]),
        storage=StorageConfig(db_path=str(tmp_path / "bot.db")),
    )


def test_build_runtime_creates_the_database(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    runtime = build_runtime(cfg)
    assert Path(cfg.storage.db_path).exists()
    runtime.close()


def test_build_runtime_wires_poller_and_notifier(tmp_path: Path) -> None:
    runtime = build_runtime(_config(tmp_path))
    assert runtime.poller is not None
    assert runtime.notifier is not None
    assert runtime.queue.maxsize == 0
    runtime.close()


def test_maintenance_purges_old_items(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    runtime = build_runtime(cfg)
    assert runtime.poller is not None
    runtime.close()
    assert run_maintenance(cfg) == 0


def test_main_reports_config_errors(tmp_path: Path) -> None:
    from nodeseek_bot.__main__ import main

    assert main(["--config", str(tmp_path / "missing.toml")]) == 2


async def test_match_batch_creates_deliveries(tmp_path: Path) -> None:
    from nodeseek_bot.repository import (
        DeliveryRepository,
        ItemRepository,
        NewItem,
        RuleRepository,
        UserRepository,
    )
    from nodeseek_bot.sources import RawItem

    runtime = build_runtime(_config(tmp_path))
    uid = UserRepository(runtime.db).ensure(tg_user_id=1, username=None)
    RuleRepository(runtime.db).add(uid, "vps", limit=3)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    ItemRepository(runtime.db).insert_many(
        [
            NewItem(
                guid="1",
                title="VPS 补货",
                title_norm="vps 补货",
                link="https://x/1",
                published_at=now,
                fetched_at=now,
            )
        ]
    )
    item = RawItem(
        guid="1",
        title="VPS 补货",
        link="https://x/1",
        published_at=datetime.now(UTC),
    )
    assert runtime.match_batch([item]) == 1
    assert runtime.match_batch([item]) == 0  # 同一条帖子不会被重复投递
    assert DeliveryRepository(runtime.db).pending_users() == [uid]
    runtime.close()


async def test_match_batch_without_rules_is_a_noop(tmp_path: Path) -> None:
    from nodeseek_bot.sources import RawItem

    runtime = build_runtime(_config(tmp_path))
    item = RawItem(
        guid="1",
        title="VPS 补货",
        link="https://x/1",
        published_at=datetime.now(UTC),
    )
    assert runtime.match_batch([item]) == 0
    runtime.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_runtime.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'nodeseek_bot.runtime'`

- [ ] **Step 3: 实现 runtime**

`src/nodeseek_bot/runtime.py` 要点：

- `build_runtime` 依次创建 `Database`（并 `initialize()`）、`MetaRepository`、
  `asyncio.Queue`、aiogram `Bot`、`AlertDispatcher`、`NodeSeekSource`，
  最后组装 `Poller` 与 `Notifier`。
- `Runtime.run()` 用 `asyncio.TaskGroup` 并发跑三个循环：
  `poller.run()`、`notifier.run()`、`_match_loop()`。
- `_match_loop()` 从队列取一批（上限 `MATCH_BATCH_LIMIT = 500`），
  读取 `rules.all_enabled()`，对每条 item 用 `normalize` + `matches` 找出命中规则，
  汇总成 `(guid, rule_id, user_id)` 元组列表交给
  `DeliveryRepository.create_pending`。
- `normalize` 必须在模块顶层导入，不要放在循环里。
- `run_maintenance` 初始化数据库、调用 `purge_older_than(retention_days)`、关闭连接、返回 0。
- `match_batch` 必须是公开方法：它是唯一把「匹配」和「投递入库」连起来的地方，
  单独暴露才能在测试里直接驱动（队列 + 循环本身不需要测试）。

- [ ] **Step 4: 实现 CLI**

`src/nodeseek_bot/__main__.py`：`argparse` 解析 `--config`（默认 `config.toml`）、
`--maintenance`、`--log-level`（默认 `INFO`）；配置错误时 log 并返回 2；
普通模式 `asyncio.run(runtime.run())`，`KeyboardInterrupt` 时优雅退出，
`finally` 里 `asyncio.run(runtime.aclose())`（Bot 的 aiohttp session 只能异步关）。

- [ ] **Step 5: 全量验证**

Run: `pytest -q`
Run: `ruff check .`
Run: `ruff format --check .`
Expected: 全部通过

- [ ] **Step 6: 提交**

```bash
git add src/nodeseek_bot/runtime.py src/nodeseek_bot/__main__.py tests/test_runtime.py
git commit -m "feat: wire runtime and add cli entry point"
```

---

## Task 14: 部署

**目标环境（已实测，不是假设）：** Debian 12 bookworm / aarch64 / 4 核 23 GiB /
Python 3.11.2（**唯一版本**）/ systemd 252 / 时区 Asia/Shanghai。
该机器此前运行过代理服务（x-ui，现已卸载）。本项目使用 long polling，
不占用任何入站端口。
git、curl、pip3 已装；`python3-venv` 需要补装；标准库全部满足，无其他系统依赖。

**Files:**
- Create: `deploy/nodeseek-bot.service`, `docs/deployment.md`

**Interfaces:**
- Consumes: Task 13 的 CLI 与 `--maintenance` 标志
- Produces: 一份可照抄的 systemd 部署流程

- [ ] **Step 1: 写 systemd unit**

`deploy/nodeseek-bot.service`

```ini
[Unit]
Description=NodeSeek keyword monitor bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=nodeseek
Group=nodeseek
WorkingDirectory=/opt/nodeseek-bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/nodeseek-bot/.venv/bin/nodeseek-bot --config /etc/nodeseek-bot/config.toml
Restart=always
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/nodeseek-bot

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: 写部署文档**

`docs/deployment.md` 必须给出可直接复制、无需思考的命令。开头先附目标环境实测表
（取自 spec 第 15.1 节），然后按下面的顺序写：

    uname -m                                   # 期望 aarch64
    python3 -V                                 # 期望 Python 3.11.2
    cat /etc/debian_version                    # 期望 12.x

**1. 补装 venv 支持**（git / curl / pip3 已存在，无需安装）

    apt update && apt install -y python3-venv

**2. 建系统用户**

    useradd --system --home /opt/nodeseek-bot --shell /usr/sbin/nologin nodeseek

**3. 取代码**

    git clone <你的仓库地址> /opt/nodeseek-bot

**4. 建 venv 并安装**

    python3 -m venv /opt/nodeseek-bot/.venv
    /opt/nodeseek-bot/.venv/bin/pip install --upgrade pip
    /opt/nodeseek-bot/.venv/bin/pip install /opt/nodeseek-bot

若报 `ensurepip is not available`，说明第 1 步没生效：重跑第 1 步，
删掉 `.venv` 目录再来一遍。

**5. 建配置**

    mkdir -p /etc/nodeseek-bot
    cp /opt/nodeseek-bot/config.example.toml /etc/nodeseek-bot/config.toml
    nano /etc/nodeseek-bot/config.toml
    chmod 600 /etc/nodeseek-bot/config.toml

**6. 建数据目录**，并确保配置里 `db_path = "/var/lib/nodeseek-bot/bot.db"`

    mkdir -p /var/lib/nodeseek-bot
    chown nodeseek:nodeseek /var/lib/nodeseek-bot

**7. 装 unit 并启动**

    cp /opt/nodeseek-bot/deploy/nodeseek-bot.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now nodeseek-bot

**8. 观察**

    systemctl status nodeseek-bot
    journalctl -u nodeseek-bot -f

**9. 每日清理过期数据**

    crontab -e
    # 追加：
    0 4 * * * /opt/nodeseek-bot/.venv/bin/nodeseek-bot --config /etc/nodeseek-bot/config.toml --maintenance

**10. 升级**

    cd /opt/nodeseek-bot && git pull
    .venv/bin/pip install .
    systemctl restart nodeseek-bot

**11. 明确不要做的事**（放在文档显眼位置）

- 不要把 `interval_seconds` 调到 20 以下。这台机器曾用于运行代理服务，
  应把该 IP 当作敏感基础设施：被目标站标记的代价可能超出本项目本身。
- 不要改成 webhook 模式。本机只有内网地址（Oracle NAT），
  配置 webhook 需要额外的端口、域名与 TLS 证书，收益为零。
- 不要把 `config.toml` 提交进仓库。

- [ ] **Step 3: 在真实服务器上照文档走一遍**

Expected：`systemctl status nodeseek-bot` 显示 `active (running)`；
`journalctl` 无异常；给 Bot 发 `/start` 有响应；
在 NodeSeek 上出现命中关键词的新帖后收到私聊通知。

- [ ] **Step 4: 提交**

```bash
git add deploy docs/deployment.md
git commit -m "docs: add systemd unit and deployment guide"
```

---

## Task 15: 开源文档收尾

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`
- Create: `README.en.md`

**Interfaces:**
- Consumes: 全部已完成功能
- Produces: 可对外公开的仓库首页

- [ ] **Step 1: 写 README.md**

按以下顺序组织，每个小节都要有实际内容：

1. 项目名 + 一句话简介 + CI 与许可证徽章
2. 功能特性：每用户 3 个关键词、仅匹配标题、空格表示 AND、
   多条命中自动合并、邀请制、管理员指令
3. 工作原理：`poller → matcher → notifier` 示意图，
   并说明只读取公开 RSS、不需要账号
4. 快速开始：BotFather 建 bot → 用 @userinfobot 查自己的 user id →
   填写配置 → 运行
5. 配置说明：逐项对应 `config.example.toml`，
   并解释为什么默认是 30 秒（源站不做 CDN 缓存，每次请求都打到源站）
6. 部署：一句摘要 + 指向 `docs/deployment.md`
7. 常见问题：延迟为什么是几十秒；为什么只支持 NodeSeek；
   为什么不做全文匹配；收不到通知怎么排查（用 `/test`）
8. 免责声明
9. 许可证

免责声明必须逐条写明：

- 只读取 NodeSeek 公开的 RSS，不使用任何账号凭据。
- 与 NodeSeek 官方无关，未获其背书。
- 帖子内容版权归原发布者，本项目仅转发标题与原始链接。
- 请保持礼貌的轮询频率，默认值已按此设定，不建议调低。
- 使用者需自行遵守目标站点的服务条款。

- [ ] **Step 2: 写 README.en.md**

英文版与中文版内容一一对应，两个文件顶部互相链接。

- [ ] **Step 3: 更新 CHANGELOG.md**

```markdown
## [0.1.0] - 2026-09-22

### Added

- 监控 NodeSeek 新主题并按关键词匹配推送
- 每用户最多 3 个关键词，空格分隔表示 AND
- 同一用户的多条命中自动合并为一条消息
- 全局限速、429 退避、发送失败重试
- 邀请制注册与一次性邀请码
- 管理员指令：stats / top / users / ban / unban / health
- 轮询失败退避与窗口溢出告警
```

- [ ] **Step 4: 更新 CONTRIBUTING.md**

写明开发环境搭建、`pytest`、`ruff check .`、`ruff format .`、
提交信息用 Conventional Commits、新增功能必须带测试、
**不得提交 `config.toml`**。

- [ ] **Step 5: 终检**

Run: `ruff check .`
Run: `ruff format --check .`
Run: `pytest -q`
Run: `git status --short`
Expected：测试全绿；`git status` 中不出现 `config.toml`、`*.db`、`data/`

- [ ] **Step 6: 提交并打标签**

```bash
git add -A
git commit -m "docs: complete open source documentation"
git tag -a v0.1.0 -m "v0.1.0"
```

---

## Task 16: Telegram 接线（原计划遗漏，补做）

Task 10–12 只产出了**纯函数**处理器，Task 13 只组装了 poller / matcher / notifier。
结果是：进程能跑、能推送，但**没有任何代码在接收 Telegram 更新**，
`/add` 发出去不会有任何反应。这是原 15 个任务里的一个真实缺口，现已补上。

**Files:**
- Create: `src/nodeseek_bot/bot/router.py`, `src/nodeseek_bot/bot/dispatcher.py`
- Create: `tests/test_router.py`, `tests/test_dispatcher.py`
- Modify: `src/nodeseek_bot/runtime.py`

**Interfaces:**
- `BotDeps` 数据类（`db`、`users`、`rules`、`items`、`invites`、`deliveries`、`meta`、
  `limits`、`admins`）与 `build_bot_deps(db, config) -> BotDeps`
- `parse_command(text) -> (command, args)`，能处理 `/add@bot 日本 vps`
- `route_message(deps, tg_user_id, username, text) -> HandleResult`（纯函数）
- `route_callback(deps, tg_user_id, data) -> HandleResult | None`（纯函数）
- `build_dispatcher(deps) -> Dispatcher`

**要点：**

- **命令解析放在路由层**，aiogram 只负责把 update 交给路由并把 `HandleResult`
  发出去。这样「邀请制放行逻辑」「`/del 编号` 的编号映射」这些容易写错的地方
  全都能用纯函数测试覆盖。
- **邀请制在这里才真正生效**：未通过邀请码加入的用户，除了 `/start`、`/help`
  和（管理员的）`/admin` 之外，一律回复邀请说明，且**不会**被写进 `users` 表。
- `/add` 与 `/test` 要把多个参数用空格拼回去，否则 `/add 日本 vps` 只会加「日本」。
- `/del <编号>` 的编号是 `/list` 里显示的序号（1 起），路由层负责换算成 `rules.id`；
  `handle_delete` 内部按 `user_id` 过滤，所以按钮回调也无法删别人的规则。
- `Runtime` 增加 `dispatcher` 字段，`run()` 用 `asyncio.TaskGroup` 跑**四个**循环。
- `start_polling(handle_signals=False, close_bot_session=False)`：
  信号交给 `asyncio.run`，session 由 `Runtime.aclose()` 统一关闭，避免双重管理。
- 启动时先 `delete_webhook(drop_pending_updates=True)`：
  轮询与 webhook 互斥，残留的 webhook 会让 `getUpdates` 报 409；
  同时丢掉离线期间积压的旧指令。失败只记警告，不阻断启动。
- `tests/test_dispatcher.py` 用一个 `BaseSession` 假实现（`make_request` 返回
  `check_response` 造出来的假响应）驱动真实 `Dispatcher`，
  这样「处理器有没有注册上」这类 bug 会被测试抓住。

---

## 上线后回填

第一次真实运行满 24 小时后，把三个实测数字回填进文档：

1. 30 秒轮询的成功率 → 写进 README 的常见问题
2. feed 自身的新鲜度下限 → 决定 README 里如何描述延迟，替换目前模糊的「几十秒」
3. 单次新增条数的峰值 → 决定 `config.example.toml` 里 `overflow_threshold` 的取值与注释

spec 第 18 节的开放问题列表在回填后同步关闭。

---

## 实现记录（Task 1–15 已全部完成）

实现期间发现计划本身有 10 处错误，均已就地修正（计划正文已同步）：

1. Task 3 `test_create_pending_is_idempotent`：`deliveries.rule_id` 有外键约束，
   直接塞 `rule_id=1` 会 IntegrityError。改为先建用户和规则，用真实 id。
2. Task 3 `test_tx_rolls_back_on_error`：嵌套 `with` 触发 ruff SIM117，合并成一个 `with`。
3. Task 5 `test_cdata_and_entities_are_decoded`：断言写错了。
   **CDATA 里的 `&amp;` 不是实体**，解析出来就是字面量 `&amp;`。
   拆成两个测试：实体（非 CDATA）会解码；CDATA 内容保持字面量
   ——后者正是 Telegram 需要 HTML 转义的原因。
4. Task 5/7 全部 `datetime.UTC` 用法：ruff UP017 要求用 `UTC` 别名，不是 `timezone.utc`。
5. Task 6 `test_matches`：`("日本vps补货", "日本 vps")` 的期望写成了 `False`，
   但子串 AND 语义下两个词都在标题里，正确答案是 `True`。负例改用 `日本 显卡 补货`。
6. Task 7 测试用 `poller._config = ...` 改私有属性：改为把 `PollerConfig` 传进
   `_build(...)`，测试不再碰私有成员。
7. Task 7 `FakeSource` 在脚本批次用完后会 `IndexError`，被 `run()` 当成失败，
   导致「成功重置计数」的测试偶发失败。改为批次为空时返回 `[]`。
8. Task 8 `test_build_messages_handles_one_very_long_title` 与标题截断（120 字符）
   自相矛盾：截断之后 5000 字的标题只产生 1 条消息。断言改为「1 条消息且很短」。
9. Task 8 测试 poke `notifier._cooldown_until`：把该字典改成公开属性 `cooldown_until`。
10. Task 8 实现里 `DeliveryRepository` 被写了两遍（第二个定义会遮蔽第一个，
    丢掉 `create_pending`）：合并成一个类。
11. **原计划没有 Telegram 接线层**（见 Task 16）：15 个任务做完，bot 仍不会
    响应任何指令。已补 `bot/router.py` + `bot/dispatcher.py` 与两组测试。

---

## Task 17: 部署时被配置报错卡住（补做）

第一次真机部署在 `--grant` 那一步就失败了：

```
ERROR nodeseek_bot: 配置错误：invalid TOML in /etc/nodeseek-bot/config.toml:
Expected '=' after a key in a key/value pair (at line 1, column 7)
```

`config.example.toml` 本身合法（已用 `tomllib` 校验，无 BOM，首行是注释），
是手改之后的文件第一行被改坏了。但原报错只给了「第 1 行第 7 列」：
既看不到出错那一行的原文，也无从知道列号从哪个字符开始数，排查只能靠猜。

改动（`src/nodeseek_bot/config.py`）：

- `_read_text()`：改用 `read_bytes()` + `decode("utf-8-sig")`。
  编辑器留下的 BOM 会被直接吃掉；文件不是 UTF-8（例如被记事本另存成 GBK）时
  抛 `ConfigError` 并说明第几个字节坏了，而不是冒出一堆 `UnicodeDecodeError` 堆栈。
- `_point_at()`：从异常消息里抠出 `line`/`column`，把出错那一行的原文和 `^` 一起打印。
  `tomllib.TOMLDecodeError` 在 3.11/3.12 上都没有 `lineno`/`colno` 属性，
  位置只存在于消息文本里，所以只能用正则从消息里反向解析。

现在的输出（`^` 正好指向第 7 列，也就是 `123456` 后面那个 `:`）：

```
ERROR nodeseek_bot: 配置错误：invalid TOML in .tmp/bad.toml:
Expected '=' after a key in a key/value pair (at line 1, column 7)
     1 | 123456:AAHdqTcvCH1vGWJxf
       |       ^
```

`tests/test_config.py` 新增 4 个测试：BOM 文件可读、非 UTF-8 抛 `ConfigError`、
出错行原文出现在消息里、第 3 行出错时指向第 3 行。

---

## Task 18: 邀请制可配置（上线后补做）

上线后提出的三个需求：改每人关键词上限、关掉邀请码、改成每人一个固定邀请码。
第一个本来就是 `limits.max_rules_per_user`，另外两个需要新开关。

新增配置（`LimitsConfig`）：

- `invite_required: bool = True` —— `False` 时不再拦未加入的用户，`/start` 直接开户。
- `invite_mode: str = "one_time"` —— `"fixed"` 时 `/invite` 返回同一个可反复使用的码。
  取值在 `load_config` 里校验（`INVITE_MODES`），写错了直接报 `ConfigError`，而不是偷偷当成 one_time。

几个实现细节：

- **不改 schema**。`invites.max_uses` 本来就存在，固定码就是 `max_uses = 1_000_000` 的普通行（`FIXED_MAX_USES`）。这台机器上已经有在跑的数据库，能不迁移就不迁移。
- **开放注册时 `/start` 忽略码**：否则用户会收到一句不可理解的「邀请码无效」。
- `NEED_INVITE_TEXT` 从 `router.py` 移到 `user_handlers.py`：同一句话原本在两个文件里各写了一遍。

测试新增 10 个：固定码可重复 redeem、开放注册下 `/start` 和其他指令都能直接用、`/invite` 提示不需要邀请码、`invite_mode` 校验。

---

## Task 19: 开源收尾与一键脚本

上线后提出的三件事：清掉与项目无关的东西、让仓库可以直接推到 GitHub、给部署加一键脚本。

**清理**：删掉本地残留（`.venv` 残骸、各种 cache、沙箱临时目录）；
把文档里只属于原机器的内容改成通用表述——
代理服务（x-ui）的历史、以及那次高频探测造成的「源站没有限流」结论，
都不该出现在公开仓库里。后者尤其重要：**那个结论是错的**，
§15.2 已按后来真实遇到的 429 重写。

**脚本**（`scripts/install.sh`、`scripts/update.sh`）：

- 用 POSIX sh 写（不用 bash 专有语法），Debian 的 dash 能跑，`/bin/sh` 就行。
- 幂等：已有配置不覆盖、不碰数据库、重复跑不炸。
- `install.sh` 把管理员 id 从写好的配置里读回来再 `--grant`，避免「配置里的 id 和授权用的 id 不一致」。
- 交互提示从 `/dev/tty` 读，所以 `curl | sh` 也能用（管道会把 stdin 吃掉，直接 `read` 会立刻返回空）。
- 配置改写交给一段内嵌的 Python，而不是 sed：token 里带 `:`、路径带 `/`，用 sed 要写一堆转义。
- `update.sh` 在换代码之前先停服务：SQLite 的 WAL 在干净退出时会并回主库，这样 `cp` 出来的备份才是完整的。

**验证**：本机没有 bash/dash 可用（沙箱禁止 msys 创建管道），所以 `sh -n` 这类语法检查做不了。改为把脚本里内嵌的两段 Python 抽出来真跑一遍：
填配置、读回管理员 id、非法输入报错，全部验证通过。
shell 部分只能在服务器上首次执行时验证。

## 发布前还需要人工做的事

- ~~占位符~~已换成 `PaopaoSugar`（`LICENSE`、`pyproject.toml`、两份 README、`docs/deployment.md`、两个脚本）。
- `tests/fixtures/nodeseek_sample.xml` 是按实测结构**仿写**的样本（本地无外网）。
  建议在服务器上 `curl -s https://rss.nodeseek.com/ -o /tmp/feed.xml` 抓一份真样本替换，
  结构一致，测试无需改动。
- 服务器上跑一遍完整测试（本机是 3.12，服务器 3.11.2）：
  `cd /opt/nodeseek-bot && .venv/bin/python -m pytest -q`
  需要在服务器上 `pip install -e ".[dev]"` 或至少装 pytest。
- 仓库已经 `git init` 并提交（见 Task 19）；
  `config.toml`、`.deps/`、`data/`、`dist/`、`*.db` 都已在 `.gitignore` 里。
