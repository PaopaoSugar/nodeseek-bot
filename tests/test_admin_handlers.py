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
