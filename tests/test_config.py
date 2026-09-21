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
    assert cfg.limits.invite_required is True
    assert cfg.limits.invite_mode == "one_time"
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


def test_invite_settings_are_read(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            MINIMAL
            + """
[limits]
invite_required = false
invite_mode = "fixed"
""",
        )
    )
    assert cfg.limits.invite_required is False
    assert cfg.limits.invite_mode == "fixed"


def test_unknown_invite_mode_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invite_mode"):
        load_config(_write(tmp_path, MINIMAL + '\n[limits]\ninvite_mode = "sometimes"\n'))


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


def test_bom_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"\xef\xbb\xbf" + MINIMAL.encode("utf-8"))
    assert load_config(path).telegram.bot_token == "123:abc"


def test_non_utf8_file_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes('[telegram]\nbot_token = "\u4e2d"\n'.encode("gbk"))
    with pytest.raises(ConfigError, match="not valid UTF-8"):
        load_config(path)


def test_toml_error_quotes_the_offending_line(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        load_config(_write(tmp_path, "123456:AAHdqTcv\n"))
    message = str(info.value)
    assert "at line 1, column 7" in message
    assert "123456:AAHdqTcv" in message
    assert "^" in message


def test_toml_error_on_a_later_line_points_at_that_line(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        load_config(_write(tmp_path, "\n[telegram]\nbot_token 123\n"))
    message = str(info.value)
    assert "at line 3" in message
    assert "bot_token 123" in message


def test_feed_url_default(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, MINIMAL))
    assert cfg.poller.feed_url == "https://rss.nodeseek.com/"
