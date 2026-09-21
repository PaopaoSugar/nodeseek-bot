from pathlib import Path

import pytest

from nodeseek_bot.bot.router import BotDeps, build_bot_deps, route_callback, route_message
from nodeseek_bot.config import Config, LimitsConfig, StorageConfig, TelegramConfig
from nodeseek_bot.db import Database


@pytest.fixture()
def deps(tmp_path: Path):
    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    config = Config(
        telegram=TelegramConfig(bot_token="1:x", admin_user_ids=[1]),
        storage=StorageConfig(db_path=str(tmp_path / "bot.db")),
    )
    bundle = build_bot_deps(db, config)
    yield bundle
    db.close()


def _join(deps: BotDeps) -> None:
    """Put user 555 into the system through a valid invite code."""
    inviter = deps.users.ensure(tg_user_id=1, username="admin")
    (code,) = deps.invites.issue(created_by=inviter, quota=3)
    result = route_message(deps, 555, "newbie", f"/start {code}")
    assert "欢迎" in result.text


def test_unknown_user_cannot_add(deps: BotDeps) -> None:
    result = route_message(deps, 555, "newbie", "/add vps")
    assert "邀请码" in result.text
    assert deps.users.find_by_tg_id(555) is None  # 不会因为发指令就被登记


def test_start_without_code_explains_invite(deps: BotDeps) -> None:
    assert "邀请码" in route_message(deps, 555, "newbie", "/start").text


def test_start_with_valid_code_joins(deps: BotDeps) -> None:
    _join(deps)
    assert deps.users.find_by_tg_id(555) is not None


def test_start_with_invalid_code(deps: BotDeps) -> None:
    assert "无效" in route_message(deps, 555, "newbie", "/start nope").text


def test_add_joins_multiple_words(deps: BotDeps) -> None:
    _join(deps)
    route_message(deps, 555, "newbie", "/add 日本 vps")
    rules = deps.rules.list_for_user(deps.users.find_by_tg_id(555) or 0)
    assert rules[0].keyword_raw == "日本 vps"
    assert rules[0].keyword_norm == "日本 vps"


def test_add_accepts_a_command_suffix(deps: BotDeps) -> None:
    _join(deps)
    route_message(deps, 555, "newbie", "/add@my_bot vps")
    assert len(deps.rules.list_for_user(deps.users.find_by_tg_id(555) or 0)) == 1


def test_del_uses_the_displayed_index(deps: BotDeps) -> None:
    _join(deps)
    route_message(deps, 555, "newbie", "/add vps")
    route_message(deps, 555, "newbie", "/add 显卡")
    assert "已删除" in route_message(deps, 555, "newbie", "/del 1").text
    left = deps.rules.list_for_user(deps.users.find_by_tg_id(555) or 0)
    assert [rule.keyword_raw for rule in left] == ["显卡"]


def test_del_without_number_shows_usage(deps: BotDeps) -> None:
    _join(deps)
    assert "用法" in route_message(deps, 555, "newbie", "/del").text


def test_del_out_of_range(deps: BotDeps) -> None:
    _join(deps)
    assert "不存在" in route_message(deps, 555, "newbie", "/del 9").text


def test_test_without_keyword_shows_usage(deps: BotDeps) -> None:
    _join(deps)
    assert "不能为空" in route_message(deps, 555, "newbie", "/test").text


def test_plain_text_gets_a_hint(deps: BotDeps) -> None:
    _join(deps)
    assert "/add" in route_message(deps, 555, "newbie", "vps").text


def test_unknown_command_lists_help(deps: BotDeps) -> None:
    _join(deps)
    assert "/list" in route_message(deps, 555, "newbie", "/wat").text


def test_help_works_before_joining(deps: BotDeps) -> None:
    assert "/add" in route_message(deps, 555, "newbie", "/help").text


def test_non_admin_cannot_use_admin_commands(deps: BotDeps) -> None:
    _join(deps)
    assert "权限" in route_message(deps, 555, "newbie", "/admin stats").text


def test_admin_can_use_admin_commands(deps: BotDeps) -> None:
    deps.users.ensure(tg_user_id=1, username="admin")
    assert "用户 1" in route_message(deps, 1, "admin", "/admin stats").text


def test_admin_does_not_need_an_invite(deps: BotDeps) -> None:
    # 管理员即使自己没走过邀请码，也应当能用管理指令
    result = route_message(deps, 1, "admin", "/admin stats")
    assert "邀请码" not in result.text
    assert "用户 0" in result.text


def test_callback_deletes_the_rule(deps: BotDeps) -> None:
    _join(deps)
    route_message(deps, 555, "newbie", "/add vps")
    rule = deps.rules.list_for_user(deps.users.find_by_tg_id(555) or 0)[0]
    result = route_callback(deps, 555, f"del:{rule.id}")
    assert result is not None
    assert "已删除" in result.text
    assert deps.rules.list_for_user(deps.users.find_by_tg_id(555) or 0) == []


def test_callback_cannot_touch_someone_elses_rule(deps: BotDeps) -> None:
    _join(deps)
    route_message(deps, 555, "newbie", "/add vps")
    rule = deps.rules.list_for_user(deps.users.find_by_tg_id(555) or 0)[0]
    deps.users.ensure(tg_user_id=777, username="other")
    result = route_callback(deps, 777, f"del:{rule.id}")
    assert result is not None and "不存在" in result.text
    assert len(deps.rules.list_for_user(deps.users.find_by_tg_id(555) or 0)) == 1


def test_callback_with_unknown_payload_is_ignored(deps: BotDeps) -> None:
    _join(deps)
    assert route_callback(deps, 555, "nope:1") is None
    assert route_callback(deps, 555, "del:abc") is None


@pytest.fixture()
def open_deps(tmp_path: Path):
    """Same wiring, but with registration open to anyone."""
    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    config = Config(
        telegram=TelegramConfig(bot_token="1:x", admin_user_ids=[1]),
        storage=StorageConfig(db_path=str(tmp_path / "bot.db")),
        limits=LimitsConfig(invite_required=False),
    )
    bundle = build_bot_deps(db, config)
    yield bundle
    db.close()


def test_open_registration_start_joins(open_deps: BotDeps) -> None:
    result = route_message(open_deps, 555, "newbie", "/start")
    assert "欢迎" in result.text
    assert open_deps.users.find_by_tg_id(555) is not None


def test_open_registration_allows_commands_without_start(open_deps: BotDeps) -> None:
    result = route_message(open_deps, 555, "newbie", "/add vps")
    assert "已添加" in result.text
    assert open_deps.users.find_by_tg_id(555) is not None


def test_open_registration_makes_invites_pointless(open_deps: BotDeps) -> None:
    assert "不需要邀请码" in route_message(open_deps, 555, "newbie", "/invite").text


def test_fixed_mode_invite_is_the_same_every_time(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    config = Config(
        telegram=TelegramConfig(bot_token="1:x", admin_user_ids=[1]),
        storage=StorageConfig(db_path=str(tmp_path / "bot.db")),
        limits=LimitsConfig(invite_mode="fixed"),
    )
    deps = build_bot_deps(db, config)
    first = route_message(deps, 555, "newbie", "/invite")
    assert first.text == route_message(deps, 555, "newbie", "/invite").text
    db.close()
