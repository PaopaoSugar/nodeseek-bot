"""Configuration loading."""

from __future__ import annotations

import dataclasses
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

INVITE_MODES = frozenset({"one_time", "fixed"})

# tomllib only reports the position inside the message text, with no
# lineno/colno attributes to read, so the position is parsed back out.
_TOML_POSITION = re.compile(r"\(at line (?P<line>\d+), column (?P<column>\d+)\)")


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
    invite_required: bool = True
    invite_mode: str = "one_time"


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


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        # utf-8-sig silently drops a BOM left behind by an editor
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"config file is not valid UTF-8: {path} ({exc.reason} at byte {exc.start})"
        ) from exc


def _point_at(text: str, exc: tomllib.TOMLDecodeError) -> str:
    """Return the offending line plus a caret, so nobody counts columns."""
    match = _TOML_POSITION.search(str(exc))
    if match is None:
        return ""
    number = int(match.group("line"))
    lines = text.splitlines()
    if not 1 <= number <= len(lines):
        return ""
    caret = " " * (int(match.group("column")) - 1)
    return f"\n  {number:>4} | {lines[number - 1]}\n       | {caret}^"


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
    text = _read_text(path)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}{_point_at(text, exc)}") from exc

    telegram_payload = _section(data, "telegram")
    token = telegram_payload.get("bot_token")
    if not token or not isinstance(token, str):
        raise ConfigError("[telegram].bot_token is required")
    if ":" not in token:
        raise ConfigError("[telegram].bot_token does not look like a bot token")

    admin_ids = telegram_payload.get("admin_user_ids", [])
    if not isinstance(admin_ids, list) or not all(isinstance(i, int) for i in admin_ids):
        raise ConfigError("[telegram].admin_user_ids must be a list of integers")

    limits = _build(LimitsConfig, _section(data, "limits"), "limits")
    if limits.invite_mode not in INVITE_MODES:
        raise ConfigError(
            f"[limits].invite_mode must be one of {sorted(INVITE_MODES)}, "
            f"got {limits.invite_mode!r}"
        )

    return Config(
        telegram=TelegramConfig(bot_token=token, admin_user_ids=list(admin_ids)),
        poller=_build(PollerConfig, _section(data, "poller"), "poller"),
        notifier=_build(NotifierConfig, _section(data, "notifier"), "notifier"),
        limits=limits,
        storage=_build(StorageConfig, _section(data, "storage"), "storage"),
    )
