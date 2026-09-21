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


def test_invite_in_fixed_mode_reuses_one_code(env) -> None:
    from nodeseek_bot.bot.user_handlers import handle_invite
    from nodeseek_bot.repository import InviteRepository

    db, users, _, _, _ = env
    invites = InviteRepository(db)
    limits = LimitsConfig(invite_mode="fixed")
    first = handle_invite(users, invites, limits, 555, "u")
    second = handle_invite(users, invites, limits, 555, "u")
    assert first.text == second.text

    code = first.text.split("：", 1)[1].split("\n", 1)[0]
    assert invites.redeem(code) is not None
    assert invites.redeem(code) is not None  # 固定码可以反复用


def test_invite_is_pointless_when_registration_is_open(env) -> None:
    from nodeseek_bot.bot.user_handlers import handle_invite
    from nodeseek_bot.repository import InviteRepository

    db, users, _, _, _ = env
    limits = LimitsConfig(invite_required=False)
    result = handle_invite(users, InviteRepository(db), limits, 555, "u")
    assert "不需要邀请码" in result.text


def test_start_joins_directly_when_registration_is_open(env) -> None:
    from nodeseek_bot.bot.user_handlers import handle_start
    from nodeseek_bot.repository import InviteRepository

    db, users, _, _, _ = env
    limits = LimitsConfig(invite_required=False)
    result = handle_start(users, InviteRepository(db), limits, 777, "new", None)
    assert "欢迎" in result.text
    assert users.find_by_tg_id(777) is not None


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

    db, users, _, _, limits = env
    result = handle_start(users, InviteRepository(db), limits, 777, "new", None)
    assert "邀请码" in result.text


def test_start_without_payload_returns_help_for_known_user(env) -> None:
    from nodeseek_bot.bot.user_handlers import handle_start
    from nodeseek_bot.repository import InviteRepository

    db, users, _, _, limits = env
    _uid(users)
    text = handle_start(users, InviteRepository(db), limits, 555, "u", None).text
    assert "/add" in text


def test_start_with_valid_code_records_inviter(env) -> None:
    from nodeseek_bot.bot.user_handlers import handle_start
    from nodeseek_bot.repository import InviteRepository

    db, users, _, _, limits = env
    invites = InviteRepository(db)
    inviter = _uid(users)
    (code,) = invites.issue(created_by=inviter, quota=1)
    assert "欢迎" in handle_start(users, invites, limits, 777, "new", code).text
    with db.tx() as conn:
        row = conn.execute("SELECT invited_by FROM users WHERE tg_user_id = 777").fetchone()
    assert row["invited_by"] == inviter


def test_start_with_invalid_code_is_rejected(env) -> None:
    from nodeseek_bot.bot.user_handlers import handle_start
    from nodeseek_bot.repository import InviteRepository

    db, users, _, _, limits = env
    text = handle_start(users, InviteRepository(db), limits, 777, "new", "bad").text
    assert "无效" in text
