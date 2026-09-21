import dataclasses
import os
from pathlib import Path

import pytest

from nodeseek_bot.db import Database, DatabaseError
from nodeseek_bot.repository import ItemRepository, NewItem


@pytest.mark.skipif(os.name == "nt", reason="Windows lets SQLite write to a read-only file")
def test_readonly_database_reports_the_fix(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    db = Database(str(path))
    db.initialize()
    db.close()
    os.chmod(path, 0o444)
    try:
        with pytest.raises(DatabaseError, match="chown -R nodeseek:nodeseek"):
            Database(str(path)).initialize()
    finally:
        os.chmod(path, 0o644)


def test_fixed_invite_is_stable_and_reusable(tmp_path: Path) -> None:
    from nodeseek_bot.repository import InviteRepository, UserRepository

    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    uid = UserRepository(db).ensure(tg_user_id=1, username=None)
    invites = InviteRepository(db)
    code = invites.fixed(uid)
    assert invites.fixed(uid) == code
    assert invites.redeem(code) == uid
    assert invites.redeem(code) == uid
    db.close()


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
    db, _ = _repo(tmp_path)
    with pytest.raises(RuntimeError), db.tx() as conn:
        conn.execute("INSERT INTO meta (key, value) VALUES ('k', 'v')")
        raise RuntimeError("boom")
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM meta").fetchone()["n"] == 0
    db.close()


def _user(tmp_path: Path, tg_id: int = 1) -> tuple[Database, int]:
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
    assert users.ensure(tg_user_id=7, username="a") == users.ensure(tg_user_id=7, username="b")
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
